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

    llm_provider: str = "fake"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_fallback_provider: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = ""

    rep_access_token: str = "demo-rep"
    session_cookie_secure: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
