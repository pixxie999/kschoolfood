"""
GitHub Actions에서 실행: NEIS 급식 메뉴를 조회하고 Claude로 레시피를 번역하여 D1에 저장합니다.
"""
import os
import json
import time
import logging
import requests
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

NEIS_API_KEY = os.environ["NEIS_API_KEY"]
OFFICE_CODE = os.environ.get("OFFICE_CODE", "K10")
SCHOOL_CODE = os.environ.get("SCHOOL_CODE", "7801106")
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
COOKRCP01_API_KEY = os.environ.get("COOKRCP01_API_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
CF_ACCOUNT_ID = os.environ["CF_ACCOUNT_ID"]
CF_API_TOKEN = os.environ["CF_API_TOKEN"]
D1_DATABASE_ID = os.environ["D1_DATABASE_ID"]

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

JSON_FORMAT_PROMPT = """Return ONLY JSON: {"english_name":"...","english_ingredients":[{"name":"...","amount":"..."}],"local_substitutes":[{"original":"...","substitute":"...","reason":"..."}],"instructions":["step1","step2"],"seo_description":"under 160 chars","nutrition_info":{"calories":"kcal","carbs":"g","protein":"g","fat":"g"}}"""


def d1_query(sql: str, params: list = None) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    result = r.json()
    if not result.get("success"):
        raise RuntimeError(f"D1 오류: {result.get('errors')}")
    return result


def get_existing_recipes() -> set:
    result = d1_query("SELECT korean_name FROM recipes")
    rows = result["result"][0].get("results", [])
    return {row["korean_name"] for row in rows}


def fetch_neis_menu(date_str: str) -> list:
    """NEIS API로 급식 메뉴 조회. date_str: YYYYMMDD"""
    url = "https://open.neis.go.kr/hub/mealServiceDietInfo"
    params = {
        "KEY": NEIS_API_KEY,
        "Type": "json",
        "pIndex": 1,
        "pSize": 1,
        "ATPT_OFCDC_SC_CODE": OFFICE_CODE,
        "SD_SCHUL_CODE": SCHOOL_CODE,
        "MLSV_YMD": date_str,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        rows = data.get("mealServiceDietInfo", [{}])[1].get("row", [])
        if not rows:
            return []
        dish_str = rows[0].get("DDISH_NM", "")
        dishes = []
        for d in dish_str.split("<br/>"):
            name = d.strip()
            import re
            name = re.sub(r'\([\d,]+\)', '', name).strip()
            if name:
                dishes.append(name)
        return dishes
    except Exception as e:
        logger.error(f"NEIS API 오류 ({date_str}): {e}")
        return []


def _clean_for_search(name: str) -> str:
    """COOKRCP01 검색을 위해 메뉴명 정제:
    - 알레르기 번호 제거: '불고기 (5.6.10)' → '불고기'
    - 쉼표 이후 제거: '배추김치, 우유' → '배추김치'
    - 괄호 내용 제거: '너비아니구이(오븐)' → '너비아니구이'
    """
    import re
    name = name.split(",")[0]  # 쉼표 첫 번째 항목만
    name = re.sub(r'\s*[\(\（][^）)]*[\)\）]', '', name)  # 괄호 제거
    return name.strip()


def search_cookrcp01(korean_name: str) -> dict:
    if not COOKRCP01_API_KEY:
        return {}
    # 1차: 정제된 전체 이름으로 검색
    clean_name = _clean_for_search(korean_name)
    search_terms = [clean_name]
    # 2차: 앞 4글자
    if len(clean_name) > 4:
        search_terms.append(clean_name[:4])
    # 3차: 앞 3글자
    if len(clean_name) > 3:
        search_terms.append(clean_name[:3])

    try:
        rows = []
        for term in search_terms:
            url = f"https://openapi.foodsafetykorea.go.kr/api/{COOKRCP01_API_KEY}/COOKRCP01/json/1/5/RCP_NM={term}"
            r = requests.get(url, timeout=10)
            data = r.json() if r.ok else {}
            rows = data.get("COOKRCP01", {}).get("row", [])
            if rows:
                break
            time.sleep(0.2)
        if not rows:
            return {}
        row = rows[0]
        instructions = []
        for i in range(1, 21):
            step = row.get(f"MANUAL{i:02d}", "").strip()
            if step:
                instructions.append(step)
        return {
            "category": row.get("RCP_PAT2", ""),
            "cooking_method": row.get("RCP_WAY2", ""),
            "ingredients_raw": row.get("RCP_PARTS_DTLS", ""),
            "instructions_ko": instructions,
            "nutrition": {
                "calories": row.get("INFO_ENG", "0") + " kcal",
                "carbs": row.get("INFO_CAR", "0") + "g",
                "protein": row.get("INFO_PRO", "0") + "g",
                "fat": row.get("INFO_FAT", "0") + "g",
                "sodium": row.get("INFO_NA", "0") + "mg",
            },
            "image_url": row.get("ATT_FILE_NO_MAIN", "") or row.get("ATT_FILE_NO_MK", ""),
        }
    except Exception as e:
        logger.warning(f"COOKRCP01 오류 ({korean_name}): {e}")
        return {}


def call_claude(prompt: str) -> dict:
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 600,
        "system": "Korean→English recipe translator. Return only valid JSON, no extra text. " + JSON_FORMAT_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Claude API 오류 {r.status_code}: {r.text[:200]}")
    text = r.json()["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def translate_recipe(korean_name: str) -> dict:
    public = search_cookrcp01(korean_name)
    if public:
        prompt = (
            f"Korean dish: '{korean_name}'\n"
            f"Category: {public.get('category', '')}\n"
            f"Cooking method: {public.get('cooking_method', '')}\n"
            f"Ingredients (Korean): {public.get('ingredients_raw', '')}\n"
            f"Instructions (Korean): {chr(10).join(public.get('instructions_ko', []))}\n\n"
            "Translate the above Korean recipe to English. "
            "Suggest Western substitutes for hard-to-find Korean ingredients."
        )
    else:
        prompt = f"Translate and create a global localized recipe for Korean school lunch dish: '{korean_name}'"

    result = call_claude(prompt)

    if public:
        if public.get("nutrition"):
            result["nutrition_info"] = public["nutrition"]
        result["image_url"] = public.get("image_url", "")
    else:
        result["image_url"] = ""

    return result


def save_recipe(korean_name: str, data: dict):
    d1_query(
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
        [
            korean_name,
            data.get("english_name", ""),
            json.dumps(data.get("english_ingredients", []), ensure_ascii=False),
            json.dumps(data.get("local_substitutes", []), ensure_ascii=False),
            json.dumps(data.get("instructions", []), ensure_ascii=False),
            data.get("seo_description", ""),
            json.dumps(data.get("nutrition_info", {}), ensure_ascii=False),
            data.get("image_url", ""),
        ],
    )


def fetch_pexels_image(english_name: str, korean_name: str = "") -> str:
    """Pexels에서 요리 이미지를 검색합니다.
    1차: '{english_name} korean food' 검색
    2차: 결과 없으면 '{english_name} food dish' 로 재시도
    """
    if not PEXELS_API_KEY or not english_name:
        return ""
    headers = {"Authorization": PEXELS_API_KEY}
    queries = [
        f"{english_name} korean food",
        f"{english_name} food dish",
        f"{english_name}",
    ]
    for query in queries:
        try:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": 3, "orientation": "landscape"},
                headers=headers,
                timeout=10,
            )
            if r.ok:
                photos = r.json().get("photos", [])
                if photos:
                    # large2x: 1880px 고해상도
                    return photos[0]["src"].get("large2x") or photos[0]["src"].get("large", "")
        except Exception as e:
            logger.warning(f"Pexels 오류 ({query}): {e}")
    return ""


def get_recipes_without_images() -> list:
    """image_url이 없거나 Unsplash URL인 레시피 목록 반환 (Pexels로 교체 대상 포함)."""
    result = d1_query(
        "SELECT korean_name, english_name FROM recipes "
        "WHERE image_url IS NULL OR image_url = '' OR image_url LIKE '%unsplash%'"
    )
    rows = result["result"][0].get("results", [])
    return rows


def update_recipe_image(korean_name: str, image_url: str):
    d1_query(
        "UPDATE recipes SET image_url = ? WHERE korean_name = ?",
        [image_url, korean_name],
    )


SKIP_NAMES = {"", "Rice", "Soup", "Side 1", "Side 2", "Side 3"}


def main():
    today = datetime.now()
    # 14일 → 7일로 단축 (비용 절감: NEIS API 호출 절반 감소)
    dates = [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]

    all_dishes = set()
    for date_str in dates:
        dishes = fetch_neis_menu(date_str)
        logger.info(f"{date_str}: {dishes}")
        all_dishes.update(d for d in dishes if d not in SKIP_NAMES and len(d) > 1)
        time.sleep(0.3)

    existing = get_existing_recipes()
    to_translate = [d for d in all_dishes if d not in existing]
    logger.info(f"번역 필요: {len(to_translate)}개 / 전체: {len(all_dishes)}개")

    for name in to_translate:
        logger.info(f"번역 중: {name}")
        try:
            result = translate_recipe(name)
            # COOKRCP01 이미지 없으면 Unsplash로 보완
            if not result.get("image_url") and PEXELS_API_KEY:
                result["image_url"] = fetch_pexels_image(result.get("english_name", ""), name)
                time.sleep(0.5)
            save_recipe(name, result)
            logger.info(f"저장 완료: {name} → {result.get('english_name', '')} | 이미지: {'있음' if result.get('image_url') else '없음'}")
        except Exception as e:
            logger.error(f"실패 ({name}): {e}")
        time.sleep(1)

    # 기존 레시피 중 이미지 없는 것 보완 (COOKRCP01 → Unsplash 순)
    no_img = get_recipes_without_images()
    logger.info(f"이미지 없는 기존 레시피: {len(no_img)}개 보완 중")
    for row in no_img:
        img = ""
        # 1순위: COOKRCP01 재시도 (정제된 이름으로)
        if COOKRCP01_API_KEY:
            public = search_cookrcp01(row["korean_name"])
            img = public.get("image_url", "")
            time.sleep(0.3)
        # 2순위: Pexels
        if not img and PEXELS_API_KEY:
            img = fetch_pexels_image(row.get("english_name", ""), row["korean_name"])
            time.sleep(0.5)
        if img:
            update_recipe_image(row["korean_name"], img)
            logger.info(f"이미지 추가: {row['korean_name']}")

    logger.info("완료")


if __name__ == "__main__":
    main()
