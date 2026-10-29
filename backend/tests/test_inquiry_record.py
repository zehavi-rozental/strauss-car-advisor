"""One inquiry record in the chat (stage 4 of the 29/10 plan, U1-U4).

Every question to the representative, whatever opened it (the inquiry card, a chat message), has exactly one record
in the conversation: a "rep_inquiry" message with the question's id, the car and the employee's words. A question
reused by a later turn is not recorded again; the representative's answer is its own message, once; an inquiry that
already exists is pointed to, never doubled; a sent inquiry is not offered again. A new verification link is offered
by the API only while an address waits for it.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from app.modules.conversation.models import EmailLinkRow
from app.shared.db import session_factory, utcnow
from .conftest import make_client

TOKEN = "rep-record"
AUTH = {"X-Rep-Token": TOKEN}
EMAIL = "employee@example.com"
PROFILE = ["מנהלת מוצר, שכר בסיס 18500",
           "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
           "יש לי קורולה קרוס, משלמת 300"]


def _client(engine, **kw):
    return make_client(engine, rep_access_token=TOKEN, **kw)


def _code(snapshot, name):
    return next(c.catalog_code for c in snapshot.catalog if name in c.model_name)


def _compare(c, choices):
    cid = c.post("/api/conversations").json()["conversation_id"]
    for m in PROFILE:
        c.post(f"/api/conversations/{cid}/messages", json={"text": m})
    c.post(f"/api/conversations/{cid}/messages", json={"choices": choices, "field": "options"})
    return cid


def _say(c, cid, text):
    return c.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()


def _view(c, cid):
    return c.get(f"/api/conversations/{cid}").json()


def _records(c, cid):
    return [m for m in _view(c, cid)["messages"] if m["source"] == "rep_inquiry"]


def _ask(c, cid, question, option_id=None, intent="general", email=None):
    return c.post(f"/api/conversations/{cid}/rep-question",
                  json={"question": question, "intent": intent, **({"option_id": option_id} if option_id else {}),
                        **({"email": email} if email else {})})


def test_a_card_inquiry_has_one_record_and_an_existing_one_is_pointed_to(engine, snapshot):
    c = _client(engine)
    niro = _code(snapshot, "NIRO")
    cid = _compare(c, [niro])
    first = _ask(c, cid, "אפשר לאשר לי את ההשתתפות ב-NIRO?", niro, "copay_verification").json()

    again = _ask(c, cid, "נוסח אחר לאותה בקשה", niro, "copay_verification").json()

    assert again["status"] == "exists" and again["question_id"] == first["question_id"]
    for _ in range(2):                                          # a refresh shows the same, once
        [record] = _records(c, cid)
        assert record["rep_question_id"] == first["question_id"]
        assert "NIRO" in record["content"] and "אפשר לאשר לי את ההשתתפות ב-NIRO?" in record["content"]
        assert "נוסח אחר" not in record["content"]
    [q] = _view(c, cid)["rep_questions"]
    assert q["status"] == "OPEN" and q["option_id"] == niro


def test_a_copay_inquiry_asked_in_the_chat_gets_the_same_record_once(engine, snapshot):
    c = _client(engine)
    niro, dolphin = _code(snapshot, "NIRO"), _code(snapshot, "DOLPHIN")
    cid = _compare(c, [niro, dolphin])

    _say(c, cid, "אני רוצה שנציג התחבורה יאשר את ההשתתפות")
    opened = [q for q in _view(c, cid)["rep_questions"] if q["status"] == "OPEN"]
    assert opened                                               # the chat path opened the question(s)
    records = _records(c, cid)
    assert sorted(r["rep_question_id"] for r in records) == sorted(q["id"] for q in opened)   # one each
    assert all("של העובד" not in r["content"] and "לפי הקטלוג:" not in r["content"] for r in records)

    _say(c, cid, "אני רוצה שנציג התחבורה יאשר את ההשתתפות")    # asked again: the same open questions
    assert len(_records(c, cid)) == len(records)


def test_the_answer_appears_once_and_the_record_keeps_only_the_question(engine, snapshot):
    c = _client(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    _ask(c, cid, "באיזה צבעים יש את NIRO?")
    [q] = c.get("/api/rep/questions", headers=AUTH).json()
    answer = "לבן, שחור ואפור."

    assert c.post(f"/api/rep/questions/{q['id']}/answer", headers=AUTH, json={"value": answer}).status_code == 200

    for _ in range(2):
        messages = _view(c, cid)["messages"]
        assert sum(answer in m["content"] for m in messages) == 1          # the answer, once
        [record] = [m for m in messages if m["source"] == "rep_inquiry"]
        assert answer not in record["content"]
        [rep] = [m for m in messages if m["source"] == "rep"]
        assert rep["rep_question_id"] == record["rep_question_id"]         # linked, not copied
    assert _view(c, cid)["rep_questions"][0]["status"] == "ANSWERED"


def test_a_sent_inquiry_is_not_offered_again(engine, snapshot):
    c = _client(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    offer = _say(c, cid, "האם יש לרכב הזה גג נפתח?")
    assert "ask_rep" in [f["field"] for f in offer["expects"]["fields"]]     # offered before
    assert _ask(c, cid, "האם יש לרכב הזה גג נפתח?").json()["status"] == "created"
    assert "ask_rep" not in [f["field"] for f in _view(c, cid)["expects"]["fields"]]


def test_records_stay_in_their_own_conversation(engine, snapshot):
    a, b = _client(engine), _client(engine)
    niro = _code(snapshot, "NIRO")
    cid_a, cid_b = _compare(a, [niro]), _compare(b, [niro])
    _ask(a, cid_a, "באיזה צבעים יש את NIRO?")
    assert len(_records(a, cid_a)) == 1
    assert _records(b, cid_b) == [] and _view(b, cid_b)["rep_questions"] == []
    assert b.get(f"/api/conversations/{cid_a}").status_code == 404


def test_a_new_verification_link_is_offered_only_while_an_address_waits(engine, snapshot):
    c = _client(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    view = _view(c, cid)
    assert view["rep_email_delivery"] == "enabled" and view["rep_email_pending"] is False
    _ask(c, cid, "באיזה צבעים יש את NIRO?", email=EMAIL)
    view = _view(c, cid)
    assert view["rep_email_pending"] is True and view["rep_email"] is None   # the address is never sent back

    resend = lambda: c.post(f"/api/conversations/{cid}/email/verify", json={})   # noqa: E731
    assert resend().json()["resend"] == "valid"                 # the first link still works
    with session_factory(c.app.state.ctx.sessions.kw["bind"])() as s, s.begin():
        s.execute(update(EmailLinkRow).values(expires_at=utcnow() - timedelta(minutes=1)))
    body = resend().json()
    assert body["resend"] == "queued" and body["email_pending"] is True

    sent = c.app.state.ctx.service.email_notifications.fake_messages
    import re
    token = re.search(r"return_token=([A-Za-z0-9_-]+)", sent[-1]["body"]).group(1)
    assert c.post("/api/conversations/return", json={"token": token}).status_code == 200
    view = _view(c, cid)
    assert view["rep_email_pending"] is False and view["rep_email_verified"] is True
    assert resend().json()["resend"] == "verified"


def test_without_mail_no_new_link_is_offered(engine, snapshot):
    c = _client(engine, email_backend="disabled")
    cid = _compare(c, [_code(snapshot, "NIRO")])
    _ask(c, cid, "באיזה צבעים יש את NIRO?", email=EMAIL)
    view = _view(c, cid)
    assert view["rep_email_delivery"] == "disabled" and view["rep_email_pending"] is False
    assert c.post(f"/api/conversations/{cid}/email/verify", json={}).status_code == 503
    [record] = _records(c, cid)
    assert "אימייל" not in record["content"]
