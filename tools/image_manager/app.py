"""
K-School Food 메뉴 관리 도구
- 구글 시트 메뉴 목록 조회 / 편집 (영어명, 재료, 조리법, 메모)
- 로컬 이미지 → WebP 변환 → R2 업로드 → 시트 반영
"""
import os, io, json, time, base64, uuid, logging, hashlib
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
from PIL import Image
import boto3
from botocore.config import Config
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from bs4 import BeautifulSoup

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30MB

# ── 설정 ───────────────────────────────────────────────────────────────────
R2_ACCOUNT_ID  = os.environ["R2_ACCOUNT_ID"]
R2_ACCESS_KEY  = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_KEY  = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET      = os.environ["R2_BUCKET_NAME"]
R2_PUBLIC_URL  = os.environ["R2_PUBLIC_URL"].rstrip("/")
SHEET_ID       = os.environ["SHEET_ID"]
SA_JSON_PATH   = os.environ.get("GOOGLE_SA_JSON_PATH", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
CF_ACCOUNT_ID  = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN   = os.environ.get("CF_API_TOKEN", "")
D1_DATABASE_ID = os.environ.get("D1_DATABASE_ID", "")

# ── 로그인 보호 ────────────────────────────────────────────────────────────
from functools import wraps
from flask import session, redirect, url_for

app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if ADMIN_PASSWORD and not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "비밀번호가 틀렸습니다."
    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>🍱 K-School Food 관리자</title>
<script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-100 min-h-screen flex items-center justify-center">
<div class="bg-white rounded-2xl shadow-lg p-8 w-full max-w-sm space-y-5">
  <div class="text-center">
    <p class="text-4xl mb-2">🍱</p>
    <h1 class="font-bold text-xl text-gray-900">K-School Food</h1>
    <p class="text-sm text-gray-400">관리자 로그인</p>
  </div>
  {'<p class="text-sm text-red-500 text-center bg-red-50 rounded-lg py-2">' + error + '</p>' if error else ''}
  <form method="post" class="space-y-4">
    <input type="password" name="password" placeholder="비밀번호"
      class="w-full border border-gray-200 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-orange-300"
      autofocus>
    <button type="submit"
      class="w-full bg-orange-500 hover:bg-orange-600 text-white font-bold py-3 rounded-xl transition-colors">
      로그인
    </button>
  </form>
</div>
</body></html>"""

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

def _load_sa() -> dict:
    if SA_JSON_PATH and Path(SA_JSON_PATH).exists():
        return json.loads(Path(SA_JSON_PATH).read_text())
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    if raw:
        return json.loads(raw)
    raise RuntimeError("서비스 계정 JSON을 찾을 수 없습니다.")

# ── Cloudflare D1 ─────────────────────────────────────────────────────────
import re

def _d1_query(sql: str, params: list | None = None) -> dict:
    """D1 REST API 쿼리 실행."""
    if not (CF_ACCOUNT_ID and CF_API_TOKEN and D1_DATABASE_ID):
        raise RuntimeError("D1 환경변수(CF_ACCOUNT_ID, CF_API_TOKEN, D1_DATABASE_ID) 미설정")
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query"
    payload: dict = {"sql": sql}
    if params:
        payload["params"] = params
    r = requests.post(url, headers={
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }, json=payload, timeout=30)
    r.raise_for_status()
    result = r.json()
    if not result.get("success"):
        raise RuntimeError(f"D1 오류: {result.get('errors')}")
    return result


def _parse_ingredients(text: str) -> list:
    """줄바꿈 구분 재료 텍스트 → JSON 배열."""
    if not text:
        return []
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(.+?)\s+([\d½¼¾]+\s*\S*)$', line)
        if m:
            result.append({"name": m.group(1).strip(), "amount": m.group(2).strip()})
        else:
            result.append({"name": line, "amount": ""})
    return result


def _parse_instructions(text: str) -> list:
    """줄바꿈 구분 조리법 텍스트 → JSON 배열."""
    if not text:
        return []
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r'^[\d]+[.)]\s*', '', line)
        line = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩]\s*', '', line)
        if line:
            result.append(line)
    return result


def upsert_recipe_to_d1(korean_name: str, fields: dict):
    """변경된 필드만 D1 recipes 테이블에 upsert. 빈 값은 기존값 유지."""
    english_name    = fields.get("english_name", "")
    image_url       = fields.get("image_url", "")
    ingredients_tx  = fields.get("ingredients", "")
    instructions_tx = fields.get("instructions", "")
    seo_description = fields.get("seo_description", "")

    # 영양정보 JSON 조합
    nutrition = {}
    for key in ("calories", "carbs", "protein", "fat"):
        if fields.get(key):
            nutrition[key] = fields[key]
    nutrition_json = json.dumps(nutrition, ensure_ascii=False) if nutrition else None

    ingredients_json  = json.dumps(_parse_ingredients(ingredients_tx), ensure_ascii=False) \
        if ingredients_tx else None
    instructions_json = json.dumps(_parse_instructions(instructions_tx), ensure_ascii=False) \
        if instructions_tx else None

    _d1_query(
        """
        INSERT INTO recipes (korean_name, english_name, image_url, english_ingredients, instructions, seo_description, nutrition_info)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(korean_name) DO UPDATE SET
            english_name        = CASE WHEN ? != '' THEN ? ELSE recipes.english_name END,
            image_url           = CASE WHEN ? != '' THEN ? ELSE recipes.image_url END,
            english_ingredients = CASE WHEN ? IS NOT NULL THEN ? ELSE recipes.english_ingredients END,
            instructions        = CASE WHEN ? IS NOT NULL THEN ? ELSE recipes.instructions END,
            seo_description     = CASE WHEN ? != '' THEN ? ELSE recipes.seo_description END,
            nutrition_info      = CASE WHEN ? IS NOT NULL THEN ? ELSE recipes.nutrition_info END
        """,
        [
            korean_name, english_name, image_url,
            ingredients_json or "[]", instructions_json or "[]",
            seo_description, nutrition_json or "{}",
            # UPDATE 값
            english_name, english_name,
            image_url, image_url,
            ingredients_json, ingredients_json,
            instructions_json, instructions_json,
            seo_description, seo_description,
            nutrition_json, nutrition_json,
        ]
    )
    logger.info(f"D1 upsert 완료: {korean_name}")


# ── Google Sheets ──────────────────────────────────────────────────────────
def _get_token(scope: str) -> str:
    sa = _load_sa()
    now = int(time.time())
    header  = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": sa["client_email"], "scope": scope,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
    }).encode()).rstrip(b"=")
    msg = header + b"." + payload
    private_key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = private_key.sign(msg, asym_padding.PKCS1v15(), hashes.SHA256())
    jwt = (msg + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")).decode()
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt,
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

def _sheet_name(token: str) -> str:
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "sheets.properties"},
        timeout=15,
    )
    r.raise_for_status()
    sheets = r.json().get("sheets", [])
    return sheets[0]["properties"]["title"] if sheets else "Sheet1"

def get_sheet_data() -> list[dict]:
    """시트 A:L 읽기"""
    token = _get_token("https://www.googleapis.com/auth/spreadsheets.readonly")
    sname = _sheet_name(token)
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A:L",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    values = r.json().get("values", [])
    if not values:
        return []

    header = [h.strip() for h in values[0]]
    def col(row, name, default=""):
        try:
            return row[header.index(name)] if name in header else default
        except IndexError:
            return default

    result = []
    for i, row in enumerate(values[1:], start=2):
        name = col(row, "korean_name")
        if not name:
            continue
        result.append({
            "row":             i,
            "korean_name":     name,
            "english_name":    col(row, "english_name"),
            "image_url":       col(row, "image_url"),
            "ingredients":     col(row, "ingredients"),
            "instructions":    col(row, "instructions"),
            "memo":            col(row, "memo"),
            "seo_description": col(row, "seo_description"),
            "calories":        col(row, "calories"),
            "carbs":           col(row, "carbs"),
            "protein":         col(row, "protein"),
            "fat":             col(row, "fat"),
        })
    return result

def update_sheet_row(row: int, fields: dict):
    """시트 한 행의 여러 컬럼을 한번에 업데이트.
    컬럼: B=english_name, C=image_url, D=미리보기, E=ingredients, F=instructions,
          G=memo, H=seo_description, I=calories, J=carbs, K=protein, L=fat
    """
    token = _get_token("https://www.googleapis.com/auth/spreadsheets")
    sname = _sheet_name(token)

    data = []
    col_map = {
        "english_name":    f"{sname}!B{row}",
        "image_url":       f"{sname}!C{row}",
        "ingredients":     f"{sname}!E{row}",
        "instructions":    f"{sname}!F{row}",
        "memo":            f"{sname}!G{row}",
        "seo_description": f"{sname}!H{row}",
        "calories":        f"{sname}!I{row}",
        "carbs":           f"{sname}!J{row}",
        "protein":         f"{sname}!K{row}",
        "fat":             f"{sname}!L{row}",
    }
    for key, range_ in col_map.items():
        if key in fields:
            data.append({"range": range_, "values": [[fields[key]]]})

    # 이미지 URL이 있으면 D열 미리보기 수식도 업데이트
    if "image_url" in fields and fields["image_url"]:
        data.append({"range": f"{sname}!D{row}", "values": [[f"=IMAGE(C{row})"]]})

    if not data:
        return

    r = requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"valueInputOption": "USER_ENTERED", "data": data},
        timeout=15,
    )
    r.raise_for_status()

# ── Cloudflare R2 ──────────────────────────────────────────────────────────
def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

def upload_to_r2(image_bytes: bytes, filename: str) -> str:
    client = get_r2_client()
    client.put_object(
        Bucket=R2_BUCKET, Key=filename, Body=image_bytes,
        ContentType="image/webp", CacheControl="public, max-age=31536000",
    )
    return f"{R2_PUBLIC_URL}/{filename}"

def convert_to_webp(file_bytes: bytes, max_size: int = 1200, quality: int = 85) -> bytes:
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6)
    return buf.getvalue()

# ── Flask 라우트 ───────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/api/recipes")
@login_required
def api_recipes():
    try:
        data = get_sheet_data()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"시트 읽기 오류: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/recipe/<int:row>", methods=["PUT"])
@login_required
def api_update_recipe(row):
    """영어명, 재료, 조리법, 메모를 시트에 저장"""
    try:
        body = request.get_json()
        if not body:
            return jsonify({"ok": False, "error": "데이터 없음"}), 400

        allowed = {"english_name", "image_url", "ingredients", "instructions", "memo",
                   "seo_description", "calories", "carbs", "protein", "fat"}
        fields = {k: v for k, v in body.items() if k in allowed}
        if not fields:
            return jsonify({"ok": False, "error": "저장할 필드 없음"}), 400

        update_sheet_row(row, fields)
        logger.info(f"시트 저장 완료: row={row}, fields={list(fields.keys())}")

        # D1에도 즉시 반영
        korean_name = body.get("korean_name", "")
        if korean_name:
            try:
                upsert_recipe_to_d1(korean_name, fields)
            except Exception as d1_err:
                logger.warning(f"D1 upsert 실패 (시트는 저장됨): {d1_err}")
                return jsonify({"ok": True, "warn": f"시트 저장 완료, D1 반영 실패: {d1_err}"})

        return jsonify({"ok": True})

    except Exception as e:
        logger.error(f"저장 오류: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    try:
        row         = int(request.form.get("row", 0))
        korean_name = request.form.get("korean_name", "")
        file        = request.files.get("image")

        if not file or not row:
            return jsonify({"ok": False, "error": "이미지와 행 번호가 필요합니다."}), 400

        original = file.read()
        webp     = convert_to_webp(original)

        # 한글 파일명 URL 인코딩 문제 방지 — 메뉴명 MD5 해시 사용
        # 같은 메뉴는 항상 같은 파일명 → 중복 업로드 방지
        name_hash = hashlib.md5(korean_name.encode()).hexdigest()[:16]
        filename  = f"recipes/{name_hash}.webp"
        public_url = upload_to_r2(webp, filename)

        update_sheet_row(row, {"image_url": public_url})

        # D1에도 즉시 반영
        if korean_name:
            try:
                upsert_recipe_to_d1(korean_name, {"image_url": public_url})
            except Exception as d1_err:
                logger.warning(f"D1 이미지 upsert 실패 (시트는 저장됨): {d1_err}")

        orig_kb = len(original) // 1024
        webp_kb = len(webp) // 1024
        logger.info(f"업로드 완료: {korean_name} → {public_url} ({orig_kb}KB → {webp_kb}KB)")

        return jsonify({"ok": True, "url": public_url, "original_kb": orig_kb, "webp_kb": webp_kb})

    except Exception as e:
        logger.error(f"업로드 오류: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ── HTML 레시피 파서 ───────────────────────────────────────────────────────

# 섹션 헤딩 키워드 분류
_HEADING_EN_INGR    = re.compile(r'ingredients?\s*[\(\|/]?\s*en', re.I)
_HEADING_KR_INGR    = re.compile(r'(재료|ingredients?\s*[\(\|/]?\s*kr|ingredients?\s*[\(\|/]?\s*korean)', re.I)
_HEADING_INGR_MIXED = re.compile(r'^ingredients?\s*/\s*재료$', re.I)
_HEADING_INGR_ONLY  = re.compile(r'^ingredients?$', re.I)
_HEADING_EN_INST    = re.compile(r'(instructions?\s*[\(\|/]?\s*en|recipe\s*steps?|step[\-\s]by[\-\s]step\s*[\(\|/]?\s*en|cooking\s*instructions?|preparation\s*steps?)', re.I)
_HEADING_KR_INST    = re.compile(r'(조리\s*순서|만드는\s*법|조리법)', re.I)
_HEADING_INST_MIXED = re.compile(r'preparation\s*steps?\s*/\s*조리', re.I)
_HEADING_INST_ONLY  = re.compile(r'^instructions?$', re.I)
_HEADING_NUTRITION  = re.compile(r'(nutrition|macro)', re.I)

# 영양소 레이블 → 키
_NUTRITION_MAP = {
    re.compile(r'calorie', re.I): 'calories',
    re.compile(r'carb', re.I):    'carbs',
    re.compile(r'protein', re.I): 'protein',
    re.compile(r'fat', re.I):     'fat',
}


def _tag_text(tag) -> str:
    """태그의 plain text를 반환 (태그 이름 포함 안 함)."""
    return tag.get_text(separator=' ', strip=True) if tag else ''


def _list_items(container) -> list[str]:
    """ul/ol 안의 li 텍스트 목록 반환. li 없으면 p 태그 사용."""
    items = []
    lis = container.find_all('li')
    if lis:
        for li in lis:
            t = li.get_text(' ', strip=True)
            if t:
                items.append(t)
    else:
        for p in container.find_all('p'):
            t = p.get_text(' ', strip=True)
            if t:
                items.append(t)
    return items


def _siblings_until_next_heading(tag):
    """헤딩 태그 다음에 오는 형제 요소들을 다음 헤딩 전까지 수집."""
    elements = []
    for sib in tag.find_next_siblings():
        if sib.name in ('h1','h2','h3','h4','h5','h6','hr'):
            break
        elements.append(sib)
    return elements


def _extract_items_from_siblings(siblings) -> list[str]:
    """형제 요소 목록에서 텍스트 항목 추출 (ul/ol/li/p 처리)."""
    items = []
    for el in siblings:
        if el.name in ('ul', 'ol'):
            items.extend(_list_items(el))
        elif el.name in ('p', 'div', 'li'):
            t = el.get_text(' ', strip=True)
            if t:
                items.append(t)
    return items


def _strip_step_prefix(text: str) -> str:
    """조리법 줄 앞의 번호/레이블 제거."""
    text = re.sub(r'^\d+\.\s*', '', text)
    text = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩]\s*', '', text)
    return text.strip()


def parse_html_recipe(html: str) -> dict:
    """
    비규격 HTML에서 레시피 데이터 추출.
    반환 키: english_name, image_url, ingredients, instructions,
             seo_description, calories, carbs, protein, fat
    """
    soup = BeautifulSoup(html, 'html.parser')

    result = {
        'english_name':    '',
        'image_url':       '',
        'ingredients':     '',
        'instructions':    '',
        'seo_description': '',
        'calories':        '',
        'carbs':           '',
        'protein':         '',
        'fat':             '',
    }

    # ── 영어 이름 ────────────────────────────────────────────────────────────
    # h1 → title 순으로 시도, 한국어 제거
    h1 = soup.find('h1')
    if h1:
        text = h1.get_text(' ', strip=True)
        # 한국어 부분 제거 (괄호 포함)
        text = re.sub(r'[가-힣]+.*', '', text).strip()
        # 남은 특수문자 정리
        text = re.sub(r'\s*[\(\[\|].*', '', text).strip()
        if text:
            result['english_name'] = text
    if not result['english_name']:
        title_tag = soup.find('title')
        if title_tag:
            text = title_tag.get_text(' ', strip=True)
            text = re.sub(r'[\|–\-].*', '', text).strip()
            text = re.sub(r'recipe$', '', text, flags=re.I).strip()
            result['english_name'] = text

    # ── 이미지 URL ──────────────────────────────────────────────────────────
    # opal.google 이미지 또는 첫 번째 <img>
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if src and not src.startswith('data:'):
            result['image_url'] = src
            break

    # ── SEO 설명 (인용구/이탤릭) ─────────────────────────────────────────────
    for tag in soup.find_all(['blockquote', 'p']):
        text = tag.get_text(' ', strip=True)
        # 따옴표로 감싸진 짧은 문장
        if re.match(r'^["""\'\'\'"]', text) and 20 < len(text) < 250:
            result['seo_description'] = text.strip('"\'"\'"\'')
            break
    # blockquote가 없으면 italic
    if not result['seo_description']:
        for tag in soup.find_all(['em', 'i']):
            text = tag.get_text(' ', strip=True)
            if 20 < len(text) < 250:
                result['seo_description'] = text
                break

    # ── 재료 ─────────────────────────────────────────────────────────────────
    en_ingr_lines: list[str] = []
    kr_ingr_lines: list[str] = []

    headings = soup.find_all(['h1','h2','h3','h4','h5','h6'])
    for hd in headings:
        htext = hd.get_text(' ', strip=True)
        siblings = _siblings_until_next_heading(hd)
        items = _extract_items_from_siblings(siblings)

        if _HEADING_INGR_MIXED.search(htext):
            # 혼합 헤딩: 전체를 영어 섹션으로 취급 (EN/KR 분리 어려움)
            en_ingr_lines.extend(items)
        elif _HEADING_EN_INGR.search(htext) or _HEADING_INGR_ONLY.search(htext):
            en_ingr_lines.extend(items)
        elif _HEADING_KR_INGR.search(htext):
            kr_ingr_lines.extend(items)

    # 헤딩 기반 추출 실패 시 → class/text 기반으로 재탐색
    if not en_ingr_lines and not kr_ingr_lines:
        for ul in soup.find_all(['ul','ol']):
            prev = ul.find_previous(['h1','h2','h3','h4','p','span'])
            if prev:
                ptext = prev.get_text(' ', strip=True).lower()
                if 'ingredient' in ptext:
                    items = _list_items(ul)
                    if re.search(r'[가-힣]', ptext):
                        kr_ingr_lines.extend(items)
                    else:
                        en_ingr_lines.extend(items)

    # 재료 텍스트 조합 (EN → KR)
    parts = []
    if en_ingr_lines:
        parts.append('\n'.join(en_ingr_lines))
    if kr_ingr_lines:
        if parts:
            parts.append('\n[재료]\n' + '\n'.join(kr_ingr_lines))
        else:
            parts.append('\n'.join(kr_ingr_lines))
    result['ingredients'] = '\n'.join(parts)

    # ── 조리법 ───────────────────────────────────────────────────────────────
    en_inst_lines: list[str] = []
    kr_inst_lines: list[str] = []

    for hd in headings:
        htext = hd.get_text(' ', strip=True)
        siblings = _siblings_until_next_heading(hd)
        items = _extract_items_from_siblings(siblings)
        # 조리법은 ol 우선
        ol = hd.find_next('ol')
        if ol:
            # ol이 다음 헤딩 전인지 확인
            ol_items = _list_items(ol)
            if ol_items and len(ol_items) > len(items):
                items = ol_items

        if _HEADING_INST_MIXED.search(htext):
            en_inst_lines.extend([_strip_step_prefix(t) for t in items])
        elif _HEADING_EN_INST.search(htext) or _HEADING_INST_ONLY.search(htext):
            en_inst_lines.extend([_strip_step_prefix(t) for t in items])
        elif _HEADING_KR_INST.search(htext):
            kr_inst_lines.extend([_strip_step_prefix(t) for t in items])

    parts = []
    if en_inst_lines:
        parts.append('\n'.join(en_inst_lines))
    if kr_inst_lines:
        if parts:
            parts.append('\n[조리법]\n' + '\n'.join(kr_inst_lines))
        else:
            parts.append('\n'.join(kr_inst_lines))
    result['instructions'] = '\n'.join(parts)

    # ── 영양 정보 ─────────────────────────────────────────────────────────────
    # 전략 1: 헤딩으로 구분된 영양 섹션
    for hd in headings:
        if _HEADING_NUTRITION.search(hd.get_text(' ', strip=True)):
            container = hd.find_next(['div','section','table'])
            if container:
                _extract_nutrition_from_container(container, result)
            break

    # 전략 2: "Calories", "Carbs" 등 레이블 텍스트로 탐색
    if not any(result[k] for k in ('calories','carbs','protein','fat')):
        _extract_nutrition_by_label(soup, result)

    return result


def _extract_nutrition_from_container(container, result: dict):
    """영양 섹션 컨테이너에서 수치 추출."""
    text = container.get_text(' ', strip=True)
    _extract_nutrition_by_label_from_text(text, result)


def _extract_nutrition_by_label(soup, result: dict):
    """전체 문서에서 영양소 레이블과 근처 수치를 찾아 추출."""
    for label_re, key in _NUTRITION_MAP.items():
        if result[key]:
            continue
        for tag in soup.find_all(string=label_re):
            # 근처(부모/형제)에서 숫자 찾기
            parent = tag.parent
            if parent:
                # 다음 형제 텍스트에서 숫자 추출
                for sib in list(parent.next_siblings)[:3]:
                    t = sib if isinstance(sib, str) else getattr(sib, 'get_text', lambda: '')()
                    m = re.search(r'([\d.]+\s*(?:kcal|g|mg)?)', str(t), re.I)
                    if m:
                        result[key] = m.group(1).strip()
                        break
                # 형제에서 못 찾으면 부모의 다음 형제
                if not result[key]:
                    for sib in list(parent.parent.next_siblings if parent.parent else [])[:3]:
                        t = getattr(sib, 'get_text', lambda sep, strip: '')(' ', True)
                        m = re.search(r'([\d.]+\s*(?:kcal|g|mg)?)', t, re.I)
                        if m:
                            result[key] = m.group(1).strip()
                            break


def _extract_nutrition_by_label_from_text(text: str, result: dict):
    """텍스트 블록에서 'Calories 320kcal' 형태 파싱."""
    for label_re, key in _NUTRITION_MAP.items():
        if result[key]:
            continue
        m = re.search(label_re.pattern + r'\s*[:\-]?\s*([\d.]+\s*(?:kcal|g|mg)?)', text, re.I)
        if m:
            result[key] = m.group(1).strip()


# ── 식단 배치 API ─────────────────────────────────────────────────────────

@app.route("/api/meals")
@login_required
def api_meals():
    """최근 N일 meal_trays 조회"""
    try:
        days = int(request.args.get("days", 28))
        result = _d1_query(
            f"SELECT date, rice, soup, side1, side2, side3, calories FROM meal_trays "
            f"ORDER BY date DESC LIMIT {days}"
        )
        rows = result["result"][0].get("results", [])
        return jsonify({"ok": True, "data": rows})
    except Exception as e:
        logger.error(f"식단 조회 오류: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/meal/<date>", methods=["PUT"])
@login_required
def api_update_meal(date):
    """식단 슬롯 재배치 저장 (rice/soup/side1/side2/side3)"""
    try:
        body = request.get_json()
        if not body:
            return jsonify({"ok": False, "error": "데이터 없음"}), 400

        allowed = {"rice", "soup", "side1", "side2", "side3"}
        fields  = {k: v for k, v in body.items() if k in allowed}
        if not fields:
            return jsonify({"ok": False, "error": "저장할 필드 없음"}), 400

        sets   = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [date]
        _d1_query(f"UPDATE meal_trays SET {sets} WHERE date = ?", params)
        logger.info(f"식단 배치 저장: {date} → {fields}")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"식단 저장 오류: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── HTML Import 엔드포인트 ─────────────────────────────────────────────────

@app.route("/api/import-html", methods=["POST"])
@login_required
def api_import_html():
    """
    HTML 파일에서 레시피 파싱 + 이미지 다운로드 → R2 업로드.
    반환: 파싱된 필드들 (저장은 안 함 — 사용자 확인 후 /api/recipe/<row>로 저장)
    """
    try:
        file        = request.files.get("html_file")
        row         = int(request.form.get("row", 0))
        korean_name = request.form.get("korean_name", "")

        if not file:
            return jsonify({"ok": False, "error": "HTML 파일이 없습니다."}), 400

        html_content = file.read().decode("utf-8", errors="ignore")
        parsed = parse_html_recipe(html_content)

        # 이미지 다운로드 → WebP 변환 → R2 업로드
        img_result = {}
        if parsed.get("image_url"):
            try:
                resp = requests.get(parsed["image_url"], timeout=20, headers={
                    "User-Agent": "Mozilla/5.0"
                })
                resp.raise_for_status()
                original_bytes = resp.content
                webp_bytes = convert_to_webp(original_bytes)

                name_hash = hashlib.md5(korean_name.encode()).hexdigest()[:16] if korean_name \
                            else hashlib.md5(parsed["image_url"].encode()).hexdigest()[:16]
                filename = f"recipes/{name_hash}.webp"
                public_url = upload_to_r2(webp_bytes, filename)

                parsed["image_url"] = public_url
                img_result = {
                    "original_kb": len(original_bytes) // 1024,
                    "webp_kb":     len(webp_bytes) // 1024,
                    "url":         public_url,
                }
                logger.info(f"HTML import 이미지 업로드: {korean_name} → {public_url}")
            except Exception as img_err:
                logger.warning(f"HTML import 이미지 업로드 실패: {img_err}")
                parsed["image_url"] = ""  # 이미지 실패 시 빈값
                img_result = {"error": str(img_err)}

        return jsonify({
            "ok":         True,
            "parsed":     parsed,
            "img_result": img_result,
        })

    except Exception as e:
        logger.error(f"HTML import 오류: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    print("=" * 50)
    print("  K-School Food 메뉴 관리 도구")
    print("  http://localhost:5001 에서 실행 중")
    print("=" * 50)
    app.run(debug=True, port=5001)
