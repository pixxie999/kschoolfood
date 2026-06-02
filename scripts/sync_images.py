"""
구글 시트의 image_url을 D1에 반영합니다.
- 시트에서 image_url이 있는 행만 처리
- D1의 기존 값과 다를 때만 UPDATE (불필요한 쓰기 방지)
"""
import os
import json
import logging
import time
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SHEET_ID       = os.environ.get("SHEET_ID", "")
GOOGLE_SA_JSON = os.environ.get("GOOGLE_SA_JSON", "")
CF_ACCOUNT_ID  = os.environ["CF_ACCOUNT_ID"]
CF_API_TOKEN   = os.environ["CF_API_TOKEN"]
D1_DATABASE_ID = os.environ["D1_DATABASE_ID"]

SHEET_NAME = None  # 자동 감지


def _get_access_token() -> str:
    import base64, time as t
    sa = json.loads(GOOGLE_SA_JSON)
    now = int(t.time())

    header  = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets.readonly",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
    }).encode()).rstrip(b"=")

    msg = header + b"." + payload
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
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "sheets.properties.title"},
        timeout=15,
    )
    r.raise_for_status()
    sheets = r.json().get("sheets", [])
    return sheets[0]["properties"]["title"] if sheets else "Sheet1"


def sheets_get(token: str, sheet_name: str) -> list:
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sheet_name}!A:B",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("values", [])


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


def get_d1_images() -> dict:
    """D1의 korean_name → image_url 맵 반환"""
    result = d1_query("SELECT korean_name, image_url FROM recipes")
    return {r["korean_name"]: r["image_url"] or "" for r in result["result"][0].get("results", [])}


def main():
    if not SHEET_ID or not GOOGLE_SA_JSON:
        logger.info("SHEET_ID 또는 GOOGLE_SA_JSON 미설정 — 건너뜀")
        return

    logger.info("Google Sheets 읽는 중...")
    token = _get_access_token()
    sheet_name = get_first_sheet_name(token)
    logger.info(f"시트 탭 이름: {sheet_name}")
    values = sheets_get(token, sheet_name)

    if not values or len(values) < 2:
        logger.info("시트 데이터 없음")
        return

    # 헤더 제외, image_url이 있는 행만
    d1_images = get_d1_images()
    updated = skipped = not_found = 0

    for row in values[1:]:
        if len(row) < 2:
            continue
        name = row[0].strip()
        url  = row[1].strip()

        if not name or not url:
            continue

        if name not in d1_images:
            logger.warning(f"D1에 없는 레시피: {name}")
            not_found += 1
            continue

        if d1_images[name] == url:
            skipped += 1
            continue

        # 변경된 경우만 UPDATE
        d1_query("UPDATE recipes SET image_url = ? WHERE korean_name = ?", [url, name])
        logger.info(f"이미지 변경: {name}")
        updated += 1
        time.sleep(0.1)

    logger.info(f"완료 — 업데이트: {updated}, 동일: {skipped}, 미등록: {not_found}")


if __name__ == "__main__":
    main()
