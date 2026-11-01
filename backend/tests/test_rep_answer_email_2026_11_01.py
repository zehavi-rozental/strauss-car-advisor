"""The representative's answer by email (1/11): the notice names the employee's own question, opens the conversation at
that very answer, never carries pay data or the answer itself, is sent once per answer, and never points a production
reader to a local address. Fake mail only (messages kept in memory); no real email.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.modules.conversation.email_notifications import EmailNotificationService, local_url, question_for_email

from .conftest import make_client
from .test_inquiry_record import EMAIL, _ask, _client, _code, _compare

AUTH = {"X-Rep-Token": "rep-record"}
KEY = Fernet.generate_key().decode("ascii")     # one server: every device reads the same encrypted address


def _server(engine):
    return _client(engine, email_encryption_key=KEY)


def _device(engine):
    return make_client(engine, rep_access_token="rep-record", email_encryption_key=KEY)


def _verified(c, cid, snapshot):
    """An inquiry with an address, the address verified through its link: answer notices go out from here on."""
    _ask(c, cid, "האם יש רכב עם גג נפתח?", email=EMAIL)
    token = re.search(r"return_token=([A-Za-z0-9_-]+)", _mail(c)[-1]["body"]).group(1)
    assert c.post("/api/conversations/return", json={"token": token}).status_code == 200


def _mail(c):
    return c.app.state.ctx.service.email_notifications.fake_messages


def _answer(c, text):
    """The representative answers the newest open question with this text; returns its id."""
    q = [q for q in c.get("/api/rep/questions?status=OPEN", headers=AUTH).json()][-1]
    assert c.post(f"/api/rep/questions/{q['id']}/answer", json={"value": text}, headers=AUTH).status_code == 200
    return q["id"]


def test_the_notice_names_the_question_and_opens_the_conversation_at_that_answer(engine, snapshot):
    c = _server(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    _verified(c, cid, snapshot)
    qid = _answer(c, "כן, יש גרסה עם גג נפתח.\nהתוספת מטופלת מול היבואן.")
    [notice] = [m for m in _mail(c) if "תשובה" in m["subject"]]
    assert notice["to"] == EMAIL
    assert "השאלה שלכם: האם יש רכב עם גג נפתח?" in notice["body"]
    assert "לצפייה בתשובת הנציג" in notice["body"] and "לצפייה בתשובת הנציג" in notice["html"]
    assert "התוספת מטופלת" not in notice["body"] + notice["html"]            # the answer is read in the conversation
    assert "18500" not in notice["body"] and "18,500" not in notice["body"]   # never pay data
    token = re.search(r"return_token=([A-Za-z0-9_-]+)", notice["body"]).group(1)
    fresh = _device(engine)                                                 # another device: no cookie of its own
    back = fresh.post("/api/conversations/return", json={"token": token}).json()
    assert back == {"conversation_id": cid, "question_id": qid} or (back["conversation_id"], back["question_id"]) == (cid, qid)
    assert fresh.get(f"/api/conversations/{cid}").status_code == 200        # the link opened it on that device


def test_two_inquiries_each_link_to_its_own_answer_once(engine, snapshot):
    c = _server(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    _verified(c, cid, snapshot)
    first = _answer(c, "כן, יש.")
    _ask(c, cid, "האם יש וו גרירה ב-NIRO?")
    second = _answer(c, "לא, אין.")
    notices = [m for m in _mail(c) if "תשובה" in m["subject"]]
    assert len(notices) == 2                                                # one per answer, never doubled
    targets = []
    for n in notices:
        token = re.search(r"return_token=([A-Za-z0-9_-]+)", n["body"]).group(1)
        targets.append(_device(engine).post("/api/conversations/return", json={"token": token}).json()["question_id"])
    assert targets == [first, second]


def test_a_link_works_once_and_a_changed_one_opens_nothing(engine, snapshot):
    c = _server(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    _verified(c, cid, snapshot)
    _answer(c, "כן.")
    token = re.search(r"return_token=([A-Za-z0-9_-]+)", [m for m in _mail(c) if "תשובה" in m["subject"]][0]["body"]).group(1)
    other = _device(engine)
    assert other.post("/api/conversations/return", json={"token": token[:-1] + ("A" if token[-1] != "A" else "B")}).status_code == 410
    assert other.get(f"/api/conversations/{cid}").status_code == 404        # no access without a valid link
    assert other.post("/api/conversations/return", json={"token": token}).status_code == 200
    assert _device(engine).post("/api/conversations/return", json={"token": token}).status_code == 410   # once


@pytest.mark.parametrize("text, kept", [
    ("האם יש רכב עם גג נפתח?", True), ("יש גג נפתח ב-NIRO 2025?", True),
    ("אני מרוויחה 18,500, יש גג נפתח?", False), ("השכר שלי 20000 - יש וו גרירה?", False),
])
def test_a_question_with_a_figure_that_may_be_pay_is_not_copied_into_an_email(text, kept):
    assert (question_for_email(text) != "") is kept


def test_on_render_a_local_link_is_never_mailed(monkeypatch):
    settings = SimpleNamespace(email_backend="smtp", email_encryption_key="", email_smtp_host="smtp.example",
                               email_from="noreply@example.test", email_public_app_url="http://127.0.0.1:8796")
    service = object.__new__(EmailNotificationService)
    service.settings, service.fernet = settings, object()
    assert service.enabled                                                   # a local test server: allowed
    monkeypatch.setenv("RENDER", "true")
    assert not service.enabled                                               # production: mail stays off
    settings.email_public_app_url = "https://strauss-drive.onrender.com"
    assert service.enabled
    assert local_url("http://localhost:10000") and not local_url("https://strauss-drive.onrender.com")


def test_a_question_already_sent_is_never_offered_again(engine, snapshot):
    c = _server(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    _ask(c, cid, "האם יש רכב עם גג נפתח?")
    again = c.post(f"/api/conversations/{cid}/messages", json={"text": "האם יש רכב עם גג נפתח?"}).json()
    assert "כבר שאלתי את נציג התחבורה" in again["reply"]
    assert all(f["field"] != "ask_rep" for f in again["expects"]["fields"])
    _answer(c, "כן, ב-NIRO.")
    after = c.post(f"/api/conversations/{cid}/messages", json={"text": "האם יש רכב עם גג נפתח?"}).json()
    assert "כבר ענה על השאלה הזו" in after["reply"] and "כן, ב-NIRO." in after["reply"]
    assert all(f["field"] != "ask_rep" for f in after["expects"]["fields"])
    assert len(c.get("/api/rep/questions?status=OPEN", headers=AUTH).json()) == 0


def test_an_offer_the_model_writes_in_its_own_words_gets_its_buttons(engine, snapshot):
    # live QA 1/11 (phone run): the model wrote "רוצים שאשאל את נציג התחבורה?" without the tool; the offer led nowhere
    from app.modules.conversation.llm.fake import FakeLLM
    from app.modules.conversation.public import LLMChain
    plain = _server(engine)
    cid = _compare(plain, [_code(snapshot, "NIRO")])
    fake = FakeLLM()
    fake.turn(reply="אין לי מידע על אפשרות לגג נפתח בקטלוג הרכבים. רוצים שאשאל את נציג התחבורה?")
    model = make_client(engine, llm=LLMChain([fake]), rep_access_token="rep-record", email_encryption_key=KEY)
    model.cookies = plain.cookies
    turn = model.post(f"/api/conversations/{cid}/messages", json={"text": "האם יש רכב עם גג נפתח?"}).json()
    assert any(f["field"] == "ask_rep" for f in turn["expects"]["fields"]), turn["reply"]


def test_the_smtp_message_has_a_named_sender_a_date_and_a_message_id_on_the_sender_s_domain(monkeypatch):
    # deliverability (1/11): standard headers only; the content and the link are unchanged
    import smtplib
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, message): sent.append(message)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings = SimpleNamespace(email_backend="smtp", email_from="noreply@mail.example.test", email_smtp_host="smtp",
                               email_smtp_port=587, email_smtp_starttls=True, email_smtp_username="",
                               email_smtp_password="")
    service = object.__new__(EmailNotificationService)
    service.settings = settings
    service._send("abc123", "employee@example.test", "נושא", "גוף", "<p>גוף</p>")
    m = sent[0]
    assert m["From"] == "STRAUSS DRIVE <noreply@mail.example.test>"
    assert m["Message-ID"] == "<abc123@mail.example.test>" and m["Date"]
    settings.email_from = "Strauss Car Advisor <noreply@mail.example.test>"
    service._send("abc124", "employee@example.test", "נושא", "גוף", "<p>גוף</p>")
    assert sent[1]["From"] == "Strauss Car Advisor <noreply@mail.example.test>"           # a name given is kept
