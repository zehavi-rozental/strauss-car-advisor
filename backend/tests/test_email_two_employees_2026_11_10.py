"""Two employees, two addresses: verification, a change of address, the answer email and its link (10/11).

Fake mail only (nothing leaves the machine). One app, two browsers (two session cookies), as on a real server. What the
product owner asked for: any valid address of the employee's own; an inquiry may be sent before (or without) a verified
address, but nothing is mailed to an unverified one; a change of address must be verified again; the answer email
names the question and the answer and opens that answer in that conversation; no duplicates; no access by changing ids.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.modules.conversation.email_notifications import sender_header
from app.modules.conversation.models import EmailLinkRow
from app.shared.db import session_factory, utcnow

from .conftest import make_client

TOKEN = "rep-two-employees"
AUTH = {"X-Rep-Token": TOKEN}
KEY = Fernet.generate_key().decode("ascii")
DANA, YOSSI, DANA_NEW = "dana.levi@example.com", "yossi.cohen@example.org", "dana.l@example.net"
PROFILE = ["מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
           "יש לי קורולה 2026, משלמת 280"]


def server(engine):
    first = make_client(engine, rep_access_token=TOKEN, email_backend="fake", email_encryption_key=KEY,
                        email_public_app_url="http://testserver")
    return first, TestClient(first.app)          # two browsers, one server


def mails(c):
    return c.app.state.ctx.service.email_notifications.fake_messages


def present(c):
    cid = c.post("/api/conversations").json()["conversation_id"]
    for message in PROFILE:
        c.post(f"/api/conversations/{cid}/messages", json={"text": message})
    c.post(f"/api/conversations/{cid}/messages", json={"choices": ["CAR-05"], "field": "options"})
    return cid


def ask(c, cid, question, email=None):
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": question, "intent": "general", **({"email": email} if email else {})})
    return r


def link(message):
    return re.search(r"return_token=([A-Za-z0-9_-]+)", message["body"]).group(1)


def to(c, address):
    return [m for m in mails(c) if m["to"] == address]


def answer(c, question_id, text):
    r = c.post(f"/api/rep/questions/{question_id}/answer", headers=AUTH, json={"value": text})
    assert r.status_code == 200, r.text


def question_id(c, cid):
    return next(q["id"] for q in c.get(f"/api/conversations/{cid}").json()["rep_questions"])


def test_two_employees_each_verify_their_own_address_and_get_only_their_own_answer(engine):
    dana, yossi = server(engine)
    a, b = present(dana), present(yossi)
    assert ask(dana, a, "האם הרכב זמין בצבע כחול?", DANA).status_code == 200
    assert ask(yossi, b, "יש ל-MG4 גג נפתח?", YOSSI).status_code == 200
    assert [m["subject"] for m in to(dana, DANA)] == ["STRAUSS DRIVE | אימות כתובת האימייל"]
    assert len(to(dana, YOSSI)) == 1                                       # any valid address, no allowlist
    assert dana.post("/api/conversations/return", json={"token": link(to(dana, DANA)[0])}).status_code == 200
    assert yossi.post("/api/conversations/return", json={"token": link(to(yossi, YOSSI)[0])}).status_code == 200

    answer(dana, question_id(dana, a), "כן, הצבע הכחול זמין")
    answer(yossi, question_id(yossi, b), "לא, אין גג נפתח בדגם הזה")
    [notice_a] = [m for m in to(dana, DANA) if "תשובה" in m["subject"]]
    [notice_b] = [m for m in to(dana, YOSSI) if "תשובה" in m["subject"]]
    assert "האם הרכב זמין בצבע כחול?" in notice_a["body"] and "כן, הצבע הכחול זמין" in notice_a["body"]
    assert "גג נפתח" not in notice_a["body"] and "כחול" not in notice_b["body"]        # each one's own only
    assert "לצפייה בתשובה בצ'אט" in notice_a["body"]

    # the answer link opens that conversation at that answer; Yossi's browser cannot open Dana's conversation
    opened = yossi.post("/api/conversations/return", json={"token": link(notice_b)})
    assert opened.json()["conversation_id"] == b and opened.json()["question_id"] == question_id(yossi, b)
    assert yossi.get(f"/api/conversations/{a}").status_code in (403, 404)
    assert yossi.get(f"/api/conversations/{a}/updates?after=0").status_code in (403, 404)
    assert ask(yossi, a, "האם יש וו גרירה?").status_code in (403, 404)
    assert dana.get(f"/api/conversations/{str(uuid.uuid4())}").status_code in (403, 404)


def test_an_inquiry_goes_before_verification_and_nothing_is_mailed_to_an_unverified_address(engine):
    dana, _ = server(engine)
    a = present(dana)
    assert ask(dana, a, "האם הרכב זמין בצבע כחול?").status_code == 200           # no address at all: sent
    view = dana.get(f"/api/conversations/{a}").json()
    assert view["rep_questions"] and view["rep_email_verified"] is False
    added = dana.post(f"/api/conversations/{a}/email/verify", json={"email": DANA}).json()
    assert added["resend"] == "queued" and dana.get(f"/api/conversations/{a}").json()["rep_email_pending"] is True
    answer(dana, question_id(dana, a), "כן")
    assert [m["subject"] for m in to(dana, DANA)] == ["STRAUSS DRIVE | אימות כתובת האימייל"]   # the answer waits
    dana.post("/api/conversations/return", json={"token": link(to(dana, DANA)[0])})
    assert len([m for m in to(dana, DANA) if "תשובה" in m["subject"]]) == 1            # released once verified


def test_a_change_of_address_is_verified_again_and_the_waiting_answer_goes_to_the_new_one_only(engine):
    dana, _ = server(engine)
    a = present(dana)
    ask(dana, a, "האם הרכב זמין בצבע כחול?", DANA)
    dana.post("/api/conversations/return", json={"token": link(to(dana, DANA)[0])})
    assert dana.get(f"/api/conversations/{a}").json()["rep_email"] == DANA
    changed = dana.post(f"/api/conversations/{a}/email/verify", json={"email": DANA_NEW})
    assert changed.status_code == 200 and changed.json()["resend"] == "queued"
    view = dana.get(f"/api/conversations/{a}").json()
    assert view["rep_email_verified"] is False and view["rep_email"] is None            # the old one is not named
    answer(dana, question_id(dana, a), "כן, זמין")
    assert not [m for m in mails(dana) if "תשובה" in m["subject"]]                       # nothing before verification
    old_link = link(to(dana, DANA)[0])
    assert dana.post("/api/conversations/return", json={"token": old_link}).status_code == 410   # used, and old
    dana.post("/api/conversations/return", json={"token": link(to(dana, DANA_NEW)[0])})
    [notice] = [m for m in mails(dana) if "תשובה" in m["subject"]]
    assert notice["to"] == DANA_NEW                                                      # never the old address
    reopened = dana.post("/api/conversations/return", json={"token": link(notice)})
    assert reopened.status_code == 200 and reopened.json()["question_id"] == question_id(dana, a)


def test_invalid_addresses_are_refused_and_save_nothing(engine):
    dana, _ = server(engine)
    a = present(dana)
    for bad in ("not-an-email", "x<victim@example.com>", "victim@example.com(c)", "a@b", "two@@example.com"):
        r = ask(dana, a, "האם הרכב זמין בצבע כחול?", bad)
        assert r.status_code == 422, bad
        assert "אינה תקינה" in r.text
    assert dana.get(f"/api/conversations/{a}").json()["rep_questions"] == []
    assert mails(dana) == []


def test_an_expired_link_is_refused_and_a_new_one_can_be_asked(engine):
    dana, _ = server(engine)
    a = present(dana)
    ask(dana, a, "האם הרכב זמין בצבע כחול?", DANA)
    with session_factory(dana.app.state.ctx.sessions.kw["bind"])() as session, session.begin():
        session.execute(update(EmailLinkRow).values(expires_at=utcnow() - timedelta(minutes=1)))
    assert dana.post("/api/conversations/return", json={"token": link(to(dana, DANA)[0])}).status_code == 410
    again = dana.post(f"/api/conversations/{a}/email/verify", json={}).json()
    assert again["resend"] == "queued" and len(to(dana, DANA)) == 2
    assert dana.post("/api/conversations/return", json={"token": link(to(dana, DANA)[1])}).status_code == 200


def test_no_duplicate_mail_for_the_same_answer_or_a_repeated_send(engine):
    dana, _ = server(engine)
    a = present(dana)
    ask(dana, a, "האם הרכב זמין בצבע כחול?", DANA)
    ask(dana, a, "האם הרכב זמין בצבע כחול?", DANA)                     # the same inquiry again: exists, no new link
    assert len(to(dana, DANA)) == 1
    dana.post("/api/conversations/return", json={"token": link(to(dana, DANA)[0])})
    qid = question_id(dana, a)
    answer(dana, qid, "כן")
    assert dana.post(f"/api/rep/questions/{qid}/answer", headers=AUTH, json={"value": "כן"}).status_code == 409
    dana.app.state.ctx.service.email_notifications.process_pending()
    assert len([m for m in mails(dana) if "תשובה" in m["subject"]]) == 1


def test_one_sender_name_for_both_mails():
    assert sender_header("noreply@send.example.com") == sender_header("Other <noreply@send.example.com>")
    assert sender_header("noreply@send.example.com").endswith("<noreply@send.example.com>")
