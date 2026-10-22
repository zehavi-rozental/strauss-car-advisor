"""A question to the transport representative is sent once (QA 22/10).

Found: a general question got a random option_id (gq-<random>), so the same question asked again — or after a
reload — opened a second question for the representative, and an answer already given was not reused. Now the
id comes from the question's words (`agent.general_question_id`); the conversation keeps what was sent and the
answer (`state.general_questions`); asked again, the bot shows the status or the answer instead of sending. The
server's open-question index (conversation, field, option_id) keeps one open row even if a send repeats."""
from __future__ import annotations

import os

import pytest

from app.modules.conversation.agent import general_question_id

from .conftest import make_client

TOKEN = "rep-secret"
Q1 = "מנהלת מוצר, שכר בסיס 18,500"
COLOUR = "אפשר לקבל אטו 3 בצבע אחר?"
REP = {"X-Rep-Token": TOKEN}


def say(client, cid, **body):
    r = client.post(f"/api/conversations/{cid}/messages", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def ask_and_send(client, cid, question):
    """The question → "לשאול את הנציג" → (preview) "לשלוח". Returns the last reply."""
    turn = say(client, cid, text=question)
    assert any(f["field"] == "ask_rep" for f in turn["expects"]["fields"])
    turn = say(client, cid, choice="ask_rep", field="ask_rep")
    if any(f["field"] == "rep_question" for f in turn["expects"]["fields"]):
        turn = say(client, cid, choice="send", field="rep_question")
    return turn


def rep_questions(client, status="OPEN"):
    return client.get(f"/api/rep/questions?status={status}", headers=REP).json()


def test_the_id_comes_from_the_words():
    assert general_question_id(COLOUR) == general_question_id("  אפשר לקבל  אטו 3 בצבע אחר ")
    assert general_question_id(COLOUR) != general_question_id("אפשר לקבל אטו 3 עם גג נפתח?")


def test_the_same_question_is_sent_once_and_its_status_is_shown(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    say(client, cid, text=Q1)
    turn = ask_and_send(client, cid, COLOUR)
    assert "שלחתי את השאלה לנציג התחבורה" in turn["reply"]
    assert len(rep_questions(client)) == 1
    turn = ask_and_send(client, cid, COLOUR)                       # the same question again
    assert "כבר שאלתי את נציג התחבורה את השאלה הזו" in turn["reply"]
    assert len(rep_questions(client)) == 1                          # no second question


def test_an_answered_question_asked_again_shows_the_answer(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    say(client, cid, text=Q1)
    ask_and_send(client, cid, COLOUR)
    qid = rep_questions(client)[0]["id"]
    r = client.post(f"/api/rep/questions/{qid}/answer", headers=REP, json={"value": "כן, יש לבן ואפור"})
    assert r.status_code == 200
    turn = ask_and_send(client, cid, COLOUR)
    assert "נציג התחבורה כבר ענה על השאלה הזו" in turn["reply"] and "כן, יש לבן ואפור" in turn["reply"]
    assert rep_questions(client) == []                               # nothing new opened
    assert len(rep_questions(client, "ANSWERED")) == 1


def test_a_different_question_is_still_sent(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    say(client, cid, text=Q1)
    ask_and_send(client, cid, COLOUR)
    turn = ask_and_send(client, cid, "אפשר לקבל אטו 3 עם גג נפתח?")
    assert "שלחתי את השאלה לנציג התחבורה" in turn["reply"]
    assert len(rep_questions(client)) == 2


def test_a_second_send_click_creates_nothing(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    say(client, cid, text=Q1)
    ask_and_send(client, cid, COLOUR)
    r = client.post(f"/api/conversations/{cid}/messages", json={"choice": "send", "field": "rep_question"})
    assert r.status_code == 422                                      # the preview is gone: nothing to send
    assert len(rep_questions(client)) == 1


def test_the_rep_still_sees_only_the_question_context(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    say(client, cid, text=Q1)
    ask_and_send(client, cid, COLOUR)
    q = rep_questions(client)[0]
    assert "18500" not in str(q) and "18,500" not in str(q)
    assert set(q["context"]) <= {"group", "car_label", "catalog_code", "suggested_amount", "occupation_text"}


# ---------- isolation and concurrency (review before publishing, 22/10) ----------

def test_two_employees_with_the_same_question_each_get_their_own_question_and_answer(engine):
    client_a = make_client(engine, rep_access_token=TOKEN)
    client_b = make_client(engine, rep_access_token=TOKEN)       # another browser: another session cookie
    cid_a = client_a.post("/api/conversations").json()["conversation_id"]
    cid_b = client_b.post("/api/conversations").json()["conversation_id"]
    for client, cid in ((client_a, cid_a), (client_b, cid_b)):
        say(client, cid, text=Q1)
        assert "שלחתי את השאלה" in ask_and_send(client, cid, COLOUR)["reply"]
    open_ = rep_questions(client_a)
    assert len(open_) == 2                                          # the same words, two conversations: two rows
    # the representative answers one of them: only that conversation gets the answer
    client_a.post(f"/api/rep/questions/{open_[0]['id']}/answer", headers=REP, json={"value": "תשובה אחת"})
    replies = {}
    for client, cid in ((client_a, cid_a), (client_b, cid_b)):
        msgs = [m["content"] for m in client.get(f"/api/conversations/{cid}").json()["messages"]]
        replies[cid] = ask_and_send(client, cid, COLOUR)["reply"]
        replies[cid + ":got"] = any("תשובה אחת" in m for m in msgs)
    answered = [c for c in (cid_a, cid_b) if replies[c + ":got"]]
    assert len(answered) == 1                                       # never both, never the wrong one
    other = cid_b if answered[0] == cid_a else cid_a
    assert "כבר ענה" in replies[answered[0]] and "תשובה אחת" in replies[answered[0]]
    assert "כבר שאלתי" in replies[other] and "תשובה אחת" not in replies[other]
    assert len(rep_questions(client_a)) == 1                        # nothing re-opened for either


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                    reason="needs real concurrent connections: the SQLite test database is one shared connection "
                           "(StaticPool); CI runs this on PostgreSQL")
def test_two_send_requests_at_the_same_moment_open_one_question(engine):
    import threading
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    say(client, cid, text=Q1)
    say(client, cid, text=COLOUR)
    say(client, cid, choice="ask_rep", field="ask_rep")             # the preview "לשלוח / לבטל" is on screen
    codes: list[int] = []
    barrier = threading.Barrier(2)

    def send():
        barrier.wait()
        codes.append(client.post(f"/api/conversations/{cid}/messages",
                                 json={"choice": "send", "field": "rep_question"}).status_code)

    threads = [threading.Thread(target=send) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes)[0] == 200 and all(c in (200, 409, 422) for c in codes), codes
    assert len(rep_questions(client)) == 1                          # one question, whatever the order
