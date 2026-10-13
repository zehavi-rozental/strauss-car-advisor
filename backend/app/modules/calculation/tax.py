"""חישוב נטו חודשי לפי שיטת הנטו של שטראוס (netMethod בקובץ הכללים).

משחזר בדיוק את גיליון "חישובים": 12 ערכי נטו, מס, ביטוח לאומי+בריאות וגילום החשמל.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.shared.money import ZERO, to_shekel

from .models import Breakdown, CalcRules, Employee, TwoTier


def income_tax_before_credits(taxable: Decimal, rules: CalcRules) -> Decimal:
    tax = ZERO
    lower = ZERO
    for bracket in rules.brackets:
        upper = bracket.up_to
        if upper is None or taxable < upper:
            tax += (taxable - lower) * bracket.rate if taxable > lower else ZERO
            return tax
        tax += (upper - lower) * bracket.rate
        lower = upper
    return tax


def two_tier(amount: Decimal, tier: TwoTier) -> Decimal:
    capped = min(amount, tier.ceiling)
    low_part = min(capped, tier.threshold)
    high_part = max(ZERO, capped - tier.threshold)
    return low_part * tier.low_rate + high_part * tier.high_rate


def _components(employee: Employee, names: list[str]) -> Decimal:
    values = {"base": employee.base, "commissions": employee.commissions, "bonus": employee.bonus}
    return sum((values[n] for n in names), ZERO)


@dataclass(frozen=True)
class StateInputs:
    """מה שמשתנה בין מצב למצב: שווי שימוש, אחזקה, השתתפות, חלק משפחתי."""

    label: str
    use_value: Decimal = ZERO
    maintenance: Decimal = ZERO
    copay: Decimal = ZERO
    family_share: Decimal = ZERO


def compute_net(employee: Employee, state: StateInputs, rules: CalcRules,
                electric_gross_up: Decimal = ZERO) -> Breakdown:
    salary = employee.base + employee.commissions + employee.bonus
    cash_gross = salary + state.maintenance + electric_gross_up

    pension = _components(employee, rules.pensionable_components) * employee.pension_rate
    study_fund = _components(employee, rules.study_fund_components) * employee.study_fund_rate

    taxable = cash_gross + state.use_value
    pension_credit = rules.pension_credit_rate * min(
        pension, rules.pension_credit_max_rate * rules.pension_credit_income_ceiling
    )
    credits = employee.credit_points * rules.credit_point_value + pension_credit
    income_tax = max(ZERO, income_tax_before_credits(taxable, rules) - credits)

    national_insurance = two_tier(taxable, rules.national_insurance)
    health = two_tier(taxable, rules.health)

    net = (cash_gross - income_tax - national_insurance - health
           - pension - study_fund - state.copay - state.family_share)

    return Breakdown(
        label=state.label,
        cash_gross=cash_gross,
        use_value=state.use_value,
        maintenance=state.maintenance,
        electric_gross_up=electric_gross_up,
        pension=pension,
        study_fund=study_fund,
        income_tax=income_tax,
        national_insurance=national_insurance,
        health=health,
        copay=state.copay,
        family_share=state.family_share,
        net=net,
        net_display=to_shekel(net),
    )


def solve_gross_up(employee: Employee, state: StateInputs, rules: CalcRules,
                   target_net: Decimal) -> Decimal:
    """מוצא את רכיב הברוטו שמעלה את הנטו בדיוק ב-target_net (חיפוש בינארי).

    הנטו עולה באופן מונוטוני ברכיב, ולכן החיפוש תמיד מתכנס. 100 איטרציות
    על טווח של עד פי 10 מהיעד נותנות דיוק הרבה מתחת לאגורה.
    """
    if target_net <= ZERO:
        return ZERO
    base_net = compute_net(employee, state, rules).net
    low, high = ZERO, target_net * 10
    for _ in range(100):
        mid = (low + high) / 2
        if compute_net(employee, state, rules, mid).net - base_net < target_net:
            low = mid
        else:
            high = mid
    return high
