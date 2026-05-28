# K-School Food WebApp 개발 워크플로우 (Workflow) - Cloudflare Workers & D1 반영

본 문서는 Cloudflare Workers(Python 환경) 및 Cloudflare D1 데이터베이스를 사용하고, GitHub Actions를 통해 Cloudflare에 자동 배포하는 아키텍처에 맞게 수정된 **단계별 개발 워크플로우**입니다.

---

## 📂 프로젝트 기본 구조 (Proposed Folder Structure)

```text
kschoolfood/
├── .github/
│   └── workflows/
│       └── deploy.yml       # GitHub Actions를 통한 Cloudflare Workers 자동 배포
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 앱 정의 및 Cloudflare Workers Entrypoint (Default class)
│   ├── config.py            # 환경 변수 및 D1 바인딩 헬퍼
│   ├── db_adapter.py        # 로컬 SQLAlchemy와 Cloudflare D1을 추상화하여 중개하는 DB 어댑터
│   ├── models.py            # (로컬 테스트용) SQLAlchemy ORM 모델
│   ├── schemas.py           # Pydantic v2 데이터 검증 스키마
│   ├── services/
│   │   ├── __init__.py
│   │   ├── neis_api.py      # NEIS Open API 연동 모듈 (실시간 급식 데이터 조회)
│   │   ├── llm_service.py   # LLM 통합 인터페이스 (Gemini / OpenAI 선택 연동)
│   │   └── affiliate.py     # 제휴 마케팅 링크 자동 매핑 엔진
│   ├── templates/           # Jinja2 HTML 템플릿
│   │   ├── base.html
│   │   ├── index.html       # 메인 식판 뷰
│   │   └── recipe.html      # 레시피 상세 및 제휴 CTA 영역
│   └── static/              # CSS, JS 정적 파일
├── schema.sql               # Cloudflare D1 초기 테이블 생성용 SQL 스크립트
├── wrangler.toml            # Cloudflare Workers 및 D1 바인딩 설정 파일
├── requirements.txt         # 파이썬 의존성 패키지 목록
├── .env.template            # 로컬 개발용 환경 변수 템플릿
└── workflow.md              # (본 파일) 개발 단계 정리
```

---

## 🛠️ 단계별 개발 워크플로우 (Development Steps)

### **[1단계] 개발 환경 구성 및 기본 구조 세팅**
1. **Python 가상환경 및 패키지 설치**
   - 로컬 테스트 환경을 구축하고 `requirements.txt`에 명시된 필수 패키지 설치.
2. **Cloudflare Wrangler CLI 설치**
   - `npm install -g wrangler` 등을 통해 Wrangler 설치 및 Cloudflare 로그인 (`wrangler login`).
3. **`wrangler.toml` 구성**
   - `compatibility_flags = ["python_workers"]` 설정.
   - `[[d1_databases]]` 바인딩(`DB`) 추가.
   - 로컬 테스트를 위한 환경 변수 세팅.

---

### **[2단계] 데이터베이스(Cloudflare D1 & SQLite) 및 추상 DB 어댑터 설계**
1. **D1 스키마 정의 (`schema.sql`)**
   - `meal_trays`, `recipes`, `affiliate_mappings` 테이블을 생성하는 D1 호환 SQL 쿼리 작성.
2. **로컬 D1 데이터베이스 생성 및 초기화**
   - `wrangler d1 create kschoolfood-db` 실행 및 데이터베이스 ID 획득.
   - `wrangler d1 execute kschoolfood-db --local --file=schema.sql` 로 로컬 환경에 테이블 구축.
   - `wrangler d1 execute kschoolfood-db --remote --file=schema.sql` 로 원격(클라우드플레어) 환경에 테이블 구축.
3. **DB 어댑터 구현 (`app/db_adapter.py`)**
   - 로컬 개발 시에는 SQLAlchemy/SQLite 파일에 접근하고, Cloudflare Workers 환경에서는 `request.scope["env"].DB` 바인딩을 통해 D1 API를 사용하도록 추상화 인터페이스 구현.
4. **초기 제휴 마케팅 데이터 시딩**
   - D1 데이터베이스의 `affiliate_mappings` 테이블에 기본 매핑 데이터를 SQL 쿼리로 삽입.

---

### **[3단계] NEIS Open API 실시간 연동 모듈 개발**
1. **실시간 급식 식단 호출 모듈 구현 (`neis_api.py`)**
   - `httpx`를 사용해 NEIS 급식 정보 오픈 API에서 특정 날짜 범위의 식단 데이터를 조회합니다.
   - 받아온 원시 데이터를 파싱하여 정제(알레르기 유발 정보 제거)하는 파이프라인 작성.

---

### **[4단계] AI 기반 번역 및 SEO 로컬라이제이션 파이프라인 개발**
1. **LLM 서비스 추상 인터페이스 정의 (`llm_service.py`)**
   - `Gemini` 혹은 `OpenAI` 중 활성화된 엔진을 유연하게 선택해 동작하도록 설계. (Cloudflare Workers 환경 변수 `ACTIVE_LLM_PROVIDER` 사용)
2. **Pydantic을 활용한 정형 출력(Structured Output) 설정**
   - LLM 응답 시 JSON이 깨지지 않도록 Pydantic v2 스키마(`schemas.py`)를 통해 응답 형식을 강제.
3. **급식 메뉴 번역 및 로컬라이징 스크립트 구현**
   - 정제된 한국어 메뉴명을 입력받아 글로벌 사용자가 이해할 수 있는 영문명으로 번역하고 현지 대체 식재료 추천 기능을 구현.
   - Pinterest, Instagram 등 SNS와 검색엔진 노출을 위한 SEO 메타 설명(Description)을 자동으로 생성.

---

### **[5단계] 제휴 마케팅 자동 매핑 엔진 개발**
1. **제휴사 링크 생성 및 키워드 매핑 로직 구현 (`affiliate.py`)**
   - 번역된 레시피 내에 키워드가 매핑되면 Amazon/iHerb 제휴 CTA 버튼 또는 링크를 자동 생성해 주는 모듈 개발.

---

### **[6단계] FastAPI 라우터 및 Cloudflare Workers Entrypoint 구현**
1. **핵심 라우터 구성 (`main.py`)**
   - `/`, `/recipe/{recipe_id}`, `/sitemap.xml` 라우트 구현.
   - FastAPI 라우터 내부에서 `Request` 객체를 통해 `request.scope["env"]`에 접근하여 D1(`DB`) 인스턴스를 가져오도록 설계.
2. **Workers 진입점 구현**
   - `WorkerEntrypoint` 클래스를 상속받아 `asgi.fetch(app, request, env)`를 호출함으로써 FastAPI를 Workers ASGI와 연동.
3. **SEO 구조화 데이터 (`ld+json`) 자동 주입**
   - Recipe Schema 마크업을 동적으로 생성하여 웹페이지에 삽입.

---

### **[7단계] 반응형 프론트엔드 및 5칸 식판 UI 구현**
1. **Jinja2 템플릿 작성 (`templates/`)**
   - HTML5 기반의 시맨틱 마크업을 작성하고 Tailwind CSS CDN을 활용해 스타일링.
2. **5칸 식판 UI (5-Compartment Meal Tray View)**
   - 모바일 우선 반응형으로 설계된 현대적이고 세련된 식판 레이아웃을 구축.
   - 주식(1칸), 국(1칸), 반찬(3칸) 영역 클릭 시 영어 레시피 및 재료 대체 정보가 들어간 상세 페이지로 이동하도록 구현.

---

### **[8단계] 통합 테스트 및 예외 처리 검증**
1. **로컬 Wrangler Dev 환경 테스트**
   - `wrangler dev` 명령어를 실행하여 로컬 Workers 환경에서 전체 데이터 파이프라인 흐름(`급식 데이터 수집 -> LLM 가공 -> D1 DB 저장 -> 제휴 링크 매핑 -> UI 노출`) 확인 및 예외 케이스 검증.

---

### **[9단계] GitHub Actions를 통한 Cloudflare Workers 자동 배포 파이프라인 구축**
1. **배포 워크플로우 정의 (`.github/workflows/deploy.yml`)**
   - main 브랜치에 코드가 push될 때 Cloudflare Wrangler Action을 실행하여 Workers 애플리케이션 자동 배포.
2. **GitHub Secrets 설정 및 검증**
   - GitHub Repository Secrets에 `CLOUDFLARE_API_TOKEN` 및 `CLOUDFLARE_ACCOUNT_ID`를 등록하여 안전하게 인증 및 배포 처리.
