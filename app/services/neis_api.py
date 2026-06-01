import httpx
import re
import logging
from typing import Dict, Any, Optional
from app.config import settings

logger = logging.getLogger(__name__)

def clean_dish_name(name: str) -> str:
    """
    급식 메뉴명에서 알레르기 유발 정보(예: (5.6.13.) 등)를 제거합니다.
    """
    # 1. 괄호로 감싸진 알레르기 번호 제거 (예: "(1.2.5.6.)" 또는 "(5.)")
    name = re.sub(r'\([0-9.\s*~]+\)', '', name)
    # 2. 괄호 없이 공백 후 끝에 붙어 있는 숫자 및 마침표 제거 (예: " 5.6.13.")
    name = re.sub(r'\s+[0-9.\s*]+$', '', name)
    # 3. 특수 문자 정리 및 양끝 공백 제거
    name = name.replace('*', '').strip()
    return name

def _get_setting(key: str, env=None, default: str = "") -> str:
    if env is not None:
        try:
            val = getattr(env, key, None)
            if val:
                return str(val)
        except Exception:
            pass
    return os.getenv(key, default)

async def fetch_meal_from_neis(date_str: str, env=None) -> Optional[Dict[str, Any]]:
    """
    NEIS Open API로부터 특정 날짜(YYYYMMDD)의 급식 식단을 가져와 파싱합니다.
    데이터가 없거나 오류 발생 시 None을 반환합니다.
    """
    neis_key = _get_setting("NEIS_API_KEY", env) or settings.NEIS_API_KEY
    office_code = _get_setting("OFFICE_CODE", env) or settings.OFFICE_CODE
    school_code = _get_setting("SCHOOL_CODE", env) or settings.SCHOOL_CODE

    if not neis_key:
        logger.warning("NEIS_API_KEY가 설정되지 않았습니다. API 호출을 건너뜁니다.")
        return None

    url = "https://open.neis.go.kr/hub/mealServiceDietInfo"
    params = {
        "KEY": neis_key,
        "Type": "json",
        "pIndex": 1,
        "pSize": 100,
        "ATPT_OFCDC_SC_CODE": office_code,
        "SD_SCHUL_CODE": school_code,
        "MLSV_YMD": date_str
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        if "mealServiceDietInfo" not in data:
            # API 응답에 급식 정보가 없는 경우 (예: 주말, 방학, 공휴일 등)
            logger.info(f"{date_str} 날짜의 급식 정보가 NEIS에 존재하지 않습니다.")
            return None

        # row 데이터 추출
        row = data["mealServiceDietInfo"][1]["row"][0]
        raw_ddish = row.get("DDISH_NM", "")
        calories = row.get("CAL_INFO", "0 Kcal")

        # <br/> 태그 또는 줄바꿈으로 메뉴 분할
        dishes = re.split(r'<br\s*/?>|\n', raw_ddish)
        cleaned_dishes = [clean_dish_name(dish) for dish in dishes if dish.strip()]

        if not cleaned_dishes:
            return None

        # 5칸 식판 UI에 맞춤형 배치 (밥, 국, 반찬 3종)
        rice = cleaned_dishes[0] if len(cleaned_dishes) > 0 else "Rice"
        soup = cleaned_dishes[1] if len(cleaned_dishes) > 1 else "Soup"
        side1 = cleaned_dishes[2] if len(cleaned_dishes) > 2 else "Side 1"
        side2 = cleaned_dishes[3] if len(cleaned_dishes) > 3 else "Side 2"
        # 5번째 이후의 모든 메뉴는 side3에 쉼표로 연결하여 저장
        side3 = ", ".join(cleaned_dishes[4:]) if len(cleaned_dishes) > 4 else "Side 3"

        return {
            "date": date_str,
            "rice": rice,
            "soup": soup,
            "side1": side1,
            "side2": side2,
            "side3": side3,
            "calories": calories
        }

    except Exception as e:
        logger.error(f"NEIS API 호출 중 오류 발생: {e}")
        return None
