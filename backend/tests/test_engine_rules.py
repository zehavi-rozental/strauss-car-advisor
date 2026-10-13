"""חוקים שחייבים להתקיים בכל קלט (hypothesis מריצה מאות קלטים אקראיים)."""
from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.calculation.public import (
    CarState,
    CopayStatus,
    Drive,
    Employee,
    OptOutState,
    evaluate,
)
from app.modules.calculation.tax import StateInputs, compute_net

from .conftest import CONFIG
from app.modules.rules.public import load_snapshot

RULES = load_snapshot(CONFIG).calc

money = lambda lo, hi: st.integers(lo, hi).map(Decimal)  # noqa: E731

employees = st.builds(
    Employee,
    group=st.sampled_from(["א", "ב", "ג", "ד"]),
    base=money(5_000, 80_000),
    commissions=money(0, 8_000),
    bonus=money(0, 5_000),
    pension_rate=st.sampled_from([Decimal("0.06"), Decimal("0.065"), Decimal("0.07")]),
    study_fund_rate=st.sampled_from([Decimal("0"), Decimal("0.025")]),
    credit_points=st.sampled_from([Decimal(x) for x in ("2.25", "2.75", "4.25", "7")]),
    eligible_children=st.integers(0, 7),
)


def car(use_value, drive=Drive.PETROL, copay=Decimal(300), group="א"):
    return CarState(label="רכב", group=group, drive=drive, use_value=use_value,
                    monthly_cost=Decimal(3000), copay=copay, copay_status=CopayStatus.CONFIRMED)


@settings(max_examples=150, deadline=None)
@given(emp=employees, uv=money(1_500, 9_000))
def test_electric_adds_exactly_the_net_bonus(emp, uv):
    petrol = evaluate(emp, car(uv), car(uv), RULES)
    electric = evaluate(emp, car(uv), car(uv, Drive.ELECTRIC), RULES)
    diff = electric.alternative.net - petrol.alternative.net
    assert abs(diff - RULES.electric_net_bonus) < Decimal("0.01")
    assert electric.alternative.electric_gross_up >= RULES.electric_net_bonus


@settings(max_examples=150, deadline=None)
@given(emp=employees, uv=money(1_500, 9_000), extra=money(1, 3_000))
def test_higher_use_value_never_raises_net(emp, uv, extra):
    low = compute_net(emp, StateInputs("א", use_value=uv), RULES).net
    high = compute_net(emp, StateInputs("ב", use_value=uv + extra), RULES).net
    assert high <= low


@settings(max_examples=100, deadline=None)
@given(emp=employees, a=money(1_500, 9_000), b=money(1_500, 9_000))
def test_delta_is_antisymmetric(emp, a, b):
    ab = evaluate(emp, car(a), car(b), RULES).delta_display
    ba = evaluate(emp, car(b), car(a), RULES).delta_display
    assert ab == -ba


@settings(max_examples=150, deadline=None)
@given(emp=employees, uv=money(0, 12_000), maint=money(0, 3_000))
def test_deductions_never_negative(emp, uv, maint):
    b = compute_net(emp, StateInputs("x", use_value=uv, maintenance=maint), RULES)
    for value in (b.income_tax, b.national_insurance, b.health, b.pension, b.study_fund):
        assert value >= 0


@settings(max_examples=50, deadline=None)
@given(emp=employees, uv=money(1_500, 9_000))
def test_deterministic(emp, uv):
    one = evaluate(emp, car(uv), OptOutState(maintenance=Decimal(1500)), RULES)
    two = evaluate(emp, car(uv), OptOutState(maintenance=Decimal(1500)), RULES)
    assert one == two
