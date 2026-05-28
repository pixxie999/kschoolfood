import sys
import os

# 프로젝트 루트 디렉터리를 sys.path에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, engine, SessionLocal
from app.models import AffiliateMapping

def init_db():
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully.")

    # 초기 제휴 마케팅 데이터 삽입
    db = SessionLocal()
    try:
        # 이미 데이터가 있는지 검사
        if db.query(AffiliateMapping).count() == 0:
            print("Inserting default affiliate mappings...")
            default_mappings = [
                AffiliateMapping(keyword="Gochujang", platform="amazon", product_identifier="B00P49Y85U"),
                AffiliateMapping(keyword="Toasted Sesame Oil", platform="iherb", product_identifier="kdy-pure-sesame-oil"),
                AffiliateMapping(keyword="Stainless Steel Tray", platform="amazon", product_identifier="B08638C4M8"),
                AffiliateMapping(keyword="Doenjang", platform="amazon", product_identifier="B005G852J4"),
                AffiliateMapping(keyword="Gochugaru", platform="amazon", product_identifier="B005G87UVS"),
                AffiliateMapping(keyword="Kimchi", platform="amazon", product_identifier="B074GD56C6"),
                AffiliateMapping(keyword="Gim", platform="iherb", product_identifier="gimMe-Organic-Roasted-Seaweed"),
                AffiliateMapping(keyword="Ramen", platform="amazon", product_identifier="B00778B90S"),
            ]
            db.add_all(default_mappings)
            db.commit()
            print("Default mappings inserted successfully.")
        else:
            print("Affiliate mappings already exist. Skipping seed data.")
    except Exception as e:
        db.rollback()
        print(f"Error initializing default mappings: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
