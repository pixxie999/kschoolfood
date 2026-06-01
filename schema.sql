-- MealTray 테이블 정의
CREATE TABLE IF NOT EXISTS meal_trays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT UNIQUE NOT NULL,
    rice TEXT,
    soup TEXT,
    side1 TEXT,
    side2 TEXT,
    side3 TEXT,
    calories TEXT
);

-- Recipe 테이블 정의
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    korean_name TEXT UNIQUE NOT NULL,
    english_name TEXT NOT NULL,
    english_ingredients TEXT,
    local_substitutes TEXT,
    instructions TEXT,
    seo_description TEXT,
    nutrition_info TEXT,
    image_url TEXT DEFAULT ''
);

-- AffiliateMapping 테이블 정의
CREATE TABLE IF NOT EXISTS affiliate_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL,
    product_identifier TEXT NOT NULL
);

-- 초기 제휴 마케팅 매핑 데이터 시드
INSERT OR IGNORE INTO affiliate_mappings (keyword, platform, product_identifier) VALUES ('Gochujang', 'amazon', 'B00P49Y85U');
INSERT OR IGNORE INTO affiliate_mappings (keyword, platform, product_identifier) VALUES ('Toasted Sesame Oil', 'iherb', 'kdy-pure-sesame-oil');
INSERT OR IGNORE INTO affiliate_mappings (keyword, platform, product_identifier) VALUES ('Stainless Steel Tray', 'amazon', 'B08638C4M8');
INSERT OR IGNORE INTO affiliate_mappings (keyword, platform, product_identifier) VALUES ('Doenjang', 'amazon', 'B005G852J4');
INSERT OR IGNORE INTO affiliate_mappings (keyword, platform, product_identifier) VALUES ('Gochugaru', 'amazon', 'B005G87UVS');
INSERT OR IGNORE INTO affiliate_mappings (keyword, platform, product_identifier) VALUES ('Kimchi', 'amazon', 'B074GD56C6');
INSERT OR IGNORE INTO affiliate_mappings (keyword, platform, product_identifier) VALUES ('Gim', 'iherb', 'gimMe-Organic-Roasted-Seaweed');
INSERT OR IGNORE INTO affiliate_mappings (keyword, platform, product_identifier) VALUES ('Ramen', 'amazon', 'B00778B90S');
