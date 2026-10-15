"""הגדרות סביבה (סעיף 12). נקראות ממשתני סביבה / ‎.env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]
# נתיב מוחלט: ההגדרות זהות לא משנה מאיזו תיקייה מריצים (uvicorn / alembic / pytest)
ENV_FILE = BACKEND_DIR / ".env"


def resolve_sqlite_url(url: str) -> str:
    """sqlite עם נתיב יחסי → נתיב תחת BACKEND_DIR. כל השאר (נתיב מוחלט, זיכרון, postgres) — ללא שינוי."""
    parsed = make_url(url)
    database = parsed.database
    if parsed.get_backend_name() != "sqlite" or not database or database == ":memory:":
        return url
    if Path(database).is_absolute():
        return url
    return parsed.set(database=str((BACKEND_DIR / database).resolve())).render_as_string(hide_password=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str = f"sqlite:///{BACKEND_DIR / 'car_advisor.db'}"
    config_mode: Literal["dev", "strict"] = "dev"
    config_dir: Path = BACKEND_DIR / "config"
    tax_csv_path: Path = BACKEND_DIR / "data" / "tax-authority-2026.csv"

    # שרשרת ספקים לפי סדר: "gemini:<model>,gemini:<model>,groq:<model>". ריקה = LLM_PROVIDER + LLM_FALLBACK_*.
    llm_chain: str = ""
    # llm_first: כל הודעה עוברת קודם למודל (הקורא — גיבוי ובדיקה צולבת). parser_first: הקורא קודם (חוסך מכסה).
    llm_mode: Literal["llm_first", "parser_first"] = "llm_first"
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    llm_cooldown_429_s: float = 60.0  # ספק שהחזיר 429 מדולג למשך הזמן הזה; 418 — עד הפעלה מחדש
    llm_cooldown_fail_s: float = 60.0  # 5xx או timeout: מדולג למשך הזמן הזה (התור הבא לא מחכה לו)
    gemini_thinking_level: str = "low"  # Gemini 3: הנמוך שמתקבל ("minimal" נדחה ב-3.7); ריק = ברירת המודל
    gemini_thinking_budget: int = 0     # Gemini 2.x: 0 = בלי חשיבה

    # תאימות לאחור (כשאין LLM_CHAIN)
    llm_provider: str = "fake"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_fallback_provider: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = ""
    llm_base_url: str = ""            # ספק תואם OpenAI: ריק = ברירת המחדל של הספק
    llm_fallback_base_url: str = ""
    llm_timeout_s: float = 4.0        # לכל קריאה: רשומה איטית → הבאה בשרשרת
    llm_turn_deadline_s: float = 6.0   # לכל תור: אחרי זמן זה על ספקים — תבניות, בלי רשומות נוספות
    llm_intro_budget_s: float = 2.5    # פתיח לתוצאות: לא ענה בזמן → התוצאה מהתבנית מיד

    max_message_chars: int = 1000
    rate_limit_per_minute: int = 30      # הודעות לשיחה בדקה
    create_rate_limit_per_minute: int = 20
    max_messages_per_conversation: int = 80

    rep_access_token: str = ""             # כותרת X-Rep-Token; ריק (ברירת המחדל) = ה-API של הנציג סגור
    rep_rate_limit_per_minute: int = 60
    session_cookie_secure: bool = False
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _sqlite_relative_to_backend(cls, value: str) -> str:
        # "sqlite:///./car_advisor.db" מהשורש היה פותח מסד חדש וריק; תמיד יחסית ל-backend/
        return resolve_sqlite_url(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
