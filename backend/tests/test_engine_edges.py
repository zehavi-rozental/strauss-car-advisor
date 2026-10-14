"""מקרי גבול ותרחישים שהדוגמאות של שטראוס לא מכסות."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.calculation.public import (
    CarState,
    CopayStatus,
    Drive,
    Employee,
    OptOutState,
    Status,
    evaluate,
)
from app.modules.calculation.tax import StateInputs, compute_net, two_tier
from app.modules.rules.public import car_state, find_catalog_car, occupation_rule

D = Decimal


def emp(**kw):
    base = dict(group="ג", base=D(18500), pension_rate=D("0.06"), study_fund_rate=D("0.025"),
                credit_points=D("2.75"), eligible_children=2)
    base.update(kw)
    return Employee(**base)


def cat(snapshot, name):
    [c] = find_catalog_car(snapshot, name)
    return c


def current(snapshot, name="COROLLA CROSS", copay=D(300)):
    return car_state(cat(snapshot, name), copay=copay, copay_status=CopayStatus.EMPLOYEE_STATED)


# ---------- זכאות ----------

def test_lower_group_allowed(snapshot, rules):
    r = evaluate(emp(group="ד"), current(snapshot), car_state(cat(snapshot, "PICANTO")), rules)
    assert r.status == Status.OK and r.eligible


def test_higher_group_not_eligible_no_calculation(snapshot, rules):
    r = evaluate(emp(group="ב"), current(snapshot), car_state(cat(snapshot, "MODEL 3")), rules)
    assert r.status == Status.NOT_ELIGIBLE
    assert r.alternative is None and r.delta_display is None


def test_opt_out_mandatory_occupation(snapshot, rules):
    occ = occupation_rule(snapshot.occupations["נציג/ת מכירות שטח"])
    r = evaluate(emp(group="ב"), current(snapshot), OptOutState(), rules, occ)
    assert r.status == Status.NOT_ELIGIBLE
    assert "חובה" in r.reason


def test_opt_out_unknown_occupation_asks_rep(snapshot, rules):
    r = evaluate(emp(), current(snapshot), OptOutState(), rules, occupation=None)
    assert r.status == Status.INCOMPLETE
    assert r.missing_fields == ["OCCUPATION_OPT_OUT"]
    assert r.today is not None and r.alternative is None   # השוואה חלקית


def test_opt_out_group_b_allowed(snapshot, rules):
    """האתגר: הבוט חייב לעבוד על עובדת מקבוצה ב שרוצה לוותר."""
    occ = occupation_rule(snapshot.occupations["אנליסט/ית נתונים"])
    r = evaluate(emp(group="ב"), current(snapshot, "NIRO", D(250)), OptOutState(), rules, occ)
    assert r.status == Status.OK
    assert r.alternative.maintenance == D(1500)
    assert r.alternative.use_value == 0 and r.alternative.copay == 0


def test_family_three_children_not_eligible(snapshot, rules):
    r = evaluate(emp(eligible_children=3), current(snapshot), car_state(cat(snapshot, "CARNIVAL")), rules)
    assert r.status == Status.NOT_ELIGIBLE


def test_family_santa_fe_group_d(snapshot, rules):
    """5,600 − 5,200 = 400 → 50% = 200, בנוסף להשתתפות."""
    r = evaluate(emp(group="ד", eligible_children=4), current(snapshot),
                 car_state(cat(snapshot, "SANTA FE")), rules)
    assert r.alternative.family_share == D(200)
    assert r.alternative.copay == D(500)


def test_family_car_cheaper_than_budget_no_gap(rules):
    fam = CarState(label="משפחתי זול", group="משפחתי", drive=Drive.PETROL, use_value=D(4000),
                   monthly_cost=D(5000), copay=D(500))
    today = CarState(label="היום", group="ד", drive=Drive.PETROL, use_value=D(4000),
                     monthly_cost=D(5000), copay=D(500))
    r = evaluate(emp(group="ד", eligible_children=4), today, fam, rules)
    assert r.alternative.family_share == 0


# ---------- חשמל ----------

def test_plug_in_gets_no_bonus_and_says_so(snapshot, rules):
    r = evaluate(emp(group="ד"), current(snapshot), car_state(cat(snapshot, "SPORTAGE HEV")), rules)
    assert r.alternative.electric_gross_up == 0
    assert any("פלאג-אין" in a for a in r.assumptions)


def test_gross_up_when_employee_pays_no_tax(rules):
    """עובד שהמס שלו מתאפס: עדיין +250 נטו בדיוק (רק ביטוח לאומי ובריאות מגולמים)."""
    low = emp(group="א", base=D(6000), credit_points=D(7), study_fund_rate=D(0))
    ev = CarState(label="EV", group="א", drive=Drive.ELECTRIC, use_value=D(2590), monthly_cost=D(3300), copay=D(200))
    pet = ev.model_copy(update={"drive": Drive.PETROL})
    r_ev, r_pet = evaluate(low, pet, ev, rules), evaluate(low, pet, pet, rules)
    assert r_ev.alternative.income_tax == 0
    assert abs(r_ev.alternative.net - r_pet.alternative.net - 250) < D("0.01")


# ---------- השתתפות ונתונים חסרים ----------

def test_catalog_default_copay_is_marked_pending(snapshot, rules):
    r = evaluate(emp(), current(snapshot), car_state(cat(snapshot, "ATTO 3")), rules)
    assert r.status == Status.OK
    assert any("ממתינה לאישור נציג" in a for a in r.assumptions)


def test_rep_copay_changes_delta(snapshot, rules):
    default = evaluate(emp(), current(snapshot), car_state(cat(snapshot, "ATTO 3")), rules)
    confirmed = evaluate(emp(), current(snapshot), car_state(cat(snapshot, "ATTO 3"), copay=D(350)), rules)
    assert default.delta_display - confirmed.delta_display == 50
    assert not any("ממתינה" in a for a in confirmed.assumptions)


def test_missing_salary_is_incomplete_not_a_number(snapshot, rules):
    r = evaluate(emp(base=None), current(snapshot), car_state(cat(snapshot, "EV3")), rules)
    assert r.status == Status.INCOMPLETE
    assert "EMPLOYEE_BASE_SALARY" in r.missing_fields
    assert r.today is None and r.delta_display is None


def test_current_car_without_use_value(rules):
    unknown = CarState(label="רכב ישן", drive=Drive.PETROL, use_value=None, copay=D(300))
    new = CarState(label="חדש", group="א", drive=Drive.PETROL, use_value=D(3000), monthly_cost=D(3000), copay=D(200))
    r = evaluate(emp(), unknown, new, rules)
    assert r.status == Status.INCOMPLETE
    assert "CURRENT_USE_VALUE" in r.missing_fields
    assert r.alternative is not None and r.today is None


def test_unused_budget_not_paid(snapshot, rules):
    """ירידת קבוצה לא מוסיפה את יתרת התקציב לשכר."""
    r = evaluate(emp(group="ד"), current(snapshot), car_state(cat(snapshot, "PICANTO")), rules)
    assert r.alternative.cash_gross == r.today.cash_gross


# ---------- ספים ותקרות ----------

@pytest.mark.parametrize("amount", [D(7522), D(7523), D(50695), D(50696), D(90000)])
def test_social_threshold_and_ceiling(rules, amount):
    ni = rules.national_insurance
    expected = min(amount, ni.threshold) * ni.low_rate + max(D(0), min(amount, ni.ceiling) - ni.threshold) * ni.high_rate
    assert two_tier(amount, ni) == expected
    if amount > ni.ceiling:
        assert two_tier(amount, ni) == two_tier(ni.ceiling, ni)


@pytest.mark.parametrize("taxable", [D(7010), D(10060), D(16150), D(22440), D(46690), D(60130)])
def test_tax_continuous_at_bracket_edges(rules, taxable):
    e = emp(credit_points=D(0), pension_rate=D(0))
    below = compute_net(e, StateInputs("x", use_value=taxable - D("0.01") - e.base), rules).income_tax
    at = compute_net(e, StateInputs("x", use_value=taxable - e.base), rules).income_tax
    assert D(0) <= at - below < D("0.01")


# ---------- מסלול משפחתי: עלות כל רכב לפי הקטלוג (familyTrack.priceSource) ----------

def test_family_track_uses_each_cars_catalog_cost(snapshot, rules):
    """קבוצה ב (תקציב 3,700), 5 ילדים. קרניבל 6,000 → פער 2,300; סנטה פה 5,600 → פער 1,900; העובד 50%."""
    employee = emp(group="ב", eligible_children=5)
    today = current(snapshot, "NIRO", D(250))
    carnival = evaluate(employee, today, car_state(cat(snapshot, "CARNIVAL")), rules)
    santa_fe = evaluate(employee, today, car_state(cat(snapshot, "SANTA FE")), rules)
    assert carnival.eligible and santa_fe.eligible
    assert carnival.alternative.family_share == D(1150)
    assert santa_fe.alternative.family_share == D(950)
    assert santa_fe.delta_display > carnival.delta_display
