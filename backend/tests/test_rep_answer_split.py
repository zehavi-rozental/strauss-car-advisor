"""The representative's answer and the advisor's next step are two messages (UX, 30/10).

The answer (and the representative's note) stays a `rep` message, linked to its question; the advisor's own next
step (the flow question, the results, the map) is a separate `template` message right after it, so a question about
pay never reads as the representative asking it. Both are written in the answer's transaction or not at all; a
repeated event, a refresh or a poll never adds them again; message_count (the employee's turns) is unchanged; a
conversation stored before this change still reads as it did (nothing is migrated).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.cases.public import MissingField, QuestionAnswered
from app.modules.conversation.models import ConversationRow, MessageRow
from app.modules.conversation.repository import ConversationRepository

from .conftest import make_client

TOKEN = "rep-split"
AUTH = {"X-Rep-Token": TOKEN}
QUESTION = "האם אפשר להתקין וו גרירה ברכב החברה?"
ANSWER = "זוהי תשובת בדיקה.\nיש לפנות למחלקת הרכב לקבלת אישור."
NOTE = "אפשר גם לשאול אותי שוב בצ'אט."
PROFILE = ["מנהלת מוצר, שכר בסיס 18500",
           "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
           "יש לי קורולה קרוס, משלמת 300"]


@pytest.fixture
def client(engine):
    return make_client(engine, rep_access_token=TOKEN)


def _ask(c, cid):
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": QUESTION, "intent": "general", "email": "employee@example.com"})
    assert r.status_code == 200 and r.json()["status"] == "created"
    [q] = c.get("/api/rep/questions", headers=AUTH).json()
    return q


def _messages(c, cid):
    return c.get(f"/api/conversations/{cid}").json()["messages"]


def _message_count(engine, cid):
    with Session(engine) as s:
        return s.scalar(select(ConversationRow.message_count).where(ConversationRow.id == uuid.UUID(cid)))


def test_the_answer_and_the_next_question_are_two_messages_once(client, engine):
    cid = client.post("/api/conversations").json()["conversation_id"]
    q = _ask(client, cid)
    before = _messages(client, cid)
    count = _message_count(engine, cid)

    r = client.post(f"/api/rep/questions/{q['id']}/answer", headers=AUTH, json={"value": ANSWER, "note": NOTE})
    assert r.status_code == 200

    for _ in range(2):                                    # a refresh reads the same, once
        msgs = _messages(client, cid)
        rep, follow = msgs[len(before):]
        assert (rep["source"], rep["rep_question_id"]) == ("rep", q["id"])
        assert ANSWER in rep["content"] and f"הערת הנציג: {NOTE}" in rep["content"]
        assert "שכר" not in rep["content"] and "כתבו" not in rep["content"]   # nothing the advisor asks
        assert follow["source"] == "template" and follow["rep_question_id"] is None
        assert follow["seq"] == rep["seq"] + 1 and follow["role"] == "assistant"
        assert ANSWER not in follow["content"] and "קבוצת הזכאות" in follow["content"]
        # the inquiry record still points to this answer: one rep message for the question
        assert [m["seq"] for m in msgs if m["source"] == "rep" and m["rep_question_id"] == q["id"]] == [rep["seq"]]
    # polling after the inquiry brings both, in order, once
    updates = client.get(f"/api/conversations/{cid}/updates?after={before[-1]['seq']}").json()["messages"]
    assert [m["source"] for m in updates] == ["rep", "template"]
    assert client.get(f"/api/conversations/{cid}/updates?after={updates[-1]['seq']}").json()["messages"] == []
    assert _message_count(engine, cid) == count            # the employee's turns: unchanged


def test_an_answer_while_results_are_shown_adds_no_follow_up(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in PROFILE:
        client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    body = client.post(f"/api/conversations/{cid}/messages", json={"text": "אטו 3"}).json()
    assert body["stage"] == "PRESENTED"
    q = _ask(client, cid)
    before = _messages(client, cid)

    assert client.post(f"/api/rep/questions/{q['id']}/answer", headers=AUTH, json={"value": ANSWER}).status_code == 200

    [rep] = _messages(client, cid)[len(before):]          # the results stay on screen; nothing else to ask
    assert rep["source"] == "rep" and ANSWER in rep["content"]


def test_the_same_event_applied_again_adds_nothing(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    q = _ask(client, cid)
    service = client.app.state.ctx.service
    event = QuestionAnswered(conversation_id=cid, question_id=q["id"], missing_field=MissingField.GENERAL_QUESTION,
                             option_id=q["option_id"], value=ANSWER, effective_date=None)
    assert service.apply_answer(event) is True
    after_first = _messages(client, cid)
    assert service.apply_answer(event) is False            # idempotent: no second answer, no second follow-up
    assert _messages(client, cid) == after_first
    assert [m["source"] for m in after_first[-2:]] == ["rep", "template"]


def test_both_messages_or_neither(client, monkeypatch):
    cid = client.post("/api/conversations").json()["conversation_id"]
    q = _ask(client, cid)
    service = client.app.state.ctx.service
    event = QuestionAnswered(conversation_id=cid, question_id=q["id"], missing_field=MissingField.GENERAL_QUESTION,
                             option_id=q["option_id"], value=ANSWER, effective_date=None)
    before = _messages(client, cid)
    original = ConversationRepository.add_message

    def failing(self, conversation_id, role, content, source, **kw):
        if source == "template":
            raise RuntimeError("write failed")
        return original(self, conversation_id, role, content, source, **kw)

    monkeypatch.setattr(ConversationRepository, "add_message", failing)
    with pytest.raises(RuntimeError):
        service.apply_answer(event)
    assert _messages(client, cid) == before                # the answer was not written without its follow-up
    monkeypatch.setattr(ConversationRepository, "add_message", original)
    assert service.apply_answer(event) is True              # not marked applied: it applies once, whole
    assert [m["source"] for m in _messages(client, cid)[len(before):]] == ["rep", "template"]


def test_a_conversation_stored_before_the_change_reads_as_before(client, engine):
    """A rep message that holds the answer and the old follow-up together is served as it was saved."""
    cid = client.post("/api/conversations").json()["conversation_id"]
    legacy = f"נציג התחבורה ענה על השאלה שלכם:\n{ANSWER}\n\nמה קבוצת הזכאות שלכם?"
    with Session(engine) as s, s.begin():
        ConversationRepository(s).add_message(uuid.UUID(cid), "assistant", legacy, "rep",
                                              rep_question_id=str(uuid.uuid4()))
    last = _messages(client, cid)[-1]
    assert (last["source"], last["content"]) == ("rep", legacy)
    assert len(list(Session(engine).scalars(select(MessageRow).where(
        MessageRow.conversation_id == uuid.UUID(cid))))) == len(_messages(client, cid))
