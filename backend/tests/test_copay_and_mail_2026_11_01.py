"""Today's copay asked with its choices in both modes, and a verification link that can be replaced (1/11).

(1) With a model, the copay question was the model's own words ("מה ההשתתפות? …") next to the three buttons; it is
now the code's question (the catalog amount and its three choices). A car with no catalog amount gets two choices:
a known amount, or the representative; nothing is suggested. (2) A verification email that did not arrive left
"the previous link is still valid" for 24 hours: after RESEND_AFTER a new link replaces it.
"""
from __future__ import annotations

import re
from datetime import timedelta

from app.modules.conversation.email_notifications import RESEND_AFTER
from app.modules.conversation.models import EmailOutboxRow
from app.shared.db import session_factory

from .conftest import make_client
from .test_expects import PROFILE, new, post, say
from .test_inquiry_record import _ask, _client, _code, _compare
from .test_routing_2026_10_31 import at_copay


def test_with_the_model_the_copay_question_is_the_code_s_with_its_three_choices(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    from app.modules.conversation.public import LLMChain
    plain = make_client(engine)
    cid, send, _ = at_copay(plain)          # COROLLA (2026): catalog copay 250; the question on screen
    fake = FakeLLM()
    fake.turn(reply="מה ההשתתפות החודשית שלכם? אם לא יודעים, כתבו אין.")
    model = make_client(engine, llm=LLMChain([fake]))
    model.cookies = plain.cookies
    turn = model.post(f"/api/conversations/{cid}/messages", json={"text": "מה זה אומר?"}).json()
    assert "כתבו אין" not in turn["reply"]
    copay = next(f for f in turn["expects"]["fields"] if f["field"] == "copay")
    assert [c["label"] for c in copay["choices"]] == ["זה הסכום שלי", "הסכום שונה", "לברר מול נציג"]


def test_a_car_with_no_catalog_amount_offers_a_known_amount_or_the_representative(engine):
    client = make_client(engine)
    found = client.get("/api/catalog/search", params={"q": "FABIA"}).json()
    cid, _ = new(client)
    say(client, cid, PROFILE)
    turn = post(client, cid, choice=found[0]["id"]).json()
    copay = next(f for f in turn["expects"]["fields"] if f["field"] == "copay")
    assert [c["label"] for c in copay["choices"]] == ["להזין סכום", "לברר מול נציג"]
    assert "בחרו אחת מהאפשרויות, או כתבו את הסכום." in turn["reply"]
    assert "לפי הקטלוג" not in turn["reply"]                               # nothing suggested
    say(client, cid, "השתתפות 320")
    car = client.get(f"/api/conversations/{cid}").json()["current_car"]
    assert car["copay"] == "320" and car["copay_provisional"] is None


def test_the_representative_choice_with_no_catalog_amount_assumes_nothing(engine):
    client = make_client(engine)
    found = client.get("/api/catalog/search", params={"q": "FABIA"}).json()
    cid, _ = new(client)
    say(client, cid, PROFILE)
    post(client, cid, choice=found[0]["id"])
    post(client, cid, choice="rep", field="copay")
    car = client.get(f"/api/conversations/{cid}").json()["current_car"]
    assert car["copay"] is None and car["copay_provisional"] is None and car["copay_unknown"]


def test_a_verification_link_that_did_not_arrive_can_be_replaced_after_a_while(engine, snapshot):
    c = _client(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    _ask(c, cid, "באיזה צבעים יש את NIRO?", email="employee@example.com")
    mail = c.app.state.ctx.service.email_notifications.fake_messages
    first = re.search(r"return_token=([A-Za-z0-9_-]+)", mail[-1]["body"]).group(1)
    resend = lambda: c.post(f"/api/conversations/{cid}/email/verify", json={}).json()["resend"]   # noqa: E731
    assert resend() == "valid"                                              # a double click: nothing new
    with session_factory(c.app.state.ctx.sessions.kw["bind"])() as s, s.begin():
        for row in s.query(EmailOutboxRow).all():                          # sent a while ago
            row.created_at = row.created_at - RESEND_AFTER - timedelta(seconds=5)
    assert resend() == "queued"                                             # after a while: a new link
    second = re.search(r"return_token=([A-Za-z0-9_-]+)", mail[-1]["body"]).group(1)
    assert second != first
    assert make_client(engine).post("/api/conversations/return", json={"token": first}).status_code == 410
    assert c.post("/api/conversations/return", json={"token": second}).status_code == 200


def test_the_answer_waits_for_the_address_and_is_mailed_once_it_is_verified(engine, snapshot):
    auth = {"X-Rep-Token": "rep-record"}
    c = _client(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    _ask(c, cid, "יש ל-NIRO גג נפתח?", email="employee@example.com")
    mail = c.app.state.ctx.service.email_notifications.fake_messages
    token = re.search(r"return_token=([A-Za-z0-9_-]+)", mail[-1]["body"]).group(1)
    [q] = c.get("/api/rep/questions?status=OPEN", headers=auth).json()
    c.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "כן."}, headers=auth)
    assert not any("תשובה" in m["subject"] for m in mail)                   # not verified yet: held, not sent
    assert c.post("/api/conversations/return", json={"token": token}).status_code == 200
    c.app.state.ctx.service.email_notifications.process_pending()
    assert sum("תשובה" in m["subject"] for m in mail) == 1                  # verified: sent once
