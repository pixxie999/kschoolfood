"""D1 write/upsert + 주제 큐 선택 + 주간 멱등 키.

흐름:
  1. select_topic() — pending 중 우선순위/last_used_at 기준 1개 선택
  2. 이번 주에 이미 같은 topic_id로 발행한 글이 있으면 skip (멱등)
  3. (호출자가) aggregate → generate → validate
  4. write_post() — auto+valid → published, auto+invalid → draft+alert, review → draft
"""
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _d1_query(sql: str, params: list | None = None) -> dict:
    account = os.environ["CF_ACCOUNT_ID"]
    db_id = os.environ["CF_D1_DATABASE_ID"]
    token = os.environ["CF_API_TOKEN"]
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{db_id}/query"
    payload: dict = {"sql": sql}
    if params:
        payload["params"] = params
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    r.raise_for_status()
    result = r.json()
    if not result.get("success"):
        raise RuntimeError(f"D1 오류: {result.get('errors')}")
    return result


def _week_key(dt: Optional[datetime] = None) -> str:
    """KST 기준 ISO 주차 (YYYY-Www) — topic_id와 함께 멱등 키."""
    kst = timezone(timedelta(hours=9))
    dt = dt or datetime.now(kst)
    return dt.strftime("%G-W%V")


def already_published_this_week(topic_id: int) -> bool:
    week = _week_key()
    # slug에 주차 키를 박아 멱등 보장
    res = _d1_query(
        "SELECT id FROM posts WHERE topic_id = ? AND slug LIKE ? LIMIT 1",
        [topic_id, f"%--{week}"],
    )
    rows = res["result"][0].get("results", [])
    return len(rows) > 0


def select_topic() -> Optional[dict]:
    """pending 중 priority 오름차순 → last_used_at 오래된 순."""
    res = _d1_query(
        "SELECT id, title_ko, category, publish_mode "
        "FROM topics WHERE state = 'pending' "
        "ORDER BY priority ASC, COALESCE(last_used_at, '1970-01-01') ASC "
        "LIMIT 1"
    )
    rows = res["result"][0].get("results", [])
    return rows[0] if rows else None


def mark_topic_used(topic_id: int, *, recycle_auto: bool):
    """auto 주제는 last_used_at만 갱신(재순환), review는 done 처리."""
    now = datetime.utcnow().isoformat() + "Z"
    if recycle_auto:
        _d1_query("UPDATE topics SET last_used_at = ? WHERE id = ?", [now, topic_id])
    else:
        _d1_query("UPDATE topics SET state = 'done', last_used_at = ? WHERE id = ?", [now, topic_id])


def write_post(
    *,
    topic: dict,
    generated: dict,
    snapshot: dict,
    validation_ok: bool,
) -> dict:
    """
    auto + valid → published
    auto + invalid → draft (관리자 검토)
    review → draft
    """
    publish_mode = topic["publish_mode"]
    is_auto = publish_mode == "auto"
    will_publish = is_auto and validation_ok
    status = "published" if will_publish else "draft"
    published_at = datetime.utcnow().isoformat() + "Z" if will_publish else None

    week = _week_key()
    slug = f"{generated['slug']}--{week}"  # 주차 멱등

    _d1_query(
        "INSERT INTO posts "
        "(topic_id, source, publish_mode, status, translate_en, slug, "
        " title_ko, body_ko, title_en, body_en, meta_en, data_snapshot, published_at) "
        "VALUES (?, 'ai', ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            topic["id"], publish_mode, status, slug,
            generated["title_ko"], generated["body_ko"],
            generated["title_en"], generated["body_en"],
            generated.get("meta_en", ""),
            json.dumps(snapshot, ensure_ascii=False),
            published_at,
        ],
    )

    mark_topic_used(topic["id"], recycle_auto=is_auto)

    return {"status": status, "slug": slug, "topic_id": topic["id"]}
