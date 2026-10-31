"""Gemini for language, code for certainty (31/10).

A short answer the parser reads in full ("18000", "5%", "אין", "קבוצה א") is saved by code and answered by the
template: no model call (a bare "18000" cost three Gemini calls in the live session). Anything with language in it
— a question, an uncertainty, a request, a remark — goes to the model. The catalog's copay for today's car is offered
as an unverified amount and saved only when the employee confirms it. "אתה באמת Gemini?" is answered honestly.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain
from app.modules.conversation.templates import ENGINE_OFFLINE
from app.modules.rules.public import load_snapshot

from .conftest import CONFIG, make_client

SNAP = load_snapshot(CONFIG)
COROLLA = next(c for c in SNAP.catalog if c.catalog_code == "CAR-07")
Q2 = {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 5, "study_fund_percent": 0,
      "credit_points": 2.75, "children_under_23": 2}


def start(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()


def model_client(engine):
    fake = FakeLLM()
    return fake, make_client(engine, llm=LLMChain([fake]))


@pytest.mark.parametrize("answers", [
    ["קבוצה א", "18000"],
    ["קבוצה א", "18,000 ₪"],
])
def test_simple_answers_are_saved_by_code_without_a_model_call(engine, answers):
    fake, client = model_client(engine)
    cid, send = start(client)
    for text in answers:
        before = len(fake.calls)
        turn = send({"text": text})
        assert len(fake.calls) == before, text            # no model call for a plain answer
        assert turn["source"] == "template" and "רשמתי" in turn["reply"]
    profile = client.get(f"/api/conversations/{cid}").json()["profile"]
    assert profile["group"] == "א" and profile["base"] == "18000"


def test_a_percent_and_a_bare_none_are_code_too(engine):
    fake, client = model_client(engine)
    _, send = start(client)
    send({"text": "קבוצה א"})
    send({"text": "18000"})
    before = len(fake.calls)
    send({"text": "אין"})                                  # commissions, bonus, study fund, children: 0, said
    send({"text": "5%"})                                   # the pension asked next
    assert len(fake.calls) == before


@pytest.mark.parametrize("text", [
    "אני לא בטוחה שהסכום נכון",
    "לא מספיק לכתוב באיזה קבוצה אני?",
    "למה אתה צריך את התפקיד שלי?",
    "נו, אז כמה יישאר לי בנטו?",
    "יש לכם רכב אדום כהה?",
    "תשאל אותי שאלה אחת בכל פעם",
    "איזה רכב הכי משתלם לי?",
    "אתה באמת משתמש בג'מיני?",
    "18000 אבל אני לא בטוחה",
])
def test_language_goes_to_the_model(engine, text):
    fake, client = model_client(engine)
    _, send = start(client)
    send({"text": "קבוצה א"})
    before = len(fake.calls)
    send({"text": text})
    assert len(fake.calls) > before, text


def test_without_the_model_the_engine_question_says_this_reply_is_a_template(engine):
    client = make_client(engine)                           # no model configured
    _, send = start(client)
    reply = send({"text": "אתה באמת משתמש בג'מיני?"})["reply"]
    assert ENGINE_OFFLINE in reply and "Gemini" in reply


# ---------- today's copay: the catalog amount, unverified until confirmed ----------

def at_copay(client):
    cid, send = start(client)
    send({"text": "קבוצה א"})
    send({"fields": Q2})
    turn = send({"text": "קורולה 2026"})
    return cid, send, turn


def test_the_catalog_copay_is_offered_as_unverified_and_never_saved_by_itself(engine):
    client = make_client(engine)
    cid, _, turn = at_copay(client)
    assert "בקטלוג החברה רשום" in turn["reply"] and "לא מאומת" in turn["reply"]
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] is None
    quick = [q for f in turn["expects"]["fields"] for q in f["quick_replies"]]
    assert {"id": "catalog:CAR-07", "label": f"כן, {int(COROLLA.default_copay)} ₪ בחודש"} in quick
    assert any(q["id"] == "unknown" for q in quick)        # "לא ידוע" stays possible


def test_the_button_confirms_the_catalog_copay(engine):
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    send({"choice": "catalog:CAR-07", "field": "copay"})
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] == str(COROLLA.default_copay)


def test_a_typed_yes_confirms_it_and_another_amount_replaces_it(engine):
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    send({"text": "כן"})
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] == str(COROLLA.default_copay)
    client2 = make_client(engine)
    cid2, send2, _ = at_copay(client2)
    send2({"text": "180"})
    assert client2.get(f"/api/conversations/{cid2}").json()["current_car"]["copay"] == "180"
