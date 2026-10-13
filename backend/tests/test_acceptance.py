"""בדיקות קבלה: ששת העובדים לדוגמה של שטראוס (גיליונות "עובדים לדוגמה" ו"חישובים").

הנתונים נטענים מהאקסל עצמו — קובץ חדש מביא בדיקות חדשות בלי לגעת בקוד.
"""
from __future__ import annotations

import pytest

from app.modules.calculation.public import Status, evaluate
from app.modules.rules.public import (
    load_snapshot,
    occupation_rule,
    parse_reference_state,
    reference_employee,
)
from app.shared.money import to_shekel

from .conftest import CONFIG

_SNAP = load_snapshot(CONFIG)
_RESULTS = {r.employee_id: r for r in _SNAP.reference_results}
CASES = [(e, _RESULTS[e.employee_id]) for e in _SNAP.reference_employees]


def _run(snapshot, emp, ref):
    current = parse_reference_state(snapshot, emp.current_car, current_copay=emp.current_copay, for_current=True)
    alternative = parse_reference_state(snapshot, ref.alternative_label)
    return evaluate(reference_employee(emp), current, alternative, snapshot.calc,
                    occupation_rule(snapshot.occupations.get(emp.occupation)))


@pytest.mark.acceptance
@pytest.mark.parametrize("emp,ref", CASES, ids=[e.employee_id for e, _ in CASES])
def test_reference_employee(snapshot, emp, ref):
    result = _run(snapshot, emp, ref)

    assert result.status == Status.OK
    assert result.eligible is ref.eligible
    assert result.reason == ref.reason

    today, alt = result.today, result.alternative
    # נטו והפרש — לשקל
    assert today.net_display == ref.net_today
    assert alt.net_display == ref.net_alternative
    assert result.delta_display == ref.delta
    # שלבי ביניים — לשקל (הייחוס באקסל מעוגל לשקל)
    assert to_shekel(today.income_tax) == ref.tax_today
    assert to_shekel(alt.income_tax) == ref.tax_alternative
    assert to_shekel(today.national_insurance + today.health) == ref.social_today
    assert to_shekel(alt.national_insurance + alt.health) == ref.social_alternative
    assert to_shekel(alt.electric_gross_up) == ref.electric_gross
    assert to_shekel(alt.family_share) == ref.family_share


@pytest.mark.acceptance
def test_all_six_present(snapshot):
    assert len(CASES) == 6, "בקובץ שטראוס יש 6 עובדים לדוגמה"
