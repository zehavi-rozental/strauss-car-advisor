"""אימות מול שטראוס: מריץ את ששת העובדים לדוגמה ומשווה לנטו הייחוס.

הרצה:  python -m app.verify
(בהמשך אותו חישוב יוצג בעמוד ‎/verify במסך ההדגמה.)
"""
from __future__ import annotations

import sys

from app.modules.calculation.public import evaluate
from app.modules.rules.public import (
    load_snapshot,
    occupation_rule,
    parse_reference_state,
    reference_employee,
)
from app.shared.settings import get_settings


def run() -> list[dict]:
    snap = load_snapshot(get_settings().config_dir)
    refs = {r.employee_id: r for r in snap.reference_results}
    rows = []
    for emp in snap.reference_employees:
        ref = refs[emp.employee_id]
        current = parse_reference_state(snap, emp.current_car, current_copay=emp.current_copay, for_current=True)
        alternative = parse_reference_state(snap, ref.alternative_label)
        res = evaluate(reference_employee(emp), current, alternative, snap.calc,
                       occupation_rule(snap.occupations.get(emp.occupation)))
        ok_today = res.today is not None and res.today.net_display == ref.net_today
        ok_alt = res.alternative is not None and res.alternative.net_display == ref.net_alternative
        rows.append(dict(id=emp.employee_id, name=emp.name, today=ref.today_label,
                         alternative=ref.alternative_label,
                         engine_today=res.today.net_display if res.today else None,
                         strauss_today=ref.net_today,
                         engine_alt=res.alternative.net_display if res.alternative else None,
                         strauss_alt=ref.net_alternative, delta=res.delta_display,
                         ok_today=ok_today, ok_alt=ok_alt))
    return rows


def main() -> None:
    rows = run()
    passed = sum(r["ok_today"] + r["ok_alt"] for r in rows)
    for r in rows:
        mark = lambda ok: "✓" if ok else "✗"  # noqa: E731
        print(f"{r['id']} {r['name']:<12} היום {r['engine_today']:>7} {mark(r['ok_today'])}   "
              f"חלופה {r['engine_alt']:>7} {mark(r['ok_alt'])}   הפרש {r['delta']:+}")
    print(f"\n{passed}/{len(rows) * 2} ערכי נטו תואמים לשטראוס")
    sys.exit(0 if passed == len(rows) * 2 else 1)


if __name__ == "__main__":
    main()
