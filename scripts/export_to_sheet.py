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

SHEET_NAME = None  # 자동 감지


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


def get_first_sheet_name(token: str) -> str:
    """스프레드시트의 첫 번째 탭 이름을 가져옵니다."""
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "sheets.properties.title"},
        timeout=15,
    )
    r.raise_for_status()
    sheets = r.json().get("sheets", [])
    return sheets[0]["properties"]["title"] if sheets else "Sheet1"


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
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
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
        params={"valueInputOption": "USER_ENTERED"},
        json={"values": [[value]]},
        timeout=15,
    )
    r.raise_for_status()


def sheets_batch_update(token: str, updates: list[dict]):
    """여러 셀 한번에 업데이트"""
    if not updates:
        return
    r = requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"valueInputOption": "USER_ENTERED", "data": updates},
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

HEADERS = ["korean_name", "image_url", "미리보기", "english_name", "memo"]

def main():
    global SHEET_NAME
    logger.info("Google Sheets 토큰 발급 중...")
    token = _get_access_token()

    # 실제 탭 이름 자동 감지
    SHEET_NAME = get_first_sheet_name(token)
    logger.info(f"시트 탭 이름: {SHEET_NAME}")

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
    preview_updates = []  # 기존 행 중 미리보기 없는 것
    updated = 0

    for recipe in recipes:
        name = recipe["korean_name"]
        if name in sheet_names:
            row_idx = sheet_names[name]
            sheet_row = values[row_idx - 1] if row_idx - 1 < len(values) else []
            sheet_img     = sheet_row[1] if len(sheet_row) > 1 else ""
            sheet_preview = sheet_row[2] if len(sheet_row) > 2 else ""

            # B열 비어 있으면 D1 이미지로 채우기
            if not sheet_img and recipe.get("image_url"):
                sheets_update_cell(token, row_idx, 2, recipe["image_url"])
                logger.info(f"이미지 채움: {name}")
                updated += 1
                time.sleep(0.15)
                sheet_img = recipe["image_url"]

            # C열(미리보기) 비어 있고 이미지 있으면 IMAGE 수식 추가
            if not sheet_preview and sheet_img:
                preview_updates.append({
                    "range": f"{SHEET_NAME}!C{row_idx}",
                    "values": [[f'=IMAGE(B{row_idx})']],
                })
        else:
            img = recipe.get("image_url", "")
            new_rows.append([
                name,
                img,
                f'=IMAGE(B{len(values) + len(new_rows) + 1})' if img else "",
                recipe.get("english_name", ""),
                "",  # memo
            ])

    # 기존 행 미리보기 일괄 추가
    if preview_updates:
        sheets_batch_update(token, preview_updates)
        logger.info(f"미리보기 수식 추가: {len(preview_updates)}개")

    # 새 행 일괄 추가
    if new_rows:
        sheets_append(token, new_rows)
        logger.info(f"새 행 추가: {len(new_rows)}개")

    logger.info(f"완료 — 신규: {len(new_rows)}, 이미지 채움: {updated}, 미리보기 추가: {len(preview_updates)}")

    # C열 행 높이 120px로 설정 (이미지가 보이도록)
    _set_row_heights(token, len(values) + len(new_rows))


def _set_row_heights(token: str, total_rows: int):
    """데이터 행 높이를 120px로 설정해 이미지가 잘 보이게 합니다."""
    if total_rows < 2:
        return
    r = requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"requests": [{
            "updateDimensionProperties": {
                "range": {"sheetId": 0, "dimension": "ROWS",
                          "startIndex": 1, "endIndex": total_rows},
                "properties": {"pixelSize": 120},
                "fields": "pixelSize"
            }
        }]},
        timeout=15,
    )
    if not r.ok:
        logger.warning(f"행 높이 설정 실패 (무시): {r.text[:100]}")


if __name__ == "__main__":
    main()
