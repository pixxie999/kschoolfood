import logging
from typing import List, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)

def generate_affiliate_url(platform: str, identifier: str, amazon_tag: str = None, iherb_code: str = None) -> str:
    """
    플랫폼에 따라 제휴 트래킹 파라미터가 포함된 URL을 생성합니다.
    """
    amz_tag = amazon_tag or settings.AMAZON_TAG
    ihb_code = iherb_code or settings.IHERB_CODE

    platform_lower = platform.lower()
    if platform_lower == "amazon":
        return f"https://www.amazon.com/dp/{identifier}/?tag={amz_tag}"
    elif platform_lower == "iherb":
        return f"https://www.iherb.com/c/{identifier}?rcode={ihb_code}"
    else:
        # 매핑 오류 시 기본 검색 경로 제공
        return f"https://www.amazon.com/s?k={identifier}&tag={amz_tag}"

async def match_affiliate_links(
    ingredients: List[Dict[str, Any]], 
    db_adapter, 
    amazon_tag: str = None, 
    iherb_code: str = None
) -> List[Dict[str, Any]]:
    """
    레시피 재료 목록을 입력받아 DB의 affiliate_mappings 테이블과 키워드 매칭을 수행한 뒤,
    매칭된 제휴 마케팅 링크를 추가하여 반환합니다.
    """
    if not ingredients:
        return []

    try:
        # DB의 모든 제휴 매핑 키워드를 로드
        mappings = await db_adapter.fetch_all("SELECT keyword, platform, product_identifier FROM affiliate_mappings")
    except Exception as e:
        logger.error(f"제휴 매핑 정보를 불러오는 중 오류 발생: {e}")
        mappings = []

    matched_ingredients = []
    
    for item in ingredients:
        name = item.get("name", "")
        amount = item.get("amount", "")
        matched_link = None
        platform = None

        # 대소문자 무관하게 부분 일치(substring)하는 키워드 탐색
        for mapping in mappings:
            keyword = mapping["keyword"]
            if keyword.lower() in name.lower():
                platform = mapping["platform"]
                matched_link = generate_affiliate_url(
                    platform=platform,
                    identifier=mapping["product_identifier"],
                    amazon_tag=amazon_tag,
                    iherb_code=iherb_code
                )
                logger.info(f"제휴 키워드 매칭 성공: {keyword} in {name} -> {platform}")
                break # 첫 번째 매칭 키워드 발견 시 탐색 중단

        matched_ingredients.append({
            "name": name,
            "amount": amount,
            "affiliate_url": matched_link,
            "platform": platform
        })

    return matched_ingredients

async def get_tray_affiliate_link(db_adapter, amazon_tag: str = None) -> Optional[str]:
    """
    5칸 식판(Stainless Steel Tray) 자체에 대한 Amazon 제휴 링크를 생성하여 반환합니다.
    """
    try:
        mapping = await db_adapter.fetch_one(
            "SELECT platform, product_identifier FROM affiliate_mappings WHERE keyword = ?", 
            ("Stainless Steel Tray",)
        )
        if mapping:
            return generate_affiliate_url(
                platform=mapping["platform"],
                identifier=mapping["product_identifier"],
                amazon_tag=amazon_tag
            )
    except Exception as e:
        logger.error(f"식판 제휴 링크 조회 실패: {e}")
    
    # DB 조회 실패 시 Fallback 링크 생성
    return generate_affiliate_url(
        platform="amazon",
        identifier="B08638C4M8",
        amazon_tag=amazon_tag
    )
