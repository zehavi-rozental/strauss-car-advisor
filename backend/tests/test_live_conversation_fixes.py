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
    turn = svc.send(cid, token, "אין")
    assert turn.reply.startswith("רשמתי: אין עמלות, אין בונוס, אין קרן השתלמות, אין ילדים.")
    p = svc.view(cid, token).state.profile
    assert (p.commissions, p.bonus, p.study_fund_percent, p.children) == (0, 0, 0, 0)
    assert p.pension_percent is None and p.credit_points is None      # לא מניחים 0
    rest = without_cta(turn.reply).split("\n")[-1]
    assert "פנסיה" in rest and "נקודות זיכוי" in rest
    assert "עמלות" not in rest and "בונוס" not in rest and "ילדים" not in rest   # רק מה שנשאר


def test_the_none_button_on_one_field_says_what_was_recorded(sessions):
    svc = service(sessions)
    cid, token = start_q2(svc)
    turn = svc.send_choice(cid, token, ["none"], "children")
    assert turn.reply.startswith("רשמתי: אין ילדים.")


# ---------- 3. זנב "ועוד" פעם אחת ----------

def test_the_leftover_tail_is_shown_once_and_then_asked_on_its_own(sessions):
    svc = service(sessions)
    cid, token = start_q2(svc)
    turn = svc.send(cid, token, "אין")
    assert "חסר עוד:" in turn.reply                         # Q3 הראשונה: הרכב, החלופות, ומה שנשאר
    turn = svc.send_choice(cid, token, ["CAR-09"], "car")
    assert "חסר עוד" not in turn.reply and "פנסיה" not in turn.reply
    turn = svc.send(cid, token, "300")
    assert "חסר עוד" not in turn.reply and "פנסיה" not in turn.reply
    turn = svc.send_choice(cid, token, ["CAR-10"], "options")
    assert "חסר עוד" not in turn.reply
    assert "פנסיה" in turn.reply and "נקודות זיכוי" in turn.reply     # עכשיו זו השאלה הראשית
    turn = svc.send(cid, token, PROFILE_REST)
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
    svc.send(cid, token, "אין")
    turn = svc.send_choice(cid, token, ["CAR-01", "CAR-12", "CAR-03"], "options")
    assert turn.reply.startswith("בחרתם להשוות: PICANTO (2025), TUCSON (2025), BYD DOLPHIN (2025).")


# ---------- 5. שאלה לנציג רק אחרי שהפרופיל מלא ----------

def test_an_unknown_copay_waits_for_the_profile_before_asking_the_rep(sessions):
    svc = service(sessions)
    cid, token = start_q2(svc)
    svc.send(cid, token, "אין")
    svc.send_choice(cid, token, ["CAR-09"], "car")
    turn = svc.send_choice(cid, token, ["unknown"], "copay")
    assert turn.stage != Stage.WAITING_FOR_REP and not open_questions(svc, cid)
    assert "נציג" in turn.reply and "איזה רכבים להשוות" in turn.reply   # נרשם; ממשיכים בשאלות
    turn = svc.send_choice(cid, token, ["CAR-10"], "options")
    assert not open_questions(svc, cid) and "פנסיה" in turn.reply           # קודם משלימים את הפרופיל
    turn = svc.send(cid, token, PROFILE_REST)
    assert turn.stage == Stage.WAITING_FOR_REP
    assert [q.missing_field.value for q in open_questions(svc, cid)] == ["CURRENT_COPAY"]


# ---------- השיחה החיה, מקצה לקצה ----------

def test_the_live_conversation_reaches_a_result(sessions):
    """אותם צעדים כמו בשיחה החיה (כפתורים), עם השלמת הפרופיל ותשובת הנציג."""
    svc = service(sessions)
    turn, token = svc.start()
    cid = turn.conversation_id
    replies = [
        svc.send_choice(cid, token, [OCCUPATION], "occupation").reply,
        svc.send(cid, token, "15000").reply,
        svc.send(cid, token, "אין").reply,
        svc.send_choice(cid, token, ["rank"], "options").reply,
        svc.send_choice(cid, token, ["CAR-09"], "car").reply,
        svc.send_choice(cid, token, ["unknown"], "copay").reply,
        svc.send(cid, token, PROFILE_REST).reply,
    ]
    assert all(CAR_AGAIN not in r and "לא הצלחתי" not in r for r in replies)
    assert sum("חסר עוד:" in r for r in replies) == 1
    [question] = open_questions(svc, cid)
    svc.cases.answer(question.id, "300")
    state = svc.view(cid, token).state
    assert state.stage == Stage.PRESENTED and state.results
    assert state.selected                                  # נבחרו לפי "הכי משתלם"
