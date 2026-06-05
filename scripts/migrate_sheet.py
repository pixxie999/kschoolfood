"""
구글 시트 컬럼 구조 마이그레이션 (1회 실행)

현재:  A=korean_name | B=image_url   | C=미리보기 | D=english_name | E=memo
변경후: A=korean_name | B=english_name | C=image_url | D=미리보기 | E=ingredients | F=instructions | G=memo

실행: python scripts/migrate_sheet.py
"""
import os, json, time, base64, logging, requests
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_SA_JSON = os.environ.get("GOOGLE_SA_JSON", "")
GOOGLE_SA_JSON_PATH = os.environ.get("GOOGLE_SA_JSON_PATH", "")


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
        "scope": "https://www.googleapis.com/auth/spreadsheets",
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


def main():
    logger.info("토큰 발급 중...")
    token = _get_token()
    sname = get_sheet_name(token)
    logger.info(f"시트 탭: {sname}")

    # 현재 시트 전체 읽기 (A:G)
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A:G",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    values = r.json().get("values", [])
    if not values:
        logger.error("시트가 비어있습니다.")
        return

    logger.info(f"현재 행 수: {len(values)}")

    # 새 헤더
    new_header = ["korean_name", "english_name", "image_url", "미리보기", "ingredients", "instructions", "memo"]

    # 기존 헤더 파악
    old_header = [h.strip() for h in values[0]]
    logger.info(f"기존 헤더: {old_header}")

    def col(row, name):
        try:
            idx = old_header.index(name)
            return row[idx] if idx < len(row) else ""
        except ValueError:
            return ""

    # 새 데이터 구성
    new_rows = [new_header]
    for i, row in enumerate(values[1:], start=2):
        korean_name = col(row, "korean_name")
        if not korean_name:
            continue
        image_url = col(row, "image_url")
        new_rows.append([
            korean_name,
            col(row, "english_name"),   # B: english_name
            image_url,                   # C: image_url
            f"=IMAGE(C{i})" if image_url else "",  # D: 미리보기
            "",  # E: ingredients (수동 입력 대기)
            "",  # F: instructions (수동 입력 대기)
            col(row, "memo"),            # G: memo
        ])

    logger.info(f"마이그레이션 행 수: {len(new_rows) - 1}개")

    # 기존 데이터 전체 지우고 새로 쓰기
    # 1. 기존 범위 clear
    r = requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A:G:clear",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    logger.info("기존 데이터 삭제 완료")

    # 2. 새 데이터 쓰기
    r = requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A1",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"valueInputOption": "USER_ENTERED"},
        json={"values": new_rows},
        timeout=15,
    )
    r.raise_for_status()
    logger.info(f"새 구조 저장 완료: {len(new_rows) - 1}개 행")

    # 3. D열(미리보기) 행 높이 120px
    requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"requests": [{
            "updateDimensionProperties": {
                "range": {"sheetId": 0, "dimension": "ROWS", "startIndex": 1, "endIndex": len(new_rows)},
                "properties": {"pixelSize": 80},
                "fields": "pixelSize"
            }
        }]},
        timeout=15,
    )
    logger.info("행 높이 설정 완료")
    logger.info("✅ 마이그레이션 완료! 시트에서 B열(영어명), E열(재료), F열(조리법)을 채워주세요.")


if __name__ == "__main__":
    main()
