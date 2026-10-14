"""רכב שמוזכר בזמן המתנה לנציג: תיקון כשבטוחים, שאלה כשלא — לא מנחשים."""
from __future__ import annotations

import pytest

from app.modules.cases.public import Status
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, make_client

PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")
CLARIFY = ("לא בטוח שהבנתי: ATTO 3 (2025) הוא הרכב שלך היום, או חלופה שתרצי לבדוק? "
           "אם זה הרכב שלך, כתבו \"זה הרכב שלי\". אם זו חלופה, אבדוק אותה ברגע שהנציג יענה.")


def waiting(sessions, llm=None):
    """נועה, "קורולה" (CAR-07), "לא יודע" → ממתינים לנציג על ההשתתפות."""
    svc = ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())
    turn, token = svc.start()
    for text in (PROFILE, "קורולה", "לא יודע"):
        turn = svc.send(turn.conversation_id, token, text)
    assert turn.stage == Stage.WAITING_FOR_REP
    [question] = svc.cases.list_open()
    return svc, turn.conversation_id, token, question


def state(svc, cid, token):
    return svc.view(cid, token).state


@pytest.mark.parametrize("text", ["התבלבלתי, זו קורולה קרוס", "אופס, התכוונתי לקורולה קרוס",
                                  "תיקון: קורולה קרוס", "רגע, טעיתי, קורולה קרוס"])
def test_cues_in_the_first_words_are_corrections(sessions, text):
    svc, cid, token, old = waiting(sessions)
    turn = svc.send(cid, token, text)
    assert svc.cases.get(old.id).status == Status.CANCELLED
    assert state(svc, cid, token).current.catalog_code == "CAR-09"
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and "כמה את/ה משתתף/ת" in turn.reply


def test_negation_is_still_not_a_correction(sessions):
    svc, cid, token, old = waiting(sessions)
    svc.send(cid, token, "זה לא קורולה קרוס")
    assert state(svc, cid, token).current.catalog_code == "CAR-07"
    assert svc.cases.get(old.id).status == Status.OPEN


def test_a_car_named_without_a_cue_gets_a_question_not_a_guess(sessions):
    svc, cid, token, old = waiting(sessions)
    turn = svc.send(cid, token, "ATTO 3")
    assert turn.reply == CLARIFY and turn.stage == Stage.WAITING_FOR_REP
    s = state(svc, cid, token)
    assert s.current.catalog_code == "CAR-07" and s.pending_rep is not None
    assert s.pending_clarification.catalog_code == "CAR-10"
    assert svc.cases.get(old.id).status == Status.OPEN


def test_this_is_my_car_rematches_and_cancels(sessions):
    svc, cid, token, old = waiting(sessions)
    svc.send(cid, token, "ATTO 3")
    turn = svc.send(cid, token, "זה הרכב שלי")
    s = state(svc, cid, token)
    assert s.current.catalog_code == "CAR-10" and s.pending_clarification is None and s.pending_rep is None
    assert svc.cases.get(old.id).status == Status.CANCELLED
    assert turn.reply.startswith("זיהיתי: ATTO 3 (2025)") and "כמה את/ה משתתף/ת" in turn.reply


def test_an_alternative_is_calculated_after_the_rep_answers(engine):
    client = make_client(engine, rep_access_token="t")
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in (PROFILE, "קורולה קרוס", "לא יודעת", "ATTO 3"):
        body = client.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()
    assert body["reply"] == CLARIFY
    body = client.post(f"/api/conversations/{cid}/messages", json={"text": "חלופה"}).json()
    assert body["reply"] == "בסדר, אבדוק את ATTO 3 (2025) כחלופה ברגע שהנציג יענה."
    assert body["stage"] == "WAITING_FOR_REP"
    [q] = client.get("/api/rep/questions", headers={"X-Rep-Token": "t"}).json()
    client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "300"}, headers={"X-Rep-Token": "t"})
    updates = client.get(f"/api/conversations/{cid}/updates").json()
    assert updates["stage"] == "PRESENTED"
    [result] = updates["results"]
    assert result["label"] == "ATTO 3 (2025)" and result["result"]["delta_display"] == "661"


def test_llm_recognises_a_free_worded_correction(sessions):
    fake = FakeLLM()
    svc, cid, token, old = waiting(sessions, LLMChain([fake]))
    fake.turn(call("match_current_car", model="COROLLA CROSS"))
    turn = svc.send(cid, token, "הרכב שלי זה קורולה קרוס הגבוהה")
    assert fake.calls[-1].tools == ["match_current_car"]
    assert state(svc, cid, token).current.catalog_code == "CAR-09"
    assert svc.cases.get(old.id).status == Status.CANCELLED
    assert turn.reply.startswith("זיהיתי: COROLLA CROSS (2025)")


def test_llm_without_a_call_falls_back_to_the_question(sessions):
    fake = FakeLLM()
    svc, cid, token, old = waiting(sessions, LLMChain([fake]))
    fake.turn()                                         # המודל לא קורא לכלי
    turn = svc.send(cid, token, "ATTO 3")
    assert fake.calls and turn.reply == CLARIFY
    assert state(svc, cid, token).current.catalog_code == "CAR-07"
