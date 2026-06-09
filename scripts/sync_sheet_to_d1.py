"""
구글 시트 → Cloudflare D1 전체 동기화

시트 컬럼:
  A: korean_name
  B: english_name
  C: image_url
  D: 미리보기 (=IMAGE 수식, 무시)
  E: ingredients  (줄바꿈 구분 텍스트)
  F: instructions (줄바꿈 구분 텍스트)
  G: memo
  H: seo_description
  I: calories
  J: carbs
  K: protein
  L: fat

- 비어있는 셀은 D1 기존값 유지 (사용자가 일부만 채워도 됨)
- image_url, english_name, ingredients, instructions 모두 반영

실행: python scripts/sync_sheet_to_d1.py
"""
import os, re, json, time, base64, logging, requests
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SHEET_ID            = os.environ["SHEET_ID"]
GOOGLE_SA_JSON      = os.environ.get("GOOGLE_SA_JSON", "")
GOOGLE_SA_JSON_PATH = os.environ.get("GOOGLE_SA_JSON_PATH", "")
CF_ACCOUNT_ID       = os.environ["CF_ACCOUNT_ID"]
CF_API_TOKEN        = os.environ["CF_API_TOKEN"]
D1_DATABASE_ID      = os.environ["D1_DATABASE_ID"]


# ── Google Sheets 인증 ────────────────────────────────────────────────────────

def _load_sa() -> dict:
    if GOOGLE_SA_JSON_PATH and Path(GOOGLE_SA_JSON_PATH).exists():
        return json.loads(Path(GOOGLE_SA_JSON_PATH).read_text())
    if GOOGLE_SA_JSON:
        return json.loads(GOOGLE_SA_JSON)
    raise RuntimeError("서비스 계정 JSON을 찾을 수 없습니다.")


def _get_token() -> str:
    sa = _load_sa()
    now = int(time.time())
    header  = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets.readonly",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
    }).encode()).rstrip(b"=")
    msg = header + b"." + payload
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(msg, asym_padding.PKCS1v15(), hashes.SHA256())
    jwt = (msg + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")).decode()
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt,
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def get_sheet_name(token: str) -> str:
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "sheets.properties"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["sheets"][0]["properties"]["title"]


def read_sheet(token: str, sname: str) -> list[dict]:
    """시트 A:L 읽어서 dict 리스트 반환."""
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A:L",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    values = r.json().get("values", [])
    if not values:
        return []

    header = [h.strip() for h in values[0]]

    def cell(row, col_name):
        try:
            idx = header.index(col_name)
            return row[idx].strip() if idx < len(row) else ""
        except ValueError:
            return ""

    result = []
    for row in values[1:]:
        name = cell(row, "korean_name")
        if not name:
            continue
        result.append({
            "korean_name":     name,
            "english_name":    cell(row, "english_name"),
            "image_url":       cell(row, "image_url"),
            "ingredients":     cell(row, "ingredients"),
            "instructions":    cell(row, "instructions"),
            "memo":            cell(row, "memo"),
            "seo_description": cell(row, "seo_description"),
            "calories":        cell(row, "calories"),
            "carbs":           cell(row, "carbs"),
            "protein":         cell(row, "protein"),
            "fat":             cell(row, "fat"),
        })
    return result


# ── Cloudflare D1 ─────────────────────────────────────────────────────────────

def d1_query(sql: str, params: list = None) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query"
    r = requests.post(url,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
        json={"sql": sql, **({"params": params} if params else {})},
        timeout=30,
    )
    r.raise_for_status()
    result = r.json()
    if not result.get("success"):
        raise RuntimeError(f"D1 오류: {result.get('errors')}")
    return result


def get_d1_recipes() -> dict:
    """D1의 현재 레시피를 korean_name 키로 반환."""
    result = d1_query(
        "SELECT korean_name, english_name, image_url, english_ingredients, instructions FROM recipes"
    )
    rows = result["result"][0].get("results", [])
    return {r["korean_name"]: r for r in rows}


# ── 텍스트 파싱 ───────────────────────────────────────────────────────────────

def parse_ingredients(text: str) -> list:
    """
    줄바꿈 구분 재료 텍스트 → JSON 배열
    예) "쌀 1컵\n쇠고기 100g" → [{"name":"쌀","amount":"1컵"}, ...]
    """
    if not text:
        return []
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # "재료명 양" 또는 "재료명" 형태 파싱
        # 마지막 공백+숫자/단위 부분을 amount로 분리
        m = re.match(r'^(.+?)\s+([\d½¼¾]+\s*\S*)$', line)
        if m:
            result.append({"name": m.group(1).strip(), "amount": m.group(2).strip()})
        else:
            result.append({"name": line, "amount": ""})
    return result


def parse_instructions(text: str) -> list:
    """
    줄바꿈 구분 조리법 텍스트 → JSON 배열
    예) "1. 씻는다\n2. 끓인다" → ["씻는다", "끓인다"]
    """
    if not text:
        return []
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 앞의 번호(1. 1) ①) 제거
        line = re.sub(r'^[\d]+[.)]\s*', '', line)
        line = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩]\s*', '', line)
        if line:
            result.append(line)
    return result


# ── 동기화 ────────────────────────────────────────────────────────────────────

def upsert_recipe(row: dict):
    """시트 1행을 D1에 upsert. 비어있는 필드는 기존값 유지."""
    ingredients_json = json.dumps(parse_ingredients(row["ingredients"]), ensure_ascii=False) \
        if row["ingredients"] else None
    instructions_json = json.dumps(parse_instructions(row["instructions"]), ensure_ascii=False) \
        if row["instructions"] else None

    # 영양정보 JSON 구성
    nutrition = {}
    if row.get("calories"):  nutrition["calories"] = row["calories"]
    if row.get("carbs"):     nutrition["carbs"] = row["carbs"]
    if row.get("protein"):   nutrition["protein"] = row["protein"]
    if row.get("fat"):       nutrition["fat"] = row["fat"]
    nutrition_json = json.dumps(nutrition, ensure_ascii=False) if nutrition else None

    seo_desc = row.get("seo_description", "")

    # INSERT: 신규면 전부 저장
    # UPDATE: 값이 있는 필드만 덮어씀 (빈 값이면 기존값 유지)
    d1_query(
        """
        INSERT INTO recipes (korean_name, english_name, image_url, english_ingredients, instructions, seo_description, nutrition_info)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(korean_name) DO UPDATE SET
            english_name        = CASE WHEN ? != '' THEN ? ELSE recipes.english_name END,
            image_url           = CASE WHEN ? != '' THEN ? ELSE recipes.image_url END,
            english_ingredients = CASE WHEN ? IS NOT NULL THEN ? ELSE recipes.english_ingredients END,
            instructions        = CASE WHEN ? IS NOT NULL THEN ? ELSE recipes.instructions END,
            seo_description     = CASE WHEN ? != '' THEN ? ELSE recipes.seo_description END,
            nutrition_info      = CASE WHEN ? IS NOT NULL THEN ? ELSE recipes.nutrition_info END
        """,
        [
            # INSERT 값
            row["korean_name"],
            row["english_name"],
            row["image_url"],
            ingredients_json or "[]",
            instructions_json or "[]",
            seo_desc,
            nutrition_json or "{}",
            # UPDATE 조건 + 값
            row["english_name"],    row["english_name"],
            row["image_url"],       row["image_url"],
            ingredients_json,       ingredients_json,
            instructions_json,      instructions_json,
            seo_desc,               seo_desc,
            nutrition_json,         nutrition_json,
        ],
    )


def main():
    logger.info("구글 시트 읽는 중...")
    token = _get_token()
    sname = get_sheet_name(token)
    sheet_rows = read_sheet(token, sname)
    logger.info(f"시트 행 수: {len(sheet_rows)}")

    updated = inserted = skipped = 0

    for row in sheet_rows:
        name = row["korean_name"]
        try:
            upsert_recipe(row)
            # 변경 여부 판단용 로그
            has_data = any([row["english_name"], row["image_url"], row["ingredients"], row["instructions"], row.get("seo_description"), row.get("calories")])
            if has_data:
                logger.info(f"동기화: {name} | 영어명={'✅' if row['english_name'] else '—'} "
                            f"이미지={'✅' if row['image_url'] else '—'} "
                            f"재료={'✅' if row['ingredients'] else '—'} "
                            f"조리법={'✅' if row['instructions'] else '—'}")
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            logger.error(f"실패 ({name}): {e}")
        time.sleep(0.1)  # D1 rate limit 방지

    logger.info(f"✅ 완료 — 동기화: {updated}개 / 메뉴명만: {skipped}개")


if __name__ == "__main__":
    main()
