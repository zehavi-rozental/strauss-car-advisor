"""Three-way check of the six reference employees: Strauss's "חישובים" sheet, an independent oracle written
from the rules file (`reference_oracle.py`, no engine code), and the engine.

- oracle vs sheet: every figure the sheet gives (net, delta, income tax, NI + health, electric gross,
  family share), in whole shekels as the sheet shows them;
- engine vs oracle: every component (cash, use value, pension, study fund, tax, NI, health, copay, family
  share, electric gross, net) to the agora, unrounded.

    python -m tests.test_reference_oracle   # prints the Expected / Oracle / Engine / Difference table
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.calculation.public import evaluate
from app.modules.rules.public import load_snapshot, occupation_rule, parse_reference_state, reference_employee

from . import reference_oracle as oracle
from .conftest import CONFIG

AGORA = Decimal("0.01")
_ROWS = {r["emp"].id: r for r in oracle.compute_all()}


def _engine(emp_id: str):
    snap = load_snapshot(CONFIG)
    emp = next(e for e in snap.reference_employees if e.employee_id == emp_id)
    ref = next(r for r in snap.reference_results if r.employee_id == emp_id)
    current = parse_reference_state(snap, emp.current_car, current_copay=emp.current_copay, for_current=True)
    alternative = parse_reference_state(snap, ref.alternative_label)
    return evaluate(reference_employee(emp), current, alternative, snap.calc,
                    occupation_rule(snap.occupations.get(emp.occupation)))


def _sheet_rows(emp_id: str):
    """(field, Strauss sheet value, oracle value in whole shekels)."""
    r = _ROWS[emp_id]
    ref, t, a = r["ref"], r["today"], r["alt"]
    s = oracle.shekel
    return [
        ("נטו היום", ref.net_today, s(t.net)),
        ("נטו בחלופה", ref.net_alt, s(a.net)),
        ("הפרש", ref.delta, s(a.net) - s(t.net)),
        ("מס היום", ref.tax_today, s(t.tax)),
        ("מס בחלופה", ref.tax_alt, s(a.tax)),
        ("ב\"ל+בריאות היום", ref.ni_today, s(t.ni + t.health)),
        ("ב\"ל+בריאות בחלופה", ref.ni_alt, s(a.ni + a.health)),
        ("החזר חשמל ברוטו", ref.electric_gross, s(a.electric_gross)),
        ("חלק משפחתי", ref.family_share, s(a.family_share)),
    ]


_COMPONENTS = [  # (oracle attribute, engine Breakdown attribute)
    ("cash", "cash_gross"), ("use_value", "use_value"), ("electric_gross", "electric_gross_up"),
    ("pension", "pension"), ("study", "study_fund"), ("tax", "income_tax"), ("ni", "national_insurance"),
    ("health", "health"), ("copay", "copay"), ("family_share", "family_share"), ("net", "net"),
]

IDS = sorted(_ROWS)


@pytest.mark.acceptance
@pytest.mark.parametrize("emp_id", IDS)
def test_oracle_matches_strauss_sheet(emp_id):
    diffs = [(f, exp, got) for f, exp, got in _sheet_rows(emp_id) if exp != got]
    assert not diffs, diffs


@pytest.mark.acceptance
@pytest.mark.parametrize("emp_id", IDS)
def test_engine_matches_oracle_to_the_agora(emp_id):
    res = _engine(emp_id)
    row = _ROWS[emp_id]
    for side, mine in (("today", row["today"]), ("alternative", row["alt"])):
        theirs = getattr(res, side)
        for o_attr, e_attr in _COMPONENTS:
            o_val, e_val = getattr(mine, o_attr), getattr(theirs, e_attr)
            # the electric component is found by bisection on both sides: equal within one agora
            assert abs(o_val - e_val) <= AGORA, (side, e_attr, o_val, e_val)
    assert res.delta_display == oracle.shekel(row["alt"].net) - oracle.shekel(row["today"].net)


@pytest.mark.acceptance
@pytest.mark.parametrize("emp_id", IDS)
def test_electric_gross_up_gives_exactly_250_net(emp_id):
    row = _ROWS[emp_id]
    if row["alt"].electric_gross == 0:
        pytest.skip("not an electric alternative")
    rules, data = oracle.load_rules(), oracle.load_data()
    alt = oracle.situation(row["ref"].alternative, data)
    without = oracle.net_of(row["emp"], alt, rules).net
    assert abs(row["alt"].net - without - rules.electric_net) <= AGORA


def main() -> None:
    print("| עובד | שדה | Expected (שטראוס) | Oracle | Engine | הפרש |")
    print("| --- | --- | ---: | ---: | ---: | ---: |")
    for emp_id in IDS:
        res = _engine(emp_id)
        t, a = res.today, res.alternative
        s = oracle.shekel
        engine_vals = [s(t.net), s(a.net), res.delta_display, s(t.income_tax), s(a.income_tax),
                       s(t.national_insurance + t.health), s(a.national_insurance + a.health),
                       s(a.electric_gross_up), s(a.family_share)]
        for (field, exp, got), eng in zip(_sheet_rows(emp_id), engine_vals):
            print(f"| {emp_id} | {field} | {exp} | {got} | {eng} | {eng - exp} |")


if __name__ == "__main__":
    main()
