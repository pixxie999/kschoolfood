"""
구글 시트에서 레시피 이미지 URL을 읽어 D1에 반영합니다.
- 시트는 '웹에 게시(CSV)' 설정이 필요합니다.
- SHEET_CSV_URL 환경변수 또는 GitHub Secret에 등록하세요.

시트 컬럼 형식 (1행: 헤더):
  A: korean_name   B: image_url   C: memo(선택, 무시)
"""

import os
import csv
import io
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SHEET_CSV_URL  = os.environ.get("SHEET_CSV_URL", "")  # 구글 시트 CSV 퍼블리시 URL
CF_ACCOUNT_ID  = os.environ["CF_ACCOUNT_ID"]
CF_API_TOKEN   = os.environ["CF_API_TOKEN"]
D1_DATABASE_ID = os.environ["D1_DATABASE_ID"]


def d1_query(sql: str, params: list = None) -> dict:
    url = (f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
           f"/d1/database/{D1_DATABASE_ID}/query")
    r = requests.post(url,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}",
                 "Content-Type": "application/json"},
        json={"sql": sql, **({"params": params} if params else {})},
        timeout=30)
    r.raise_for_status()
    result = r.json()
    if not result.get("success"):
        raise RuntimeError(f"D1 오류: {result.get('errors')}")
    return result


def fetch_sheet() -> list[dict]:
    """구글 시트 CSV → [{"korean_name": ..., "image_url": ...}] 반환"""
    r = requests.get(SHEET_CSV_URL, timeout=15)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = []
    for row in reader:
        name = (row.get("korean_name") or "").strip()
        url  = (row.get("image_url")   or "").strip()
        if name and url.startswith("http"):
            rows.append({"korean_name": name, "image_url": url})
    return rows


def get_existing_names() -> set:
    result = d1_query("SELECT korean_name FROM recipes")
    return {r["korean_name"] for r in result["result"][0].get("results", [])}


def main():
    if not SHEET_CSV_URL:
        logger.info("SHEET_CSV_URL 미설정 — 건너뜀")
        return
    logger.info("구글 시트에서 이미지 URL 읽는 중...")
    rows = fetch_sheet()
    logger.info(f"시트 항목 수: {len(rows)}")

    existing = get_existing_names()
    updated = skipped = not_found = 0

    for row in rows:
        name = row["korean_name"]
        url  = row["image_url"]

        if name not in existing:
            logger.warning(f"레시피 없음 (D1 미등록): {name}")
            not_found += 1
            continue

        d1_query(
            "UPDATE recipes SET image_url = ? WHERE korean_name = ?",
            [url, name]
        )
        logger.info(f"업데이트: {name}")
        updated += 1

    logger.info(f"완료 — 업데이트: {updated}, 미등록: {not_found}")


if __name__ == "__main__":
    main()
