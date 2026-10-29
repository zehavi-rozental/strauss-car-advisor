from __future__ import annotations

import re
import uuid
from datetime import timedelta

from cryptography.fernet import Fernet

from app.modules.conversation.models import ConversationRow, EmailLinkRow, EmailOutboxRow
from app.shared.db import session_factory, utcnow
from .conftest import make_client

TOKEN = "rep-email-test"
EMAIL = "employee@example.com"
KEY = Fernet.generate_key().decode("ascii")
AUTH = {"X-Rep-Token": TOKEN}
PROFILE = ["מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
           "יש לי קורולה 2026, משלמת 280"]


def _client(engine):
    return make_client(engine, rep_access_token=TOKEN, email_backend="fake",
                       email_encryption_key=KEY, email_public_app_url="http://testserver")


def _present(c):
    cid = c.post("/api/conversations").json()["conversation_id"]
    for message in PROFILE:
        c.post(f"/api/conversations/{cid}/messages", json={"text": message})
    c.post(f"/api/conversations/{cid}/messages", json={"choices": ["CAR-05"], "field": "options"})
    return cid


def _ask(c, cid):
    response = c.post(f"/api/conversations/{cid}/rep-question", json={
        "question": "האם הרכב זמין בצבע כחול?", "intent": "general", "email": EMAIL,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _link(message):
    match = re.search(r"return_token=([A-Za-z0-9_-]+)", message["body"])
    assert match is not None
    return match.group(1)


def test_verified_email_link_resumes_conversation_on_another_device_and_answer_sends_one_notice(engine):
    employee = _client(engine)
    conversation_id = _present(employee)
    result = _ask(employee, conversation_id)
    assert result["email_status"] == "sent"

    service = employee.app.state.ctx.service.email_notifications
    verification_messages = service.fake_messages
    assert len(verification_messages) == 1
    assert verification_messages[0]["to"] == EMAIL
    assert verification_messages[0]["subject"]

    # The email is not stored in cleartext, and the one-time link gives a new device a session cookie.
    with session_factory(employee.app.state.ctx.sessions.kw["bind"])() as session:
        conversation = session.get(ConversationRow, uuid.UUID(conversation_id))
        assert conversation is not None and EMAIL not in conversation.email_ciphertext

    other_device = _client(engine)
    returned = other_device.post("/api/conversations/return", json={"token": _link(verification_messages[0])})
    assert returned.status_code == 200
    assert returned.json()["conversation_id"] == conversation_id
    assert other_device.get(f"/api/conversations/{conversation_id}").status_code == 200
    assert other_device.get(f"/api/conversations/{conversation_id}").json()["rep_email_verified"] is True
    assert other_device.post("/api/conversations/return",
                             json={"token": _link(verification_messages[0])}).status_code == 410

    [question] = employee.get("/api/rep/questions", headers=AUTH).json()
    answer = employee.post(f"/api/rep/questions/{question['id']}/answer", headers=AUTH,
                           json={"value": "כן, הצבע הכחול זמין"})
    assert answer.status_code == 200
    assert len(service.fake_messages) == 2
    notice = service.fake_messages[1]
    assert notice["to"] == EMAIL
    assert question["id"] not in notice["body"]  # tokens identify the conversation, not leak question identifiers

    final_device = _client(engine)
    returned = final_device.post("/api/conversations/return", json={"token": _link(notice)})
    assert returned.status_code == 200
    updates = final_device.get(f"/api/conversations/{conversation_id}/updates").json()
    assert any("הצבע הכחול זמין" in message["content"] for message in updates["messages"])

    with session_factory(employee.app.state.ctx.sessions.kw["bind"])() as session:
        outbox = list(session.query(EmailOutboxRow).all())
        assert [row.status for row in outbox] == ["SENT", "SENT"]
        assert all(not row.recipient_ciphertext and not row.token_ciphertext for row in outbox)


def test_email_failure_retries_with_backoff_and_does_not_duplicate_outbox(engine, monkeypatch):
    c = _client(engine)
    conversation_id = _present(c)
    service = c.app.state.ctx.service.email_notifications

    def fail(*_args):
        raise OSError("provider unavailable")

    monkeypatch.setattr(service, "_send", fail)
    result = _ask(c, conversation_id)
    assert result["email_status"] == "pending"
    with session_factory(c.app.state.ctx.sessions.kw["bind"])() as session, session.begin():
        [row] = session.query(EmailOutboxRow).all()
        assert row.status == "RETRY" and row.attempts == 1
        assert row.last_error == "OSError"
        row.next_attempt_at = utcnow() - timedelta(seconds=1)

    monkeypatch.undo()
    assert service.process_pending() == 1
    assert len(service.fake_messages) == 1
    _ask(c, conversation_id)  # same open question: no duplicate verification email
    assert len(service.fake_messages) == 1
    with session_factory(c.app.state.ctx.sessions.kw["bind"])() as session:
        rows = list(session.query(EmailOutboxRow).all())
        assert len(rows) == 1 and rows[0].status == "SENT" and rows[0].attempts == 2


def test_email_return_rejects_modified_and_expired_tokens(engine):
    c = _client(engine)
    conversation_id = _present(c)
    _ask(c, conversation_id)
    [message] = c.app.state.ctx.service.email_notifications.fake_messages
    raw_token = _link(message)
    altered = ("A" if raw_token[0] != "A" else "B") + raw_token[1:]

    assert c.post("/api/conversations/return", json={"token": altered}).status_code == 410
    with session_factory(c.app.state.ctx.sessions.kw["bind"])() as session, session.begin():
        link = session.query(EmailLinkRow).one()
        link.expires_at = utcnow() - timedelta(seconds=1)
    assert c.post("/api/conversations/return", json={"token": raw_token}).status_code == 410


def test_answer_email_failure_does_not_undo_answer_and_retry_sends_once(engine, monkeypatch):
    employee = _client(engine)
    conversation_id = _present(employee)
    _ask(employee, conversation_id)
    service = employee.app.state.ctx.service.email_notifications
    verification = service.fake_messages[0]

    other_device = _client(engine)
    assert other_device.post("/api/conversations/return", json={"token": _link(verification)}).status_code == 200
    [question] = employee.get("/api/rep/questions", headers=AUTH).json()

    def fail(*_args):
        raise OSError("provider unavailable")

    monkeypatch.setattr(service, "_send", fail)
    answer_text = "כן, הצבע הכחול זמין"
    response = employee.post(f"/api/rep/questions/{question['id']}/answer", headers=AUTH,
                             json={"value": answer_text})
    assert response.status_code == 200
    updates = employee.get(f"/api/conversations/{conversation_id}/updates").json()["messages"]
    answer_messages = [message for message in updates if message.get("rep_question_id") == question["id"]
                       and message["source"] == "rep"]
    assert len(answer_messages) == 1 and answer_text in answer_messages[0]["content"]

    with session_factory(employee.app.state.ctx.sessions.kw["bind"])() as session, session.begin():
        answer_outbox = session.query(EmailOutboxRow).filter_by(purpose="ANSWER").one()
        assert answer_outbox.status == "RETRY" and answer_outbox.attempts == 1
        answer_outbox.next_attempt_at = utcnow() - timedelta(seconds=1)

    monkeypatch.undo()
    assert service.process_pending() == 1
    assert service.process_pending() == 0
    assert len(service.fake_messages) == 2
    notice = service.fake_messages[1]
    assert notice["to"] == EMAIL
    assert answer_text not in notice["body"] and "18500" not in notice["body"]
    assert question["question_text"] not in notice["body"]
    assert employee.post(f"/api/rep/questions/{question['id']}/answer", headers=AUTH,
                         json={"value": answer_text}).status_code == 409
    assert len(service.fake_messages) == 2
    with session_factory(employee.app.state.ctx.sessions.kw["bind"])() as session:
        outbox = list(session.query(EmailOutboxRow).order_by(EmailOutboxRow.created_at).all())
        assert [row.status for row in outbox] == ["SENT", "SENT"]
        assert [row.attempts for row in outbox] == [1, 2]


def test_answer_notice_waits_for_email_verification_without_spending_retries(engine):
    employee = _client(engine)
    conversation_id = _present(employee)
    _ask(employee, conversation_id)
    service = employee.app.state.ctx.service.email_notifications
    verification = service.fake_messages[0]
    [question] = employee.get("/api/rep/questions", headers=AUTH).json()

    answer = employee.post(f"/api/rep/questions/{question['id']}/answer", headers=AUTH,
                           json={"value": "כן, הצבע הכחול זמין"})
    assert answer.status_code == 200
    assert len(service.fake_messages) == 1
    with session_factory(employee.app.state.ctx.sessions.kw["bind"])() as session:
        answer_notice = session.query(EmailOutboxRow).filter_by(purpose="ANSWER").one()
        # out of the sending queue until verified (AWAITING), so it never holds back other mail
        assert answer_notice.status == "AWAITING" and answer_notice.attempts == 0

    other_device = _client(engine)
    returned = other_device.post("/api/conversations/return", json={"token": _link(verification)})
    assert returned.status_code == 200
    delivery = other_device.app.state.ctx.service.email_notifications
    assert len(service.fake_messages) == 1
    assert len(delivery.fake_messages) == 1
    assert delivery.fake_messages[0]["to"] == EMAIL
    with session_factory(employee.app.state.ctx.sessions.kw["bind"])() as session:
        answer_notice = session.query(EmailOutboxRow).filter_by(purpose="ANSWER").one()
        assert answer_notice.status == "SENT" and answer_notice.attempts == 1


def test_email_is_required_for_real_delivery_and_invalid_address_is_rejected(engine):
    c = make_client(engine, rep_access_token=TOKEN, email_backend="smtp",
                    email_encryption_key=KEY, email_smtp_host="smtp.example.test",
                    email_from="advisor@example.test", email_public_app_url="https://advisor.example.test")
    cid = _present(c)
    missing = c.post(f"/api/conversations/{cid}/rep-question",
                     json={"question": "האם הרכב זמין?", "intent": "general"})
    assert missing.status_code == 422
    invalid = c.post(f"/api/conversations/{cid}/rep-question", json={
        "question": "האם הרכב זמין?", "intent": "general", "email": "not-an-email",
    })
    assert invalid.status_code == 422
    assert c.get("/api/rep/questions", headers=AUTH).json() == []
