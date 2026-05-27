# K-School Food WebApp 개발 워크플로우 (Workflow)

본 문서는 `coding_agent_spec.md` 및 `coding_agent_skills.md`에 명시된 요구사항과 유저의 기술 스택 답변(FastAPI, SQLite, NEIS OpenAPI, Gemini/OpenAI 선택적 연동, .env 관리)을 바탕으로 작성된 **단계별 개발 워크플로우**입니다.

---

## 📂 프로젝트 기본 구조 (Proposed Folder Structure)
개발을 시작하기 전에 구성할 기본적인 디렉토리 및 파일 구조입니다.

```text
kschoolfood/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 애플리케이션 진입점 및 라우팅
│   ├── config.py            # .env 환경 변수 관리 및 앱 설정
│   ├── database.py          # SQLAlchemy SQLite 연결 및 세션 설정
│   ├── models.py            # SQLAlchemy DB 모델 정의 (Menu, Recipe, Affiliate 등)
│   ├── schemas.py           # Pydantic v2 데이터 검증 스키마 (LLM 출력 및 API용)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── neis_api.py      # NEIS Open API 연동 모듈 (실시간 급식 데이터 조회)
│   │   ├── llm_service.py   # LLM 통합 인터페이스 (Gemini / OpenAI 선택 연동)
│   │   └── affiliate.py     # 제휴 마케팅 링크 자동 매핑 엔진
│   ├── templates/           # Jinja2 HTML 템플릿 (SEO 메타태그, Tailwind CSS, 5칸 식판 UI)
│   │   ├── base.html
│   │   ├── index.html       # 메인 식판 뷰
│   │   └── recipe.html      # 레시피 상세 및 제휴 CTA 영역
│   └── static/              # CSS, JS, 이미지 등 정적 파일
├── .env.template            # 환경 변수 템플릿 파일
├── coding_agent_skills.md   # 기술 스택 요구사항 사양서
├── coding_agent_spec.md     # K-School Food 핵심 사양서
└── workflow.md              # (본 파일) 개발 단계 정리
```

---

## 🛠️ 단계별 개발 워크플로우 (Development Steps)

### **[1단계] 개발 환경 구성 및 기본 구조 세팅**
1. **가상환경(venv) 생성 및 활성화**
   - Python 가상환경을 구축하여 의존성을 독립적으로 관리합니다.
2. **필수 패키지 설치 (`requirements.txt` 작성)**
   - 백엔드: `fastapi`, `uvicorn`, `jinja2`
   - 데이터베이스: `sqlalchemy`
   - API 요청 및 연동: `httpx`, `requests`
   - LLM SDK: `google-generativeai`, `openai`
   - 설정 관리: `python-dotenv`, `pydantic`
3. **환경 변수 파일 (`.env`) 설정**
   - NEIS Open API Key, OpenAI API Key, Gemini API Key, Amazon Associates Tag, iHerb Rewards Code 등을 설정할 수 있는 템플릿 작성 및 적용.

---

### **[2단계] 데이터베이스(SQLite) 및 SQLAlchemy 모델링**
1. **데이터베이스 연결 세팅 (`database.py`)**
   - SQLite 파일 데이터베이스(`kschoolfood.db`) 연결 및 SQLAlchemy 엔진/세션 팩토리 구성.
2. **테이블 모델 정의 (`models.py`)**
   - `MealTray` / `Menu`: 특정 날짜의 5칸 급식 식단(밥/죽/면, 국, 반찬 3종)을 저장하는 테이블.
   - `Recipe`: 각 메뉴별 영문 번역명, 조리 방법, 대체 식재료, 영양 정보, 생성된 SEO 텍스트 저장 테이블.
   - `AffiliateMapping`: 한글 식재료명/키워드와 제휴 쇼핑몰 링크(Amazon ASIN, iHerb Category 등) 간의 매핑 정보 테이블.
3. **테이블 초기 생성 스크립트 작성**

---

### **[3단계] NEIS Open API 실시간 연동 모듈 개발**
1. **교육청 코드 및 학교 코드 탐색/설정**
   - NEIS OpenAPI를 정상 조회하기 위해 필요한 행정표준코드(시도교육청코드, 학교고유코드)를 정의하고 환경변수 또는 설정 파일에 등록합니다.
2. **실시간 급식 식단 호출 모듈 구현 (`neis_api.py`)**
   - `httpx`를 사용해 NEIS 급식 정보 오픈 API에서 특정 날짜 범위의 식단 데이터를 조회합니다.
   - 받아온 원시 데이터(메뉴명 리스트, 알레르기 유발 정보 등)를 파싱하여 정제하는 파이프라인을 작성합니다.

---

### **[4단계] AI 기반 번역 및 SEO 로컬라이제이션 파이프라인 개발**
1. **LLM 서비스 추상 인터페이스 정의 (`llm_service.py`)**
   - 사용자의 설정(.env)에 따라 **Gemini** 혹은 **OpenAI** 중 활성화된 엔진을 유연하게 선택해 동작하도록 설계합니다.
2. **Pydantic을 활용한 정형 출력(Structured Output) 설정**
   - LLM 응답 시 JSON이 깨지지 않도록 Pydantic v2 스키마(`schemas.py`)를 통해 응답 형식을 강제합니다. (번역명, 대체 재료 목록, SEO용 요약 설명, 칼로리 정보 등)
3. **급식 메뉴 번역 및 로컬라이징 스크립트 구현**
   - 정제된 한국어 메뉴명을 입력받아 글로벌 사용자가 이해할 수 있는 영문명으로 번역합니다.
   - 서양 등 현지 마트에서 구하기 힘든 한국 식재료(예: 고추장, 참기름, 깻잎 등)에 대한 현지 대체 식재료 추천 기능을 구현합니다.
   - Pinterest, Instagram 등 SNS와 검색엔진 노출을 위한 SEO 메타 설명(Description)을 자동으로 생성합니다.

---

### **[5단계] 제휴 마케팅 자동 매핑 엔진 개발**
1. **제휴사 링크 생성 유틸리티 구축 (`affiliate.py`)**
   - 사양서에 명시된 규칙대로 Amazon 및 iHerb 주소를 동적으로 생성하는 함수를 작성합니다.
     - Amazon: `https://www.amazon.com/dp/{PRODUCT_ASIN}/?tag={YOUR_AMAZON_TAG}`
     - iHerb: `https://www.iherb.com/c/{CATEGORY}?rcode={YOUR_IHERB_CODE}`
2. **키워드 매핑 딕셔너리/데이터 구축**
   - `Gochujang` -> Chung Jung One Gochujang (Amazon ASIN)
   - `Toasted Sesame Oil` -> iHerb Category / ID
   - `Stainless Steel Tray` -> Amazon ASIN for 5-compartment tray
   - 위 항목들을 저장하고 있는 매핑 테이블/딕셔너리를 구현합니다.
3. **콘텐츠 내 동적 CTA 삽입 로직**
   - 번역된 레시피 및 재료 리스트를 파싱하여 매핑된 키워드가 존재할 경우, 자동으로 구매 유도용 제휴 링크가 삽입되도록 설계합니다.

---

### **[6단계] FastAPI 라우터 및 SEO 시스템 구현**
1. **핵심 라우터 구성 (`main.py`)**
   - `/`: 메인 식판 뷰 (오늘의 급식 식판 렌더링)
   - `/recipe/{recipe_id}`: 개별 메뉴 레시피 정보 및 제휴 마케팅 링크 표시 페이지
   - `/sitemap.xml`: 신규 레시피 등록 시 검색엔진 수집이 원활하도록 동적 사이트맵 제공
2. **SEO 구조화 데이터 (`ld+json`) 자동 주입**
   - Recipe Schema 마크업을 동적으로 생성하여 웹페이지에 삽입함으로써 구글과 핀터레스트 등 검색 엔진에서 리치 결과(Rich Snippet)로 표시되도록 구현합니다.

---

### **[7단계] 반응형 프론트엔드 및 5칸 식판 UI 구현**
1. **Jinja2 템플릿 작성 (`templates/`)**
   - HTML5 기반의 시맨틱 마크업을 작성하고 Tailwind CSS CDN을 활용해 스타일링합니다.
2. **5칸 식판 UI (5-Compartment Meal Tray View)**
   - 모바일 우선 반응형으로 설계된 현대적이고 세련된 식판 레이아웃을 구축합니다.
   - 주식(1칸), 국(1칸), 반찬(3칸) 영역으로 구성되어 각 영역 클릭 시 해당 요리의 영어 번역 레시피 및 재료 대체 정보가 들어간 상세 페이지/모달로 이동하도록 개발합니다.
3. **제휴 마케팅 배너 및 CTA 스타일링**
   - 레시피 상세 영역 내에 가시성이 뛰어나면서도 UX를 해치지 않는 프리미엄 제휴 구매 링크 컴포넌트를 배치합니다.

---

### **[8단계] 통합 테스트 및 예외 처리 검증**
1. **API 호출 예외 처리 테스트**
   - NEIS Open API 장애 발생 또는 주말/공휴일 등 급식 데이터가 없는 날에 대한 Fallback 로직 검증.
   - LLM API 할당량 초과 또는 네트워크 오류 발생 시 에러 핸들링.
2. **종합 데이터 파이프라인 흐름 확인**
   - `급식 데이터 수집 -> LLM 현지화 가공 -> DB 저장 -> 제휴 링크 매핑 -> UI 노출` 과정이 원활하게 연계되는지 검증합니다.
3. **SEO 크롤러 호환성 체크**
   - 생성된 `<title>`, `<meta>`, `OpenGraph` 태그 및 `ld+json` 구조화 데이터가 구글 서치콘솔/핀터레스트 가이드라인에 부합하는지 테스트합니다.
