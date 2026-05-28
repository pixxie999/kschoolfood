from sqlalchemy import Column, Integer, String, Text, Date
from app.database import Base

class MealTray(Base):
    __tablename__ = "meal_trays"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String(8), unique=True, index=True, nullable=False) # YYYYMMDD 포맷
    rice = Column(String(100), nullable=True)     # 주식 (밥/죽/면)
    soup = Column(String(100), nullable=True)     # 국/찌개
    side1 = Column(String(100), nullable=True)    # 반찬 1
    side2 = Column(String(100), nullable=True)    # 반찬 2
    side3 = Column(String(100), nullable=True)    # 반찬 3
    calories = Column(String(50), nullable=True)  # 칼로리 정보 (예: "820 kcal")

class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(Integer, primary_key=True, index=True)
    korean_name = Column(String(100), unique=True, index=True, nullable=False) # 한국어 메뉴명 (예: "제육볶음")
    english_name = Column(String(150), nullable=False) # 번역된 영어 메뉴명
    english_ingredients = Column(Text, nullable=True)  # JSON String: 영어 재료 목록 및 용량
    local_substitutes = Column(Text, nullable=True)    # JSON String: 대체 가능한 현지 식재료 정보
    instructions = Column(Text, nullable=True)         # JSON String 또는 Text: 조리 단계
    seo_description = Column(Text, nullable=True)      # SNS 및 검색엔진용 메타 설명
    nutrition_info = Column(Text, nullable=True)       # JSON String: 상세 영양 정보

class AffiliateMapping(Base):
    __tablename__ = "affiliate_mappings"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(100), unique=True, index=True, nullable=False) # 영어 키워드 (예: Gochujang, Toasted Sesame Oil)
    platform = Column(String(50), nullable=False)                          # amazon 또는 iherb
    product_identifier = Column(String(100), nullable=False)               # ASIN 또는 카테고리/제품 ID
