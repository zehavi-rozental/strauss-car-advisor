"""קבצי הגדרות (dev/strict) ומסד הנתונים (כסף חוזר כ-Decimal, ייבוא אטומי)."""
from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, select

from app.modules.rules.public import ConfigError, RulesStore, load_snapshot
from app.modules.vehicles.import_csv import import_csv
from app.modules.vehicles.models import TaxVehicle
from app.modules.vehicles.public import VehicleKey, VehicleRepository, match_current_car
from app.shared.db import Base, Money, make_engine, session_factory

from .conftest import BACKEND, CONFIG


@pytest.fixture
def config_copy(tmp_path) -> Path:
    dst = tmp_path / "config"
    shutil.copytree(CONFIG, dst)
    return dst


def _break_rules(config_dir: Path) -> None:
    p = config_dir / "strauss-rules.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    del data["budgets"]["ג"]  # תקציב חסר = שגיאה, אף פעם לא ברירת מחדל
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_strict_rejects_missing_money(config_copy):
    _break_rules(config_copy)
    with pytest.raises(ConfigError):
        RulesStore(config_copy, mode="strict").reload()


def test_dev_keeps_previous_version_on_bad_file(config_copy):
    store = RulesStore(config_copy, mode="dev")
    good = store.current
    _break_rules(config_copy)
    assert store.reload() is good
    assert store.last_error


def test_rules_change_needs_no_code(config_copy):
    """מחליפים נתונים, לא קוד: שינוי תקציב בקובץ משנה את החישוב."""
    p = config_copy / "strauss-rules.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["budgets"]["ב"] = 4000
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load_snapshot(config_copy).calc.budgets["ב"] == Decimal(4000)


def test_id_numbers_are_not_loaded(snapshot):
    for e in snapshot.reference_employees:
        assert not any(v == "312456783" for v in e.model_dump().values())


def test_money_roundtrip_sqlite():
    engine = make_engine("sqlite://")
    md = MetaData()
    t = Table("m", md, Column("id", Integer, primary_key=True), Column("v", Money))
    md.create_all(engine)
    with engine.begin() as c:
        c.execute(t.insert(), [{"id": 1, "v": Decimal("1234.5678")}])
        assert c.execute(select(t.c.v)).scalar_one() == Decimal("1234.5678")


@pytest.fixture(scope="module")
def vehicle_session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[TaxVehicle.__table__])
    session = session_factory(engine)()
    report = import_csv(BACKEND / "data" / "tax-authority-2026.csv", session)
    assert report.imported == 77_988 and report.duplicates == 0
    # הרצה שנייה לא מכפילה
    import_csv(BACKEND / "data" / "tax-authority-2026.csv", session)
    return session


def test_import_idempotent(vehicle_session):
    assert vehicle_session.query(TaxVehicle).count() == 77_988


def test_key_needs_vehicle_type(vehicle_session):
    """ATTO 3 ו-TANG חולקים קוד תוצר+דגם; רק סוג הרכב מבדיל."""
    rows = vehicle_session.query(TaxVehicle).filter_by(
        tax_year=2026, reg_year=2025, manufacturer_code="01014", model_code="00003").all()
    assert len({r.vehicle_type for r in rows}) == len(rows)


def test_codes_keep_leading_zeros(vehicle_session):
    info = VehicleRepository(vehicle_session).get(VehicleKey(2026, 2025, "1", "00413", "00296"))
    assert info is not None and info.use_value == Decimal(4070)


def test_drive_decoding(vehicle_session):
    repo = VehicleRepository(vehicle_session)
    assert repo.get(VehicleKey(2026, 2025, "1", "01327", "00730")).drive == "ELECTRIC"   # MODEL 3
    assert repo.get(VehicleKey(2026, 2026, "1", "00416", "00294")).drive == "PLUG_IN"    # SPORTAGE HEV


def test_catalog_matches_tax_authority(snapshot, vehicle_session):
    from app.modules.vehicles.load_catalog import verify_catalog
    assert verify_catalog(snapshot, VehicleRepository(vehicle_session)) == []


def test_current_car_match_rules(vehicle_session):
    repo = VehicleRepository(vehicle_session)
    assert match_current_car(repo, "רכב שלא קיים").status == "NOT_FOUND"
    choose = match_current_car(repo, "COROLLA CROSS", 2024)
    assert choose.status == "CHOOSE" and 1 < len(choose.choices) <= 5
    assert len({c.use_value for c in choose.choices}) == len(choose.choices)
