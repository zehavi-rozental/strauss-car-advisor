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


# ---------- כינויים בעברית לרכבי הקטלוג (extensions.yaml) ----------

def test_hebrew_aliases_find_catalog_cars(snapshot):
    from app.modules.rules.public import find_catalog_car

    def codes(text):
        return [c.catalog_code for c in find_catalog_car(snapshot, text)]

    assert codes("קורולה קרוס") == ["CAR-09"]
    assert codes("קורולה") == ["CAR-07"]          # התאמה מדויקת קודמת לחלקית
    assert codes("אטו 3") == ["CAR-10"] and codes("אטו") == ["CAR-10"]
    assert codes("טוסון (2025)") == ["CAR-12"]
    assert codes("מאזדה3") == ["CAR-13"]           # רווחים לא משנים


def test_alias_for_unknown_car_is_rejected(config_copy):
    p = config_copy / "extensions.yaml"
    p.write_text(p.read_text(encoding="utf-8") + '  CAR-99: ["רכב דמיוני"]\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        RulesStore(config_copy, mode="strict").reload()


def test_aliases_are_data_not_code(config_copy):
    p = config_copy / "extensions.yaml"
    p.write_text(p.read_text(encoding="utf-8") + '  CAR-05: ["קיה נירו"]\n', encoding="utf-8")
    from app.modules.rules.public import find_catalog_car
    snap = RulesStore(config_copy, mode="strict").reload()
    assert [c.catalog_code for c in find_catalog_car(snap, "קיה נירו")] == ["CAR-05"]


# ---------- קובץ הכללים המעודכן: מסלול משפחתי, שדות חובה, שדות שעברו ----------

def _edit_rules(config_dir: Path, edit) -> None:
    p = config_dir / "strauss-rules.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    edit(data)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_family_cars_come_from_the_rules_file(snapshot):
    assert snapshot.strauss.family_track.cars == ["CAR-19", "CAR-20"]
    assert snapshot.strauss.family_track.price_source == "catalogue"
    assert snapshot.calc.pension_credit_max_rate == Decimal("0.07")
    assert snapshot.calc.pensionable_components == ["base", "commissions", "bonus"]


def test_family_car_missing_from_catalog_fails(config_copy):
    _edit_rules(config_copy, lambda d: d["familyTrack"].update(cars=["CAR-19", "CAR-20", "CAR-99"]))
    with pytest.raises(ConfigError, match="CAR-99"):
        RulesStore(config_copy, mode="strict").reload()


def test_family_group_car_not_on_the_list_fails(config_copy):
    _edit_rules(config_copy, lambda d: d["familyTrack"].update(cars=["CAR-19"]))
    with pytest.raises(ConfigError, match="CAR-20"):
        RulesStore(config_copy, mode="strict").reload()


def test_listed_family_car_must_be_in_the_family_group(config_copy):
    _edit_rules(config_copy, lambda d: d["familyTrack"].update(cars=["CAR-19", "CAR-20", "CAR-10"]))
    with pytest.raises(ConfigError, match="CAR-10.*משפחתי"):
        RulesStore(config_copy, mode="strict").reload()


def test_missing_rule_field_has_a_hebrew_message(config_copy):
    _edit_rules(config_copy, lambda d: d["netMethod"]["pensionCredit"].pop("maxContributionRate"))
    with pytest.raises(ConfigError) as err:
        RulesStore(config_copy, mode="strict").reload()
    assert "strauss-rules.json" in str(err.value)
    assert "חסר השדה netMethod.pensionCredit.maxContributionRate" in str(err.value)


def test_field_moved_to_the_rules_file_cannot_stay_in_extensions(config_copy):
    p = config_copy / "extensions.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "\npension_credit_max_rate: 0.07\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="pension_credit_max_rate → netMethod.pensionCredit.maxContributionRate"):
        RulesStore(config_copy, mode="strict").reload()


# ---------- חלופות שהקבצים מזכירים: שינוי בקובץ הכללים בלבד, לא בקוד ----------

def _noa(snap, alternative="ATTO 3 (2025)", **employee_changes):
    from app.modules.calculation.public import evaluate
    from app.modules.rules.public import occupation_rule, parse_reference_state, reference_employee
    ref = next(e for e in snap.reference_employees if e.employee_id == "E-001")
    employee = reference_employee(ref).model_copy(update=employee_changes)
    current = parse_reference_state(snap, ref.current_car, current_copay=ref.current_copay, for_current=True)
    return evaluate(employee, current, parse_reference_state(snap, alternative), snap.calc,
                    occupation_rule(snap.occupations.get(ref.occupation)))


def test_contributions_on_base_salary_only(snapshot, config_copy):
    _edit_rules(config_copy, lambda d: d["netMethod"].update(contributionsBase="base"))
    alt = load_snapshot(config_copy)
    assert alt.calc.pensionable_components == alt.calc.study_fund_components == ["base"]
    gross = _noa(snapshot, commissions=Decimal(3000))
    base = _noa(alt, commissions=Decimal(3000))
    assert gross.today.pension == Decimal(21500) * Decimal("0.06")        # בסיס + עמלות
    assert base.today.pension == Decimal(18500) * Decimal("0.06")         # בסיס בלבד
    assert base.today.net > gross.today.net


def test_electric_as_a_flat_gross_component(snapshot, config_copy):
    _edit_rules(config_copy, lambda d: d["electric"].update(method="gross"))
    gross_up = _noa(snapshot)
    flat = _noa(load_snapshot(config_copy))
    assert flat.alternative.electric_gross_up == Decimal(250)              # 250 ברוטו, לא מגולם
    assert gross_up.alternative.electric_gross_up > Decimal(250)
    assert flat.alternative.net < gross_up.alternative.net                 # המס יורד מה-250
    assert flat.today.net == gross_up.today.net                            # היום (היברידי) לא משתנה


def test_copay_reduces_the_taxable_use_value(snapshot, config_copy):
    _edit_rules(config_copy, lambda d: d["netMethod"]["participation"].update(reducesValueOfUse=True))
    default = _noa(snapshot)
    reduced = _noa(load_snapshot(config_copy))
    assert default.today.use_value == Decimal(4070)
    assert reduced.today.use_value == Decimal(4070 - 300)
    assert reduced.today.income_tax < default.today.income_tax
    assert reduced.today.net > default.today.net
    assert reduced.today.copay == default.today.copay == Decimal(300)     # עדיין יורדת מהנטו


@pytest.mark.parametrize("edit, field", [
    (lambda d: d["netMethod"].update(contributionsBase="bonus"), "netMethod.contributionsBase"),
    (lambda d: d["electric"].update(method="net"), "electric.method"),
    (lambda d: d["netMethod"]["rounding"].update(display="agora"), "netMethod.rounding.display"),
    (lambda d: d["netMethod"]["participation"].update(treatment="gross_deduction"),
     "netMethod.participation.treatment"),
    (lambda d: d["familyTrack"].update(priceSource="fixed"), "familyTrack.priceSource"),
])
def test_unknown_rule_values_fail_with_a_hebrew_message(config_copy, edit, field):
    _edit_rules(config_copy, edit)
    with pytest.raises(ConfigError) as err:
        RulesStore(config_copy, mode="strict").reload()
    assert f"ערך לא נתמך ב-{field}" in str(err.value) and "strauss-rules.json" in str(err.value)
