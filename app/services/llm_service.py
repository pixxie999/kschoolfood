import json
import logging
import os
import httpx
from typing import Dict, Any, Optional
from app.config import settings
from app.db_adapter import get_db_adapter

logger = logging.getLogger(__name__)

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
  "seo_description": "Short SEO description under 160 chars",
  "nutrition_info": {
    "calories": "350 kcal",
    "carbs": "40g",
    "protein": "15g",
    "fat": "10g"
  }
}
"""

def _get_setting(key: str, env=None, default: str = "") -> str:
    if env is not None:
        try:
            val = getattr(env, key, None)
            if val:
                return str(val)
        except Exception:
            pass
    return os.getenv(key, default)


async def call_claude_api(prompt: str, api_key: str) -> Optional[Dict[str, Any]]:
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not configured.")
    logger.error(f"Claude API 키 길이: {len(api_key)}, 시작: {api_key[:10]}")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "system": "You are a professional chef and SEO specialist. Translate Korean school lunch dishes into English recipes for a global audience. Be concise. Return only valid JSON, no extra text.\n" + JSON_FORMAT_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(headers={"Accept-Encoding": "identity"}) as client:
        resp = await client.post(url, headers=headers, json=payload, timeout=30.0)
        if not resp.is_success:
            logger.error(f"Claude API 에러: {resp.text[:300]}")
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())


async def translate_and_localize_recipe(korean_name: str, db_adapter=None, env=None) -> Optional[Dict[str, Any]]:
    """
    D1 DB 캐시에서만 레시피를 조회합니다.
    레시피 번역은 GitHub Actions (precompute.yml)에서 사전 처리됩니다.
    """
    if not korean_name or korean_name.strip() in ["", "Rice", "Soup", "Side 1", "Side 2", "Side 3"]:
        return None

    if db_adapter is None:
        db_adapter = get_db_adapter()

    try:
        cached = await db_adapter.fetch_one(
            "SELECT * FROM recipes WHERE korean_name = ?", (korean_name,)
        )
        if cached:
            logger.info(f"DB 캐시 히트: {korean_name}")
            return {
                "id": cached["id"],
                "korean_name": cached["korean_name"],
                "english_name": cached["english_name"],
                "english_ingredients": json.loads(cached["english_ingredients"] or "[]"),
                "local_substitutes": json.loads(cached["local_substitutes"] or "[]"),
                "instructions": json.loads(cached["instructions"] or "[]"),
                "seo_description": cached["seo_description"],
                "nutrition_info": json.loads(cached["nutrition_info"] or "{}"),
                "image_url": cached.get("image_url", ""),
            }
    except Exception as e:
        logger.error(f"DB 캐시 조회 오류: {e}")

    logger.info(f"레시피 미캐시 (사전 처리 대기 중): {korean_name}")
    return None


async def _save_and_return(korean_name: str, result_dict: dict, db_adapter) -> Optional[Dict[str, Any]]:
    try:
        await db_adapter.execute(
            """
            INSERT INTO recipes (
                korean_name, english_name, english_ingredients,
                local_substitutes, instructions, seo_description,
                nutrition_info, image_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(korean_name) DO UPDATE SET
                english_name = excluded.english_name,
                english_ingredients = excluded.english_ingredients,
                local_substitutes = excluded.local_substitutes,
                instructions = excluded.instructions,
                seo_description = excluded.seo_description,
                nutrition_info = excluded.nutrition_info,
                image_url = COALESCE(excluded.image_url, recipes.image_url)
            """,
            (
                korean_name,
                result_dict.get("english_name"),
                json.dumps(result_dict.get("english_ingredients", []), ensure_ascii=False),
                json.dumps(result_dict.get("local_substitutes", []), ensure_ascii=False),
                json.dumps(result_dict.get("instructions", []), ensure_ascii=False),
                result_dict.get("seo_description"),
                json.dumps(result_dict.get("nutrition_info", {}), ensure_ascii=False),
                result_dict.get("image_url", ""),
            )
        )
        saved = await db_adapter.fetch_one(
            "SELECT * FROM recipes WHERE korean_name = ?", (korean_name,)
        )
        if saved:
            return {
                "id": saved["id"],
                "korean_name": saved["korean_name"],
                "english_name": saved["english_name"],
                "english_ingredients": result_dict.get("english_ingredients", []),
                "local_substitutes": result_dict.get("local_substitutes", []),
                "instructions": result_dict.get("instructions", []),
                "seo_description": saved["seo_description"],
                "nutrition_info": result_dict.get("nutrition_info", {}),
                "image_url": result_dict.get("image_url", ""),
            }
    except Exception as e:
        logger.error(f"레시피 DB 저장 오류: {e}")

    return None
