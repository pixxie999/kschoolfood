import os
import httpx
import re
import json
import logging
from typing import Dict, Any, Optional
from app.config import settings

logger = logging.getLogger(__name__)

ALLERGY_MAP = {
    "1": "Egg", "2": "Milk", "3": "Buckwheat", "4": "Peanut",
    "5": "Soy", "6": "Wheat", "7": "Mackerel", "8": "Crab",
    "9": "Shrimp", "10": "Pork", "11": "Peach", "12": "Tomato",
    "13": "Sulfites", "14": "Walnut", "15": "Chicken", "16": "Beef",
    "17": "Squid", "18": "Shellfish"
}

def parse_allergies(raw_name: str) -> list:
    """'불고기 (5.6.16)' → ['Soy', 'Wheat', 'Beef']"""
    match = re.search(r'\(([0-9.]+)\)', raw_name)
    if not match:
        return []
    codes = [c.strip() for c in match.group(1).split('.') if c.strip()]
    return [ALLERGY_MAP[c] for c in codes if c in ALLERGY_MAP]

def clean_dish_name(name: str) -> str:
    name = re.sub(r'\([0-9.\s*~]+\)', '', name)
    name = re.sub(r'\s+[0-9.\s*]+$', '', name)
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
    neis_key = _get_setting("NEIS_API_KEY", env) or settings.NEIS_API_KEY
    office_code = _get_setting("OFFICE_CODE", env) or settings.OFFICE_CODE
    school_code = _get_setting("SCHOOL_CODE", env) or settings.SCHOOL_CODE

    if not neis_key:
        logger.warning("NEIS_API_KEY가 설정되지 않았습니다.")
        return None

    url = "https://open.neis.go.kr/hub/mealServiceDietInfo"
    params = {
        "KEY": neis_key, "Type": "json", "pIndex": 1, "pSize": 100,
        "ATPT_OFCDC_SC_CODE": office_code, "SD_SCHUL_CODE": school_code,
        "MLSV_YMD": date_str
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        if "mealServiceDietInfo" not in data:
            logger.info(f"{date_str} 급식 정보 없음 (주말/방학/공휴일)")
            return None

        row = data["mealServiceDietInfo"][1]["row"][0]
        raw_ddish = row.get("DDISH_NM", "")
        calories = row.get("CAL_INFO", "0 Kcal")

        dishes_raw = re.split(r'<br\s*/?>|\n', raw_ddish)
        dishes_raw = [d.strip() for d in dishes_raw if d.strip()]

        # 알레르기 파싱 (정제 전 원본에서)
        allergy_data = {}
        keys = ["rice", "soup", "side1", "side2", "side3"]
        for i, raw in enumerate(dishes_raw[:5]):
            allergy_data[keys[i]] = parse_allergies(raw)

        cleaned = [clean_dish_name(d) for d in dishes_raw]

        rice  = cleaned[0] if len(cleaned) > 0 else "Rice"
        soup  = cleaned[1] if len(cleaned) > 1 else "Soup"
        side1 = cleaned[2] if len(cleaned) > 2 else "Side 1"
        side2 = cleaned[3] if len(cleaned) > 3 else "Side 2"
        # 5번째 dish만 사용 (6번째 이후는 무시) — 합쳐진 문자열로 저장하면 레시피 매칭 불가
        side3 = cleaned[4] if len(cleaned) > 4 else "Side 3"

        return {
            "date": date_str, "rice": rice, "soup": soup,
            "side1": side1, "side2": side2, "side3": side3,
            "calories": calories,
            "allergies": json.dumps(allergy_data, ensure_ascii=False)
        }

    except Exception as e:
        logger.error(f"NEIS API 오류: {e}")
        return None
