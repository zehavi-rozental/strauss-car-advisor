from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.rules.public import load_snapshot

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND / "config"


@pytest.fixture(scope="session")
def snapshot():
    return load_snapshot(CONFIG)


@pytest.fixture(scope="session")
def rules(snapshot):
    return snapshot.calc


# ---------- שיחה ו-API ----------

from decimal import Decimal  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.main import create_app  # noqa: E402
from app.modules.conversation.public import LLMChain  # noqa: E402
from app.modules.cases.public import CasesService  # noqa: E402,F401  (רישום טבלת rep_questions)
from app.modules.vehicles.models import TaxVehicle  # noqa: E402
from app.shared.db import Base, make_engine, session_factory  # noqa: E402
from app.shared.settings import Settings  # noqa: E402


def _tax_row(reg_year, model_code, name, use_value, drive="HYBRID", vehicle_type="1", description=None):
    return TaxVehicle(tax_year=2026, reg_year=reg_year, vehicle_type=vehicle_type, manufacturer_code="00413",
                      model_code=model_code, manufacturer_name="TOYOTA", model_description=description or name,
                      commercial_name=name, fuel_code="5", drive=drive, automatic=True, list_price=None,
                      use_value=Decimal(use_value), effective_date="2026-01-01")


@pytest.fixture
def engine():
    """מסד בזיכרון עם כמה רכבים מדומים ממאגר רשות המסים."""
    eng = make_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with session_factory(eng)() as s, s.begin():
        s.add_all([
            _tax_row(2023, "00296", "COROLLA CROSS", "3910"),
            _tax_row(2022, "00500", "FABIA", "2400", "PETROL", description="NJ32 AMBITION"),
            _tax_row(2022, "00501", "FABIA", "2650", "PETROL", vehicle_type="2", description="NJ34 STYLE"),
        ])
    return eng


@pytest.fixture
def sessions(engine):
    return session_factory(engine)


def make_client(engine, llm=None, **overrides) -> TestClient:
    settings = Settings(_env_file=None, llm_provider="none", **overrides)
    app = create_app(settings, engine=engine, llm=llm if llm is not None else LLMChain())
    return TestClient(app)
