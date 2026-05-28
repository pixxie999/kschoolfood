import json
import logging
from typing import Dict, Any, Optional
from app.config import settings
from app.schemas import RecipeTranslationResponse
from app.db_adapter import get_db_adapter

logger = logging.getLogger(__name__)

# LLM API 지연 로딩용 전역 변수
_gemini_model = None
_openai_client = None

def get_gemini_model():
    global _gemini_model
    if _gemini_model is None:
        import google.generativeai as genai
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY가 구성되지 않았습니다.")
        genai.configure(api_key=settings.GEMINI_API_KEY)
        # 토큰 절약과 경제적인 호출을 위해 비용 효율적인 gemini-2.5-flash 모델 적용
        _gemini_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": RecipeTranslationResponse,
                "temperature": 0.2,
            },
            system_instruction="You are a professional chef and SEO specialist. Translate the Korean school lunch dish name into a global recipe and localize it for Western markets. Provide short, concise instructions. Waste no tokens on explanation."
        )
    return _gemini_model

def get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY가 구성되지 않았습니다.")
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client

async def translate_and_localize_recipe(korean_name: str, db_adapter=None) -> Optional[Dict[str, Any]]:
    """
    한국어 메뉴명을 영어 레시피, 대체 재료, SEO 설명 등으로 번역 및 로컬라이징합니다.
    경제적 처리를 위해 먼저 DB 캐시를 체크하여 이미 가공된 레시피가 있다면 즉시 반환하고,
    캐시 미스일 때만 비용 효율적인 LLM API를 호출한 뒤 DB에 저장(캐싱)합니다.
    """
    if not korean_name or korean_name.strip() in ["", "Rice", "Soup", "Side 1", "Side 2", "Side 3"]:
        return None

    # DB 어댑터가 주어지지 않은 경우 기본 어댑터 사용
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
    provider = settings.ACTIVE_LLM_PROVIDER
    prompt = f"Translate and create a global localized recipe for: '{korean_name}'"
    result_dict = None

    try:
        if provider == "gemini":
            logger.info(f"Gemini API 호출 (비용 효율적 모델 gemini-1.5-flash): {korean_name}")
            model = get_gemini_model()
            # 비동기 세션을 타지 않고 동기식 호출을 비동기로 래핑하거나 직접 동기 호출 처리
            response = model.generate_content(prompt)
            result_dict = json.loads(response.text)

        elif provider == "openai":
            logger.info(f"OpenAI API 호출 (비용 효율적 모델 gpt-4o-mini): {korean_name}")
            client = get_openai_client()
            # gpt-4o-mini를 기본 비용 효율 모델로 설정하고 structured output 적용
            response = client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a professional chef. Translate the Korean dish into a localized recipe. No talk, JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format=RecipeTranslationResponse,
                temperature=0.2,
            )
            result_dict = json.loads(response.choices[0].message.content)
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
        # 방금 저장한 레시피 데이터 다시 가져오기 (ID 포함 반환용)
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
        # DB 저장에 실패하더라도 메모리 결과는 리턴하여 사용자 서비스 제공 보장
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
