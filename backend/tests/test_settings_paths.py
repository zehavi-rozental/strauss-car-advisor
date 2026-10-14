"""ההגדרות לא תלויות בתיקייה שממנה מריצים: ‎.env ונתיב ה-sqlite יחסיים ל-backend/."""
from __future__ import annotations

import logging

import pytest
from sqlalchemy.engine import make_url

from app.api.main import create_app, startup_summary
from app.modules.conversation.public import LLMChain, build_llm_chain
from app.shared.settings import BACKEND_DIR, ENV_FILE, Settings, resolve_sqlite_url


def test_env_file_path_is_absolute():
    env_file = Settings.model_config["env_file"]
    assert env_file == ENV_FILE == BACKEND_DIR / ".env" and ENV_FILE.is_absolute()


@pytest.mark.parametrize("url, relative", [
    ("sqlite:///./car_advisor.db", "car_advisor.db"),
    ("sqlite:///car_advisor.db", "car_advisor.db"),
    ("sqlite:///data/x.db", "data/x.db"),
    ("sqlite+pysqlite:///./car_advisor.db", "car_advisor.db"),
])
def test_relative_sqlite_resolves_under_backend_dir(url, relative, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                         # כאילו הריצו מהשורש / מתיקייה אחרת
    resolved = Settings(_env_file=None, database_url=url).database_url
    assert make_url(resolved).database == str((BACKEND_DIR / relative).resolve())
    assert make_url(resolved).drivername == make_url(url).drivername


@pytest.mark.parametrize("url", [
    "sqlite://",
    "sqlite:///:memory:",
    "postgresql+psycopg://user:secret@db.example:5432/car_advisor",
])
def test_other_urls_are_unchanged(url):
    assert Settings(_env_file=None, database_url=url).database_url == url


def test_absolute_sqlite_is_unchanged(tmp_path):
    url = f"sqlite:///{(tmp_path / 'abs.db').as_posix()}"
    assert resolve_sqlite_url(url) == url
    assert Settings(_env_file=None, database_url=url).database_url == url


def test_default_database_is_under_backend_dir():
    assert make_url(Settings(_env_file=None).database_url).database == str(BACKEND_DIR / "car_advisor.db")


# ---------- שורת ההפעלה בלוג ----------

def test_startup_summary_names_paths_and_chain_but_no_secrets():
    s = Settings(_env_file=None, llm_chain="gemini:model-a,groq:model-b,openrouter:model-c",
                 gemini_api_key="SECRET-123", groq_api_key="SECRET-456",
                 database_url="postgresql+psycopg://user:pg-password@db/x")
    line = startup_summary(s, build_llm_chain(s))
    assert str(ENV_FILE) in line
    assert "llm_chain=[gemini:model-a, groq:model-b]" in line and "disabled=[openrouter:model-c]" in line
    assert "database=postgresql" in line
    for secret in ("SECRET-123", "SECRET-456", "pg-password", "user:"):
        assert secret not in line


def test_startup_logs_one_info_line_with_the_sqlite_path(engine, caplog):
    s = Settings(_env_file=None, llm_provider="none", database_url="sqlite:///./car_advisor.db",
                 gemini_api_key="SECRET-123")
    with caplog.at_level(logging.INFO, logger="app.api.main"):
        create_app(s, engine=engine, llm=LLMChain())
    [line] = [r.getMessage() for r in caplog.records if r.name == "app.api.main"]
    assert line.startswith("startup env_file=")
    assert f"database={(BACKEND_DIR / 'car_advisor.db').resolve()}" in line
    assert "llm_chain=[templates]" in line and "SECRET-123" not in line
