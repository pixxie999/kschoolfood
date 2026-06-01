import os
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
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

async def render_index(url, env):
    query = parse_qs(url.query)
    date = query.get("date", [None])[0] if "date" in query else None
    
    if not date:
        date = get_korean_today()
    
    try:
        parsed_date = datetime.strptime(date, "%Y%m%d")
        formatted_date = parsed_date.strftime("%Y-%m-%d (%A)")
    except ValueError:
        formatted_date = date

    db = get_db_adapter(env)
    meal = await db.fetch_one("SELECT * FROM meal_trays WHERE date = ?", (date,))
    
    if not meal:
        neis_meal = await fetch_meal_from_neis(date, env=env)
        if neis_meal:
            await db.execute(
                """
                INSERT INTO meal_trays (date, rice, soup, side1, side2, side3, calories)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    neis_meal["date"], neis_meal["rice"], neis_meal["soup"],
                    neis_meal["side1"], neis_meal["side2"], neis_meal["side3"],
                    neis_meal["calories"]
                )
            )
            meal = await db.fetch_one("SELECT * FROM meal_trays WHERE date = ?", (date,))
    
    if not meal:
        meal = {
            "date": date, "rice": "No School Lunch", "soup": "Weekend or Public Holiday",
            "side1": "Enjoy your break!", "side2": "No Menu Available",
            "side3": "See you on weekdays", "calories": "0 Kcal"
        }

    dishes = [
        {"role": "Main Rice", "korean": meal.get("rice"), "key": "rice"},
        {"role": "Soup/Stew", "korean": meal.get("soup"), "key": "soup"},
        {"role": "Banchan (Side 1)", "korean": meal.get("side1"), "key": "side1"},
        {"role": "Banchan (Side 2)", "korean": meal.get("side2"), "key": "side2"},
        {"role": "Banchan (Side 3)", "korean": meal.get("side3"), "key": "side3"},
    ]

    translated_dishes = []
    for dish in dishes:
        ko_name = dish["korean"]
        en_recipe = None
        if ko_name and ko_name.strip() not in ["", "No School Lunch", "Weekend or Public Holiday", "Enjoy your break!", "No Menu Available", "See you on weekdays"]:
            en_recipe = await translate_and_localize_recipe(ko_name, db, env=env)
        
        translated_dishes.append({
            "role": dish["role"],
            "korean_name": ko_name,
            "key": dish["key"],
            "english_name": en_recipe.get("english_name") if en_recipe else ko_name,
            "recipe_id": en_recipe.get("id") if en_recipe else None,
            "has_recipe": en_recipe is not None
        })

    tray_affiliate_url = await get_tray_affiliate_link(db)

    try:
        current_dt = datetime.strptime(date, "%Y%m%d")
        prev_date = (current_dt - timedelta(days=1)).strftime("%Y%m%d")
        next_date = (current_dt + timedelta(days=1)).strftime("%Y%m%d")
    except ValueError:
        prev_date = date
        next_date = date

    template = jinja_env.get_template("index.html")
    html = template.render(
        date=date,
        formatted_date=formatted_date,
        meal=meal,
        dishes=translated_dishes,
        tray_affiliate_url=tray_affiliate_url,
        prev_date=prev_date,
        next_date=next_date
    )
    return Response(html, headers={"Content-Type": "text/html; charset=utf-8"})

async def render_recipe(recipe_id, env):
    db = get_db_adapter(env)
    recipe_row = await db.fetch_one("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    if not recipe_row:
        return Response("Recipe not found", status=404)

    ingredients = json.loads(recipe_row["english_ingredients"]) if recipe_row["english_ingredients"] else []
    substitutes = json.loads(recipe_row["local_substitutes"]) if recipe_row["local_substitutes"] else []
    instructions = json.loads(recipe_row["instructions"]) if recipe_row["instructions"] else []
    nutrition = json.loads(recipe_row["nutrition_info"]) if recipe_row["nutrition_info"] else {}

    matched_ingredients = await match_affiliate_links(ingredients, db)
    tray_affiliate_url = await get_tray_affiliate_link(db)

    ld_json = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe_row["english_name"],
        "image": "https://images.unsplash.com/photo-1541832676-9b763b0239ab?q=80&w=600",
        "description": recipe_row["seo_description"],
        "recipeIngredient": [f"{item['name']} ({item['amount']})" for item in ingredients],
        "recipeInstructions": [{"@type": "HowToStep", "text": step} for step in instructions],
        "nutrition": {
            "@type": "NutritionInformation",
            "calories": nutrition.get("calories", "N/A"),
            "carbohydrateContent": nutrition.get("carbs", "N/A"),
            "proteinContent": nutrition.get("protein", "N/A"),
            "fatContent": nutrition.get("fat", "N/A")
        }
    }
    
    template = jinja_env.get_template("recipe.html")
    html = template.render(
        recipe=recipe_row,
        ingredients=matched_ingredients,
        substitutes=substitutes,
        instructions=instructions,
        nutrition=nutrition,
        tray_affiliate_url=tray_affiliate_url,
        ld_json=json.dumps(ld_json)
    )
    return Response(html, headers={"Content-Type": "text/html; charset=utf-8"})

async def render_sitemap(host, env):
    db = get_db_adapter(env)
    recipes = await db.fetch_all("SELECT id FROM recipes")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml_content += f'  <url>\n    <loc>{host}/</loc>\n    <lastmod>{today}</lastmod>\n    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>\n'
    for r in recipes:
        xml_content += f'  <url>\n    <loc>{host}/recipe/{r["id"]}</loc>\n    <lastmod>{today}</lastmod>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n'
    xml_content += '</urlset>'

    return Response(xml_content, headers={"Content-Type": "application/xml; charset=utf-8"})


async def render_robots(host):
    txt = f"""User-agent: *
Allow: /
Disallow:

Sitemap: {host}/sitemap.xml
"""
    return Response(txt, headers={"Content-Type": "text/plain; charset=utf-8"})

class Default(WorkerEntrypoint):
    async def on_fetch(self, request):
        env = getattr(self, 'env', None)
        url = urlparse(request.url)
        host = f"{url.scheme}://{url.netloc}"

        path = url.path
        if path == "/" or path == "":
            return await render_index(url, env)
        elif path.startswith("/recipe/"):
            try:
                recipe_id = int(path.split("/")[-1])
                return await render_recipe(recipe_id, env)
            except ValueError:
                return Response("Invalid recipe ID", status=400)
        elif path == "/sitemap.xml":
            return await render_sitemap(host, env)
        elif path == "/robots.txt":
            return await render_robots(host)
        else:
            return Response("Not Found", status=404)
