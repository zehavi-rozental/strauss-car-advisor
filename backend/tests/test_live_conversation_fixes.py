"""באגים מבדיקה חיה בדפדפן (2026-10-15): הכפתור "הכי משתלם" ב-Q3, "אין" לשאלה המקובצת, זנב "ועוד" חוזר,
שורות אישור חסרות, ושאלה לנציג שנפתחה לפני שהפרופיל הושלם. כל הבדיקות בלי מודל שפה (תבניות + קורא)."""
from __future__ import annotations

import pytest

from app.modules.cases.public import Status as QuestionStatus
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.conversation.templates import CAR_AGAIN
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, without_cta

OCCUPATION = "מנהל/ת אזור מכירות"          # קבוצה ג בטבלת העיסוקים
PROFILE_REST = "פנסיה 6%, 2.25 נקודות זיכוי"


def service(sessions):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain())


def start_q2(svc):
    """כמו בשיחה החיה: תפקיד מהרשימה (כפתור), ואז שכר בסיס."""
    turn, token = svc.start()
    cid = turn.conversation_id
    svc.send_choice(cid, token, [OCCUPATION], "occupation")
    svc.send(cid, token, "15000")
    return cid, token


def bare_none(svc, cid, token):
    """23/11: a bare "אין" to the grouped salary question saves nothing and asks what it is about (buttons); the employee
    picks "אין עמלות, בונוס, קרן השתלמות וילדים" — what a bare "אין" used to record by itself."""
    asked = svc.send(cid, token, "אין")
    assert asked.expects["field"] == "none_choice"
    return svc.send_choice(cid, token, ["none_all"], "none_choice")


def open_questions(svc, cid):
    return svc.cases.list_for_conversation(cid, QuestionStatus.OPEN)


# ---------- 1. "הכי משתלם" לפני שהרכב של היום ידוע ----------

@pytest.mark.parametrize("how", ["button", "הכי משתלם", "מה הכי משתלם לי"])
def test_rank_before_the_current_car_is_kept_and_calculated_later(sessions, how):
    svc = service(sessions)
    cid, token = start_q2(svc)
    svc.send(cid, token, "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 2 ילדים")
    turn = (svc.send_choice(cid, token, ["rank"], "options") if how == "button" else svc.send(cid, token, how))
    assert CAR_AGAIN not in turn.reply and "לא הצלחתי" not in turn.reply
    assert "הכי משתלמות" in turn.reply                 # אישור: הבקשה נרשמה
    assert "איזה רכבים להשוות" not in turn.reply    # לא שואלים שוב על החלופות
    svc.send(cid, token, "יש לי קורולה קרוס")
    turn = svc.send(cid, token, "300")
    assert turn.stage == Stage.PRESENTED and turn.results


# ---------- 2. "אין" לשאלה המקובצת ----------

def test_a_bare_none_answers_the_zero_fields_and_asks_the_rest(sessions):
    svc = service(sessions)
    cid, token = start_q2(svc)
    turn = bare_none(svc, cid, token)
    assert turn.reply.startswith("רשמתי: אין עמלות, אין בונוס, אין קרן השתלמות, אין ילדים.")
    p = svc.view(cid, token).state.profile
    assert (p.commissions, p.bonus, p.study_fund_percent, p.children) == (0, 0, 0, 0)
    assert p.pension_percent is None and p.credit_points is None      # לא מניחים 0
    rest = without_cta(turn.reply).split("\n")[-1]
    assert "פנסיה" in rest                                             # רק מה שנשאר, פריט אחד בכל פעם (16/11)
    assert "עמלות" not in rest and "בונוס" not in rest and "ילדים" not in rest


def test_the_none_button_on_one_field_says_what_was_recorded(sessions):
    svc = service(sessions)
    cid, token = start_q2(svc)
    turn = svc.send_choice(cid, token, ["none"], "children")
    assert turn.reply.startswith("רשמתי: אין ילדים.")


# ---------- 3. זנב "ועוד" פעם אחת ----------

def test_what_is_left_after_a_bare_none_is_asked_before_the_car(sessions):
    """16/11: each salary field on its own — after "אין" (four zeros said), the pension and the credit points are asked
    next, never moved behind the car as a "חסר עוד" tail."""
    svc = service(sessions)
    cid, token = start_q2(svc)
    turn = bare_none(svc, cid, token)
    assert "חסר עוד" not in turn.reply and turn.stage == Stage.COLLECT_PROFILE and "פנסיה" in turn.reply
    turn = svc.send(cid, token, PROFILE_REST)
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and "חסר עוד" not in turn.reply
    svc.send_choice(cid, token, ["CAR-09"], "car")
    svc.send(cid, token, "300")
    turn = svc.send_choice(cid, token, ["CAR-10"], "options")
    assert turn.stage == Stage.PRESENTED and turn.results


# ---------- 4. שורות אישור ----------

def test_the_group_found_from_the_occupation_is_confirmed(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    turn = svc.send_choice(turn.conversation_id, token, [OCCUPATION], "occupation")
    assert turn.reply.startswith(f"זיהיתי: {OCCUPATION}, קבוצה ג.")


def test_the_chosen_alternatives_are_confirmed(sessions):
    svc = service(sessions)
    cid, token = start_q2(svc)
    bare_none(svc, cid, token)                         # 16/11: the salary first; the car question offers the cars
    svc.send(cid, token, PROFILE_REST)
    turn = svc.send_choice(cid, token, ["CAR-01", "CAR-12", "CAR-03"], "options")
    assert turn.reply.startswith("בחרתם להשוות: PICANTO (2025), TUCSON (2025), BYD DOLPHIN (2025).")


# ---------- 5. ההשתתפות של היום — מהעובד, לא מהנציג (כלל א, 22/10) ----------

def test_an_unknown_copay_is_recorded_and_asked_from_the_employee_never_the_rep(sessions):
    svc = service(sessions)
    cid, token = start_q2(svc)
    bare_none(svc, cid, token)
    svc.send(cid, token, PROFILE_REST)                # 16/11: the salary first, then the car
    svc.send_choice(cid, token, ["CAR-09"], "car")
    turn = svc.send(cid, token, "לא ידוע")   # a catalog car: three choices on screen; "לא ידוע" is typed
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and not open_questions(svc, cid)   # the ways to go on (16/11)
    turn = svc.send(cid, token, "לא ידוע")   # again: recorded
    assert turn.stage != Stage.WAITING_FOR_REP and not open_questions(svc, cid)
    assert "רשמתי שזה לא ידוע" in turn.reply and "איזה רכבים להשוות" in turn.reply   # נרשם; ממשיכים בשאלות
    turn = svc.send_choice(cid, token, ["CAR-10"], "options")
    # needed for the calculation now: asked again from the employee (where it is, or an estimate), never the rep
    assert turn.stage != Stage.WAITING_FOR_REP and not open_questions(svc, cid) and not turn.results
    assert "ההשתתפות ברכב של היום" in turn.reply and "בתלוש השכר" in turn.reply
    turn = svc.send(cid, token, "ההשתתפות שלי היום 300")
    assert turn.stage == Stage.PRESENTED and turn.results


# ---------- השיחה החיה, מקצה לקצה ----------

def test_the_live_conversation_reaches_a_result(sessions):
    """אותם צעדים כמו בשיחה החיה (כפתורים), עם השלמת הפרופיל ותשובת הנציג."""
    svc = service(sessions)
    turn, token = svc.start()
    cid = turn.conversation_id
    replies = [
        svc.send_choice(cid, token, [OCCUPATION], "occupation").reply,
        svc.send(cid, token, "15000").reply,
        bare_none(svc, cid, token).reply,               # 23/11: "אין" asks; the button records the zeros
        svc.send(cid, token, PROFILE_REST).reply,          # 16/11: what is left of the salary, before the car
        svc.send_choice(cid, token, ["CAR-09"], "car").reply,
        svc.send(cid, token, "לא ידוע").reply,           # a catalog car: three choices on screen; typed
        svc.send_choice(cid, token, ["rank"], "options").reply,
        svc.send(cid, token, "300").reply,                 # today's copay, from the employee (rule A)
    ]
    assert all(CAR_AGAIN not in r and "לא הצלחתי" not in r for r in replies)
    assert not any("חסר עוד:" in r for r in replies)       # nothing moved behind the car
    assert open_questions(svc, cid) == []                  # the catalog copay is used; no question (22/10)
    state = svc.view(cid, token).state
    assert state.stage == Stage.PRESENTED and state.results
    assert state.selected                                  # נבחרו לפי "הכי משתלם"
