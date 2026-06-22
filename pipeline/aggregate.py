"""NEIS 다중일 수집 + 집계 → data_snapshot JSON.

블로그 자동 생성용. Claude는 이 snapshot의 수치만 사용한다(환각 차단).

사용:
    from pipeline.aggregate import build_snapshot
    snapshot = build_snapshot(category="data", topic_id=1, days=30)
"""
import os
import re
import json
import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ALLERGY_MAP = {
    "1": "Egg", "2": "Milk", "3": "Buckwheat", "4": "Peanut",
    "5": "Soy", "6": "Wheat", "7": "Mackerel", "8": "Crab",
    "9": "Shrimp", "10": "Pork", "11": "Peach", "12": "Tomato",
    "13": "Sulfites", "14": "Walnut", "15": "Chicken", "16": "Beef",
    "17": "Squid", "18": "Shellfish",
}

NEIS_URL = "https://open.neis.go.kr/hub/mealServiceDietInfo"


def _parse_allergies(raw: str) -> list[str]:
    m = re.search(r"\(([0-9.]+)\)", raw)
    if not m:
        return []
    codes = [c.strip() for c in m.group(1).split(".") if c.strip()]
    return [ALLERGY_MAP[c] for c in codes if c in ALLERGY_MAP]


def _clean_name(raw: str) -> str:
    name = re.sub(r"\([0-9.\s*~]+\)", "", raw)
    name = re.sub(r"\s+[0-9.\s*]+$", "", name)
    return name.replace("*", "").strip()


def _kcal_to_int(s: str) -> Optional[int]:
    m = re.search(r"(\d+)", s or "")
    return int(m.group(1)) if m else None


def fetch_neis_day(date_str: str, *, key: str, office: str, school: str, timeout: int = 15) -> Optional[dict]:
    """하루치 급식 — {date, calories, kcal, dishes:[{name, allergies}]}"""
    params = {
        "KEY": key, "Type": "json", "pIndex": 1, "pSize": 100,
        "ATPT_OFCDC_SC_CODE": office, "SD_SCHUL_CODE": school,
        "MLSV_YMD": date_str,
    }
    try:
        r = requests.get(NEIS_URL, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"NEIS {date_str} 오류: {e}")
        return None
    if "mealServiceDietInfo" not in data:
        return None
    try:
        row = data["mealServiceDietInfo"][1]["row"][0]
    except (IndexError, KeyError):
        return None
    raw_ddish = row.get("DDISH_NM", "")
    raws = [d.strip() for d in re.split(r"<br\s*/?>|\n", raw_ddish) if d.strip()]
    dishes = [{"name": _clean_name(r), "allergies": _parse_allergies(r)} for r in raws]
    return {
        "date": date_str,
        "calories": row.get("CAL_INFO", ""),
        "kcal": _kcal_to_int(row.get("CAL_INFO", "")),
        "dishes": dishes,
    }


def fetch_range(start: datetime, end: datetime, *, key: str, office: str, school: str) -> list[dict]:
    """[start, end] 평일만 수집. NEIS rate-limit 회피 위해 짧은 sleep."""
    out: list[dict] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # 평일만
            day = fetch_neis_day(cur.strftime("%Y%m%d"), key=key, office=office, school=school)
            if day:
                out.append(day)
            time.sleep(0.1)
        cur += timedelta(days=1)
    return out


# ── 집계 함수 ─────────────────────────────────────────────────────────────────

def _flatten_dishes(days: list[dict]) -> list[dict]:
    """모든 dish를 평면화 (요일, 월, 알레르기 포함)."""
    flat = []
    for d in days:
        try:
            dt = datetime.strptime(d["date"], "%Y%m%d")
            weekday = dt.weekday()  # 0=월
            month = dt.month
        except ValueError:
            weekday = month = None
        for dish in d.get("dishes", []):
            flat.append({
                "date": d["date"],
                "weekday": weekday,
                "month": month,
                "name": dish["name"],
                "allergies": dish.get("allergies", []),
            })
    return flat


def _top_n(counter: Counter, n: int) -> list[dict]:
    return [{"name": k, "count": v} for k, v in counter.most_common(n)]


def aggregate(days: list[dict]) -> dict:
    """범용 집계 — 어떤 주제든 참조할 수 있는 핵심 통계 모음."""
    flat = _flatten_dishes(days)
    total_days = len(days)
    total_dishes = len(flat)

    by_name = Counter(f["name"] for f in flat)
    by_weekday = {i: Counter() for i in range(5)}
    by_month = {}
    by_allergy = Counter()
    has_kimchi = 0

    for d in days:
        names = [x["name"] for x in d.get("dishes", [])]
        if any("김치" in n for n in names):
            has_kimchi += 1

    for f in flat:
        if f["weekday"] is not None:
            by_weekday[f["weekday"]][f["name"]] += 1
        if f["month"] is not None:
            by_month.setdefault(f["month"], Counter())[f["name"]] += 1
        for a in f["allergies"]:
            by_allergy[a] += 1

    kcal_list = [d["kcal"] for d in days if d.get("kcal")]
    avg_kcal = round(sum(kcal_list) / len(kcal_list)) if kcal_list else None

    weekday_top = {
        ["월", "화", "수", "목", "금"][i]: _top_n(by_weekday[i], 10)
        for i in range(5) if by_weekday[i]
    }
    month_top = {str(m): _top_n(c, 10) for m, c in sorted(by_month.items())}

    return {
        "period": {
            "start": days[0]["date"] if days else None,
            "end": days[-1]["date"] if days else None,
            "school_days": total_days,
        },
        "totals": {
            "dishes": total_dishes,
            "unique_dishes": len(by_name),
            "kimchi_appearances": has_kimchi,
            "kimchi_ratio": round(has_kimchi / total_days, 3) if total_days else None,
        },
        "avg_kcal": avg_kcal,
        "top_overall": _top_n(by_name, 50),
        "top_by_weekday": weekday_top,
        "top_by_month": month_top,
        "top_allergies": _top_n(by_allergy, 18),
    }


def build_snapshot(*, days: int = 30, end_date: Optional[datetime] = None) -> dict:
    """env에서 NEIS 자격 읽어 days일 평일 수집 + 집계."""
    key = os.environ["NEIS_API_KEY"]
    office = os.environ.get("OFFICE_CODE", "K10")
    school = os.environ.get("SCHOOL_CODE", "7801106")

    kst = timezone(timedelta(hours=9))
    end = end_date or datetime.now(kst).replace(tzinfo=None)
    start = end - timedelta(days=days)

    logger.info(f"NEIS 수집: {start:%Y-%m-%d} ~ {end:%Y-%m-%d}")
    days_data = fetch_range(start, end, key=key, office=office, school=school)
    logger.info(f"수집된 평일 데이터: {len(days_data)}일")

    snapshot = aggregate(days_data)
    snapshot["meta"] = {"generated_at": datetime.utcnow().isoformat() + "Z", "source": "neis"}
    return snapshot


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    snap = build_snapshot(days=30)
    print(json.dumps(snap, ensure_ascii=False, indent=2))
