"""שאלות מקובצות כמו בתרחיש הרשמי של האתגר: שלוש שאלות, כל אחת שואלת רק מה שעדיין חסר."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, without_cta

# מסמך האתגר — התרחיש הרשמי
SCENARIO_1 = "קבוצה ג, 14,200 ₪"
SCENARIO_2 = "עמלות משתנות כ-1,000 ₪, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי. ויש לי היום רכב חברה."
# נוסח השאלות (Q1 בלי צורת לוכסן — ראו templates._group_or_occupation)
Q1 = "מה קבוצת הזכאות שלכם (א-ד), או מה התפקיד שלכם? ומה שכר הבסיס החודשי שלכם?"
Q2 = ("יש רכיבי שכר נוספים — עמלות, בונוסים? ואילו הפרשות יורדות לכם: פנסיה, קרן השתלמות? "
      "כמה נקודות זיכוי יש לכם? וכמה ילדים עד גיל 23?")
Q3 = "איזה רכב צמוד יש לכם היום, וכמה משלמים עליו בחודש?"


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
    assert Q1 in first.reply and first.reply.endswith(".")
    second = svc.send(first.conversation_id, token, SCENARIO_1)           # תור 2
    assert without_cta(second.reply).endswith(Q2)
    third = svc.send(first.conversation_id, token, SCENARIO_2)            # תור 3: שאלת הרכב
    assert third.stage == Stage.COLLECT_CURRENT_CAR
    assert without_cta(third.reply).endswith(Q3 + "\nחסר עוד: בונוס, ילדים.")   # מה שנשאר מ-Q2: נאמר, לא נשמט
    p = svc.view(first.conversation_id, token).state.profile
    assert (p.group, p.base, p.commissions, p.pension_percent, p.study_fund_percent, p.credit_points) == (
        "ג", 14200, 1000, 6, Decimal("2.5"), Decimal("2.25"))
    assert p.approximate_fields == ["commissions"] and p.bonus is None and p.children is None


def test_scenario_continues_with_the_leftovers_and_the_car(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    for m in (SCENARIO_1, SCENARIO_2, "אין בונוס, 2 ילדים", "יש לי קורולה קרוס", "300", "אטו 3"):
        turn = svc.send(turn.conversation_id, token, m)
    assert turn.stage == Stage.PRESENTED and turn.results[0].label == "ATTO 3 (2025)"


def test_noa_skips_q2_when_everything_was_given(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, "
                                                 "פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים")
    assert "זיהיתי: מנהל/ת מוצר, קבוצה ג.\n\n" + Q3 in turn.reply   # אישור התפקיד, ואז Q3
    turn = svc.send(turn.conversation_id, token, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש")
    assert "\n\nאיזה רכבים להשוות" in turn.reply and "מה ההשתתפות" not in turn.reply   # רק מה שחסר
    turn = svc.send(turn.conversation_id, token, "אני רוצה לבדוק את BYD אטו 3 החשמלי")
    assert turn.results[0].result["delta_display"] == "661"


def test_each_group_names_only_what_is_missing(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר")
    assert without_cta(turn.reply) == "זיהיתי: מנהל/ת מוצר, קבוצה ג.\n\nמה שכר הבסיס החודשי שלכם (ברוטו)?"   # Q1: רק השכר
    turn = svc.send(turn.conversation_id, token, "18,500, פנסיה 6%, 2 ילדים")
    assert without_cta(turn.reply).endswith("יש רכיבי שכר נוספים — עמלות, בונוסים? ואילו הפרשות יורדות לכם: "
                                            "קרן השתלמות? כמה נקודות זיכוי יש לכם?")   # Q2: בלי פנסיה וילדים


def test_group_only_asks_the_occupation_when_opting_out(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    for m in (SCENARIO_1, "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 2 ילדים",
              "יש לי קורולה קרוס", "300"):
        turn = svc.send(turn.conversation_id, token, m)
    assert "איזה רכבים להשוות" in turn.reply                                   # התפקיד לא נשאל עד עכשיו
    turn = svc.send(turn.conversation_id, token, "ויתור")
    assert without_cta(turn.reply) == "בחרתם להשוות: ויתור על רכב.\n\nכדי לבדוק אם אפשר לוותר על הרכב, מה התפקיד שלכם?"
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר")
    assert turn.stage == Stage.PRESENTED and turn.results[0].result["status"] == "OK"
    assert svc.view(turn.conversation_id, token).state.profile.occupation == "מנהל/ת מוצר"


def test_a_full_q3_answer_is_read_with_the_q2_leftovers(sessions):
    """תשובה ישירה ל-Q3 יחד עם מה שנשאר מ-Q2: הכול נשמר, ונשאלת רק החלופה."""
    svc = service(sessions)
    turn, token = svc.start()
    for m in (SCENARIO_1, SCENARIO_2):
        turn = svc.send(turn.conversation_id, token, m)
    turn = svc.send(turn.conversation_id, token, "אין בונוס, 2 ילדים, יש לי קורולה קרוס ואני משתתפת ב-300")
    s = svc.view(turn.conversation_id, token).state
    assert (s.profile.bonus, s.profile.children, s.current.catalog_code, s.current.copay) == (0, 2, "CAR-09", 300)
    assert turn.stage == Stage.CHOOSE_OPTIONS
    assert "\n\nאיזה רכבים להשוות" in turn.reply
    assert "לא זיהיתי" not in turn.reply and "מה ההשתתפות" not in turn.reply and "חסר עוד" not in turn.reply


# ---------- Q3 שואלת על הרכב של היום ועל החלופות: התפקיד נקבע בקוד, לא מנחשים ----------

ASK_ATTO = "ATTO 3 (2025) הוא הרכב שלכם היום, או חלופה שתרצו לבדוק?"


def at_q3(svc):
    turn, token = svc.start()
    for m in (SCENARIO_1, SCENARIO_2):
        turn = svc.send(turn.conversation_id, token, m)
    return turn, token


def test_a_bare_car_name_at_q3_is_the_car_today(sessions):
    """The question on screen asks only about the car today: a bare name answers it (no role question)."""
    svc = service(sessions)
    turn, token = at_q3(svc)
    turn = svc.send(turn.conversation_id, token, "ATTO 3")
    assert turn.reply.startswith("זיהיתי: ATTO 3 (2025)")
    s = svc.view(turn.conversation_id, token).state
    assert s.current.catalog_code == "CAR-10" and s.selected == [] and s.pending_clarification is None


def conflict(sessions):
    """The LLM matched "ATTO 3" as the car today although the words say "רוצה לבדוק": the bot asks."""
    fake = FakeLLM()
    fake.turn(call("save_profile", eligibility_group="ג", base_salary=14200))
    fake.turn(call("save_profile", commissions=1000, pension_percent=6, study_fund_percent=2.5,
                   credit_points=2.25, approximate_fields=["commissions"]))
    fake.turn(call("match_current_car", model="ATTO 3"))
    svc = service(sessions, LLMChain([fake]))
    turn, token = at_q3(svc)
    turn = svc.send(turn.conversation_id, token, "אני רוצה לבדוק ATTO 3")
    assert without_cta(turn.reply) == ASK_ATTO and turn.expects["field"] == "clarification"
    assert [c["label"] for c in turn.expects["choices"]] == ["הרכב שלי היום", "חלופה לבדוק"]
    return svc, turn, token


def test_the_alternative_button_selects_the_car(sessions):
    svc, turn, token = conflict(sessions)
    turn = svc.send_choice(turn.conversation_id, token, ["alternative"])
    s = svc.view(turn.conversation_id, token).state
    assert s.selected == ["CAR-10"] and s.current.source is None and s.pending_clarification is None
    assert turn.reply.startswith("בחרתם להשוות: ATTO 3 (2025).\n\nאיזה רכב צמוד יש לכם היום")   # עכשיו רק הרכב של היום

def test_an_alternative_cue_selects_the_car_as_an_alternative(sessions):
    svc = service(sessions)
    turn, token = at_q3(svc)
    turn = svc.send(turn.conversation_id, token, "אני רוצה לבדוק ATTO 3")
    s = svc.view(turn.conversation_id, token).state
    assert s.selected == ["CAR-10"] and s.current.source is None
    turn = svc.send(turn.conversation_id, token, "קורולה קרוס")               # עכשיו נשאל רק הרכב של היום
    assert svc.view(turn.conversation_id, token).state.current.catalog_code == "CAR-09"


def test_a_current_cue_sets_the_current_car(sessions):
    svc = service(sessions)
    turn, token = at_q3(svc)
    turn = svc.send(turn.conversation_id, token, "יש לי ATTO 3")
    s = svc.view(turn.conversation_id, token).state
    assert s.current.catalog_code == "CAR-10" and s.selected == []


def test_an_llm_call_against_the_cue_is_blocked_and_the_bot_asks(sessions):
    fake = FakeLLM()
    fake.turn(call("save_profile", eligibility_group="ג", base_salary=14200))
    fake.turn(call("save_profile", commissions=1000, pension_percent=6, study_fund_percent=2.5,
                   credit_points=2.25, approximate_fields=["commissions"]))
    fake.turn(call("match_current_car", model="ATTO 3"))                       # "רוצה לבדוק" = חלופה
    svc = service(sessions, LLMChain([fake]))
    turn, token = at_q3(svc)
    turn = svc.send(turn.conversation_id, token, "אני רוצה לבדוק ATTO 3")
    assert without_cta(turn.reply) == ASK_ATTO
    s = svc.view(turn.conversation_id, token).state
    assert s.current.source is None and s.selected == []                       # גם הקורא לא רץ כגיבוי
    assert s.pending_clarification.catalog_code == "CAR-10"


def test_an_llm_calculate_for_the_current_car_is_blocked(sessions):
    fake = FakeLLM()
    fake.turn(call("save_profile", eligibility_group="ג", base_salary=14200))
    fake.turn(call("save_profile", commissions=1000, pension_percent=6, study_fund_percent=2.5,
                   credit_points=2.25, approximate_fields=["commissions"]))
    fake.turn(call("calculate", options=["ATTO 3"]))                           # "יש לי" = הרכב של היום
    svc = service(sessions, LLMChain([fake]))
    turn, token = at_q3(svc)
    turn = svc.send(turn.conversation_id, token, "יש לי ATTO 3")
    assert without_cta(turn.reply) == ASK_ATTO
    assert svc.view(turn.conversation_id, token).state.selected == []


def test_a_stated_copay_makes_a_single_car_the_current_one(sessions):
    svc = service(sessions)
    turn, token = at_q3(svc)
    turn = svc.send(turn.conversation_id, token, "קורולה קרוס, אין לי השתתפות")
    s = svc.view(turn.conversation_id, token).state
    assert (s.current.catalog_code, s.current.copay) == ("CAR-09", 0)
    assert s.pending_clarification is None and "חלופה שתרצו לבדוק?" not in turn.reply


def test_a_year_other_than_the_catalog_makes_a_single_car_the_current_one(sessions):
    svc = service(sessions)
    turn, token = at_q3(svc)
    turn = svc.send(turn.conversation_id, token, "COROLLA CROSS 2023")
    s = svc.view(turn.conversation_id, token).state
    assert (s.current.source, s.current.label) == ("tax", "COROLLA CROSS (2023)")
    assert s.pending_clarification is None and "חלופה שתרצו לבדוק?" not in turn.reply


def test_the_my_car_today_button_sets_the_current_car(sessions):
    svc, turn, token = conflict(sessions)
    turn = svc.send_choice(turn.conversation_id, token, ["mine"])
    s = svc.view(turn.conversation_id, token).state
    assert s.current.catalog_code == "CAR-10" and s.selected == [] and s.pending_clarification is None


def test_the_role_question_is_not_asked_twice_for_the_same_car(sessions):
    """A second conflict on the same car is not asked again: the parser acts on the words."""
    svc, turn, token = conflict(sessions)
    svc.agent.llm.entries[0].provider.turn(call("match_current_car", model="ATTO 3"))
    turn = svc.send(turn.conversation_id, token, "אני רוצה לבדוק ATTO 3")
    assert "חלופה שתרצו לבדוק?" not in turn.reply
    s = svc.view(turn.conversation_id, token).state
    assert s.pending_clarification is None and s.current.source is None