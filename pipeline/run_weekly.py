"""주간 블로그 1편 생성 파이프라인 — GitHub Actions에서 실행.

흐름:
  1. select_topic → 이번 주 멱등 체크
  2. build_snapshot (NEIS 수집 + 집계, AI 주제만)
  3. generate_post (Claude Haiku 한/영)
  4. validate_post (수치 대조)
  5. write_post (D1 발행/초안)
"""
import sys
import json
import logging

import markdown as md

from pipeline.aggregate import build_snapshot
from pipeline.generate import generate_post
from pipeline.validate import validate_post
from pipeline.write_d1 import select_topic, already_published_this_week, write_post


def _md_to_html(text: str) -> str:
    return md.markdown(text or "", extensions=["tables", "fenced_code"])

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    topic = select_topic()
    if not topic:
        logger.info("발행할 주제 없음(모두 done) — 종료")
        return 0
    logger.info(f"주제: #{topic['id']} {topic['title_ko']} ({topic['category']}/{topic['publish_mode']})")

    if already_published_this_week(topic["id"]):
        logger.info("이번 주 이미 발행됨 — 멱등 skip")
        return 0

    # data 카테고리는 30일 NEIS 집계 필요. 다른 카테고리는 더 적은 집계로도 충분.
    days = 60 if topic["category"] == "data" else 30
    snapshot = build_snapshot(days=days)

    generated = generate_post(topic["title_ko"], topic["category"], snapshot)
    if not generated:
        logger.error("생성 실패 — 종료")
        return 1

    ok, reasons = validate_post(generated, snapshot)
    # 검증은 markdown 원문 기준. 저장 직전에 HTML로 변환해 D1에 박는다 (Worker는 의존성 없이 렌더).
    generated["body_en"] = _md_to_html(generated["body_en"])
    generated["body_ko"] = _md_to_html(generated["body_ko"])
    if not ok:
        logger.warning(f"검증 실패 — {reasons}")
    else:
        logger.info("검증 통과")

    result = write_post(topic=topic, generated=generated, snapshot=snapshot, validation_ok=ok)
    logger.info(f"완료 — {json.dumps(result, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
