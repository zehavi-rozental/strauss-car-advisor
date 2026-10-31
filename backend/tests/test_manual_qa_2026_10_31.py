"""Manual QA with live Gemini on a local test server (31/10): what the employee saw, pinned without a model.

- "האם יש לכם עמלות חודשיות ()?": the model put its hidden source tag in brackets; the tag was removed, the brackets
  stayed.
- An inquiry card said "ביקשנו לשלוח… קישור לאימות" on a server whose mail is fake: nothing was ever sent.
- "אוקי אז מה ההמשך אני כבר רוצה" (the model's quota was used up, the templates answered) got "לא בטוח שהבנתי".
- Group א and today's car COROLLA (2026) "קבוצה ב" read as a contradiction: the car's catalog group is not the
  employee's eligibility, and the alternatives come from the employee's group.
- "יש לכם רכב אדום כהה?": answered honestly (no colour or stock is invented), offered to the representative, and
  nothing reaches the representative until the employee sends it.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text as sql

from app.modules.conversation.guards import strip_tags
from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain
from app.modules.conversation.templates import UNSURE
from app.modules.rules.public import load_snapshot

from .conftest import CONFIG, make_client

SNAP = load_snapshot(CONFIG)
GROUPS = {c.catalog_code: c.group for c in SNAP.catalog}
Q2 = {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 5, "study_fund_percent": 0,
      "credit_points": 2.75, "children_under_23": 2}


def start(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()


def rows(engine) -> int:
    with engine.connect() as c:
        return c.execute(sql("select count(*) from rep_questions")).scalar_one()


# ---------- the empty brackets ----------

@pytest.mark.parametrize("raw, shown", [
    ("האם יש לכם עמלות חודשיות ([[state]])?", "האם יש לכם עמלות חודשיות?"),
    ("שווי השימוש נקבע לפי רשות המסים ( [[faq:use_value]] ).", "שווי השימוש נקבע לפי רשות המסים."),
    ("רשמתי: שכר בסיס 18,000 ₪ (ברוטו). [[state]]", "רשמתי: שכר בסיס 18,000 ₪ (ברוטו)."),     # real brackets stay
])
def test_a_tag_in_brackets_leaves_no_empty_brackets(raw, shown):
    assert strip_tags(raw) == shown


def test_the_model_s_tag_in_brackets_never_reaches_the_screen(engine):
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))
    _, send = start(client)
    send({"text": "קבוצה א"})
    send({"text": "18000"})
    fake.turn(reply="האם יש לכם עמלות חודשיות ([[state]])?")
    turn = send({"text": "תשאל אותי הלאה"})
    assert "()" not in turn["reply"]


# ---------- fake mail says so ----------

def test_fake_mail_says_no_real_email_is_sent_and_asks_no_address(engine):
    client = make_client(engine, email_fake_notice=True)
    cid, send = start(client)
    send({"text": "קבוצה א"})
    assert client.get(f"/api/conversations/{cid}").json()["rep_email_simulated"] is True
    r = client.post(f"/api/conversations/{cid}/rep-question",
                    json={"question": "האם ניתן לבחור רכב בצבע אדום כהה?", "intent": "general"})
    assert r.status_code == 200 and r.json()["status"] == "created"
    record = [m for m in client.get(f"/api/conversations/{cid}").json()["messages"] if m["source"] == "rep_inquiry"]
    assert "לא נשלח מייל אמיתי" in record[-1]["content"] and "ביקשנו לשלוח" not in record[-1]["content"]


def test_real_mail_keeps_its_wording(engine):
    client = make_client(engine)                       # the fake backend standing in for real mail (notice off)
    cid, _ = start(client)
    assert client.get(f"/api/conversations/{cid}").json()["rep_email_simulated"] is False


# ---------- "let's move on" ----------

@pytest.mark.parametrize("text", ["אוקי אז מה ההמשך אני כבר רוצה", "בוא נתקדם", "יאללה הלאה"])
def test_a_request_to_move_on_picks_the_question_up(engine, text):
    client = make_client(engine)
    _, send = start(client)
    send({"text": "קבוצה א"})
    turn = send({"text": text})
    assert UNSURE not in turn["reply"] and "לא הצלחתי" not in turn["reply"]
    assert "שכר הבסיס" in turn["reply"]                                    # the same open question, picked up
    assert turn["reply"].split("\n\n")[0] in SNAP.faq.social_replies["proceed"]


def test_a_value_with_a_move_on_word_is_still_read_as_a_value(engine):
    client = make_client(engine)
    _, send = start(client)
    send({"text": "קבוצה א"})
    assert "רשמתי: שכר בסיס 18,000" in send({"text": "18000, ונמשיך"})["reply"]


# ---------- the car's group and the employee's group ----------

def test_today_s_car_group_is_not_the_employee_s_eligibility(engine):
    client = make_client(engine)
    cid, send = start(client)
    send({"text": "קבוצה א"})
    send({"fields": Q2})
    turn = send({"text": "קורולה 2026"})
    assert "קבוצה ב" in turn["reply"] and "לפי קבוצת הזכאות שלכם, א" in turn["reply"]
    send({"text": "300"})
    fields = client.get(f"/api/conversations/{cid}").json()["expects"]["fields"]
    options = next(f for f in fields if f["field"] == "options")
    assert {GROUPS[c["id"]] for c in options["choices"] if c["id"] in GROUPS} == {"א"}


# ---------- a colour question ----------

def test_a_colour_question_invents_nothing_and_opens_nothing_by_itself(engine):
    client = make_client(engine)
    cid, send = start(client)
    send({"text": "קבוצה א"})
    before = rows(engine)
    turn = send({"text": "יש לכם רכב אדום כהה?"})
    assert "אדום" not in turn["reply"].split("\n\n")[0] or "אין לי מידע" in turn["reply"]
    assert "כן, בקטלוג" not in turn["reply"] and "יש לנו" not in turn["reply"]
    assert any(f["field"] == "ask_rep" for f in turn["expects"]["fields"])      # an offer, with buttons
    assert rows(engine) == before                                              # nothing sent
