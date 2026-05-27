# Required Skill Matrix & Tech Stack Checklist for Coding Agent

To successfully build and deploy the K-School Food web application, you must master and apply the following technical skill sets:

## 1. Backend Development & Data Pipelines (Python)
- **Frameworks:** FastAPI or Flask for routing, middleware management, and JSON serialization.
- **Web Scraping & APIs:** `requests`, `httpx`, or `BeautifulSoup` to pull/mock external nutrition data (NEIS Open API structures).
- **ORM / Database:** SQLAlchemy with SQLite for managing relations between `MealSets`, `Recipes`, `Ingredients`, and `AffiliateLinks`.

## 2. Generative AI Integration (Prompt Engineering & Tool Calling)
- **API Integration:** Integration of `google-generativeai` or `openai` Python SDKs.
- **Structured Outputs:** Utilizing Pydantic v2 to enforce JSON schemas from the LLM outputs (ensuring fields like `translated_name`, `local_substitute`, `nutrition_score` return uncorrupted data).

## 3. Programmatic Monetization & URL Routing
- **String Manipulation & URL Building:** Dynamic URL encoding for affiliate tracking parameters (`tag`, `rcode`).
- **Affiliate API Handling:** Optional integration of Amazon Product Advertising API to check real-time stock/pricing for key ASINs.

## 4. Frontend & SEO Optimization
- **Tailwind CSS:** Micro-layouts mimicking mobile-first application frames (Visual School Tray).
- **SEO Automation:** Generating structured schema data (`ld+json` Recipe schema) dynamically on the backend to guarantee instantaneous indexing by Google, Bing, and Pinterest scrapers.
