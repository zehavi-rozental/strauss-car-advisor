"""Start command for Render (and for the same image anywhere else).

    python deploy/render_start.py

1. DATABASE_URL: Render's managed Postgres gives "postgres://" or "postgresql://"; the driver we install is psycopg2,
   so both become "postgresql+psycopg2://" (SQLAlchemy 2.1 would otherwise pick psycopg 3, which is not installed).
2. alembic upgrade head   (alembic owns every table; safe to repeat).
3. The tax-authority CSV is loaded only when the table is empty (the import replaces whole tax years, so
   running it on every boot would empty the table while it runs).
4. uvicorn, on $PORT, behind Render's proxy (real client IP for the rate limiter via X-Forwarded-For).

No secrets are read or printed here; the settings come from the environment as everywhere else.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

url = os.environ.get("DATABASE_URL", "")
for prefix in ("postgres://", "postgresql://"):
    if url.startswith(prefix):
        os.environ["DATABASE_URL"] = "postgresql+psycopg2://" + url[len(prefix):]
        break


def log(message: str) -> None:
    print(f"[render_start] {message}", flush=True)


def migrate() -> None:
    log("alembic upgrade head")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


def load_tax_file_if_empty() -> None:
    from sqlalchemy import select

    from app.modules.vehicles.import_csv import import_csv
    from app.modules.vehicles.models import TaxVehicle
    from app.shared.db import get_engine, session_factory
    from app.shared.settings import get_settings

    sessions = session_factory(get_engine())
    with sessions() as session:
        loaded = session.execute(select(TaxVehicle.tax_year).limit(1)).first() is not None
    if loaded:
        log("tax file already loaded")
        return
    log("loading the tax-authority file (first start only)")
    with sessions() as session:
        log(str(import_csv(get_settings().tax_csv_path, session)).splitlines()[0])


def main() -> None:
    migrate()
    load_tax_file_if_empty()
    port = os.environ.get("PORT", "10000")
    log(f"uvicorn on :{port}")
    os.environ.setdefault("FORWARDED_ALLOW_IPS", "*")    # env, not a "*" argument: a shell may glob-expand it
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "app.api.serve_spa:create_render_app", "--factory",
                               "--host", "0.0.0.0", "--port", port, "--proxy-headers"])


if __name__ == "__main__":
    main()
