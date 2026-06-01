import logging
import httpx
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _get_api_key(env=None) -> str:
    if env is not None:
        try:
            val = getattr(env, "COOKRCP01_API_KEY", None)
            if val:
                return str(val)
        except Exception:
            pass
    import os
    return os.getenv("COOKRCP01_API_KEY", "")


async def search_recipe_from_public(korean_name: str, env=None) -> Optional[Dict[str, Any]]:
    """
    식품안전나라 COOKRCP01 API에서 한국어 메뉴명으로 레시피를 검색합니다.
    매칭되면 재료·조리법·영양정보·이미지를 반환, 없으면 None을 반환합니다.
    """
    api_key = _get_api_key(env)
    if not api_key:
        logger.warning("COOKRCP01_API_KEY가 설정되지 않았습니다.")
        return None

    # 검색어: 메뉴명의 첫 단어로 검색 (예: "된장찌개볶음밥" → "된장찌개")
    search_term = korean_name.strip()

    url = f"https://openapi.foodsafetykorea.go.kr/api/{api_key}/COOKRCP01/json/1/5/RCP_NM={search_term}"

    try:
        async with httpx.AsyncClient(headers={"Accept-Encoding": "identity"}) as client:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

        rows = data.get("COOKRCP01", {}).get("row", [])
        if not rows:
            # 첫 단어만으로 재시도 (예: "쇠고기무국" → "무국")
            first_word = search_term[:3]
            if first_word != search_term:
                url2 = f"https://openapi.foodsafetykorea.go.kr/api/{api_key}/COOKRCP01/json/1/5/RCP_NM={first_word}"
                resp2 = await client.get(url2, timeout=10.0)
                if resp2.is_success:
                    rows = resp2.json().get("COOKRCP01", {}).get("row", [])

        if not rows:
            return None

        row = rows[0]
        return _parse_recipe_row(row)

    except Exception as e:
        logger.error(f"COOKRCP01 API 호출 오류: {e}")
        return None


def _parse_recipe_row(row: dict) -> Dict[str, Any]:
    """COOKRCP01 응답 row를 내부 포맷으로 변환합니다."""
    # 재료 파싱: "쇠고기 200g, 무 300g, ..." 형태
    parts_raw = row.get("RCP_PARTS_DTLS", "")
    ingredients = _parse_ingredients(parts_raw)

    # 조리 과정: MANUAL01~20
    instructions = []
    for i in range(1, 21):
        key = f"MANUAL{i:02d}"
        step = row.get(key, "").strip()
        if step:
            instructions.append(step)

    # 영양정보
    nutrition = {
        "calories": row.get("INFO_ENG", "0") + " kcal",
        "carbs": row.get("INFO_CAR", "0") + "g",
        "protein": row.get("INFO_PRO", "0") + "g",
        "fat": row.get("INFO_FAT", "0") + "g",
        "sodium": row.get("INFO_NA", "0") + "mg",
    }

    # 대표 이미지
    image_url = row.get("ATT_FILE_NO_MAIN", "") or row.get("ATT_FILE_NO_MK", "")

    return {
        "korean_name": row.get("RCP_NM", ""),
        "category": row.get("RCP_PAT2", ""),
        "cooking_method": row.get("RCP_WAY2", ""),
        "ingredients_raw": parts_raw,
        "ingredients": ingredients,
        "instructions_ko": instructions,
        "nutrition": nutrition,
        "image_url": image_url,
    }


def _parse_ingredients(parts_raw: str):
    """재료 문자열을 [{"name": ..., "amount": ...}] 리스트로 파싱합니다."""
    if not parts_raw:
        return []

    import re
    # 줄바꿈 또는 쉼표로 분리
    items = re.split(r'[\n,]', parts_raw)
    result = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        # "재료명 분량" 형태 분리 (마지막 숫자+단위를 분량으로)
        match = re.match(r'^(.+?)\s+([\d/.]+\s*[가-힣a-zA-Z㎖㎖g㎏ml컵큰술작은술개줄장쪽]+.*)$', item)
        if match:
            result.append({"name": match.group(1).strip(), "amount": match.group(2).strip()})
        else:
            result.append({"name": item, "amount": ""})
    return result
