"""
레시피 분류 태깅 파이프라인 (Claude Haiku Batch API)

Mode:
  --mode submit  : category/tags 미입력 레시피 → Batch 제출 → batch_id 저장
  --mode collect : 이전 Batch 결과 수거 → D1 업데이트
  --mode both    : collect 먼저, 그 다음 submit (매일 크론에 적합)

분류 체계:
  category (택1): rice, soup, stir_fry, braised, salad, kimchi, fried, noodle, dessert, other
  tags (복수):    quick, vegetarian, spicy_0, spicy_1, spicy_2, spicy_3,
                  kid_friendly, meal_prep, gluten_free, high_protein
"""
import os, sys, json, time, logging, requests
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── 환경변수 (함수 내 접근) ────────────────────────────────────────────────
def _anthropic_key() -> str:
    return os.environ["ANTHROPIC_API_KEY"]

def _d1_cfg() -> tuple[str, str, str]:
    return (
        os.environ["CF_ACCOUNT_ID"],
        os.environ["CF_API_TOKEN"],
        os.environ["D1_DATABASE_ID"],
    )

# ── 분류 상수 ─────────────────────────────────────────────────────────────
VALID_CATEGORIES = {
    "rice", "soup", "stir_fry", "braised", "salad",
    "kimchi", "fried", "noodle", "dessert", "other",
}
VALID_TAGS = {
    "quick", "vegetarian",
    "spicy_0", "spicy_1", "spicy_2", "spicy_3",
    "kid_friendly", "meal_prep", "gluten_free", "high_protein",
}

PENDING_FILE = Path(__file__).parent / "pending_tag_batches.json"
BATCH_SIZE   = 500   # Batch API 한 번에 최대 요청 수


# ── D1 헬퍼 ──────────────────────────────────────────────────────────────
def d1_query(sql: str, params: list = None) -> dict:
    account_id, api_token, db_id = _d1_cfg()
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{db_id}/query"
    payload: dict = {"sql": sql}
    if params:
        payload["params"] = params
    r = requests.post(url, headers={
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }, json=payload, timeout=30)
    r.raise_for_status()
    result = r.json()
    if not result.get("success"):
        raise RuntimeError(f"D1 오류: {result.get('errors')}")
    return result


# ── Anthropic Batch API 헬퍼 ──────────────────────────────────────────────
def _headers() -> dict:
    return {
        "x-api-key": _anthropic_key(),
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "message-batches-2024-09-24",
        "content-type": "application/json",
    }

def submit_batch(requests_list: list) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages/batches",
        headers=_headers(),
        json={"requests": requests_list},
        timeout=60,
    )
    r.raise_for_status()
    batch_id = r.json()["id"]
    logger.info(f"Batch 제출 완료: {batch_id} ({len(requests_list)}건)")
    return batch_id

def get_batch_status(batch_id: str) -> dict:
    r = requests.get(
        f"https://api.anthropic.com/v1/messages/batches/{batch_id}",
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def get_batch_results(batch_id: str) -> list:
    """JSONL 스트림으로 결과 수거."""
    r = requests.get(
        f"https://api.anthropic.com/v1/messages/batches/{batch_id}/results",
        headers=_headers(),
        timeout=60,
        stream=True,
    )
    r.raise_for_status()
    results = []
    for line in r.iter_lines():
        if line:
            results.append(json.loads(line))
    return results


# ── 프롬프트 생성 ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are a Korean food classifier. Return ONLY valid JSON, no extra text.\n"
    "Classify the dish into:\n"
    "  category (exactly one): rice | soup | stir_fry | braised | salad | kimchi | fried | noodle | dessert | other\n"
    "  tags (array, 0-4 items): quick | vegetarian | spicy_0 | spicy_1 | spicy_2 | spicy_3 | kid_friendly | meal_prep | gluten_free | high_protein\n"
    "Spicy tags are mutually exclusive (spicy_0=none, spicy_1=mild, spicy_2=medium, spicy_3=hot).\n"
    'JSON format: {"category": "...", "tags": [...]}'
)

def build_prompt(recipe: dict) -> str:
    parts = [
        f"Korean name: {recipe['korean_name']}",
        f"English name: {recipe.get('english_name') or '(unknown)'}",
    ]
    ingr = recipe.get("english_ingredients") or "[]"
    try:
        items = json.loads(ingr) if isinstance(ingr, str) else ingr
        names = [i.get("name", "") for i in items[:8] if i.get("name")]
        if names:
            parts.append(f"Ingredients: {', '.join(names)}")
    except Exception:
        pass
    nutrition = recipe.get("nutrition_info") or "{}"
    try:
        n = json.loads(nutrition) if isinstance(nutrition, str) else nutrition
        if n.get("calories"):
            parts.append(f"Calories: {n['calories']}")
        if n.get("protein"):
            parts.append(f"Protein: {n['protein']}")
    except Exception:
        pass
    return "\n".join(parts)


# ── 태깅 결과 파싱 ─────────────────────────────────────────────────────────
def parse_tag_response(raw: str) -> dict | None:
    import re
    # JSON 블록 추출 (greedy)
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', raw, re.DOTALL)
    text = m.group(1) if m else raw.strip()
    # 직접 JSON 파싱
    m2 = re.search(r'\{.*\}', text, re.DOTALL)
    if not m2:
        return None
    try:
        data = json.loads(m2.group(0))
    except json.JSONDecodeError:
        return None
    category = data.get("category", "other")
    if category not in VALID_CATEGORIES:
        category = "other"
    tags = [t for t in data.get("tags", []) if t in VALID_TAGS]
    return {"category": category, "tags": tags}


# ── Step 1: Submit ────────────────────────────────────────────────────────
def run_submit():
    logger.info("=== [SUBMIT] 미태깅 레시피 조회 중 ===")

    result = d1_query(
        "SELECT id, korean_name, english_name, english_ingredients, nutrition_info "
        "FROM recipes WHERE (category IS NULL OR category = '') "
        "ORDER BY id LIMIT 2000"
    )
    recipes = result["result"][0].get("results", [])
    if not recipes:
        logger.info("태깅할 레시피 없음 — 종료")
        return

    logger.info(f"태깅 대상: {len(recipes)}개")

    # BATCH_SIZE 단위로 분할 제출
    pending = load_pending()
    submitted = 0

    for i in range(0, len(recipes), BATCH_SIZE):
        chunk = recipes[i : i + BATCH_SIZE]
        requests_list = [
            {
                "custom_id": str(r["id"]),
                "params": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 128,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": build_prompt(r)}],
                },
            }
            for r in chunk
        ]
        batch_id = submit_batch(requests_list)
        pending.append({
            "batch_id": batch_id,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "count": len(chunk),
        })
        submitted += len(chunk)
        time.sleep(1)

    save_pending(pending)
    logger.info(f"✅ 총 {submitted}개 레시피 Batch 제출 완료")


# ── Step 2: Collect ───────────────────────────────────────────────────────
def run_collect():
    pending = load_pending()
    if not pending:
        logger.info("대기 중인 배치 없음")
        return

    remaining = []
    total_updated = 0

    for entry in pending:
        batch_id = entry["batch_id"]
        status_data = get_batch_status(batch_id)
        status = status_data.get("processing_status")

        if status != "ended":
            logger.info(f"Batch {batch_id}: 아직 처리 중 ({status}) — 다음 실행에서 재시도")
            remaining.append(entry)
            continue

        logger.info(f"Batch {batch_id}: 완료 — 결과 수거 중")
        results = get_batch_results(batch_id)

        updates = []
        for item in results:
            if item.get("result", {}).get("type") != "succeeded":
                continue
            recipe_id  = item["custom_id"]
            raw_text   = item["result"]["message"]["content"][0]["text"]
            parsed     = parse_tag_response(raw_text)
            if parsed:
                updates.append((parsed["category"], json.dumps(parsed["tags"]), recipe_id))
            else:
                logger.warning(f"파싱 실패 id={recipe_id}: {raw_text[:80]}")

        # D1 배치 업데이트 (100건씩)
        for j in range(0, len(updates), 100):
            chunk = updates[j : j + 100]
            for category, tags_json, recipe_id in chunk:
                d1_query(
                    "UPDATE recipes SET category = ?, tags = ? WHERE id = ?",
                    [category, tags_json, int(recipe_id)],
                )
                time.sleep(0.05)
            logger.info(f"  D1 업데이트: {j+len(chunk)}/{len(updates)}")

        total_updated += len(updates)
        logger.info(f"Batch {batch_id}: {len(updates)}개 태깅 완료")

    save_pending(remaining)
    logger.info(f"✅ 총 {total_updated}개 레시피 태그 업데이트 완료")


# ── pending 파일 관리 ─────────────────────────────────────────────────────
def load_pending() -> list:
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text())
        except Exception:
            return []
    return []

def save_pending(data: list):
    PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ── 진입점 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode = "both"
    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]
        elif arg == "--mode" and len(sys.argv) > sys.argv.index(arg) + 1:
            mode = sys.argv[sys.argv.index(arg) + 1]

    if mode == "submit":
        run_submit()
    elif mode == "collect":
        run_collect()
    elif mode == "both":
        run_collect()   # 전날 결과 먼저 수거
        run_submit()    # 오늘 새 레시피 제출
    else:
        logger.error(f"알 수 없는 mode: {mode}")
        sys.exit(1)
