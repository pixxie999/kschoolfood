import os
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs, quote
from jinja2 import Environment, FileSystemLoader

try:
    from workers import WorkerEntrypoint, Response
    IS_WORKERS = True
except ImportError:
    IS_WORKERS = False
    class WorkerEntrypoint:
        pass
    class Response:
        def __init__(self, body, headers=None, status=200):
            pass

from app.db_adapter import get_db_adapter
from app.services.neis_api import fetch_meal_from_neis
from app.services.llm_service import translate_and_localize_recipe
from app.services.affiliate import match_affiliate_links, get_tray_affiliate_link

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
jinja_env = Environment(loader=FileSystemLoader(os.path.join(BASE_DIR, "app", "templates")))

def get_korean_today() -> str:
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y%m%d")

def get_week_dates(anchor: str) -> list:
    """anchor 날짜가 속한 주의 월~금 날짜 리스트 반환"""
    try:
        dt = datetime.strptime(anchor, "%Y%m%d")
    except ValueError:
        kst = timezone(timedelta(hours=9))
        dt = datetime.now(kst)
    monday = dt - timedelta(days=dt.weekday())
    return [(monday + timedelta(days=i)).strftime("%Y%m%d") for i in range(5)]

async def _get_meal(date: str, db, env) -> dict:
    """D1 캐시 → NEIS 순으로 meal 조회, 없으면 빈 dict"""
    meal = await db.fetch_one("SELECT * FROM meal_trays WHERE date = ?", (date,))
    if not meal:
        neis_meal = await fetch_meal_from_neis(date, env=env)
        if neis_meal:
            await db.execute(
                "INSERT INTO meal_trays (date, rice, soup, side1, side2, side3, calories, allergies) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (neis_meal["date"], neis_meal["rice"], neis_meal["soup"],
                 neis_meal["side1"], neis_meal["side2"], neis_meal["side3"],
                 neis_meal["calories"], neis_meal.get("allergies", "{}"))
            )
            meal = await db.fetch_one("SELECT * FROM meal_trays WHERE date = ?", (date,))
    return meal or {}

def _build_dishes(meal: dict, allergies: dict) -> list:
    SKIP = {"", "No School Lunch", "Weekend or Public Holiday",
            "Enjoy your break!", "No Menu Available", "See you on weekdays"}
    roles = [
        ("Main Rice",       "rice"),
        ("Soup/Stew",       "soup"),
        ("Banchan (Side 1)","side1"),
        ("Banchan (Side 2)","side2"),
        ("Banchan (Side 3)","side3"),
    ]
    return [
        {
            "role": role, "key": key,
            "korean_name": meal.get(key, ""),
            "allergies": allergies.get(key, []),
        }
        for role, key in roles
        if meal.get(key, "") not in SKIP
    ]

async def render_index(url, env):
    query = parse_qs(url.query)
    date = query.get("date", [None])[0] or get_korean_today()

    try:
        parsed_date = datetime.strptime(date, "%Y%m%d")
        formatted_date = parsed_date.strftime("%Y-%m-%d (%A)")
    except ValueError:
        formatted_date = date

    db = get_db_adapter(env)
    meal = await _get_meal(date, db, env)

    if not meal:
        meal = {
            "date": date, "rice": "No School Lunch", "soup": "Weekend or Public Holiday",
            "side1": "Enjoy your break!", "side2": "No Menu Available",
            "side3": "See you on weekdays", "calories": "0 Kcal", "allergies": "{}"
        }

    try:
        allergies = json.loads(meal.get("allergies") or "{}")
    except Exception:
        allergies = {}

    dishes_raw = _build_dishes(meal, allergies)
    translated_dishes = []
    for dish in dishes_raw:
        ko = dish["korean_name"]
        recipe = await translate_and_localize_recipe(ko, db, env=env) if ko else None
        translated_dishes.append({
            **dish,
            "english_name": recipe.get("english_name") if recipe else ko,
            "recipe_id": recipe.get("id") if recipe else None,
            "has_recipe": recipe is not None,
        })

    # 빠진 칸 채우기 (5칸 유지)
    all_keys = ["rice","soup","side1","side2","side3"]
    present = {d["key"] for d in translated_dishes}
    for role, key in [("Main Rice","rice"),("Soup/Stew","soup"),
                      ("Banchan (Side 1)","side1"),("Banchan (Side 2)","side2"),
                      ("Banchan (Side 3)","side3")]:
        if key not in present:
            translated_dishes.insert(all_keys.index(key), {
                "role": role, "key": key, "korean_name": meal.get(key,""),
                "allergies": [], "english_name": meal.get(key,""),
                "recipe_id": None, "has_recipe": False
            })

    tray_affiliate_url = await get_tray_affiliate_link(db)

    try:
        cur = datetime.strptime(date, "%Y%m%d")
        prev_date = (cur - timedelta(days=1)).strftime("%Y%m%d")
        next_date = (cur + timedelta(days=1)).strftime("%Y%m%d")
    except ValueError:
        prev_date = next_date = date

    template = jinja_env.get_template("index.html")
    html = template.render(
        date=date, formatted_date=formatted_date, meal=meal,
        dishes=translated_dishes, tray_affiliate_url=tray_affiliate_url,
        prev_date=prev_date, next_date=next_date
    )
    return Response(html, headers={"Content-Type": "text/html; charset=utf-8"})


async def render_week(url, env):
    query = parse_qs(url.query)
    anchor = query.get("date", [None])[0] or get_korean_today()
    dates = get_week_dates(anchor)

    db = get_db_adapter(env)
    week_data = []
    for date in dates:
        meal = await _get_meal(date, db, env)
        try:
            dt = datetime.strptime(date, "%Y%m%d")
            label = dt.strftime("%a %m/%d")
        except ValueError:
            label = date

        if not meal:
            week_data.append({"date": date, "label": label, "dishes": [], "calories": "", "no_meal": True})
            continue

        try:
            allergies = json.loads(meal.get("allergies") or "{}")
        except Exception:
            allergies = {}

        dishes_raw = _build_dishes(meal, allergies)
        dishes = []
        for dish in dishes_raw:
            ko = dish["korean_name"]
            recipe = await translate_and_localize_recipe(ko, db, env=env) if ko else None
            dishes.append({
                **dish,
                "english_name": recipe.get("english_name") if recipe else ko,
                "recipe_id": recipe.get("id") if recipe else None,
                "has_recipe": recipe is not None,
                "image_url": recipe.get("image_url", "") if recipe else "",
            })
        week_data.append({
            "date": date, "label": label,
            "dishes": dishes, "calories": meal.get("calories",""), "no_meal": False
        })

    # 이전/다음 주 앵커
    try:
        anchor_dt = datetime.strptime(anchor, "%Y%m%d")
        prev_week = (anchor_dt - timedelta(days=7)).strftime("%Y%m%d")
        next_week = (anchor_dt + timedelta(days=7)).strftime("%Y%m%d")
    except ValueError:
        prev_week = next_week = anchor

    tray_affiliate_url = await get_tray_affiliate_link(db)
    template = jinja_env.get_template("week.html")
    html = template.render(
        week_data=week_data, anchor=anchor,
        prev_week=prev_week, next_week=next_week,
        tray_affiliate_url=tray_affiliate_url,
        week_label=datetime.strptime(dates[0], "%Y%m%d").strftime("Week of %B %d, %Y")
    )
    return Response(html, headers={"Content-Type": "text/html; charset=utf-8"})


async def render_search(url, env):
    query = parse_qs(url.query)
    q = (query.get("q", [None])[0] or "").strip()

    results = []
    if q:
        db = get_db_adapter(env)
        like = f"%{q}%"
        results = await db.fetch_all(
            """SELECT id, korean_name, english_name, seo_description, image_url
               FROM recipes
               WHERE english_name LIKE ? OR korean_name LIKE ?
               ORDER BY english_name LIMIT 30""",
            (like, like)
        )

    template = jinja_env.get_template("search.html")
    html = template.render(q=q, results=results)
    return Response(html, headers={"Content-Type": "text/html; charset=utf-8"})


async def render_recipe(recipe_id, env):
    db = get_db_adapter(env)
    recipe_row = await db.fetch_one("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    if not recipe_row:
        return Response("Recipe not found", status=404)

    ingredients = json.loads(recipe_row["english_ingredients"] or "[]")
    substitutes = json.loads(recipe_row["local_substitutes"] or "[]")
    instructions = json.loads(recipe_row["instructions"] or "[]")
    nutrition = json.loads(recipe_row["nutrition_info"] or "{}")

    matched_ingredients = await match_affiliate_links(ingredients, db)
    tray_affiliate_url = await get_tray_affiliate_link(db)

    image = recipe_row.get("image_url") or "https://images.unsplash.com/photo-1541832676-9b763b0239ab?q=80&w=600"
    ld_json = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe_row["english_name"],
        "image": image,
        "description": recipe_row["seo_description"],
        "recipeIngredient": [f"{i['name']} ({i['amount']})" for i in ingredients],
        "recipeInstructions": [{"@type": "HowToStep", "text": s} for s in instructions],
        "nutrition": {
            "@type": "NutritionInformation",
            "calories": nutrition.get("calories","N/A"),
            "carbohydrateContent": nutrition.get("carbs","N/A"),
            "proteinContent": nutrition.get("protein","N/A"),
            "fatContent": nutrition.get("fat","N/A"),
        }
    }

    template = jinja_env.get_template("recipe.html")
    html = template.render(
        recipe=recipe_row, ingredients=matched_ingredients,
        substitutes=substitutes, instructions=instructions,
        nutrition=nutrition, tray_affiliate_url=tray_affiliate_url,
        ld_json=json.dumps(ld_json)
    )
    return Response(html, headers={"Content-Type": "text/html; charset=utf-8"})


async def render_sitemap(host, env):
    db = get_db_adapter(env)
    recipes = await db.fetch_all("SELECT id FROM recipes")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    xml  = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += f'  <url><loc>{host}/</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>\n'
    xml += f'  <url><loc>{host}/week</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>\n'
    for r in recipes:
        xml += f'  <url><loc>{host}/recipe/{r["id"]}</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>\n'
    xml += '</urlset>'
    return Response(xml, headers={"Content-Type": "application/xml; charset=utf-8"})


async def render_robots(host):
    txt = f"User-agent: *\nAllow: /\nDisallow:\n\nSitemap: {host}/sitemap.xml\n"
    return Response(txt, headers={"Content-Type": "text/plain; charset=utf-8"})


async def handle_review_post(request, recipe_id, env):
    db = get_db_adapter(env)
    ip = request.headers.get("cf-connecting-ip", "unknown")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = await db.fetch_one(
        "SELECT id FROM reviews WHERE recipe_id=? AND country=? AND DATE(created_at)=?",
        (recipe_id, ip, today)
    )
    if existing:
        return Response(json.dumps({"error": "Already reviewed today."}), status=429,
                        headers={"Content-Type": "application/json"})
    try:
        body = await request.json()
    except Exception:
        return Response(json.dumps({"error": "Invalid JSON"}), status=400,
                        headers={"Content-Type": "application/json"})
    rating = body.get("rating")
    comment = (body.get("comment") or "").strip()[:500]
    if not isinstance(rating, int) or not (1 <= rating <= 5):
        return Response(json.dumps({"error": "Rating must be 1-5"}), status=400,
                        headers={"Content-Type": "application/json"})
    if comment and len(comment) < 3:
        comment = ""
    country = request.headers.get("cf-ipcountry", "")
    await db.execute(
        "INSERT INTO reviews (recipe_id, rating, comment, country) VALUES (?, ?, ?, ?)",
        (recipe_id, rating, comment, f"{ip}|{country}")
    )
    return Response(json.dumps({"ok": True}), headers={"Content-Type": "application/json"})


async def get_reviews(recipe_id, env):
    db = get_db_adapter(env)
    reviews = await db.fetch_all(
        "SELECT rating, comment, country, created_at FROM reviews WHERE recipe_id=? ORDER BY id DESC LIMIT 20",
        (recipe_id,)
    )
    avg_row = await db.fetch_one(
        "SELECT AVG(rating) as avg, COUNT(*) as cnt FROM reviews WHERE recipe_id=?",
        (recipe_id,)
    )
    avg = round(avg_row["avg"] or 0, 1) if avg_row else 0
    cnt = avg_row["cnt"] if avg_row else 0
    clean = []
    for r in reviews:
        raw = r.get("country","")
        cc = raw.split("|")[1] if "|" in raw else raw
        clean.append({"rating": r["rating"], "comment": r["comment"] or "",
                      "country": cc, "created_at": (r["created_at"] or "")[:10]})
    return {"avg": avg, "count": cnt, "reviews": clean}


class Default(WorkerEntrypoint):
    async def on_fetch(self, request):
        env = getattr(self, 'env', None)
        url = urlparse(request.url)
        host = f"{url.scheme}://{url.netloc}"
        path = url.path

        if path in ("/", ""):
            return await render_index(url, env)
        elif path == "/week":
            return await render_week(url, env)
        elif path == "/search":
            return await render_search(url, env)
        elif path.startswith("/recipe/"):
            try:
                return await render_recipe(int(path.split("/")[-1]), env)
            except ValueError:
                return Response("Invalid recipe ID", status=400)
        elif path == "/sitemap.xml":
            return await render_sitemap(host, env)
        elif path == "/robots.txt":
            return await render_robots(host)
        elif path.startswith("/api/reviews/"):
            try:
                recipe_id = int(path.split("/")[-1])
            except ValueError:
                return Response("Invalid ID", status=400)
            if request.method == "POST":
                return await handle_review_post(request, recipe_id, env)
            else:
                data = await get_reviews(recipe_id, env)
                return Response(json.dumps(data), headers={"Content-Type": "application/json"})
        else:
            return Response("Not Found", status=404)
