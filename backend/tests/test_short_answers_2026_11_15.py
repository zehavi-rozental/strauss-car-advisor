"""Short answers in context (15/11): a known value, no contribution, not knowing, and a real ambiguity are four
different things. "לא ידוע לי" is never 0; 0% is held for a "כן"; a percentage out of range is never saved;
a blanket answer to several fields is never spread; "לשתיהם" / "בעצם רק…" answer the question held in the state.
"""
from __future__ import annotations

import pytest

from .conftest import make_client

Q2_BUT_STUDY_FUND = {"fields": {"base_salary": 18000, "commissions": 0, "bonus": 0, "credit_points": 2.25,
                                "children_under_23": 2}}


def start(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()["reply"]

    return say, lambda: client.get(f"/api/conversations/{cid}").json()["profile"]


def study_fund_on_screen(engine, pension):
    say, profile = start(engine)
    say("קבוצה ב")
    say("שאלה אחת כל פעם")
    asked = say({"fields": {**Q2_BUT_STUDY_FUND["fields"], "pension_percent": pension}})
    assert "קרן השתלמות" in asked
    return say, profile


@pytest.mark.parametrize("answer", ["גם", "אותו דבר", "גם כן"])
def test_the_same_value_as_the_pension(engine, answer):
    say, profile = study_fund_on_screen(engine, 2)
    reply = say(answer)
    assert profile()["study_fund_percent"] == "2" and "קרן השתלמות 2%" in reply


def test_the_same_value_out_of_range_is_explained_never_saved(engine):
    say, profile = study_fund_on_screen(engine, 6)
    reply = say("גם")
    assert profile()["study_fund_percent"] is None and "טווח" in reply


def test_not_knowing_is_recorded_as_unknown_never_zero(engine):
    say, profile = study_fund_on_screen(engine, 6)
    reply = say("לא ידוע לי")
    p = profile()
    assert p["study_fund_percent"] is None and "study_fund_percent" in p["unknown_fields"] and "לא ידוע" in reply


def test_none_is_zero(engine):
    say, profile = study_fund_on_screen(engine, 6)
    say("אין")
    assert profile()["study_fund_percent"] == "0"


def test_a_zero_pension_waits_for_yes(engine):
    say, profile = start(engine)
    say("קבוצה ב")
    say("שכר בסיס 18,500, אין עמלות ואין בונוס")
    assert "לאשר" in say("פנסיה 0")
    say("לא")
    assert profile()["pension_percent"] is None                     # "לא": not saved, asked again
    say("פנסיה 0")
    say("כן")
    assert profile()["pension_percent"] == "0"


@pytest.mark.parametrize("blanket", ["אין לי כלום", "לא ידוע לי", "הכל 5"])
def test_a_blanket_answer_to_several_fields_is_never_spread(engine, blanket):
    say, profile = start(engine)
    say("קבוצה ב")
    say("שכר בסיס 18,500")
    reply = say(blanket)
    p = profile()
    assert [p["commissions"], p["bonus"], p["pension_percent"], p["study_fund_percent"], p["credit_points"],
            p["children"]] == [None] * 6 and p["unknown_fields"] == []
    assert "אחד-אחד" in reply


def test_one_number_for_two_fields_then_both_then_only_one(engine):
    say, profile = start(engine)
    say("קבוצה ב")
    say("שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%")
    assert "או לשניהם" in say("2")                                   # asked, never guessed
    say("לשתיהם")
    assert (profile()["credit_points"], profile()["children"]) == ("2", 2)
    say("בעצם רק לילדים")
    assert (profile()["credit_points"], profile()["children"]) == (None, 2)
