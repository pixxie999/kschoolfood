import json
import logging
import os
import httpx
from typing import Dict, Any, Optional
from app.config import settings
from app.db_adapter import get_db_adapter

logger = logging.getLogger(__name__)

# JSON schema example for LLM prompting
JSON_FORMAT_PROMPT = """
You MUST return ONLY valid JSON in this exact structure:
{
  "english_name": "Descriptive English name",
  "english_ingredients": [
    {"name": "Ingredient name", "amount": "Amount"}
  ],
  "local_substitutes": [
    {"original": "Korean ingredient", "substitute": "Local substitute", "reason": "Reason"}
  ],
  "instructions": [
    "Step 1", "Step 2"
  ],
  "seo_description": "Short SEO description",
  "nutrition_info": {
    "calories": "350 kcal",
    "carbs": "40g",
    "protein": "15g",
    "fat": "10g"
  }
}
"""

async def call_gemini_api_async(prompt: str, api_key: str = None) -> Optional[Dict[str, Any]]:
    key = api_key or settings.GEMINI_API_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY is not configured.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"

    system_text = "You are a professional chef and SEO specialist. Translate the Korean school lunch dish name into a global recipe and localize it for Western markets. Provide short, concise instructions. Waste no tokens on explanation.\n" + JSON_FORMAT_PROMPT
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": system_text + "\n\n" + prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }
    
    async with httpx.AsyncClient(headers={"Accept-Encoding": "identity"}) as client:
        resp = await client.post(url, json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)

async def call_openai_api_async(prompt: str, api_key: str = None) -> Optional[Dict[str, Any]]:
    key = api_key or settings.OPENAI_API_KEY
    if not key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a professional chef. Translate the Korean dish into a localized recipe. No talk, JSON only.\n" + JSON_FORMAT_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        
        text = data["choices"][0]["message"]["content"]
        return json.loads(text)

def _get_setting(key: str, env=None, default: str = "") -> str:
    if env is not None:
        try:
            val = getattr(env, key, None)
            if val:
                return str(val)
        except Exception:
            pass
    return os.getenv(key, default)

async def translate_and_localize_recipe(korean_name: str, db_adapter=None, env=None) -> Optional[Dict[str, Any]]:
    """
    한국어 메뉴명을 영어 레시피, 대체 재료, SEO 설명 등으로 번역 및 로컬라이징합니다.
    경제적 처리를 위해 먼저 DB 캐시를 체크하여 이미 가공된 레시피가 있다면 즉시 반환하고,
    캐시 미스일 때만 비용 효율적인 LLM API를 호출한 뒤 DB에 저장(캐싱)합니다.
    """
    if not korean_name or korean_name.strip() in ["", "Rice", "Soup", "Side 1", "Side 2", "Side 3"]:
        return None

    if db_adapter is None:
        db_adapter = get_db_adapter()

    # 1. DB 캐시 검사
    try:
        cached_recipe = await db_adapter.fetch_one(
            "SELECT * FROM recipes WHERE korean_name = ?", (korean_name,)
        )
        if cached_recipe:
            logger.info(f"DB 캐시 히트: {korean_name}")
            return {
                "id": cached_recipe["id"],
                "korean_name": cached_recipe["korean_name"],
                "english_name": cached_recipe["english_name"],
                "english_ingredients": json.loads(cached_recipe["english_ingredients"]),
                "local_substitutes": json.loads(cached_recipe["local_substitutes"]),
                "instructions": json.loads(cached_recipe["instructions"]),
                "seo_description": cached_recipe["seo_description"],
                "nutrition_info": json.loads(cached_recipe["nutrition_info"]),
            }
    except Exception as e:
        logger.error(f"레시피 DB 캐시 검사 중 오류 발생: {e}")

    # 2. 캐시 미스 시 LLM 호출 (Gemini 또는 OpenAI)
    provider = _get_setting("ACTIVE_LLM_PROVIDER", env) or settings.ACTIVE_LLM_PROVIDER
    prompt = f"Translate and create a global localized recipe for: '{korean_name}'"
    result_dict = None

    gemini_key = _get_setting("GEMINI_API_KEY", env) or settings.GEMINI_API_KEY
    openai_key = _get_setting("OPENAI_API_KEY", env) or settings.OPENAI_API_KEY

    try:
        if provider == "gemini":
            logger.info(f"Gemini API 호출 (비용 효율적 모델 gemini-2.5-flash): {korean_name}")
            result_dict = await call_gemini_api_async(prompt, api_key=gemini_key)

        elif provider == "openai":
            logger.info(f"OpenAI API 호출 (비용 효율적 모델 gpt-4o-mini): {korean_name}")
            result_dict = await call_openai_api_async(prompt, api_key=openai_key)
        else:
            raise ValueError(f"지원하지 않는 LLM 제공자 설정: {provider}")

    except Exception as e:
        logger.error(f"LLM API 호출 또는 JSON 파싱 중 오류 발생: {e}")
        return None

    if not result_dict:
        return None

    # 3. 신규 가공 결과 DB에 캐싱 저장
    try:
        await db_adapter.execute(
            """
            INSERT INTO recipes (
                korean_name, english_name, english_ingredients, 
                local_substitutes, instructions, seo_description, nutrition_info
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                korean_name,
                result_dict.get("english_name"),
                json.dumps(result_dict.get("english_ingredients", [])),
                json.dumps(result_dict.get("local_substitutes", [])),
                json.dumps(result_dict.get("instructions", [])),
                result_dict.get("seo_description"),
                json.dumps(result_dict.get("nutrition_info", {})),
            )
        )
        saved_recipe = await db_adapter.fetch_one(
            "SELECT * FROM recipes WHERE korean_name = ?", (korean_name,)
        )
        if saved_recipe:
            return {
                "id": saved_recipe["id"],
                "korean_name": saved_recipe["korean_name"],
                "english_name": saved_recipe["english_name"],
                "english_ingredients": result_dict.get("english_ingredients"),
                "local_substitutes": result_dict.get("local_substitutes"),
                "instructions": result_dict.get("instructions"),
                "seo_description": saved_recipe["seo_description"],
                "nutrition_info": result_dict.get("nutrition_info"),
            }
    except Exception as e:
        logger.error(f"레시피 DB 캐시 저장 중 오류 발생: {e}")
        return {
            "id": None,
            "korean_name": korean_name,
            "english_name": result_dict.get("english_name"),
            "english_ingredients": result_dict.get("english_ingredients"),
            "local_substitutes": result_dict.get("local_substitutes"),
            "instructions": result_dict.get("instructions"),
            "seo_description": result_dict.get("seo_description"),
            "nutrition_info": result_dict.get("nutrition_info"),
        }

    return None

