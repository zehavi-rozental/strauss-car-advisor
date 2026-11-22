"""No contributions at all, said with a question, a typo or as a restatement (19/11; templates and a fake model).

Manual check on 8897: "אי לי הפרשות, מה זה?" got the explanation and nothing else, then "כתבתי שכלום" got "one at a time"
and the pension and the study fund were asked apart. Root causes, each in an existing mechanism:
  - the zero for both contributions was read from the WHOLE message: with a question in it, either the question was
    dropped (spelled right) or the zero was (typed one letter short); now the statement and the question are read apart;
  - a zero word typed one letter short ("אי") was not a zero cue; now, next to the contributions' own word only;
  - "כתבתי שכלום" restates an answer: read as "כלום" (as "שאלתי מה זה" is read as the question), and the bare answer is
    checked against what the question on screen really asked (the two contributions), not the whole salary group.
Both values are saved only on "לאשר"; one field said alone is that field only; a later correction recalculates.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain

from .conftest import make_client

HOLD = "לרשום פנסיה 0% וקרן השתלמות 0%"
EXPLAINED = "הפרשות הן האחוזים שיורדים לכם מהשכר"


def start(engine, llm=None):
    client = make_client(engine, llm=LLMChain([llm]) if llm is not None else None)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def profile():
        return client.get(f"/api/conversations/{cid}").json()["profile"]
    return say, profile


def at_contributions(say):
    say("קבוצה ב")
    say("18,500")
    turn = say("אין עמלות ואין בונוס")
    assert "אילו הפרשות יורדות לכם" in turn["reply"]
    return turn


def test_the_reported_message_holds_both_and_answers_the_question(engine):
    say, profile = start(engine)
    at_contributions(say)
    turn = say("אי לי הפרשות, מה זה?")                     # a typo, a statement and a question in one message
    assert EXPLAINED in turn["reply"] and HOLD in turn["reply"]
    p = profile()
    assert p["pension_percent"] is None and p["study_fund_percent"] is None   # nothing before "לאשר"
    say({"choice": "yes", "field": "confirmation"})
    p = profile()
    assert Decimal(p["pension_percent"]) == 0 and Decimal(p["study_fund_percent"]) == 0


def test_spelled_right_the_question_is_no_longer_dropped(engine):
    say, profile = start(engine)
    at_contributions(say)
    turn = say("אין לי הפרשות, מה זה?")
    assert EXPLAINED in turn["reply"] and HOLD in turn["reply"]


def test_a_restated_nothing_is_the_same_answer(engine):
    say, profile = start(engine)
    at_contributions(say)
    turn = say("כתבתי שכלום")
    assert HOLD in turn["reply"] and "אחד-אחד" not in turn["reply"]
    say("לאשר")
    p = profile()
    assert Decimal(p["pension_percent"]) == 0 and Decimal(p["study_fund_percent"]) == 0


def test_refused_confirmation_saves_nothing(engine):
    say, profile = start(engine)
    at_contributions(say)
    say("אין הפרשות")
    say({"choice": "no", "field": "confirmation"})
    p = profile()
    assert p["pension_percent"] is None and p["study_fund_percent"] is None


def test_one_field_alone_is_that_field_only(engine):
    say, profile = start(engine)
    at_contributions(say)
    turn = say("אין לי קרן השתלמות")
    p = profile()
    assert Decimal(p["study_fund_percent"]) == 0 and p["pension_percent"] is None
    assert "פנסיה" in turn["reply"] and HOLD not in turn["reply"]
    say2, profile2 = start(engine)
    at_contributions(say2)
    turn = say2("אין לי פנסיה")
    assert HOLD not in turn["reply"] and "פנסיה של 0%" in turn["reply"]       # held alone, its own confirmation
    assert profile2()["study_fund_percent"] is None


def test_a_later_correction_updates_one_value_and_recalculates(engine):
    say, profile = start(engine)
    at_contributions(say)
    say("אין הפרשות")
    say("לאשר")
    say("2.25 נקודות זיכוי, 2 ילדים")
    say("יש לי קורולה קרוס 2025")
    say("השתתפות 300")
    first = say("הכי משתלם")["results"][0]                  # group ב: the eligible cars, ranked
    turn = say({"fields": {"pension_percent": 6}})                 # "הנתונים שלי": the pension only
    assert "6% במקום 0%" in turn["reply"]
    p = profile()
    assert Decimal(p["pension_percent"]) == 6 and Decimal(p["study_fund_percent"]) == 0
    again = turn["results"][0]
    assert again["today"]["net_display"] != first["today"]["net_display"]     # recalculated


def test_a_restatement_keeps_a_word_that_begins_with_its_joining_letter(engine):
    say, profile = start(engine)
    say("קבוצה ב")
    say("כתבתי שכר 18,500")                                         # "שכר", not "ש" + "כר"
    assert profile()["base"] == "18500"


def test_with_the_model_the_zero_and_the_explanation_are_code_s(engine):
    fake = FakeLLM()
    say, profile = start(engine, fake)
    at_contributions(say)
    turn = say("אי לי הפרשות, מה זה?")
    assert HOLD in turn["reply"] and profile()["pension_percent"] is None
