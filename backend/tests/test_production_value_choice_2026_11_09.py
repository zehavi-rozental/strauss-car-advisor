"""Production, 9/11: the contributions were asked; "2" → "האם ה-2% מתייחס לפנסיה או לקרן ההשתלמות?" → "לשתיהם" →
"לא בטוח שהבנתי" and both contributions back to missing → "גתבתי לך ש-2 לשתיהם" → the same question again.

Now one number for several fields is held by code (conversation.value_choice, from the integration branch of 5/11),
asked with a button per field and "לשניהם", and the answer is read from the words (spellings as typed in
production included), else from one structured model call that code validates. Saved through save_profile's gates,
and used by the calculation. Synthetic data only.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.llm.port import LLMChain

from .conftest import make_client

PAY = {"base_salary": 18000, "commissions": 0, "bonus": 0, "credit_points": 2.25, "children_under_23": 2}
QUESTION = "המספר 2 מתייחס לאחוז הפנסיה או לאחוז קרן ההשתלמות, או לשניהם?"


def start(engine, llm=None):
    client = make_client(engine, llm=LLMChain([llm])) if llm is not None else make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def view():
        return client.get(f"/api/conversations/{cid}").json()
    say("קבוצה ג")
    say({"fields": PAY})                       # the contributions are now the question on screen
    return say, view


def contributions(view):
    p = view()["profile"]
    return tuple(None if p[k] is None else Decimal(str(p[k])) for k in ("pension_percent", "study_fund_percent"))


def test_the_production_sequence_saves_two_percent_to_both(engine):
    say, view = start(engine)
    asked = say("2")
    assert QUESTION in asked["reply"] and "לא בטוח שהבנתי" not in asked["reply"]
    assert [c["id"] for c in asked["expects"]["choices"]] == ["pension_percent", "study_fund_percent", "all"]
    assert contributions(view) == (None, None)                  # nothing saved before the answer
    done = say("לשתיהם")
    assert "רשמתי: פנסיה 2%, קרן השתלמות 2%" in done["reply"] and "לא בטוח שהבנתי" not in done["reply"]
    assert contributions(view) == (Decimal(2), Decimal(2))
    assert done["expects"]["field"] != "value_choice"           # the flow goes on to the next question


@pytest.mark.parametrize("answer", ["לשתיהם", "לשתייהם", "שתיהם", "לשתיהן", "לשניהם", "גם וגם", "לשתי ההפרשות",
                                    "גתבתי לך ש-2 לשתיהם", "כתבתי לך ש-2 לשניהם"])
def test_both_in_any_spelling_or_said_again_is_both(engine, answer):
    say, view = start(engine)
    say("2")
    say(answer)
    assert contributions(view) == (Decimal(2), Decimal(2))


def test_an_answer_not_understood_keeps_the_question_and_the_insisting_answer_resolves_it(engine):
    say, view = start(engine)
    say("2")
    again = say("אוקיי")                                       # not an answer: asked again in other words, never dropped
    assert again["expects"]["field"] == "value_choice" and contributions(view) == (None, None)
    say("גתבתי לך ש-2 לשתיהם")
    assert contributions(view) == (Decimal(2), Decimal(2))


def test_the_button_for_both_saves_both(engine):
    say, view = start(engine)
    say("2")
    say({"choice": "all", "field": "value_choice"})
    assert contributions(view) == (Decimal(2), Decimal(2))


def test_when_the_model_asks_the_question_itself_the_answer_is_still_read(engine):
    fake = FakeLLM()
    say, view = start(engine, fake)
    fake.turn(reply="האם ה-2% מתייחס לפנסיה או לקרן ההשתלמות?")
    say("2")
    say("לשתיהם")
    assert contributions(view) == (Decimal(2), Decimal(2))


def test_the_two_percent_reach_the_calculation(engine):
    say, view = start(engine)
    say("2")
    say("לשתיהם")
    say("קורולה 2026")
    say({"choice": "catalog:CAR-07", "field": "copay"})
    say({"choice": "rank", "field": "options"})
    v = view()
    assert v["stage"] == "PRESENTED" and len(v["results"]) == 3
    today = v["results"][0]["result"]["today"]
    gross = Decimal(today["cash_gross"])
    assert Decimal(today["pension"]) == gross * Decimal("0.02")          # 2% of the contributions base (gross)
    assert Decimal(today["study_fund"]) == gross * Decimal("0.02")
