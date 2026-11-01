"""The representative policy, the gaps closed on 1/11.

(1) A car's property the catalog does not hold (colour, sunroof, equipment, cameras, comfort, availability) is never
answered from the model's own knowledge: when the model answers it without offering the representative, the offer
replaces that answer (the same as without a model). (2) An inquiry sent from the chat ("לשלוח") has no address:
one can be added afterwards and verified by its link, so the answer can still be mailed.
"""
from __future__ import annotations

import re

import pytest

from .conftest import make_client
from .test_inquiry_record import _client, _code, _compare

AUTH = {"X-Rep-Token": "rep-record"}
OFFER = "רוצים שאשאל אותו"


@pytest.mark.parametrize("question", ["יש מצלמת רוורס ב-NIRO?", "יש ל-NIRO גג נפתח?", "באיזה צבעים יש את NIRO?",
                                      "מה זמן האספקה של NIRO?", "מתי אפשר לקבל את NIRO?", "איך הנוחות ב-NIRO?"])
def test_a_property_the_catalog_does_not_hold_is_offered_to_the_representative(engine, snapshot, question):
    c = _client(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    turn = c.post(f"/api/conversations/{cid}/messages", json={"text": question}).json()
    assert any(f["field"] == "ask_rep" for f in turn["expects"]["fields"]), turn["reply"]


def test_with_the_model_an_unverified_answer_about_a_property_becomes_the_offer(engine, snapshot):
    from app.modules.conversation.llm.fake import FakeLLM
    from app.modules.conversation.public import LLMChain
    plain = _client(engine)
    cid = _compare(plain, [_code(snapshot, "NIRO")])
    fake = FakeLLM()
    fake.turn(reply="כן, ל-NIRO יש גרסה עם גג נפתח ומצלמת רוורס.")     # nothing verified says so
    model = make_client(engine, llm=LLMChain([fake]), rep_access_token="rep-record")
    model.cookies = plain.cookies
    turn = model.post(f"/api/conversations/{cid}/messages", json={"text": "יש ל-NIRO גג נפתח?"}).json()
    assert "כן, ל-NIRO יש" not in turn["reply"] and OFFER in turn["reply"]
    assert any(f["field"] == "ask_rep" for f in turn["expects"]["fields"])


def test_an_inquiry_sent_from_the_chat_can_get_an_address_afterwards(engine, snapshot):
    c = _client(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    say = lambda body: c.post(f"/api/conversations/{cid}/messages", json=body).json()   # noqa: E731
    say({"text": "יש ל-NIRO גג נפתח?"})
    say({"choice": "ask_rep", "field": "ask_rep"})
    sent = say({"choice": "send", "field": "rep_question"})
    assert "שלחתי את השאלה לנציג התחבורה" in sent["reply"]
    view = c.get(f"/api/conversations/{cid}").json()
    assert view["rep_email_delivery"] == "enabled" and not view["rep_email_pending"]   # no address yet
    r = c.post(f"/api/conversations/{cid}/email/verify", json={"email": "later@example.test"})
    assert r.status_code == 200 and r.json()["resend"] == "queued"
    mail = c.app.state.ctx.service.email_notifications.fake_messages
    token = re.search(r"return_token=([A-Za-z0-9_-]+)", mail[-1]["body"]).group(1)
    assert mail[-1]["to"] == "later@example.test"
    assert c.post("/api/conversations/return", json={"token": token}).status_code == 200
    [q] = c.get("/api/rep/questions?status=OPEN", headers=AUTH).json()
    c.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "כן."}, headers=AUTH)
    assert any("תשובה" in m["subject"] and m["to"] == "later@example.test" for m in mail)   # the answer is mailed
