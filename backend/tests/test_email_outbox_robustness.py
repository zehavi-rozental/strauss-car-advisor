"""Email around the representative's answer (stage 3 of the 29/10 plan, N1-N7).

Mail is a notice, never the record: the inquiry and the answer live in the conversation. Without mail delivery the
employee still asks and reads the answer, and nothing is said about email. With it: one verification per
conversation, a new link only when the previous one expired or failed (limited), one notice per answer after
verification, and notices that wait for a verification never hold back the queue. A mail failure (SMTP, a key that
no longer decrypts) never loses the representative's answer, and no address or token reaches the log or the chat.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import update

from app.modules.conversation import email_notifications as mail
from app.modules.conversation.models import ConversationRow, EmailLinkRow, EmailOutboxRow
from app.shared.db import session_factory, utcnow
from .conftest import make_client

TOKEN = "rep-mail"
AUTH = {"X-Rep-Token": TOKEN}
EMAIL = "employee@example.com"
KEY = Fernet.generate_key().decode("ascii")
PROFILE = ["מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
           "יש לי קורולה 2026, משלמת 280"]
SMTP = dict(email_backend="smtp", email_encryption_key=KEY, email_smtp_host="smtp.example.test",
            email_from="advisor@example.test", email_public_app_url="https://advisor.example.test")


def _client(engine, **settings):
    settings = settings or dict(email_backend="fake", email_encryption_key=KEY,
                                email_public_app_url="http://testserver")
    return make_client(engine, rep_access_token=TOKEN, **settings)


def _present(c):
    cid = c.post("/api/conversations").json()["conversation_id"]
    for message in PROFILE:
        c.post(f"/api/conversations/{cid}/messages", json={"text": message})
    c.post(f"/api/conversations/{cid}/messages", json={"choices": ["CAR-05"], "field": "options"})
    return cid


def _ask(c, cid, question, email=None):
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": question, "intent": "general", **({"email": email} if email else {})})
    assert r.status_code == 200, r.text
    return r.json()


def _answer_all(c, value):
    for q in c.get("/api/rep/questions", headers=AUTH).json():
        assert c.post(f"/api/rep/questions/{q['id']}/answer", headers=AUTH, json={"value": value}).status_code == 200


def _messages(c, cid, source):
    return [m["content"] for m in c.get(f"/api/conversations/{cid}").json()["messages"] if m["source"] == source]


def _db(c):
    return session_factory(c.app.state.ctx.sessions.kw["bind"])()


def _outbox(c, **where):
    with _db(c) as s:
        return [(r.purpose, r.status) for r in s.query(EmailOutboxRow).filter_by(**where).order_by(
            EmailOutboxRow.created_at)]


def _token(message):
    return re.search(r"return_token=([A-Za-z0-9_-]+)", message["body"]).group(1)


def _notifier(c):
    return c.app.state.ctx.service.email_notifications


def test_without_mail_the_inquiry_and_the_answer_work_and_nothing_is_said_about_email(engine):
    c = _client(engine, email_backend="disabled")
    cid = _present(c)

    first = _ask(c, cid, "באיזה צבעים יש את NIRO?", email=EMAIL)     # an address typed anyway is ignored
    second = _ask(c, cid, "יש לרכב הזה גג נפתח?")
    assert first["status"] == second["status"] == "created"
    assert first["email_delivery"] == "disabled" and first["email_verified"] is False
    _answer_all(c, "לבן ושחור.\nגג נפתח: לא.")

    for _ in range(2):                                               # a refresh reads the same
        sent = _messages(c, cid, "rep_inquiry")
        answers = _messages(c, cid, "rep")
        assert len(sent) == 2 and len(answers) == 2
        assert not any("אימייל" in m or "מייל" in m for m in sent + answers)
        assert all("לבן ושחור.\nגג נפתח: לא." in m for m in answers)
    view = c.get(f"/api/conversations/{cid}").json()
    assert view["rep_email_delivery"] == "disabled" and view["rep_email"] is None
    assert _outbox(c) == []
    with _db(c) as s:
        assert s.get(ConversationRow, __import__("uuid").UUID(cid)).email_ciphertext is None


def test_verify_once_then_one_notice_per_answer_and_a_one_time_return_link(engine, caplog):
    caplog.set_level(logging.DEBUG)
    c = _client(engine)
    cid = _present(c)
    sent = _notifier(c).fake_messages

    first = _ask(c, cid, "באיזה צבעים יש את NIRO?", email=EMAIL)
    assert first["email_delivery"] == "enabled" and len(sent) == 1 and "אימות" in sent[0]["subject"]
    assert "קישור לאימות" in _messages(c, cid, "rep_inquiry")[0]
    assert c.post("/api/conversations/return", json={"token": _token(sent[0])}).status_code == 200

    # verified for this conversation: the next inquiry needs no address and sends no new verification
    second = _ask(c, cid, "יש לרכב הזה גג נפתח?")
    assert second["email_verified"] is True and len(sent) == 1
    assert "נעדכן אתכם גם באימייל" in _messages(c, cid, "rep_inquiry")[1]

    _answer_all(c, "כן")
    notices = [m for m in sent if "אימות" not in m["subject"]]
    assert len(notices) == 2 and all(m["to"] == EMAIL for m in notices)       # one per answer
    service = c.app.state.ctx.service
    for q in c.get("/api/rep/questions?status=ANSWERED", headers=AUTH).json():   # applied twice: nothing more
        from app.modules.cases.public import MissingField, QuestionAnswered
        assert service.apply_answer(QuestionAnswered(cid, q["id"], MissingField(q["missing_field"]), q["option_id"]
                                                     or "", q["answer_value"], None)) is False
    assert len(sent) == 3 and len(_outbox(c, purpose="ANSWER")) == 2

    # the notice's link opens this conversation on another device, once
    other = _client(engine)
    link = _token(notices[0])
    assert other.post("/api/conversations/return", json={"token": link}).status_code == 200
    assert len(_messages(other, cid, "rep")) == 2
    assert _client(engine).post("/api/conversations/return", json={"token": link}).status_code == 410

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert EMAIL not in logged and link not in logged and _token(sent[0]) not in logged


def test_an_expired_verification_link_is_replaced_with_limits(engine):
    c = _client(engine)
    cid = _present(c)
    sent = _notifier(c).fake_messages
    _ask(c, cid, "באיזה צבעים יש את NIRO?", email=EMAIL)
    resend = lambda: c.post(f"/api/conversations/{cid}/email/verify", json={"email": EMAIL})   # noqa: E731

    assert resend().status_code == 200 and len(sent) == 1       # the first link is still valid: no second one

    def expire():
        with _db(c) as s, s.begin():
            s.execute(update(EmailLinkRow).values(expires_at=utcnow() - timedelta(minutes=1)))

    expire()
    assert resend().status_code == 200 and len(sent) == 2
    assert c.post("/api/conversations/return", json={"token": _token(sent[0])}).status_code == 410   # old: void
    for _ in range(10):                                         # at most MAX_VERIFY_SENDS links in all
        expire()
        resend()
    assert len(sent) == mail.MAX_VERIFY_SENDS
    expire()
    with _db(c) as s, s.begin():                                # the last one is valid again: it verifies
        s.execute(update(EmailLinkRow).values(expires_at=utcnow() + timedelta(hours=1)))
    assert c.post("/api/conversations/return", json={"token": _token(sent[-1])}).status_code == 200
    stranger = _client(engine).post(f"/api/conversations/{cid}/email/verify", json={"email": EMAIL})
    assert stranger.status_code == 404                          # another employee's cookie: never theirs


def test_notices_waiting_for_verification_never_hold_back_the_queue(engine):
    c = _client(engine)
    waiting = _present(c)                                      # an employee who never verifies
    notifier = _notifier(c)
    with _db(c) as s, s.begin():                               # 30 notices queued the old way (PENDING)
        for n in range(30):
            s.add(EmailOutboxRow(dedupe_key=f"answer:legacy-{n}", conversation_id=__import__("uuid").UUID(waiting),
                                 purpose="ANSWER", recipient_ciphertext=notifier._encrypt(EMAIL),
                                 token_ciphertext=notifier._encrypt("t"), status="PENDING", attempts=0,
                                 next_attempt_at=utcnow() - timedelta(hours=1)))

    other = _client(engine)
    cid = _present(other)
    _ask(other, cid, "באיזה צבעים יש את NIRO?", email="second@example.com")

    assert [m["to"] for m in _notifier(other).fake_messages] == ["second@example.com"]   # not starved
    assert {status for _, status in _outbox(c, conversation_id=__import__("uuid").UUID(waiting))} == {"AWAITING"}


def test_a_key_that_no_longer_decrypts_never_loses_the_answer(engine):
    c = _client(engine)
    cid = _present(c)
    _ask(c, cid, "באיזה צבעים יש את NIRO?", email=EMAIL)
    assert c.post("/api/conversations/return", json={"token": _token(_notifier(c).fake_messages[0])}).status_code == 200
    with _db(c) as s, s.begin():                               # a notice queued under the old key
        s.add(EmailOutboxRow(dedupe_key="answer:old-key", conversation_id=__import__("uuid").UUID(cid),
                             purpose="ANSWER", recipient_ciphertext=_notifier(c)._encrypt(EMAIL),
                             token_ciphertext=_notifier(c)._encrypt("t"), status="PENDING", attempts=0,
                             next_attempt_at=utcnow()))
    _notifier(c).fernet = Fernet(Fernet.generate_key())        # the key was rotated

    _answer_all(c, "לבן ושחור")

    assert any("לבן ושחור" in m for m in _messages(c, cid, "rep"))     # the answer is in the chat
    view = c.get(f"/api/conversations/{cid}")
    assert view.status_code == 200 and view.json()["rep_email_verified"] is False   # not a 500
    assert ("ANSWER", "FAILED") in _outbox(c, dedupe_key="answer:old-key")        # dropped, not retried forever


def test_smtp_is_never_called_inside_a_request_and_its_failure_loses_nothing(engine, monkeypatch):
    calls = []

    class Down:
        def __init__(self, *args, **kwargs):
            calls.append(1)
            raise OSError("smtp down")

    monkeypatch.setattr(mail.smtplib, "SMTP", Down)
    c = _client(engine, **SMTP)
    cid = _present(c)
    assert c.post(f"/api/conversations/{cid}/rep-question",
                  json={"question": "באיזה צבעים יש את NIRO?", "intent": "general"}).status_code == 422
    _ask(c, cid, "באיזה צבעים יש את NIRO?", email=EMAIL)
    _answer_all(c, "לבן ושחור")
    assert calls == []                                         # the worker sends; the requests never wait for it

    _notifier(c).process_pending()                             # what the worker does
    assert calls and {status for _, status in _outbox(c, purpose="VERIFY")} == {"RETRY"}
    assert [m for m in _messages(c, cid, "rep") if "לבן ושחור" in m]       # the answer stays in the chat
    assert c.get(f"/api/conversations/{cid}").json()["rep_email_delivery"] == "enabled"


@pytest.mark.parametrize("backend", ["fake", "disabled"])
def test_an_invalid_address_saves_nothing_only_when_mail_is_on(engine, backend):
    c = _client(engine, email_backend=backend, email_encryption_key=KEY, email_public_app_url="http://testserver")
    cid = _present(c)
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "באיזה צבעים יש את NIRO?", "intent": "general", "email": "not-an-email"})
    if backend == "fake":
        assert r.status_code == 422 and c.get("/api/rep/questions", headers=AUTH).json() == []
    else:                                                       # no mail: the address is not used at all
        assert r.status_code == 200
