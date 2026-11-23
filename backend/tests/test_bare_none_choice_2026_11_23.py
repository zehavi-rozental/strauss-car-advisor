"""Acceptance finding #4, 23/11: a bare "אין" to the combined salary question recorded 0 for commissions, bonus, study
fund and children at once. The approved rule: when the question asks more than one salary field and the answer is a
bare negative naming no field, nothing is saved; the bot asks what the "אין" is about, with up to three buttons (only
the ones that fit the fields asked), and saves only after a choice. A named negative ("אין בונוס") is saved as before;
"אין לי כלום" and the contributions-only question keep their earlier behaviour. Templates only, no model.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain

from .conftest import make_client

QUESTION = 'על מה ה"אין"?'
CTA = 'בחרו אחת מהאפשרויות, או כתבו במפורש, למשל: "אין עמלות ואין בונוס".'
LABELS = ["אין עמלות ואין בונוס", "אין ילדים עד גיל 23", "אין עמלות, בונוס, קרן השתלמות וילדים"]
SALARY = ("commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children")


def start(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        return r.json()

    def profile():
        return client.get(f"/api/conversations/{cid}").json()["profile"]
    for text in ("יש לי טויוטה קורולה 2024 ומשלם 500", "ג", "18500"):
        say(text)
    return say, profile


def buttons(turn):
    fields = turn["expects"].get("fields") or []
    return [(f["field"], f["closed"], [c["label"] for c in f["choices"]]) for f in fields]


def test_a_bare_none_saves_nothing_and_asks_with_three_buttons(engine):
    say, profile = start(engine)
    turn = say("אין")
    assert QUESTION in turn["reply"] and CTA in turn["reply"]
    assert "רשמתי" not in turn["reply"]
    assert buttons(turn) == [("none_choice", True, LABELS)]
    assert all(profile()[f] is None for f in SALARY)


@pytest.mark.parametrize("choice, zero", [
    ("none_commissions_bonus", ("commissions", "bonus")),
    ("none_children", ("children",)),
    ("none_all", ("commissions", "bonus", "study_fund_percent", "children")),
])
def test_each_choice_saves_only_its_own_fields(engine, choice, zero):
    say, profile = start(engine)
    say("אין")
    turn = say({"choice": choice})
    assert "רשמתי" in turn["reply"]
    p = profile()
    for f in SALARY:
        assert (p[f] is not None and float(p[f]) == 0) if f in zero else p[f] is None, (choice, f, p[f])
    assert p["pension_percent"] is None and p["credit_points"] is None    # never 0 from "אין"


def test_typing_it_explicitly_after_the_question_is_read_as_usual(engine):
    say, profile = start(engine)
    say("אין")
    say("אין עמלות ואין בונוס")
    p = profile()
    assert float(p["commissions"]) == 0 and float(p["bonus"]) == 0
    assert p["study_fund_percent"] is None and p["children"] is None


def test_a_choice_no_longer_offered_is_refused(engine):
    say, profile = start(engine)
    say("אין")
    say("אין עמלות ואין בונוס")
    with pytest.raises(AssertionError, match="422"):       # the buttons left with the question: nothing is saved
        say({"choice": "none_all"})
    assert profile()["children"] is None and profile()["study_fund_percent"] is None


def test_a_named_negative_is_saved_as_before(engine):
    say, profile = start(engine)
    turn = say("אין בונוס")
    assert buttons(turn)[0][0] != "none_choice"
    p = profile()
    assert float(p["bonus"]) == 0 and p["commissions"] is None and p["children"] is None


def test_nothing_at_all_keeps_the_one_at_a_time_clarification(engine):
    say, profile = start(engine)
    turn = say("אין לי כלום")
    assert "בואו נעבור על זה אחד-אחד" in turn["reply"]
    assert all(f != "none_choice" for f, _, _ in buttons(turn))
    assert all(profile()[f] is None for f in SALARY)


def test_the_contributions_question_keeps_its_joint_confirmation(engine):
    say, profile = start(engine)
    say({"fields": {"commissions": 0, "bonus": 0, "credit_points": 2.75, "children_under_23": 2}})
    turn = say("אין")
    assert all(f != "none_choice" for f, _, _ in buttons(turn))
    assert QUESTION not in turn["reply"]


def test_the_bare_none_question_and_its_button_need_no_model_call(engine):
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in ("קבוצה א", "18000"):
        client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    before = len(fake.calls)
    asked = client.post(f"/api/conversations/{cid}/messages", json={"text": "אין"}).json()
    assert asked["expects"]["field"] == "none_choice" and QUESTION in asked["reply"]
    chosen = client.post(f"/api/conversations/{cid}/messages",
                         json={"choice": "none_commissions_bonus", "field": "none_choice"}).json()
    assert "רשמתי" in chosen["reply"] and len(fake.calls) == before
