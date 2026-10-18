"""כל חלופה מוסברת בשפה פשוטה, כמו "למה?" בתרחיש הרשמי. כל מספר — משדות המנוע, לא מחושב בתבנית."""
from __future__ import annotations

from decimal import Decimal

from app.modules.calculation.public import CopayStatus, evaluate
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.conversation.templates import results_text
from app.modules.conversation.stage import ConversationState, OptionResult
from app.modules.rules.public import RulesStore, car_state, load_snapshot, occupation_rule, reference_employee
from app.shared.money import to_shekel

from .conftest import CONFIG

PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, {children} ילדים")
CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"


def compare(sessions, choice, children=2):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    turn, token = svc.start()
    for m in (PROFILE.format(children=children), CAR, choice):
        turn = svc.send(turn.conversation_id, token, m)
    return turn


def money(v):
    return f"{to_shekel(Decimal(str(v))):,} ₪"


def D(v):
    return Decimal(str(v))


def test_atto_3_is_explained_in_plain_lines(sessions):
    snap = load_snapshot(CONFIG)
    atto = next(c for c in snap.catalog if c.catalog_code == "CAR-10")
    budget = snap.calc.budgets["ג"]
    turn = compare(sessions, "אטו 3")
    r = turn.results[0].result
    assert r["cost"]["monthly_cost"] == str(atto.monthly_cost) and r["cost"]["budget"] == str(budget)
    assert r["cost"]["under_budget"] is (atto.monthly_cost < budget)
    # the chat text is two short lines; the detail lives in the structured result
    assert turn.reply == "בדקתי את ATTO 3 (2025). הנטו שלך יגדל ב-661 ₪ בחודש.\nממתין לאישור נציג על ההשתתפות."
    assert r["why"] == "שווי השימוש נמוך יותר, ולכן המס יקטן." and r["copay_pending"] is True


def test_every_line_has_whole_shekel_display_values(sessions):
    r = compare(sessions, "אטו 3").results[0].result
    for line in r["lines"]:
        for exact, shown in (("before", "before_display"), ("after", "after_display"), ("change", "change_display")):
            if line[exact] is not None:
                assert D(line[shown]) == to_shekel(D(line[exact]))
    use = next(line for line in r["lines"] if line["code"] == "USE_VALUE")
    assert D(use["change"]) == D(use["after"]) - D(use["before"])


def test_family_track_shows_the_family_share(sessions):
    turn = compare(sessions, "סנטה פה", children=4)
    r = turn.results[0].result
    assert any(d["code"] == "FAMILY_SHARE" for d in r["drivers"])
    assert r["cost"]["under_budget"] is False and r["why"].endswith(".")
    assert "יקטן ב-1,047 ₪" in turn.reply


def test_not_eligible_option_has_no_cost_or_why_lines():
    """רכב מקבוצה גבוהה (נועה בקבוצה ג, MODEL 3 בקבוצה ד) — רק הסיבה, בלי עלות ובלי "למה?"."""
    snap = load_snapshot(CONFIG)
    ref = next(e for e in snap.reference_employees if e.employee_id == "E-001")
    today = next(c for c in snap.catalog if c.catalog_code == "CAR-09")
    model3 = next(c for c in snap.catalog if c.catalog_code == "CAR-14")
    result = evaluate(reference_employee(ref), car_state(today, copay=Decimal(300), copay_status=CopayStatus.EMPLOYEE_STATED),
                      car_state(model3), snap.calc, occupation_rule(snap.occupations.get(ref.occupation)))
    assert result.status.value == "NOT_ELIGIBLE" and result.cost is None and result.drivers == []
    state = ConversationState(results=[OptionResult(option_id="CAR-14", label=model3.label,
                                                    result=result.model_dump(mode="json"))])
    text = results_text(state)
    assert text == f"{model3.label} לא זמין לך: {result.reason}"


def test_drivers_add_up_to_the_net_change_for_every_reference_employee_and_alternative():
    """לכל אחד מששת העובדים לדוגמה ולכל חלופה: סכום הגורמים = ההפרש בנטו (בדיוק, Decimal),
    וסכום ערכי התצוגה = ההפרש המוצג."""
    from app.modules.calculation.public import OptOutState
    from app.modules.rules.public import parse_reference_state

    snap = load_snapshot(CONFIG)
    checked = 0
    for emp in snap.reference_employees:
        current = parse_reference_state(snap, emp.current_car, current_copay=emp.current_copay, for_current=True)
        occupation = occupation_rule(snap.occupations.get(emp.occupation))
        alternatives = [car_state(c) for c in snap.catalog] + [OptOutState()]
        for alternative in alternatives:
            r = evaluate(reference_employee(emp), current, alternative, snap.calc, occupation)
            if r.today is None or r.alternative is None:
                continue
            assert sum((d.net_effect for d in r.drivers), Decimal(0)) == r.alternative.net - r.today.net,                 (emp.employee_id, alternative.label)
            assert sum((d.net_effect_display for d in r.drivers), Decimal(0)) == r.delta_display,                 (emp.employee_id, alternative.label)
            assert [abs(d.net_effect) for d in r.drivers] == sorted((abs(d.net_effect) for d in r.drivers),
                                                                    reverse=True)
            checked += 1
    assert checked >= 6 * 10
