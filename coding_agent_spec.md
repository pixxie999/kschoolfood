# Technical Specification & System Prompt: K-School Food WebApp (Global)

You are an expert full-stack developer and automation engineer. Your task is to build a lightweight, SEO-optimized, highly automated Python web application ("K-School Food") targeting a global audience (primarily English-speaking K-content fans and health-conscious parents).

---

## 1. Core Architectural Requirements

### A. Tech Stack
- **Backend:** Python (FastAPI or Flask) - Optimized for high performance and easy API integrations.
- **Frontend:** Responsive HTML5, Tailwind CSS (embedded/CDN allowed for initial version). Must be fully responsive, matching a Progressive Web App (PWA) look and feel for mobile devices.
- **Database:** SQLite or PostgreSQL (using SQLAlchemy) to store menu items, recipe steps, mapped affiliate URLs, and localized keywords.

### B. Core Features to Implement
1. **Dynamic Meal Tray View (Main UI):** A visual "5-Compartment Tray" rendering a main dish, soup, and 3 side dishes.
2. **AI-Driven Localization Pipeline:**
   - Script to consume Korean school lunch raw data (e.g., from NEIS open API or mock JSON).
   - Integrate an LLM API (OpenAI/Gemini) to automatically translate menus, suggest local ingredient substitutes, and draft SEO descriptions.
3. **Automated Affiliate URL Mapping Engine:**
   - An internal utility that matches standardized ingredient keywords to predefined Amazon Associates and iHerb Rewards tagging formats.
   - Inject these monetization links dynamic into the recipe breakdown section.

---

## 2. Automated Monetization Integration Specifications

Every single recipe page must automatically append monetized call-to-actions (CTAs) using the following programmatic logic:

### A. Affiliate Link Structure
- **Amazon Global Storefront/Product Link:** `https://www.amazon.com/dp/{PRODUCT_ASIN}/?tag={YOUR_AMAZON_TAG}`
- **iHerb Rewards Link:** `https://www.iherb.com/c/{CATEGORY}?rcode={YOUR_IHERB_CODE}`

### B. Dynamic Ingredient Mapping Schema
Implement a mapping data dictionary or DB table that cross-references standard Korean recipe outputs to localized buying links:
- `Gochujang` $ightarrow$ Amazon Product ASIN for Chung Jung One Gochujang
- `Toasted Sesame Oil` $ightarrow$ iHerb Product Category / Specific product ID
- `Stainless Steel Tray` $ightarrow$ Amazon ASIN for 5-compartment school lunch tray

---

## 3. SEO & Structural Integrity
- Clean semantic HTML structure.
- Automatic meta-tag generation per recipe page (`<title>`, `<meta name="description">`, OpenGraph tags for Pinterest/Instagram scraping).
- Auto-generated `sitemap.xml` pipeline to register new recipe pages seamlessly.
