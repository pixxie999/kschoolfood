"""
K-School Food 메뉴 관리 도구
- 구글 시트 메뉴 목록 조회 / 편집 (영어명, 재료, 조리법, 메모)
- 로컬 이미지 → WebP 변환 → R2 업로드 → 시트 반영
"""
import os, io, json, time, base64, uuid, logging
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
from PIL import Image
import boto3
from botocore.config import Config
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

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
    """시트 A:G 읽기"""
    token = _get_token("https://www.googleapis.com/auth/spreadsheets.readonly")
    sname = _sheet_name(token)
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{sname}!A:G",
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
            "row":          i,
            "korean_name":  name,
            "english_name": col(row, "english_name"),
            "image_url":    col(row, "image_url"),
            "ingredients":  col(row, "ingredients"),
            "instructions": col(row, "instructions"),
            "memo":         col(row, "memo"),
        })
    return result

def update_sheet_row(row: int, fields: dict):
    """시트 한 행의 여러 컬럼을 한번에 업데이트.
    컬럼: B=english_name, C=image_url, D=미리보기, E=ingredients, F=instructions, G=memo
    """
    token = _get_token("https://www.googleapis.com/auth/spreadsheets")
    sname = _sheet_name(token)

    data = []
    col_map = {
        "english_name": f"{sname}!B{row}",
        "image_url":    f"{sname}!C{row}",
        "ingredients":  f"{sname}!E{row}",
        "instructions": f"{sname}!F{row}",
        "memo":         f"{sname}!G{row}",
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

        allowed = {"english_name", "ingredients", "instructions", "memo"}
        fields = {k: v for k, v in body.items() if k in allowed}
        if not fields:
            return jsonify({"ok": False, "error": "저장할 필드 없음"}), 400

        update_sheet_row(row, fields)
        logger.info(f"저장 완료: row={row}, fields={list(fields.keys())}")
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

        safe_name  = "".join(c if c.isalnum() else "_" for c in korean_name)[:30]
        filename   = f"recipes/{safe_name}_{uuid.uuid4().hex[:8]}.webp"
        public_url = upload_to_r2(webp, filename)

        update_sheet_row(row, {"image_url": public_url})

        orig_kb = len(original) // 1024
        webp_kb = len(webp) // 1024
        logger.info(f"업로드 완료: {korean_name} → {public_url} ({orig_kb}KB → {webp_kb}KB)")

        return jsonify({"ok": True, "url": public_url, "original_kb": orig_kb, "webp_kb": webp_kb})

    except Exception as e:
        logger.error(f"업로드 오류: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    print("=" * 50)
    print("  K-School Food 메뉴 관리 도구")
    print("  http://localhost:5001 에서 실행 중")
    print("=" * 50)
    app.run(debug=True, port=5001)
