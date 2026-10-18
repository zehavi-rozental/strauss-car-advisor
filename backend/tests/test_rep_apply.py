"""תשובות הנציג חוזרות לשיחה: חישוב מחדש, אישור, המשך זרימה, ויתור — פעם אחת, ובלי לאבד אף תשובה."""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.modules.calculation.public import CopayStatus, evaluate
from app.modules.cases.public import CasesService, QuestionAlreadyAnswered, QuestionAnswered
from app.modules.conversation.models import CalculationRunRow, MessageRow
from app.modules.rules.public import car_state, find_catalog_car, load_snapshot, occupation_rule, reference_employee

from .conftest import CONFIG, make_client

TOKEN = "rep-secret"
AUTH = {"X-Rep-Token": TOKEN}
NOA = [
    "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
    "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש",
    "אטו 3",
]


@pytest.fixture
def client(engine):
    return make_client(engine, rep_access_token=TOKEN)


def say(client, cid, *texts):
    body = None
    for text in texts:
        body = client.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()
    return body


def start(client, *texts):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, say(client, cid, *texts)


def last_seq(client, cid):
    return client.get(f"/api/conversations/{cid}/updates").json()["messages"][-1]["seq"]


def answer(client, qid, value, **extra):
    return client.post(f"/api/rep/questions/{qid}/answer", json={"value": value, **extra}, headers=AUTH)


def open_questions(client):
    return client.get("/api/rep/questions", headers=AUTH).json()


def runs(sessions):
    with sessions() as s:
        return s.scalar(select(func.count()).select_from(CalculationRunRow))


def engine_delta(copay: int) -> Decimal:
    """ההפרש של נועה ל-ATTO 3 בהשתתפות נתונה — ישירות מהמנוע, לא מספר קבוע בבדיקה."""
    snap = load_snapshot(CONFIG)
    ref = next(e for e in snap.reference_employees if e.employee_id == "E-001")
    [today] = find_catalog_car(snap, "COROLLA CROSS")
    [atto] = find_catalog_car(snap, "ATTO 3")
    result = evaluate(reference_employee(ref), car_state(today, copay=Decimal(300), copay_status=CopayStatus.EMPLOYEE_STATED),
                      car_state(atto, copay=Decimal(copay), copay_status=CopayStatus.CONFIRMED), snap.calc,
                      occupation_rule(snap.occupations.get(ref.occupation)))
    return result.delta_display


# ---------- השתתפות ברכב החדש ----------

def test_rep_changes_the_copay_and_the_option_is_recalculated(client, sessions):
    cid, body = start(client, *NOA)
    assert body["results"][0]["delta_display"] == "661"
    [q] = open_questions(client)
    seq, runs_before = last_seq(client, cid), runs(sessions)

    r = answer(client, q["id"], "350", effective_date="2027-01-01")
    assert r.status_code == 200
    updates = client.get(f"/api/conversations/{cid}/updates?after={seq}").json()
    [rep] = updates["messages"]
    new_delta = engine_delta(350)
    assert rep["source"] == "rep" and new_delta != Decimal(661)
    assert rep["content"] == (f"הנציג עדכן: השתתפות 350 ₪ בחודש, מ-1/1. "
                              f"ההפרש עודכן: במקום +661 ₪, עכשיו {new_delta:+,} ₪ בחודש.")
    assert "←" not in rep["content"] and "→" not in rep["content"]
    [result] = updates["results"]
    assert Decimal(result["result"]["delta_display"]) == new_delta
    assert result["result"]["alternative"]["copay"] == "350"
    assert runs(sessions) == runs_before + 1
    assert updates["stage"] == "PRESENTED" and updates["pending_questions"] == []

    assert answer(client, q["id"], "400").status_code == 409          # תשובה שנייה
    again = client.get(f"/api/conversations/{cid}/updates?after={seq}").json()
    assert len(again["messages"]) == 1                                # שום דבר לא יושם פעמיים


def test_rep_confirms_the_suggested_copay_without_recalculating(client, sessions):
    cid, _ = start(client, *NOA)
    [q] = open_questions(client)
    seq, runs_before = last_seq(client, cid), runs(sessions)
    answer(client, q["id"], q["suggested_value"], effective_date="2027-01-01")
    updates = client.get(f"/api/conversations/{cid}/updates?after={seq}").json()
    assert [m["content"] for m in updates["messages"]] == [
        "נציג התחבורה אישר את ההשתתפות ב-ATTO 3 (2025) (בתוקף מ-01.01.2027)."]
    assumptions = updates["results"][0]["result"]["assumptions"]
    assert "השתתפות ב-ATTO 3 (2025) — אושר על ידי נציג התחבורה (בתוקף מ-01.01.2027)" in assumptions
    assert not any("ממתינה לאישור נציג" in a for a in assumptions)
    assert updates["results"][0]["result"]["delta_display"] == "661"
    assert runs(sessions) == runs_before                              # בלי חישוב מחדש


def test_confirmed_copay_is_used_next_time_without_a_new_question(client):
    cid, _ = start(client, *NOA)
    [q] = open_questions(client)
    answer(client, q["id"], "350")
    body = say(client, cid, "אטו 3")
    assert open_questions(client) == []
    assert body["results"][0]["alternative"]["copay"] == "350"
    assert not any("ממתינה" in a for a in body["results"][0]["assumptions"])


# ---------- שאלה חוסמת: הזרימה ממשיכה ----------

def test_rep_answer_unblocks_and_the_flow_continues(client):
    cid, body = start(client, NOA[0], "יש לי קורולה קרוס", "לא יודעת")
    assert body["stage"] == "WAITING_FOR_REP"
    [q] = open_questions(client)
    seq = last_seq(client, cid)
    answer(client, q["id"], "300")
    updates = client.get(f"/api/conversations/{cid}/updates?after={seq}").json()
    assert updates["stage"] == "CHOOSE_OPTIONS"
    [rep] = updates["messages"]
    assert rep["content"].startswith("נציג התחבורה השיב: ההשתתפות הנוכחית שלכם ב-COROLLA CROSS (2025) היא 300 ₪.")
    assert "איזה רכבים להשוות" in rep["content"]
    body = say(client, cid, "אטו 3")                                  # ממשיכים כרגיל
    assert body["stage"] == "PRESENTED" and body["results"][0]["delta_display"] == "661"


# ---------- ויתור כשהעיסוק לא בטבלה: כן/לא, ואז סכום האחזקה ----------

UNKNOWN = ["אסטרונאוטית", "כן", "ג, שכר בסיס 18,500",
           "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
           "יש לי קורולה קרוס", "300", "ויתור"]


def test_opt_out_denied_by_the_rep(client):
    cid, body = start(client, *UNKNOWN)
    assert body["stage"] == "WAITING_FOR_REP"
    [q] = open_questions(client)
    assert (q["missing_field"], q["answer_type"]) == ("OCCUPATION_OPT_OUT", "BOOLEAN")
    answer(client, q["id"], "לא")
    updates = client.get(f"/api/conversations/{cid}/updates").json()
    assert updates["stage"] == "PRESENTED"
    [result] = updates["results"]
    assert result["result"]["status"] == "NOT_ELIGIBLE"
    assert result["result"]["reason"] == "לפי נציג התחבורה, בעיסוק שלכם לא ניתן לוותר על הרכב"
    assert open_questions(client) == []


def test_opt_out_allowed_then_maintenance_amount(client):
    cid, _ = start(client, *UNKNOWN)
    [q] = open_questions(client)
    answer(client, q["id"], "כן")
    updates = client.get(f"/api/conversations/{cid}/updates").json()
    assert updates["stage"] == "WAITING_FOR_REP"
    [m] = open_questions(client)
    assert (m["missing_field"], m["answer_type"], m["mode"]) == ("MAINTENANCE_AMOUNT", "MONEY", "REQUIRED")
    assert m["context"] == {"group": "ג", "occupation_text": "אסטרונאוטית"}
    answer(client, m["id"], "1800")
    updates = client.get(f"/api/conversations/{cid}/updates").json()
    assert updates["stage"] == "PRESENTED"
    [result] = updates["results"]
    assert result["result"]["status"] == "OK"
    assert result["result"]["alternative"]["maintenance"] == "1800"


# ---------- עמידות: כשל ביישום, יישום כפול, תשובות במקביל ----------

def test_answer_stored_but_apply_failed_is_applied_on_the_next_read(client):
    service = client.app.state.ctx.service
    cid, _ = start(client, NOA[0], "יש לי קורולה קרוס", "לא יודעת")
    [q] = open_questions(client)
    real = service.agent.apply_answer
    calls = {"n": 0}

    def fails_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("down")
        return real(*args, **kwargs)

    service.agent.apply_answer = fails_once
    assert answer(client, q["id"], "300").status_code == 200        # התשובה נשמרה למרות הכשל
    assert client.get(f"/api/conversations/{cid}").json()["stage"] == "CHOOSE_OPTIONS"
    assert calls["n"] == 2


def test_applying_the_same_answer_twice_changes_nothing(client, sessions):
    service = client.app.state.ctx.service
    cid, _ = start(client, *NOA)
    [q] = open_questions(client)
    answer(client, q["id"], "350")
    event = QuestionAnswered(conversation_id=cid, question_id=q["id"], missing_field=service.cases.get(q["id"]).missing_field,
                             option_id="CAR-10", value="350", effective_date=None)
    assert service.apply_answer(event) is False
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(MessageRow).where(MessageRow.source == "rep")) == 1


def test_two_answers_at_once_exactly_one_succeeds(tmp_path):
    from app.modules.conversation.public import ConversationService, LLMChain
    from app.modules.rules.public import RulesStore

    from .test_turn_transactions import file_db

    sessions = file_db(tmp_path)
    cases = CasesService(sessions)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain(), cases)
    turn, token = svc.start()
    for text in NOA:
        turn = svc.send(turn.conversation_id, token, text)
    [q] = cases.list_open()
    barrier, outcomes = threading.Barrier(2), []

    def rep(value):
        barrier.wait(timeout=10)
        try:
            cases.answer(q.id, value)
            outcomes.append("ok")
        except QuestionAlreadyAnswered:
            outcomes.append("conflict")

    threads = [threading.Thread(target=rep, args=(v,)) for v in ("350", "400")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(outcomes) == ["conflict", "ok"]
    assert cases.get(q.id).answer_value in ("350", "400")


# ---------- הסכום של הנציג כהנחה, ומזהים עם רווחים ----------

def test_rep_amount_is_an_assumption_now_and_in_later_calculations(client):
    cid, _ = start(client, *NOA)
    [q] = open_questions(client)
    seq = last_seq(client, cid)
    answer(client, q["id"], "350", effective_date="2027-01-01")
    expected = "השתתפות ב-ATTO 3 (2025): 350 ₪ לפי נציג התחבורה (בתוקף מ-01.01.2027)"
    updates = client.get(f"/api/conversations/{cid}/updates?after={seq}").json()
    assert expected in updates["results"][0]["result"]["assumptions"]
    body = say(client, cid, "אטו 3")                                  # חישוב מאוחר יותר
    assert expected in body["results"][0]["assumptions"]


def test_rep_amount_without_a_date_has_no_date_part(client):
    cid, _ = start(client, *NOA)
    [q] = open_questions(client)
    seq = last_seq(client, cid)
    answer(client, q["id"], "350")
    assumptions = client.get(f"/api/conversations/{cid}/updates?after={seq}").json()["results"][0]["result"]["assumptions"]
    assert "השתתפות ב-ATTO 3 (2025): 350 ₪ לפי נציג התחבורה" in assumptions
    assert not any("בתוקף" in a for a in assumptions)


def test_a_confirmation_keeps_its_wording_in_later_calculations(client):
    cid, _ = start(client, *NOA)
    [q] = open_questions(client)
    answer(client, q["id"], q["suggested_value"])
    body = say(client, cid, "אטו 3")
    assumptions = body["results"][0]["assumptions"]
    assert "השתתפות ב-ATTO 3 (2025) — אושר על ידי נציג התחבורה" in assumptions
    assert not any("לפי נציג התחבורה" in a for a in assumptions)


def test_padded_ids_are_trimmed(client):
    cid, _ = start(client, *NOA)
    [q] = open_questions(client)
    assert client.get(f"/api/rep/questions/%20{q['id']}%20", headers=AUTH).status_code == 200
    assert answer(client, f"%20{q['id']}%20", "350").status_code == 200
    assert client.get(f"/api/conversations/%20{cid}%20/updates").status_code == 200
    assert client.get(f"/api/conversations/%20{cid}%20").status_code == 200


# ---------- נוסח ההמתנה מהתרחיש הרשמי ----------

PENDING_SENTENCE = "חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה."


def test_pending_copay_uses_the_scenario_wording_until_the_rep_confirms(client):
    cid, body = start(client, *NOA)
    assert PENDING_SENTENCE in body["reply"]
    assert "לפי הקטלוג — ממתינה לאישור נציג" not in body["reply"]      # במקום שורת ההנחה הכללית
    assert body["results"][0]["copay_pending"] is True
    assert "השתתפות ב-ATTO 3 (2025) לפי הקטלוג — ממתינה לאישור נציג" in body["results"][0]["assumptions"]
    [q] = open_questions(client)
    answer(client, q["id"], q["suggested_value"])
    updates = client.get(f"/api/conversations/{cid}/updates").json()
    assert updates["results"][0]["result"]["copay_pending"] is False
    body = say(client, cid, "אטו 3")                                   # חישוב מאוחר יותר
    assert PENDING_SENTENCE not in body["reply"]
    assert "השתתפות ב-ATTO 3 (2025) — אושר על ידי נציג התחבורה" in body["results"][0]["assumptions"]
