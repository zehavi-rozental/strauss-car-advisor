"""The system against the delivered files, with no engine code on the expected side.

Everything expected here is read straight from `strauss-rules.json`, `strauss-data.xlsx` and `tax-authority-2026.csv` (with json, openpyxl
and csv, not the loader) or computed by `reference_oracle.py`, a separate implementation of the net method written from the rules file.
Where test_financial_baseline pins the branch to the base commit, this one asks the harder question: does the code do what the files
say? A failure here is a finding to report to the owner. **The files are not changed to make it pass.**
"""
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

import openpyxl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.calculation.public import CopayStatus, Employee, OptOutState, Status, evaluate
from app.modules.conversation.stage import ConversationState, CurrentCar, Profile
from app.modules.conversation.tools import OPT_OUT_ID, classify_options
from app.modules.rules.public import car_state, load_snapshot, occupation_rule

from . import reference_oracle as oracle
from .conftest import CONFIG

AGORA = Decimal("0.01")
RAW = json.loads((CONFIG / "strauss-rules.json").read_text(encoding="utf-8"))
BOOK = openpyxl.load_workbook(CONFIG / "strauss-data.xlsx", data_only=True)
RULES, DATA = oracle.load_rules(), oracle.load_data()
SNAP = load_snapshot(CONFIG)
CATALOG = {c.catalog_code: c for c in SNAP.catalog}
FAMILY = RAW["familyTrack"]
ORDER = RAW["groupOrder"]
COMPONENTS = [("cash", "cash_gross"), ("use_value", "use_value"), ("electric_gross", "electric_gross_up"), ("pension", "pension"),
              ("study", "study_fund"), ("tax", "income_tax"), ("ni", "national_insurance"), ("health", "health"), ("copay", "copay"),
              ("family_share", "family_share"), ("net", "net")]


DRIVE_WORDS = {"בנזין": "PETROL", "היברידי": "HYBRID", "פלאג-אין": "PLUG_IN", "חשמלי": "ELECTRIC"}


def rows(sheet: str) -> list[tuple]:
    return [r for r in BOOK[sheet].iter_rows(min_row=2, values_only=True) if r[0]]


# ---------- the data: loaded exactly as delivered ----------

@pytest.mark.parametrize("row", rows("קטלוג רכבים"), ids=lambda r: r[0])
def test_every_catalog_row_is_loaded_exactly(row):
    car = CATALOG[row[0]]
    assert (car.group, car.manufacturer_code, car.model_name, car.model_code, car.reg_year) == (row[1], row[3], row[4], row[5], row[7])
    assert DRIVE_WORDS[row[8]] == car.drive.value
    assert (car.list_price, car.use_value, car.monthly_cost, car.default_copay) == tuple(Decimal(str(row[i])) for i in (9, 10, 11, 12))


@pytest.mark.parametrize("row", rows("עיסוקים"), ids=lambda r: r[0])
def test_every_occupation_row_is_loaded_exactly(row):
    occ = SNAP.occupations[row[0]]
    assert (occ.group, occ.car_mandatory, occ.opt_out_allowed, occ.maintenance) == (row[1], row[2] == "כן", row[3] == "כן", Decimal(str(row[4] or 0)))


def test_the_budgets_and_the_method_are_loaded_as_in_the_rules_file():
    calc, nm = SNAP.calc, RAW["netMethod"]
    assert calc.group_order == ORDER and {k: Decimal(str(v)) for k, v in RAW["budgets"].items()} == dict(calc.budgets)
    assert calc.family_min_children == FAMILY["minChildren"] and calc.rules_version == RAW["version"]
    assert nm["creditPointValue"] == 242 and nm["taxYear"] == 2025      # the file says: 2025 tables, for illustration
    assert RAW["unusedBudget"] == "not_paid" and RAW["chooseFrom"] == "own_group_or_lower"
    pc = nm["pensionCredit"]
    assert Decimal(str(pc["rate"])) * Decimal(str(pc["maxContributionRate"])) * pc["incomeCeiling"] == Decimal("232.75")   # as its note says


def _tax_file() -> dict[tuple[str, str, str, str], dict[str, str]]:
    text = (CONFIG.parent / "data" / "tax-authority-2026.csv").read_bytes().decode("cp1255")
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader)]
    out = {}
    for row in reader:
        rec = {h: c.strip() for h, c in zip(header, row)}
        out[(rec["שנת רישום"], rec["קוד תוצר"], rec["קוד דגם"], rec["תאור דגם"])] = rec
    return out


TAX_FILE = _tax_file()
DRIVE_FIELD = RAW["valueOfUse"]["driveField"]


@pytest.mark.parametrize("row", rows("קטלוג רכבים"), ids=lambda r: r[0])
def test_every_catalog_car_has_the_use_value_and_price_of_the_tax_file(row):
    """The workbook says its use values were taken from the tax authority's file: checked, car by car."""
    rec = TAX_FILE[(str(row[7]), str(row[3]).zfill(5), str(row[5]).zfill(5), str(row[6]).strip())]
    assert Decimal(rec["שווי שימוש"]) == Decimal(str(row[10])) and Decimal(rec["מחיר מחירון"]) == Decimal(str(row[9]))
    # the drive code by the rules file's driveField (the CSV's own header numbers them differently: FINDINGS, gap 9)
    drive_column = next(k for k in rec if k.startswith("1-חשמלי"))
    assert DRIVE_FIELD[rec[drive_column]] == row[8]


# ---------- eligibility: the rules file, written out here again ----------

def expected_cars(group: str, children: int, current_code: str | None) -> set[str]:
    rank = ORDER.index(group)
    out = {r[0] for r in rows("קטלוג רכבים") if r[1] in ORDER and ORDER.index(r[1]) <= rank}
    if children >= FAMILY["minChildren"]:
        out |= set(FAMILY["cars"])
    return out - {current_code}


def _state(group: str, children: int, current: CurrentCar, occupation: str | None = None) -> ConversationState:
    return ConversationState(profile=Profile(group=group, occupation=occupation, base=Decimal(18000), commissions=Decimal(0), bonus=Decimal(0),
                                             pension_percent=Decimal(6), study_fund_percent=Decimal(0), credit_points=Decimal("2.25"),
                                             children=children), current=current)


def _catalog_current(code: str = "CAR-09") -> CurrentCar:
    return CurrentCar(source="catalog", catalog_code=code, label=code, use_value=CATALOG[code].use_value, copay=Decimal(300))


@pytest.mark.parametrize("group", ORDER)
@pytest.mark.parametrize("children", [0, 3, 4, 5, 9])
@pytest.mark.parametrize("current", ["no-car", "catalog"])
def test_eligibility_by_group_and_children(group, children, current):
    today = CurrentCar(source="none", maintenance=Decimal(0)) if current == "no-car" else _catalog_current()
    options, excluded = classify_options(SNAP, _state(group, children, today))
    cars = {o.id for o in options} - {OPT_OUT_ID}
    assert cars == expected_cars(group, children, today.catalog_code)
    # everything the employee may not choose is listed with its rule, none is lost
    assert len(cars) + len(excluded) + (1 if today.catalog_code else 0) == len(rows("קטלוג רכבים"))


@pytest.mark.parametrize("row", rows("עיסוקים"), ids=lambda r: r[0])
def test_opting_out_only_where_the_occupation_allows_it(row):
    allowed = row[2] != "כן" and row[3] == "כן"
    options, _ = classify_options(SNAP, _state(row[1], 2, _catalog_current(), occupation=row[0]))
    assert (OPT_OUT_ID in {o.id for o in options}) is allowed


# ---------- the net: engine against the separate implementation ----------

BOUNDARIES = [7010, 7522, 9500, 10060, 16150, 22440, 46690, 50695, 60130]
BASES = sorted({b + d for b in BOUNDARIES for d in (-1, 0, 1)} | {5000, 9000, 12000, 18500, 27000, 45000, 70000, 120000})
PROFILES = [("6", "2.5", "2.25", "0", "0"), ("0", "0", "0", "0", "0"), ("7", "7.5", "2.75", "2400", "1000"), ("7.5", "2.5", "5", "0", "12000"),
            ("6", "0", "12", "500", "0")]       # credit points that leave no tax to pay
CARS = {r[0]: r[4] for r in rows("קטלוג רכבים")}


def _people(group: str, base: Decimal, profile: tuple[str, ...], children: int):
    pension, study, points, commissions, bonus = (Decimal(x) for x in profile)
    theirs = oracle.Employee(id="X", name="", occupation="", group=group, base=base, commissions=commissions, bonus=bonus, pension_pct=pension,
                             study_pct=study, points=points, children=children, current_car="", current_copay=Decimal(0))
    ours = Employee(group=group, base=base, commissions=commissions, bonus=bonus, pension_rate=pension / 100, study_fund_rate=study / 100,
                    credit_points=points, eligible_children=children)
    return theirs, ours


def _differences(where: str, res, today, alt) -> list[str]:
    out = []
    for side, mine in (("today", today), ("alternative", alt)):
        theirs = getattr(res, side)
        out += [f"{where} {side}.{e}: {getattr(mine, o)} != {getattr(theirs, e)}" for o, e in COMPONENTS if abs(getattr(mine, o) - getattr(theirs, e)) > AGORA]
    if res.delta_display != oracle.shekel(alt.net) - oracle.shekel(today.net):
        out.append(f"{where} delta {res.delta_display}")
    return out


def _compare(group: str, base: Decimal, profile: tuple[str, ...], children: int, code: str, current_kind: str) -> list[str]:
    theirs, ours = _people(group, base, profile, children)
    if current_kind == "car":
        today_car = DATA.cars["COROLLA CROSS"]
        today = oracle.Situation(label="", car=today_car, copay=Decimal(300))
        current = car_state(CATALOG["CAR-09"], copay=Decimal(300), copay_status=CopayStatus.EMPLOYEE_STATED)
    else:
        today = oracle.Situation(label="", maintenance=Decimal(1500))
        current = OptOutState(maintenance=Decimal(1500))
    car = DATA.cars[CARS[code]]
    alt = oracle.Situation(label="", car=car, copay=car.default_copay)
    res = evaluate(ours, current, car_state(CATALOG[code]), SNAP.calc, None)
    if res.status != Status.OK:
        return [f"{code} group {group}: status {res.status}"]
    return _differences(f"{code}/{group}/{base}/{profile}/{current_kind}", res, oracle.evaluate(theirs, today, RULES), oracle.evaluate(theirs, alt, RULES))


def test_the_engine_matches_the_separate_implementation_across_every_car_and_salary_boundary():
    bad: list[str] = []
    runs = 0
    for base in BASES:
        for profile in PROFILES:
            for code, _name in CARS.items():
                family = code in FAMILY["cars"]
                for current in ("car", "out"):
                    bad += _compare("ד", Decimal(base), profile, 5 if family else 2, code, current)
                    runs += 1
    assert runs > 3000 and not bad, bad[:8]


@pytest.mark.parametrize("code", sorted(CARS))
def test_a_taxable_income_exactly_on_each_tax_boundary(code):
    """The use value is added to the taxable income: the base chosen so that the sum lands on each boundary, and one shekel either side."""
    use_value = CATALOG[code].use_value
    family = code in FAMILY["cars"]
    bad: list[str] = []
    for boundary in BOUNDARIES:
        for d in (-1, 0, 1):
            base = Decimal(boundary + d) - use_value
            if base > 0:
                bad += _compare("ד", base, ("0", "0", "2.25", "0", "0"), 5 if family else 2, code, "car")
    assert not bad, bad[:6]


@pytest.mark.parametrize("group", ORDER)
@pytest.mark.parametrize("code", FAMILY["cars"])
def test_the_family_track_share_in_every_group(group, code):
    """50% of the gap over the group's budget, by the catalog's cost; and nothing when the cost is within the budget."""
    theirs, ours = _people(group, Decimal(18500), PROFILES[0], 5)
    res = evaluate(ours, car_state(CATALOG["CAR-09"], copay=Decimal(300), copay_status=CopayStatus.EMPLOYEE_STATED), car_state(CATALOG[code]), SNAP.calc, None)
    gap = max(CATALOG[code].monthly_cost - Decimal(RAW["budgets"][group]), Decimal(0))
    assert res.alternative.family_share == gap * Decimal(str(FAMILY["gapSplitEmployee"]))


@pytest.mark.parametrize("children", [0, 3])
def test_the_family_track_is_closed_below_the_minimum(children):
    _, ours = _people("ד", Decimal(18500), PROFILES[0], children)
    res = evaluate(ours, car_state(CATALOG["CAR-09"], copay=Decimal(300), copay_status=CopayStatus.EMPLOYEE_STATED), car_state(CATALOG["CAR-19"]), SNAP.calc, None)
    assert res.status == Status.NOT_ELIGIBLE


def test_an_unused_budget_is_not_paid():
    """unusedBudget: not_paid. The same car, the same person: a higher group's bigger budget adds nothing to the net."""
    nets = set()
    for group in ORDER:
        _, ours = _people(group, Decimal(18500), PROFILES[0], 2)
        res = evaluate(ours, car_state(CATALOG["CAR-09"], copay=Decimal(300), copay_status=CopayStatus.EMPLOYEE_STATED) if group in "גד" else OptOutState(maintenance=Decimal(1500)),
                       car_state(CATALOG["CAR-01"]), SNAP.calc, None)
        nets.add(res.alternative.net)
    assert len(nets) == 1


@pytest.mark.parametrize("code", [r[0] for r in rows("קטלוג רכבים") if r[8] == "חשמלי"])
@pytest.mark.parametrize("base", [6000, 18500, 47000, 90000])
def test_an_electric_car_leaves_exactly_250_more_net_at_any_salary(code, base):
    theirs, ours = _people("ד", Decimal(base), PROFILES[0], 2)
    car = DATA.cars[CARS[code]]
    res = evaluate(ours, OptOutState(maintenance=Decimal(1500)), car_state(CATALOG[code]), SNAP.calc, None)
    without = oracle.net_of(theirs, oracle.Situation(label="", car=car, copay=car.default_copay), RULES)
    assert abs(res.alternative.net - without.net - Decimal(RAW["electric"]["netBonus"])) <= AGORA


@pytest.mark.parametrize("code", [r[0] for r in rows("קטלוג רכבים") if r[8] != "חשמלי"])
def test_only_an_electric_car_gets_the_refund(code):
    """A plug-in gets none: extensions.plug_in_counts_as_electric, a decision of the project, not of the rules file (FINDINGS, gap 8)."""
    _, ours = _people("ד", Decimal(18500), PROFILES[0], 5)
    res = evaluate(ours, OptOutState(maintenance=Decimal(1500)), car_state(CATALOG[code]), SNAP.calc, None)
    assert res.alternative.electric_gross_up == 0


@pytest.mark.parametrize("name", [r[0] for r in rows("עיסוקים") if r[3] == "כן"])
def test_the_maintenance_is_taxable_and_not_pensionable(name):
    """waiver: maintenanceIsTaxable true, maintenanceIsPensionable false, in the occupation's amount."""
    assert RAW["waiver"]["maintenanceIsTaxable"] is True and RAW["waiver"]["maintenanceIsPensionable"] is False
    occ = SNAP.occupations[name]
    theirs, ours = _people(occ.group, Decimal(18500), PROFILES[0], 2)
    res = evaluate(ours, car_state(CATALOG["CAR-01"], copay=Decimal(150), copay_status=CopayStatus.EMPLOYEE_STATED), OptOutState(), SNAP.calc, occupation_rule(occ))
    assert res.status == Status.OK
    alt = oracle.net_of(theirs, oracle.Situation(label="", maintenance=occ.maintenance), RULES)
    assert abs(res.alternative.net - alt.net) <= AGORA
    assert res.alternative.pension == Decimal(18500) * Decimal(6) / 100          # the pension is on the salary, not on the maintenance
    assert res.alternative.cash_gross == Decimal(18500) + occ.maintenance


@settings(max_examples=300, deadline=None)
@given(base=st.integers(3000, 200000), commissions=st.integers(0, 30000), bonus=st.integers(0, 40000), pension=st.sampled_from(["0", "5", "6", "6.5", "7", "7.5"]),
       study=st.sampled_from(["0", "2.5", "7.5"]), points=st.sampled_from(["0", "1", "2.25", "2.75", "3.5", "5", "8"]), code=st.sampled_from(sorted(CARS)),
       current=st.sampled_from(["car", "out"]))
def test_random_employees_match_the_separate_implementation(base, commissions, bonus, pension, study, points, code, current):
    bad = _compare("ד", Decimal(base), (pension, study, points, str(commissions), str(bonus)), 5 if code in FAMILY["cars"] else 2, code, current)
    assert not bad, bad[:4]
