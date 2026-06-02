"""
D1 레시피를 구글 시트로 내보냅니다.
- 시트에 없는 레시피 → 새 행 추가
- 시트에 이미 있는 레시피 → 건드리지 않음 (사용자 편집 보존)
- 헤더: korean_name | image_url | english_name | memo

실행 후 sync_images.py로 시트 → D1 방향 동기화
"""
import os
import json
import logging
import time
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SHEET_ID       = os.environ["SHEET_ID"]
GOOGLE_SA_JSON = os.environ["GOOGLE_SA_JSON"]
CF_ACCOUNT_ID  = os.environ["CF_ACCOUNT_ID"]
CF_API_TOKEN   = os.environ["CF_API_TOKEN"]
D1_DATABASE_ID = os.environ["D1_DATABASE_ID"]

SHEET_NAME = "Sheet1"  # 시트 탭 이름 (기본값)


# ── Google Sheets API (JWT 직접 인증) ──────────────────────────────────────

def _get_access_token() -> str:
    import base64, hashlib, hmac, struct, time as t
    sa = json.loads(GOOGLE_SA_JSON)
    now = int(t.time())

    header  = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
    }).encode()).rstrip(b"=")

    msg = header + b"." + payload

    # RSA-SHA256 서명 (cryptography 라이브러리 사용)
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    signature = private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")

    jwt = (msg + b"." + sig_b64).decode()
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt,
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def sheets_get(token: str, range_: str) -> list:
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{range_}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("values", [])


def sheets_append(token: str, rows: list[list]):
    if not rows:
        return
    r = requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{SHEET_NAME}!A1:append",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        json={"values": rows},
        timeout=15,
    )
    r.raise_for_status()


def sheets_update_cell(token: str, row: int, col: int, value: str):
    """1-indexed row/col"""
    col_letter = chr(64 + col)
    range_ = f"{SHEET_NAME}!{col_letter}{row}"
    r = requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{range_}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"valueInputOption": "RAW"},
        json={"values": [[value]]},
        timeout=15,
    )
    r.raise_for_status()


# ── Cloudflare D1 ──────────────────────────────────────────────────────────

def d1_query(sql: str, params: list = None) -> dict:
    url = (f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
           f"/d1/database/{D1_DATABASE_ID}/query")
    r = requests.post(url,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
        json={"sql": sql, **({"params": params} if params else {})},
        timeout=30)
    r.raise_for_status()
    result = r.json()
    if not result.get("success"):
        raise RuntimeError(f"D1 오류: {result.get('errors')}")
    return result


def get_all_recipes() -> list[dict]:
    result = d1_query(
        "SELECT korean_name, english_name, image_url FROM recipes ORDER BY id"
    )
    return result["result"][0].get("results", [])


# ── 메인 ───────────────────────────────────────────────────────────────────

HEADERS = ["korean_name", "image_url", "english_name", "memo"]

def main():
    logger.info("Google Sheets 토큰 발급 중...")
    token = _get_access_token()

    # 시트 현재 데이터 읽기
    values = sheets_get(token, f"{SHEET_NAME}!A:D")

    # 헤더 없으면 추가
    if not values:
        sheets_append(token, [HEADERS])
        values = [HEADERS]
        logger.info("헤더 행 추가")

    # 시트에 있는 korean_name 목록 (1행=헤더 제외)
    sheet_names = {}  # korean_name → row번호 (1-indexed)
    for i, row in enumerate(values[1:], start=2):
        if row:
            sheet_names[row[0]] = i

    # D1 레시피 전체 조회
    recipes = get_all_recipes()
    logger.info(f"D1 레시피 수: {len(recipes)}, 시트 기존 행: {len(sheet_names)}")

    new_rows = []
    updated = 0
    for recipe in recipes:
        name = recipe["korean_name"]
        if name in sheet_names:
            # 이미 시트에 있음 → image_url 컬럼(B)만 비어 있으면 D1 값으로 채우기
            row_idx = sheet_names[name]
            sheet_row = values[row_idx - 1] if row_idx - 1 < len(values) else []
            sheet_img = sheet_row[1] if len(sheet_row) > 1 else ""
            if not sheet_img and recipe.get("image_url"):
                sheets_update_cell(token, row_idx, 2, recipe["image_url"])
                logger.info(f"이미지 채움: {name}")
                updated += 1
                time.sleep(0.2)
        else:
            # 새 레시피 → 추가 대기열
            new_rows.append([
                name,
                recipe.get("image_url", ""),
                recipe.get("english_name", ""),
                "",   # memo 빈칸 (사용자 작성용)
            ])

    # 새 행 일괄 추가
    if new_rows:
        sheets_append(token, new_rows)
        logger.info(f"새 행 추가: {len(new_rows)}개")

    logger.info(f"완료 — 신규: {len(new_rows)}, 이미지 채움: {updated}")


if __name__ == "__main__":
    main()
