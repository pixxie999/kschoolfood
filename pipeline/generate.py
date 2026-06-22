"""Claude Haiku로 블로그 글 한/영 동시 생성.

주제(title_ko/category) + data_snapshot을 입력받아
{slug, title_ko, body_ko, title_en, body_en, meta_en} JSON을 반환.

수치는 data_snapshot에서만 가져오도록 시스템 프롬프트로 강제.
"""
import os
import re
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 8192


def _system_prompt() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    year = datetime.now(timezone.utc).strftime("%Y")
    return f"""You are a bilingual food/education writer for kschoolfood.com.
오늘 날짜: {today} (작성 연도: {year}).

[사실 확인 규칙]
- 모든 수치·랭킹·비율은 입력 `data_snapshot`에서만 가져온다. 새 숫자를 만들어내지 않는다.
- snapshot에 없는 수치는 본문에 쓰지 않는다. 필요하면 "참고용" 문구로 우회한다.
- 영양·알레르기 관련 단정 표현 금지. 영문 면책 문구(For reference only — consult your school/healthcare provider) 포함.
- 특정 브랜드·업체명 언급 금지.

[문체]
- 영어본은 영어권 독자가 자연스럽게 읽는 글. 직역체 금지.
- 한글본은 영어본과 동일 내용의 검토용. 두 언어가 같은 사실을 담아야 한다.
- 본문은 markdown. 제목(##), 짧은 단락, 필요시 표/리스트.
- 글마다 그 데이터의 고유 인사이트(Information Gain) 1개 이상.

[출력 형식]
응답은 단 하나의 JSON 객체. 그 외 텍스트 금지. 코드펜스로 감싸도 좋다.
```json
{{
  "slug": "kebab-case-english-seo-slug",
  "title_ko": "한글 제목",
  "body_ko": "## 도입...\\n\\n본문(markdown)",
  "title_en": "English Title",
  "body_en": "## Intro...\\n\\nBody (markdown)",
  "meta_en": "150자 내외 영문 메타 디스크립션"
}}
```
"""


def _user_prompt(title_ko: str, category: str, data_snapshot: dict) -> str:
    return f"""주제: {title_ko}
카테고리: {category}

[데이터 스냅샷 — 이 안의 수치만 사용]
```json
{json.dumps(data_snapshot, ensure_ascii=False, indent=2)}
```

위 데이터를 근거로 한/영 블로그 글을 작성하세요."""


def _parse_response(raw: str) -> Optional[dict]:
    # greedy 매칭 — 본문에 } 가 있어도 끝까지
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    payload = m.group(1) if m else raw
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as e:
        logger.error(f"JSON 파싱 실패: {e}\n원문 앞 300자: {raw[:300]}")
        return None
    required = {"slug", "title_ko", "body_ko", "title_en", "body_en", "meta_en"}
    missing = required - set(obj.keys())
    if missing:
        logger.error(f"필수 필드 누락: {missing}")
        return None
    return obj


def generate_post(title_ko: str, category: str, data_snapshot: dict) -> Optional[dict]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 미설정")

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": _system_prompt(), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _user_prompt(title_ko, category, data_snapshot)}],
    )
    raw = resp.content[0].text if resp.content else ""
    return _parse_response(raw)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sample_snapshot = {
        "period": {"start": "20260501", "end": "20260531", "school_days": 22},
        "totals": {"dishes": 110, "unique_dishes": 60, "kimchi_appearances": 18, "kimchi_ratio": 0.818},
        "avg_kcal": 685,
        "top_overall": [{"name": "배추김치", "count": 18}, {"name": "기장밥", "count": 12}],
    }
    post = generate_post("김치가 급식에 등장하는 비율", "data", sample_snapshot)
    print(json.dumps(post, ensure_ascii=False, indent=2) if post else "FAILED")
