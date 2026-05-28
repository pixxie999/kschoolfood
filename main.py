import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Cloudflare Workers 환경에서는 workers 모듈이 런타임에 내장되어 있으므로 FFI 바인딩을 적용
try:
    from workers import WorkerEntrypoint
    import asgi
    IS_WORKERS = True
except ImportError:
    IS_WORKERS = False

from app.db_adapter import get_db_adapter
from app.services.neis_api import fetch_meal_from_neis
from app.services.llm_service import translate_and_localize_recipe
from app.services.affiliate import match_affiliate_links, get_tray_affiliate_link

app = FastAPI(title="K-School Food Global WebApp")

# Jinja2 템플릿 경로 설정 (루트 진입점 기준으로 app/templates 폴더를 바라보도록 설정)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app", "templates"))

# 정적 파일 서빙 설정 (로컬 개발용)
static_path = os.path.join(BASE_DIR, "app", "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

def get_korean_today() -> str:
    """한국 시간(KST) 기준 오늘 날짜(YYYYMMDD)를 반환합니다."""
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y%m%d")

@app.get("/", response_class=HTMLResponse)
async def main_tray_view(request: Request, date: Optional[str] = None):
    if not date:
        date = get_korean_today()
    
    try:
        parsed_date = datetime.strptime(date, "%Y%m%d")
        formatted_date = parsed_date.strftime("%Y-%m-%d (%A)")
    except ValueError:
        formatted_date = date

    db = get_db_adapter(request)

    meal = await db.fetch_one("SELECT * FROM meal_trays WHERE date = ?", (date,))
    
    if not meal:
        logger_name = "main"
        import logging
        logger = logging.getLogger(logger_name)
        logger.info(f"식단 캐시 미스. NEIS API 호출 날짜: {date}")
        
        neis_meal = await fetch_meal_from_neis(date)
        if neis_meal:
            await db.execute(
                """
                INSERT INTO meal_trays (date, rice, soup, side1, side2, side3, calories)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    neis_meal["date"],
                    neis_meal["rice"],
                    neis_meal["soup"],
                    neis_meal["side1"],
                    neis_meal["side2"],
                    neis_meal["side3"],
                    neis_meal["calories"]
                )
            )
            meal = await db.fetch_one("SELECT * FROM meal_trays WHERE date = ?", (date,))
    
    if not meal:
        meal = {
            "date": date,
            "rice": "No School Lunch",
            "soup": "Weekend or Public Holiday",
            "side1": "Enjoy your break!",
            "side2": "No Menu Available",
            "side3": "See you on weekdays",
            "calories": "0 Kcal"
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
            en_recipe = await translate_and_localize_recipe(ko_name, db)
        
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

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "date": date,
            "formatted_date": formatted_date,
            "meal": meal,
            "dishes": translated_dishes,
            "tray_affiliate_url": tray_affiliate_url,
            "prev_date": prev_date,
            "next_date": next_date
        }
    )

@app.get("/recipe/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail_view(request: Request, recipe_id: int):
    db = get_db_adapter(request)

    recipe_row = await db.fetch_one("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    if not recipe_row:
        return Response("Recipe not found", status_code=404)

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
    ld_json_str = json.dumps(ld_json)

    return templates.TemplateResponse(
        request=request,
        name="recipe.html",
        context={
            "recipe": recipe_row,
            "ingredients": matched_ingredients,
            "substitutes": substitutes,
            "instructions": instructions,
            "nutrition": nutrition,
            "tray_affiliate_url": tray_affiliate_url,
            "ld_json": ld_json_str
        }
    )

@app.get("/sitemap.xml")
async def dynamic_sitemap(request: Request):
    db = get_db_adapter(request)
    host = request.base_url
    recipes = await db.fetch_all("SELECT id FROM recipes")
    
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml_content += f'  <url>\n    <loc>{host}</loc>\n    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>\n'
    for r in recipes:
        xml_content += f'  <url>\n    <loc>{host}recipe/{r["id"]}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n'
    xml_content += '</urlset>'
    
    return Response(content=xml_content, media_type="application/xml")

if IS_WORKERS:
    class Default(WorkerEntrypoint):
        async def fetch(self, request, env=None):
            return await asgi.fetch(app, request, env)
