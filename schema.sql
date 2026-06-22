-- MealTray 테이블 정의
CREATE TABLE IF NOT EXISTS meal_trays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT UNIQUE NOT NULL,
    rice TEXT,
    soup TEXT,
    side1 TEXT,
    side2 TEXT,
    side3 TEXT,
    calories TEXT,
    allergies TEXT DEFAULT '{}'
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

-- Reviews 테이블 정의
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment TEXT,
    country TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
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

-- ─────────────────────────────────────────────────────────────────────────
-- 블로그 자동발행/초안 서브시스템 (pipeline/CLAUDE.md 참조)
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS topics (
    id            INTEGER PRIMARY KEY,
    title_ko      TEXT NOT NULL,
    category      TEXT NOT NULL,
    publish_mode  TEXT NOT NULL,
    neis_query    TEXT,
    state         TEXT NOT NULL DEFAULT 'pending',
    priority      INTEGER NOT NULL DEFAULT 100,
    last_used_at  TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id      INTEGER,
    source        TEXT NOT NULL DEFAULT 'ai',
    publish_mode  TEXT NOT NULL DEFAULT 'review',
    status        TEXT NOT NULL DEFAULT 'draft',
    translate_en  INTEGER NOT NULL DEFAULT 1,
    author        TEXT,
    slug          TEXT UNIQUE NOT NULL,
    title_ko      TEXT,
    body_ko       TEXT,
    title_en      TEXT NOT NULL,
    body_en       TEXT NOT NULL,
    meta_en       TEXT,
    hero_image    TEXT,
    data_snapshot TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    published_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source);
CREATE INDEX IF NOT EXISTS idx_posts_mode   ON posts(publish_mode);

-- 50개 초기 주제 시드 (pipeline/CLAUDE.md §10)
INSERT OR IGNORE INTO topics (id, title_ko, category, publish_mode, priority) VALUES
 (1,  '전국 학교 급식 최다 메뉴 TOP50',          'data',      'auto',   10),
 (2,  '지역별 인기 급식 메뉴 비교',                'data',      'auto',   20),
 (3,  '초·중·고 급식 메뉴의 차이',                 'data',      'auto',   30),
 (4,  '월별 최다 등장 급식 메뉴',                   'data',      'auto',   40),
 (5,  '김치가 급식에 등장하는 비율',                'data',      'auto',   50),
 (6,  '학교급별 평균 칼로리',                       'data',      'auto',   60),
 (7,  '국·찌개 인기 랭킹',                          'data',      'auto',   70),
 (8,  '급식 후식 랭킹',                              'data',      'auto',   80),
 (9,  '알레르기 유발 식재료 최다 메뉴 TOP20',       'data',      'auto',   90),
 (10, '요일별 급식 패턴',                            'data',      'auto',  100),
 (49, '오늘 전국에서 가장 많이 나온 급식 메뉴',      'data',      'auto',  110),
 (11, '한국 학교 급식이란? — 가이드',              'culture',   'review',200),
 (12, '한국 전통 음식 10가지',                       'culture',   'review',210),
 (13, '한국·일본·미국 급식 비교',                    'culture',   'review',220),
 (14, '제육볶음 해부',                                'culture',   'review',230),
 (15, '떡볶이 급식 등장 빈도',                        'culture',   'review',240),
 (16, '한국의 국물 문화',                              'culture',   'review',250),
 (17, '김치의 종류',                                   'culture',   'review',260),
 (18, '학생 최애 급식 메뉴',                           'culture',   'review',270),
 (19, '급식 우유 이야기',                              'culture',   'review',280),
 (20, '밥+국+반찬 구조 이해하기',                      'culture',   'review',290),
 (48, '급식 메뉴로 배우는 한국어 50',                  'culture',   'review',300),
 (21, '봄 급식 특집',                                  'season',    'auto',  400),
 (22, '여름 급식 특집',                                'season',    'auto',  410),
 (23, '가을 급식 특집',                                'season',    'auto',  420),
 (24, '겨울 급식 특집',                                'season',    'auto',  430),
 (25, '삼복 삼계탕 이야기',                            'season',    'review',440),
 (26, '동지 팥죽 이야기',                              'season',    'review',450),
 (27, '연말 특식 모음',                                'season',    'review',460),
 (28, '졸업·입학식 급식',                              'season',    'review',470),
 (29, '정월대보름 급식',                               'season',    'review',480),
 (30, '어린이날 급식',                                 'season',    'review',490),
 (31, '급식 평균 영양 분석',                           'nutrition', 'review',500),
 (32, '건강한 급식 조합 TOP10',                        'nutrition', 'review',510),
 (33, '채식 친화 급식 메뉴',                            'nutrition', 'review',520),
 (34, '급식의 나트륨 분석',                            'nutrition', 'review',530),
 (35, '고단백 급식 메뉴',                              'nutrition', 'review',540),
 (36, '글루텐프리 급식 옵션',                           'nutrition', 'review',550),
 (37, '우유 알레르기와 급식',                          'allergy',   'review',600),
 (38, '견과류 알레르기와 급식',                         'allergy',   'review',610),
 (39, '한국 표시 알레르기 18종 해설',                   'allergy',   'review',620),
 (40, '달걀 알레르기와 급식',                          'allergy',   'review',630),
 (41, '집에서 만드는 제육볶음 레시피',                  'recipe',    'review',700),
 (42, '집에서 만드는 카레라이스 레시피',                 'recipe',    'review',710),
 (43, '집에서 만드는 떡볶이 레시피',                    'recipe',    'review',720),
 (44, '집에서 만드는 미역국 레시피',                    'recipe',    'review',730),
 (45, '한국식 도시락 만들기',                           'recipe',    'review',740),
 (46, '2026년 급식 트렌드',                            'trend',     'review',800),
 (47, 'K-콘텐츠 속 학교 급식',                          'trend',     'review',810),
 (50, '우리 학교 급식 찾는 법',                         'trend',     'review',820);
