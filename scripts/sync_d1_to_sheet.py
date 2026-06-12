"""
Cloudflare D1 → 구글 시트 역방향 동기화

D1에는 있지만 시트에 없는 레시피를 시트 하단에 새 행으로 추가합니다.
이미 시트에 있는 행은 건드리지 않습니다.

시트 컬럼:
  A: korean_name
  B: english_name
  C: image_url
  D: 미리보기 (=IMAGE 수식)
  E: ingredients  (줄바꿈 구분 텍스트)
  F: instructions (줄바꿈 구분 텍스트)
  G: memo
  H: seo_description
  I: calories
  J: carbs
  K: protein
  L: fat

실행: python scripts/sync_d1_to_sheet.py
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


def _get_token(scope: str = "https://www.googleapis.com/auth/spreadsheets") -> str:
    sa = _load_sa()
    now = int(time.time())
    header  = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": sa["client_email"],
        "scope": scope,
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


def get_sheet_korean_names(token: str, sname: str) -> set:
    """시트 A열(korean_name)을 읽어 집합으로 반환."""
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A:A",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    values = r.json().get("values", [])
    # 첫 행(헤더) 제외
    return {row[0].strip() for row in values[1:] if row and row[0].strip()}


def append_rows_to_sheet(token: str, sname: str, rows: list[list]):
    """시트 하단에 행 추가 (batchUpdate 대신 append 사용)."""
    r = requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A:L:append",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"values": rows},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


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


def get_d1_recipes() -> list[dict]:
    """D1 전체 레시피 조회."""
    result = d1_query(
        "SELECT korean_name, english_name, image_url, "
        "english_ingredients, instructions, seo_description, nutrition_info "
        "FROM recipes ORDER BY id ASC"
    )
    return result["result"][0].get("results", [])


# ── JSON → 텍스트 변환 (시트용) ───────────────────────────────────────────────

def ingredients_to_text(json_str: str) -> str:
    """[{"name":"쌀","amount":"1컵"},...] → '쌀 1컵\n쇠고기 100g'"""
    try:
        items = json.loads(json_str or "[]")
        lines = []
        for i in items:
            name   = i.get("name", "").strip()
            amount = i.get("amount", "").strip()
            lines.append(f"{name} {amount}".strip() if amount else name)
        return "\n".join(lines)
    except Exception:
        return ""


def instructions_to_text(json_str: str) -> str:
    """["씻는다","끓인다"] → '1. 씻는다\n2. 끓인다'"""
    try:
        steps = json.loads(json_str or "[]")
        return "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
    except Exception:
        return ""


def parse_nutrition(json_str: str) -> dict:
    try:
        return json.loads(json_str or "{}")
    except Exception:
        return {}


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("=== D1 → 구글 시트 역방향 동기화 시작 ===")

    token = _get_token()
    sname = get_sheet_name(token)
    logger.info(f"시트명: {sname}")

    # 시트에 이미 있는 메뉴명 수집
    existing_names = get_sheet_korean_names(token, sname)
    logger.info(f"시트 기존 레시피: {len(existing_names)}개")

    # D1 전체 레시피 조회
    d1_recipes = get_d1_recipes()
    logger.info(f"D1 전체 레시피: {len(d1_recipes)}개")

    # 시트에 없는 것만 필터
    missing = [r for r in d1_recipes if r["korean_name"] not in existing_names]
    logger.info(f"시트에 없는 레시피: {len(missing)}개")

    if not missing:
        logger.info("추가할 레시피 없음 — 종료")
        return

    # 시트에 추가할 행 구성 (A~L)
    new_rows = []
    for r in missing:
        nutrition = parse_nutrition(r.get("nutrition_info"))
        new_rows.append([
            r["korean_name"],                                    # A
            r.get("english_name") or "",                        # B
            r.get("image_url") or "",                           # C
            f'=IMAGE(C{len(existing_names) + 1 + len(new_rows) + 1})' if r.get("image_url") else "",  # D 미리보기
            ingredients_to_text(r.get("english_ingredients")),  # E
            instructions_to_text(r.get("instructions")),        # F
            "",                                                  # G memo (비워둠)
            r.get("seo_description") or "",                     # H
            nutrition.get("calories", ""),                      # I
            nutrition.get("carbs", ""),                         # J
            nutrition.get("protein", ""),                       # K
            nutrition.get("fat", ""),                           # L
        ])

    # 100행씩 나눠서 append (시트 API 제한 대비)
    CHUNK = 100
    total_added = 0
    for i in range(0, len(new_rows), CHUNK):
        chunk = new_rows[i:i + CHUNK]
        append_rows_to_sheet(token, sname, chunk)
        total_added += len(chunk)
        logger.info(f"  시트 추가: {total_added}/{len(new_rows)}")
        time.sleep(0.5)

    logger.info(f"✅ 완료 — {total_added}개 레시피를 시트에 추가했습니다.")

    # 추가된 메뉴명 출력
    for r in missing:
        logger.info(f"  + {r['korean_name']} / {r.get('english_name') or '(영어명 없음)'}")


if __name__ == "__main__":
    main()
