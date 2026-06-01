import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class Settings:
    NEIS_API_KEY: str = os.getenv("NEIS_API_KEY", "")
    OFFICE_CODE: str = os.getenv("OFFICE_CODE", "K10")
    SCHOOL_CODE: str = os.getenv("SCHOOL_CODE", "7801106")

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    ACTIVE_LLM_PROVIDER: str = os.getenv("ACTIVE_LLM_PROVIDER", "claude").lower()

    COOKRCP01_API_KEY: str = os.getenv("COOKRCP01_API_KEY", "")

    AMAZON_TAG: str = os.getenv("AMAZON_TAG", "your_amazon_tag_here")
    IHERB_CODE: str = os.getenv("IHERB_CODE", "your_iherb_code_here")

settings = Settings()
