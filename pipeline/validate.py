"""생성된 글의 수치가 data_snapshot 안에서 검증 가능한지 확인.

- 본문에서 숫자 토큰을 추출
- snapshot을 평면화한 숫자 집합과 대조
- 매칭 비율이 임계치 미만이면 fail → auto 글이라도 draft 강등
"""
import re
import logging
from typing import Iterable

logger = logging.getLogger(__name__)

NUMBER_RE = re.compile(r"\b(\d{1,4}(?:\.\d+)?)(?:%|개|일|회|kcal|Kcal)?\b")
MIN_MATCH_RATIO = 0.6  # 본문에 등장한 숫자 중 60% 이상이 snapshot에 있어야 통과
TRIVIAL = {"0", "1", "2", "3", "4", "5", "10", "100"}  # 흔한 일반 숫자는 검증에서 제외


def _flatten_numbers(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _flatten_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _flatten_numbers(v)
    elif isinstance(obj, (int, float)):
        out.add(str(obj))
        # 0.818 → 81.8, 82 (퍼센트 변환도 매칭에 포함)
        if isinstance(obj, float) and 0 < obj < 1:
            pct = obj * 100
            out.add(f"{pct:.1f}")
            out.add(str(round(pct)))
    elif isinstance(obj, str):
        for m in NUMBER_RE.finditer(obj):
            out.add(m.group(1))
    return out


def _extract_body_numbers(body: str) -> list[str]:
    return [m.group(1) for m in NUMBER_RE.finditer(body) if m.group(1) not in TRIVIAL]


def validate_post(post: dict, snapshot: dict) -> tuple[bool, list[str]]:
    """Returns (ok, reasons). reasons 비어있으면 통과."""
    reasons = []

    if not post.get("body_en") or not post.get("body_ko"):
        reasons.append("본문 누락")
        return False, reasons
    if not post.get("slug") or not re.match(r"^[a-z0-9][a-z0-9-]{2,80}$", post["slug"]):
        reasons.append(f"slug 형식 오류: {post.get('slug')}")

    snap_nums = _flatten_numbers(snapshot)
    body_nums = _extract_body_numbers(post["body_en"]) + _extract_body_numbers(post["body_ko"])

    if not body_nums:
        # 수치가 없는 문화/문체 글일 수 있음 — 통과
        return (not reasons), reasons

    # snapshot에 매칭되는 숫자
    matched = [n for n in body_nums if n in snap_nums]
    ratio = len(matched) / len(body_nums)
    if ratio < MIN_MATCH_RATIO:
        unmatched = [n for n in body_nums if n not in snap_nums][:10]
        reasons.append(
            f"본문 수치 {len(body_nums)}개 중 {len(matched)}개만 snapshot에 존재 "
            f"({ratio:.0%} < {MIN_MATCH_RATIO:.0%}). 미확인 예: {unmatched}"
        )

    return (not reasons), reasons


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    snap = {"totals": {"kimchi_ratio": 0.818, "school_days": 22}, "avg_kcal": 685}
    post = {
        "slug": "kimchi-frequency-2026",
        "title_ko": "x", "body_ko": "김치는 22일 중 18일 등장(81.8%).",
        "title_en": "x", "body_en": "Kimchi appeared on 18 of 22 days (81.8%).",
        "meta_en": "x",
    }
    print(validate_post(post, snap))
