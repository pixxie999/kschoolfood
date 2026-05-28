import os
import json
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

# Jinja2 템플릿 경로 설정 (상대 경로 및 절대 경로 호환성 확보)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# 정적 파일 서빙 설정 (로컬 개발용)
# Cloudflare Workers 환경에서는 wrangler가 static assets를 서빙할 수 있으나,
# 로컬 개발 및 예외 방지를 위해 마운트 설정
if os.path.exists(os.path.join(BASE_DIR, "static")):
    app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

def get_korean_today() -> str:
    """한국 시간(KST) 기준 오늘 날짜(YYYYMMDD)를 반환합니다."""
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y%m%d")

@app.get("/", response_class=HTMLResponse)
async def main_tray_view(request: Request, date: Optional[str] = None):
    # 1. 날짜 설정 (기본값: 오늘)
    if not date:
        date = get_korean_today()
    
    # YYYY-MM-DD 형식 포맷팅용
    try:
        parsed_date = datetime.strptime(date, "%Y%m%d")
        formatted_date = parsed_date.strftime("%Y-%m-%d (%A)")
    except ValueError:
        formatted_date = date

    db = get_db_adapter(request)

    # 2. DB 캐시에서 식단 정보 조회
    meal = await db.fetch_one("SELECT * FROM meal_trays WHERE date = ?", (date,))
    
    if not meal:
        # 3. 캐시 미스 시 NEIS API 호출
        logger_name = "app.main"
        import logging
        logger = logging.getLogger(logger_name)
        logger.info(f"식단 캐시 미스. NEIS API 호출 날짜: {date}")
        
        neis_meal = await fetch_meal_from_neis(date)
        if neis_meal:
            # DB 캐시 저장
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
            # 저장 후 재조회
            meal = await db.fetch_one("SELECT * FROM meal_trays WHERE date = ?", (date,))
    
    # 4. 데이터가 아예 없는 경우 (주말/방학/장애) Fallback 설정
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

    # 5. 각 요리 한글명을 영어 레시피로 번역 & 로컬라이징 연동 (비어있거나 기본값인 경우 제외)
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
        # 주말 멘트나 빈 값은 번역하지 않음
        if ko_name and ko_name.strip() not in ["", "No School Lunch", "Weekend or Public Holiday", "Enjoy your break!", "No Menu Available", "See you on weekdays"]:
            # 단일 메뉴 내에 콤마가 여러 개 포함될 수 있으므로, 대표 키워드 매칭을 위해 우선 첫번째 것 위주로 대표번역 하거나 그대로 넘김
            en_recipe = await translate_and_localize_recipe(ko_name, db)
        
        translated_dishes.append({
            "role": dish["role"],
            "korean_name": ko_name,
            "key": dish["key"],
            "english_name": en_recipe.get("english_name") if en_recipe else ko_name,
            "recipe_id": en_recipe.get("id") if en_recipe else None,
            "has_recipe": en_recipe is not None
        })

    # 식판 전용 제휴 마케팅 링크 가져오기 (5-Compartment Tray)
    tray_affiliate_url = await get_tray_affiliate_link(db)

    # 6. 이전 날짜/다음 날짜 계산
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

    # 1. DB에서 레시피 데이터 조회
    recipe_row = await db.fetch_one("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    if not recipe_row:
        # 에러 또는 없는 레시피 페이지 처리
        return Response("Recipe not found", status_code=404)

    # 2. JSON 파싱
    ingredients = json.loads(recipe_row["english_ingredients"]) if recipe_row["english_ingredients"] else []
    substitutes = json.loads(recipe_row["local_substitutes"]) if recipe_row["local_substitutes"] else []
    instructions = json.loads(recipe_row["instructions"]) if recipe_row["instructions"] else []
    nutrition = json.loads(recipe_row["nutrition_info"]) if recipe_row["nutrition_info"] else {}

    # 3. 제휴 마케팅 자동 매핑 링크 주입
    matched_ingredients = await match_affiliate_links(ingredients, db)
    tray_affiliate_url = await get_tray_affiliate_link(db)

    # 4. SEO를 위한 동적 ld+json 구조화 데이터 빌드
    ld_json = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe_row["english_name"],
        "image": "https://images.unsplash.com/photo-1541832676-9b763b0239ab?q=80&w=600", # 기본 한식 일러스트 이미지 플레이스홀더
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
    
    # 호스트 네임 구하기 (배포 환경 및 로컬 환경 자동 매핑)
    host = request.base_url
    
    # 1. DB의 모든 레시피 조회
    recipes = await db.fetch_all("SELECT id FROM recipes")
    
    # 2. XML 동적 빌드
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    
    # 메인 페이지 추가
    xml_content += f'  <url>\n    <loc>{host}</loc>\n    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>\n'
    
    # 레시피 상세 페이지 추가
    for r in recipes:
        xml_content += f'  <url>\n    <loc>{host}recipe/{r["id"]}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n'
        
    xml_content += '</urlset>'
    
    return Response(content=xml_content, media_type="application/xml")

# Cloudflare Workers 진입점 클래스 정의
if IS_WORKERS:
    class Default(WorkerEntrypoint):
        async def fetch(self, request, env=None):
            # env 객체를 ASGI scope에 삽입하여 FastAPI 내부에서 꺼내쓸 수 있도록 전달
            return await asgi.fetch(app, request, env)
