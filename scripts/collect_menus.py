"""
NEIS에서 7일치 급식 메뉴를 수집하여 구글 시트에 신규 메뉴명만 추가합니다.
번역/레시피 생성은 하지 않습니다. 사람이 시트에서 직접 입력합니다.

실행: python scripts/collect_menus.py
"""
import os, re, json, time, base64, logging, requests
from datetime import datetime, timedelta
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

NEIS_API_KEY  = os.environ["NEIS_API_KEY"]
OFFICE_CODE   = os.environ.get("OFFICE_CODE", "K10")
SCHOOL_CODE   = os.environ.get("SCHOOL_CODE", "7801106")
SHEET_ID      = os.environ["SHEET_ID"]
GOOGLE_SA_JSON      = os.environ.get("GOOGLE_SA_JSON", "")
GOOGLE_SA_JSON_PATH = os.environ.get("GOOGLE_SA_JSON_PATH", "")

SKIP_NAMES = {"", "밥", "쌀밥", "잡곡밥", "현미밥"}


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


def get_existing_names(token: str, sname: str) -> set:
    """시트 A열에서 기존 메뉴명 목록 반환."""
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A:A",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    values = r.json().get("values", [])
    # 헤더 제외
    return {row[0].strip() for row in values[1:] if row}


def append_new_menus(token: str, sname: str, new_names: list):
    """신규 메뉴명을 A열에만 추가 (B~G열은 비워둠)."""
    if not new_names:
        return
    rows = [[name] for name in new_names]
    r = requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A1:append",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        json={"values": rows},
        timeout=15,
    )
    r.raise_for_status()


# ── NEIS API ──────────────────────────────────────────────────────────────────

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
            name = re.sub(r'\s*[\(\（][^）)]*[\)\）]', '', d).strip()  # 알레르기 코드 제거
            name = re.sub(r'\s*\([\d.,]+\)', '', name).strip()
            if name and name not in SKIP_NAMES and len(name) > 1:
                dishes.append(name)
        return dishes
    except Exception as e:
        logger.error(f"NEIS API 오류 ({date_str}): {e}")
        return []


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    today = datetime.now()
    dates = [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]

    # NEIS에서 7일치 메뉴 수집
    all_dishes = set()
    for date_str in dates:
        dishes = fetch_neis_menu(date_str)
        logger.info(f"{date_str}: {dishes}")
        all_dishes.update(dishes)
        time.sleep(0.3)

    logger.info(f"수집된 메뉴: {len(all_dishes)}개")

    # 구글 시트에서 기존 메뉴 확인
    token = _get_token()
    sname = get_sheet_name(token)
    existing = get_existing_names(token, sname)
    logger.info(f"시트 기존 메뉴: {len(existing)}개")

    # 신규 메뉴만 추가
    new_names = sorted(all_dishes - existing)
    if new_names:
        append_new_menus(token, sname, new_names)
        logger.info(f"✅ 신규 메뉴 {len(new_names)}개 추가: {new_names}")
    else:
        logger.info("신규 메뉴 없음 — 시트 변경 없음")


if __name__ == "__main__":
    main()
