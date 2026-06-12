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

def _from_json(value):
    try:
        return json.loads(value) if isinstance(value, str) else (value or [])
    except Exception:
        return []

jinja_env.filters["from_json"] = _from_json

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
    result = []
    for role, key in roles:
        val = meal.get(key, "")
        if val in SKIP:
            continue
        # "|" 구분자로 여러 메뉴가 합쳐진 경우 개별 항목으로 분리
        # 첫 번째 항목은 원래 key 사용, 추가 항목은 sub_N으로 표기
        names = [n.strip() for n in val.split("|") if n.strip()]
        for i, name in enumerate(names):
            result.append({
                "role": role,
                "key": key,
                "korean_name": name,
                "allergies": allergies.get(key, []) if i == 0 else [],
                "is_sub": i > 0,  # 같은 칸의 추가 메뉴 여부
            })
    return result

async def _fetch_recipes_batch(ko_names: list, db) -> dict:
    """여러 메뉴명을 한 번의 IN 쿼리로 조회 — D1 reads 대폭 절약"""
    if not ko_names:
        return {}
    placeholders = ",".join("?" * len(ko_names))
    rows = await db.fetch_all(
        f"SELECT id, korean_name, english_name, image_url, seo_description, "
        f"english_ingredients, local_substitutes, instructions, nutrition_info "
        f"FROM recipes WHERE korean_name IN ({placeholders})",
        tuple(ko_names)
    )
    return {r["korean_name"]: r for r in rows}


async def render_index(url, env):
    query = parse_qs(url.query)
    date = query.get("date", [None])[0] or get_korean_today()

    try:
        parsed_date = datetime.strptime(date, "%Y%m%d")
        formatted_date = parsed_date.strftime("%Y-%m-%d (%A)")
    except ValueError:
        formatted_date = date

    db = get_db_adapter(env)

    # 주말(토=5, 일=6)은 D1 조회 없이 바로 빈 식단 처리
    try:
        is_weekend = datetime.strptime(date, "%Y%m%d").weekday() >= 5
    except ValueError:
        is_weekend = False

    meal = None if is_weekend else await _get_meal(date, db, env)

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

    # 개별 쿼리 대신 한 번에 일괄 조회 (D1 reads 절약)
    ko_names = [d["korean_name"] for d in dishes_raw if d["korean_name"]]
    recipe_map = await _fetch_recipes_batch(ko_names, db)

    # 각 dish를 번역하고 is_sub끼리 부모 dish의 subs 리스트에 합침
    flat = []
    for dish in dishes_raw:
        ko = dish["korean_name"]
        recipe = recipe_map.get(ko)
        flat.append({
            **dish,
            "english_name": recipe.get("english_name") if recipe else ko,
            "recipe_id": recipe.get("id") if recipe else None,
            "has_recipe": recipe is not None,
            "image_url": (recipe.get("image_url") or "") if recipe else "",
        })

    # is_sub 항목을 부모 항목의 subs 리스트로 합침
    translated_dishes = []
    for d in flat:
        if d.get("is_sub") and translated_dishes and translated_dishes[-1]["key"] == d["key"]:
            translated_dishes[-1].setdefault("subs", []).append(d)
        else:
            translated_dishes.append({**d, "subs": []})

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
                "recipe_id": None, "has_recipe": False, "is_sub": False, "subs": [],
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
    return Response(html, headers={
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "public, max-age=1800, s-maxage=3600",  # 브라우저 30분, CF 엣지 1시간
    })


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
        ko_names = [d["korean_name"] for d in dishes_raw if d["korean_name"]]
        recipe_map = await _fetch_recipes_batch(ko_names, db)
        dishes = []
        for dish in dishes_raw:
            ko = dish["korean_name"]
            recipe = recipe_map.get(ko)
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
    return Response(html, headers={
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "public, max-age=1800, s-maxage=3600",
    })


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

    # recipeInstructions — name+url 필드 포함 (Search Console 필수)
    recipe_instructions = []
    for idx, step in enumerate(instructions, start=1):
        recipe_instructions.append({
            "@type": "HowToStep",
            "name": f"Step {idx}",
            "text": step,
            "url": f"https://kschoolfood.com/recipe/{recipe_id}#step{idx}",
        })

    # 리뷰/평점 데이터 (D1에서 집계)
    rating_row = await db.fetch_one(
        "SELECT COUNT(*) as cnt, AVG(rating) as avg FROM reviews WHERE recipe_id = ?",
        (recipe_id,)
    )
    aggregate_rating = None
    if rating_row and rating_row.get("cnt", 0) > 0:
        aggregate_rating = {
            "@type": "AggregateRating",
            "ratingValue": round(rating_row["avg"], 1),
            "reviewCount": rating_row["cnt"],
            "bestRating": 5,
            "worstRating": 1,
        }

    ld_json = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe_row["english_name"],
        "image": [image],
        "description": recipe_row.get("seo_description") or "",
        "keywords": recipe_row.get("korean_name", ""),
        "recipeCategory": "Main Course",
        "recipeCuisine": "Korean",
        "recipeYield": "2 servings",
        "prepTime": "PT15M",
        "cookTime": "PT20M",
        "totalTime": "PT35M",
        "recipeIngredient": [f"{i['name']} ({i['amount']})" for i in ingredients],
        "recipeInstructions": recipe_instructions if recipe_instructions else [
            {
                "@type": "HowToStep",
                "name": "Prepare",
                "text": f"Prepare and cook {recipe_row['english_name']} following traditional Korean method.",
                "url": f"https://kschoolfood.com/recipe/{recipe_id}#step1",
            }
        ],
        "nutrition": {
            "@type": "NutritionInformation",
            "calories": nutrition.get("calories", ""),
            "carbohydrateContent": nutrition.get("carbs", ""),
            "proteinContent": nutrition.get("protein", ""),
            "fatContent": nutrition.get("fat", ""),
        },
        **({"aggregateRating": aggregate_rating} if aggregate_rating else {}),
    }

    template = jinja_env.get_template("recipe.html")
    html = template.render(
        recipe=recipe_row, ingredients=matched_ingredients,
        substitutes=substitutes, instructions=instructions,
        nutrition=nutrition, tray_affiliate_url=tray_affiliate_url,
        ld_json=json.dumps(ld_json)
    )
    return Response(html, headers={
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "public, max-age=3600, s-maxage=86400",  # 브라우저 1시간, CF 엣지 1일
    })


async def render_recipes(url, env):
    """레시피 탐색 페이지 — category/tag 필터 + 페이지네이션"""
    query = parse_qs(url.query)
    category = (query.get("category", [None])[0] or "").strip()
    tag      = (query.get("tag", [None])[0] or "").strip()
    page     = max(1, int((query.get("page", [1])[0] or 1)))
    per_page = 24

    db = get_db_adapter(env)

    # 필터 SQL 조건
    conditions, params = [], []
    if category:
        conditions.append("category = ?")
        params.append(category)
    if tag:
        conditions.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    # 영어명이 있는 레시피만
    conditions.append("(english_name IS NOT NULL AND english_name != '')")
    where = "WHERE " + " AND ".join(conditions)

    offset = (page - 1) * per_page
    params_page = params + [per_page, offset]

    recipes = await db.fetch_all(
        f"SELECT id, korean_name, english_name, image_url, category, tags, seo_description "
        f"FROM recipes {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params_page)
    )
    # 총 건수 (페이지네이션용)
    count_row = await db.fetch_one(
        f"SELECT COUNT(*) as cnt FROM recipes {where}", tuple(params)
    )
    total = count_row["cnt"] if count_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page)

    # 카테고리 집계
    cats = await db.fetch_all(
        "SELECT category, COUNT(*) as cnt FROM recipes "
        "WHERE category IS NOT NULL AND category != '' AND english_name != '' "
        "GROUP BY category ORDER BY cnt DESC"
    )

    # tags JSON 파싱
    parsed_recipes = []
    for r in recipes:
        try:
            tags_list = json.loads(r.get("tags") or "[]")
        except Exception:
            tags_list = []
        parsed_recipes.append({**r, "tags_list": tags_list})

    template = jinja_env.get_template("recipes.html")
    html = template.render(
        recipes=parsed_recipes, category=category, tag=tag,
        page=page, total_pages=total_pages, total=total,
        categories=cats,
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
    xml += f'  <url><loc>{host}/recipes</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>\n'
    xml += f'  <url><loc>{host}/about</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.5</priority></url>\n'
    xml += f'  <url><loc>{host}/privacy</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.3</priority></url>\n'
    xml += f'  <url><loc>{host}/terms</loc><lastmod>{today}</lastmod><changefreq>monthly</changefreq><priority>0.3</priority></url>\n'
    for r in recipes:
        xml += f'  <url><loc>{host}/recipe/{r["id"]}</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>\n'
    xml += '</urlset>'
    return Response(xml, headers={"Content-Type": "application/xml; charset=utf-8"})


async def render_robots(host):
    txt = f"User-agent: *\nAllow: /\nDisallow:\n\nSitemap: {host}/sitemap.xml\n"
    return Response(txt, headers={"Content-Type": "text/plain; charset=utf-8"})


async def render_ads_txt(env):
    pub_id = getattr(env, 'ADSENSE_PUBLISHER_ID', '') if env else os.environ.get('ADSENSE_PUBLISHER_ID', '')
    if pub_id:
        pub_id = pub_id.replace("ca-", "", 1)
    else:
        pub_id = "pub-XXXXXXXXXXXXXXXX"
    txt = f"google.com, {pub_id}, DIRECT, f08c47fec0942fa0\n"
    return Response(txt, headers={"Content-Type": "text/plain; charset=utf-8"})


async def render_static_page(template_name, env):
    tmpl = jinja_env.get_template(template_name)
    html = tmpl.render()
    return Response(html, headers={"Content-Type": "text/html; charset=utf-8"})


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
        elif path == "/recipes":
            return await render_recipes(url, env)
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
        elif path == "/ads.txt":
            return await render_ads_txt(env)
        elif path == "/privacy":
            return await render_static_page("privacy.html", env)
        elif path == "/terms":
            return await render_static_page("terms.html", env)
        elif path == "/about":
            return await render_static_page("about.html", env)
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
