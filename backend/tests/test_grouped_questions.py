"""שאלות מקובצות כמו בתרחיש הרשמי של האתגר: שלוש שאלות, כל אחת שואלת רק מה שעדיין חסר."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

# מסמך האתגר — התרחיש הרשמי
SCENARIO_1 = "קבוצה ג, 14,200 ₪"
SCENARIO_2 = "עמלות משתנות כ-1,000 ₪, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי. ויש לי היום רכב חברה."
# נוסח השאלות (Q1 בלי צורת לוכסן — ראו templates._group_or_occupation)
Q1 = "מה קבוצת הזכאות שלך (א-ד), או מה התפקיד שלך? ומה שכר הבסיס החודשי שלך?"
Q2 = ("יש רכיבי שכר נוספים — עמלות, בונוסים? ואילו הפרשות יורדות לך: פנסיה, קרן השתלמות, נקודות זיכוי? "
      "וכמה ילדים עד גיל 23?")
Q3 = ("מה הרכב היום (דגם ושנת רישום) ומה ההשתתפות שלך בו? ואיזה רכב מעניין אותך — הנה הקטלוג בקבוצה שלך, "
      "ואפשר גם מקבוצה נמוכה יותר.")


def service(sessions, llm=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())


def scenario_llm():
    fake = FakeLLM()
    fake.turn(call("save_profile", eligibility_group="ג", base_salary=14200))
    fake.turn(call("save_profile", commissions=1000, pension_percent=6, study_fund_percent=2.5,
                   credit_points=2.25, approximate_fields=["commissions"]))
    return LLMChain([fake])


@pytest.mark.parametrize("llm", ["templates", "llm_first"])
def test_official_scenario_reaches_the_car_question_in_three_bot_turns(sessions, llm):
    svc = service(sessions, scenario_llm() if llm == "llm_first" else None)
    first, token = svc.start()                                            # תור 1
    assert first.reply.endswith(Q1)
    second = svc.send(first.conversation_id, token, SCENARIO_1)           # תור 2
    assert second.reply == Q2
    third = svc.send(first.conversation_id, token, SCENARIO_2)            # תור 3: שאלת הרכב
    assert third.stage == Stage.COLLECT_CURRENT_CAR
    lines = third.reply.splitlines()
    assert lines[0] == Q3 and lines[1].startswith("1. ")
    assert lines[-1] == "ועוד: יש לך בונוס חודשי? כמה ילדים עד גיל 23 יש לך?"   # מה שנשאר מ-Q2
    p = svc.view(first.conversation_id, token).state.profile
    assert (p.group, p.base, p.commissions, p.pension_percent, p.study_fund_percent, p.credit_points) == (
        "ג", 14200, 1000, 6, Decimal("2.5"), Decimal("2.25"))
    assert p.approximate_fields == ["commissions"] and p.bonus is None and p.children is None


def test_scenario_continues_with_the_leftovers_and_the_car(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    for m in (SCENARIO_1, SCENARIO_2, "אין בונוס, 2 ילדים", "קורולה קרוס", "300", "אטו 3"):
        turn = svc.send(turn.conversation_id, token, m)
    assert turn.stage == Stage.PRESENTED and turn.results[0].label == "ATTO 3 (2025)"


def test_noa_skips_q2_when_everything_was_given(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, "
                                                 "פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים")
    assert turn.reply.startswith(Q3)
    turn = svc.send(turn.conversation_id, token, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש")
    assert "\n\nאיזה רכב מעניין אותך" in turn.reply and "מה ההשתתפות" not in turn.reply   # רק מה שחסר
    turn = svc.send(turn.conversation_id, token, "אני רוצה לבדוק את BYD אטו 3 החשמלי")
    assert turn.results[0].result["delta_display"] == "661"


def test_each_group_names_only_what_is_missing(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר")
    assert turn.reply == "מה שכר הבסיס החודשי שלך (ברוטו)?"                # Q1: רק השכר
    turn = svc.send(turn.conversation_id, token, "18,500, פנסיה 6%, 2 ילדים")
    assert turn.reply == ("יש רכיבי שכר נוספים — עמלות, בונוסים? ואילו הפרשות יורדות לך: "
                          "קרן השתלמות, נקודות זיכוי?")                     # Q2: בלי פנסיה וילדים


def test_group_only_asks_the_occupation_when_opting_out(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    for m in (SCENARIO_1, "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 2 ילדים",
              "קורולה קרוס", "300"):
        turn = svc.send(turn.conversation_id, token, m)
    assert "ויתור על רכב" in turn.reply                                    # התפקיד לא נשאל עד עכשיו
    turn = svc.send(turn.conversation_id, token, "ויתור")
    assert turn.reply == "כדי לבדוק אם אפשר לוותר על הרכב, מה התפקיד שלך?"
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר")
    assert turn.stage == Stage.PRESENTED and turn.results[0].result["status"] == "OK"
    assert svc.view(turn.conversation_id, token).state.profile.occupation == "מנהל/ת מוצר"
