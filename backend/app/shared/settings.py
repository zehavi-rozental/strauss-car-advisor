"""הגדרות סביבה (סעיף 12). נקראות ממשתני סביבה / ‎.env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{BACKEND_DIR / 'car_advisor.db'}"
    config_mode: Literal["dev", "strict"] = "dev"
    config_dir: Path = BACKEND_DIR / "config"
    tax_csv_path: Path = BACKEND_DIR / "data" / "tax-authority-2026.csv"

    # שרשרת ספקים לפי סדר: "gemini:<model>,gemini:<model>,groq:<model>". ריקה = LLM_PROVIDER + LLM_FALLBACK_*.
    llm_chain: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    llm_cooldown_429_s: float = 60.0  # ספק שהחזיר 429 מדולג למשך הזמן הזה; 418 — עד הפעלה מחדש

    # תאימות לאחור (כשאין LLM_CHAIN)
    llm_provider: str = "fake"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_fallback_provider: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = ""
    llm_base_url: str = ""            # ספק תואם OpenAI: ריק = ברירת המחדל של הספק
    llm_fallback_base_url: str = ""
    llm_timeout_s: float = 20.0

    max_message_chars: int = 1000
    rate_limit_per_minute: int = 30      # הודעות לשיחה בדקה
    create_rate_limit_per_minute: int = 20
    max_messages_per_conversation: int = 80

    rep_access_token: str = "demo-rep"
    session_cookie_secure: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
