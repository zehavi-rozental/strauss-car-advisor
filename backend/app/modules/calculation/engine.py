"""evaluate(): השוואה בין המצב היום לחלופה אחת.

שלבים (סעיף 6 באיפיון):
 1. בדיקת שלמות הפרופיל.
 2. זכאות לחלופה (קבוצה / מסלול משפחתי / ויתור לפי עיסוק).
 3. השלמת נתוני החלופה (אחזקה לפי עיסוק, חלק משפחתי).
 4. איסוף שדות חסרים → INCOMPLETE עם השוואה חלקית.
 5. נטו היום, נטו בחלופה (+ גילום חשמל), הפרש ושורות הסבר.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.shared.money import ZERO, to_shekel

from .models import (
    FAMILY_GROUP,
    Breakdown,
    CalcRules,
    CarState,
    CopayStatus,
    Drive,
    Employee,
    Line,
    OccupationRule,
    OptOutState,
    ScenarioResult,
    State,
    Status,
)
from .tax import StateInputs, compute_net, solve_gross_up

ENGINE_VERSION = "1.0.0"

REASON_OWN_OR_LOWER = "הרכב מהקבוצה שלך או נמוכה ממנה"
REASON_HIGHER_GROUP = "הרכב מקבוצה גבוהה מהקבוצה שלך"
REASON_OPT_OUT_OK = "העיסוק מאפשר ויתור"
REASON_OPT_OUT_MANDATORY = "בעיסוק שלך הרכב הוא חובה, ולכן אי אפשר לוותר עליו"
REASON_OPT_OUT_NOT_ALLOWED = "העיסוק שלך לא מאפשר ויתור על הרכב"
REASON_OCCUPATION_UNKNOWN = "העיסוק לא נמצא בטבלת העיסוקים — נשלחה שאלה לנציג"


def _is_electric(state: State, rules: CalcRules) -> bool:
    if not isinstance(state, CarState):
        return False
    if state.drive == Drive.ELECTRIC:
        return True
    return state.drive == Drive.PLUG_IN and rules.plug_in_counts_as_electric


def _family_share(employee: Employee, state: State, rules: CalcRules) -> Optional[Decimal]:
    """חלק העובד בפער שמעל תקציב הקבוצה — רק לרכב מהמסלול המשפחתי."""
    if not isinstance(state, CarState) or state.group != FAMILY_GROUP:
        return ZERO
    if state.monthly_cost is None or employee.group is None:
        return None
    budget = rules.budgets[employee.group]
    gap = max(ZERO, state.monthly_cost - budget)
    return gap * rules.family_gap_employee_share


def _missing_employee_fields(employee: Employee) -> list[str]:
    missing = []
    for field, code in (("group", "EMPLOYEE_GROUP"), ("base", "EMPLOYEE_BASE_SALARY"),
                        ("pension_rate", "EMPLOYEE_PENSION_RATE"),
                        ("credit_points", "EMPLOYEE_CREDIT_POINTS")):
        if getattr(employee, field) is None:
            missing.append(code)
    return missing


def _check_eligibility(employee: Employee, alternative: State, rules: CalcRules,
                       occupation: Optional[OccupationRule]) -> tuple[Optional[bool], str, list[str]]:
    """מחזיר (זכאי?, סיבה, שדות חסרים). None = אי אפשר להכריע עדיין."""
    if isinstance(alternative, OptOutState):
        if occupation is None:
            return None, REASON_OCCUPATION_UNKNOWN, ["OCCUPATION_OPT_OUT"]
        if occupation.car_mandatory:
            return False, REASON_OPT_OUT_MANDATORY, []
        if not occupation.opt_out_allowed:
            return False, REASON_OPT_OUT_NOT_ALLOWED, []
        return True, REASON_OPT_OUT_OK, []

    if alternative.group == FAMILY_GROUP:
        n = employee.eligible_children
        if n >= rules.family_min_children:
            share = int(rules.family_gap_employee_share * 100)
            return True, (f"מסלול ברוכת ילדים ({n} ילדים); "
                          f"על הפער מעל התקציב - {share}% עובד"), []
        return False, (f"המסלול המשפחתי מיועד ל-{rules.family_min_children} ילדים "
                       f"ומעלה עד גיל 23"), []

    if alternative.group is None:
        return False, "הרכב אינו בקטלוג החברה", []
    if employee.group is None:
        return None, "חסרה קבוצת הזכאות", ["EMPLOYEE_GROUP"]
    order = rules.group_order
    if order.index(alternative.group) <= order.index(employee.group):
        return True, REASON_OWN_OR_LOWER, []
    return False, REASON_HIGHER_GROUP, []


def _resolve_alternative(alternative: State, occupation: Optional[OccupationRule]) -> State:
    if isinstance(alternative, OptOutState) and occupation is not None and occupation.opt_out_allowed:
        return alternative.model_copy(update={"maintenance": occupation.maintenance})
    return alternative


def _state_inputs(employee: Employee, state: State, rules: CalcRules,
                  prefix: str) -> tuple[Optional[StateInputs], list[str]]:
    missing: list[str] = []
    if isinstance(state, OptOutState):
        if state.maintenance is None:
            missing.append(f"{prefix}_MAINTENANCE")
            return None, missing
        return StateInputs(label=state.label, maintenance=state.maintenance), missing

    if state.use_value is None:
        missing.append(f"{prefix}_USE_VALUE")
    if state.copay is None:
        missing.append(f"{prefix}_COPAY")
    family = _family_share(employee, state, rules)
    if family is None:
        missing.append(f"{prefix}_MONTHLY_COST")
    if missing:
        return None, missing
    return StateInputs(label=state.label, use_value=state.use_value, copay=state.copay,
                       family_share=family), missing


def _breakdown(employee: Employee, state: State, inputs: StateInputs,
               rules: CalcRules) -> Breakdown:
    if not _is_electric(state, rules):
        return compute_net(employee, inputs, rules)
    if rules.electric_method == "gross_up":
        extra = solve_gross_up(employee, inputs, rules, rules.electric_net_bonus)
    else:
        extra = rules.electric_net_bonus
    return compute_net(employee, inputs, rules, extra)


def _lines(today_state: State, alt_state: State, today: Optional[Breakdown],
           alt: Optional[Breakdown], rules: CalcRules) -> list[Line]:
    lines: list[Line] = []

    def val(b: Optional[Breakdown], attr: str) -> Optional[Decimal]:
        return None if b is None else getattr(b, attr)

    def add(code: str, label: str, attr: str, note: Optional[str] = None) -> None:
        before, after = val(today, attr), val(alt, attr)
        if (before or ZERO) == (after or ZERO) and note is None:
            return
        lines.append(Line(code=code, label=label, before=before, after=after, note=note))

    add("USE_VALUE", "שווי שימוש", "use_value")
    add("MAINTENANCE", "אחזקת רכב (חייבת במס, לא פנסיונית)", "maintenance")
    add("COPAY", "השתתפות עובד ברכב", "copay")
    add("FAMILY_SHARE", "חלק העובד בפער המסלול המשפחתי", "family_share")

    if _is_electric(alt_state, rules) or _is_electric(today_state, rules):
        note = (f"+{int(rules.electric_net_bonus)} ₪ נטו החזר חשמל (מגולם)"
                if rules.electric_method == "gross_up"
                else f"{int(rules.electric_net_bonus)} ₪ ברוטו החזר חשמל")
        add("ELECTRIC", "החזר חשמל — רכיב ברוטו", "electric_gross_up", note=note)
    add("INCOME_TAX", "מס הכנסה", "income_tax")
    lines.append(Line(code="SOCIAL", label="ביטוח לאומי ובריאות",
                      before=None if today is None else today.national_insurance + today.health,
                      after=None if alt is None else alt.national_insurance + alt.health))
    return lines


def evaluate(employee: Employee, current: State, alternative: State, rules: CalcRules,
             occupation: Optional[OccupationRule] = None) -> ScenarioResult:
    assumptions = [f"חישוב חודשי לפי לוחות {rules.tax_year} (קובץ כללים {rules.rules_version})"]
    common = dict(rules_version=rules.rules_version, extensions_version=rules.extensions_version,
                  engine_version=ENGINE_VERSION)

    missing = _missing_employee_fields(employee)
    if missing:
        return ScenarioResult(status=Status.INCOMPLETE, eligible=None,
                              reason="חסרים פרטי שכר כדי לחשב", missing_fields=missing,
                              assumptions=assumptions, **common)

    eligible, reason, missing = _check_eligibility(employee, alternative, rules, occupation)
    if eligible is False:
        return ScenarioResult(status=Status.NOT_ELIGIBLE, eligible=False, reason=reason,
                              assumptions=assumptions, **common)

    alternative = _resolve_alternative(alternative, occupation)

    today_inputs, m1 = _state_inputs(employee, current, rules, "CURRENT")
    alt_inputs, m2 = (None, []) if eligible is None else _state_inputs(employee, alternative, rules, "NEW_CAR")
    missing = missing + m1 + [m.replace("NEW_CAR_MAINTENANCE", "OCCUPATION_OPT_OUT") for m in m2]

    today = _breakdown(employee, current, today_inputs, rules) if today_inputs else None
    alt = _breakdown(employee, alternative, alt_inputs, rules) if alt_inputs else None

    for state in (current, alternative):
        if isinstance(state, CarState) and state.copay_status == CopayStatus.CATALOG_DEFAULT_PENDING:
            assumptions.append(f"השתתפות ב-{state.label} לפי הקטלוג — ממתינה לאישור נציג")
        if isinstance(state, CarState) and state.drive == Drive.PLUG_IN and not rules.plug_in_counts_as_electric:
            assumptions.append(f"{state.label} הוא פלאג-אין, ולכן אינו מקבל החזר חשמל")

    status = Status.OK if (today and alt and not missing) else Status.INCOMPLETE
    delta = alt.net_display - today.net_display if (today and alt) else None

    return ScenarioResult(
        status=status,
        eligible=eligible,
        reason=reason,
        missing_fields=missing,
        today=today,
        alternative=alt,
        delta_display=delta,
        lines=_lines(current, alternative, today, alt, rules),
        assumptions=assumptions,
        **common,
    )


def evaluate_state(employee: Employee, state: State, rules: CalcRules) -> Optional[Breakdown]:
    """נטו של מצב בודד (לדירוג חלופות ולמסך האימות)."""
    inputs, missing = _state_inputs(employee, state, rules, "STATE")
    if missing or _missing_employee_fields(employee):
        return None
    return _breakdown(employee, state, inputs, rules)


__all__ = ["evaluate", "evaluate_state", "ENGINE_VERSION", "to_shekel"]
