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
    reply = turn.reply
    # מהתרחיש הרשמי: עלות לחברה מול תקציב הקבוצה, וההפרש לא משולם בשכר
    assert f"עלות לחברה {money(atto.monthly_cost)} מול תקציב קבוצה ג {money(budget)} — ההפרש מהתקציב לא משולם בשכר" \
        in reply
    assert "נטו משוער: היום 11,032 ₪, בחלופה 11,693 ₪ — שינוי של +661 ₪ בחודש." in reply
    assert "הנה ההשוואה (נטו משוער היום: 11,032 ₪):" in reply
    use = next(d for d in r["drivers"] if d["code"] == "USE_VALUE")
    electric = next(d for d in r["drivers"] if d["code"] == "ELECTRIC_ADDED")
    why = (f"למה? מתווסף החזר חשמל של {money(electric['amount_display'])} נטו; "
           f"שווי השימוש נמוך ב-{money(-D(use['amount']))} ולכן המס יורד ב-"
           f"{money(-D(use['tax_change']))}.")
    assert why in reply
    assert "החזר חשמל 250 ₪ נטו" in reply and r["copay_pending"] is True


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
    family = next(d for d in r["drivers"] if d["code"] == "FAMILY_SHARE")
    assert f"חלק העובד במסלול המשפחתי עולה ב-{money(family['amount_display'])}" in turn.reply
    assert r["cost"]["under_budget"] is False and "לא משולם בשכר" not in turn.reply


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
    assert f"• {model3.label}: לא ניתן — {result.reason}" in text
    assert "עלות לחברה" not in text and "למה?" not in text
