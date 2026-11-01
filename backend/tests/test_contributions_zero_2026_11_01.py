""""אין הפרשות": nothing deducted, said for both contributions at once (1/11).

Live QA on 8796: with pension and study fund still missing, "אין הפרשות" got "לא הצלחתי להבין" and the same question
again. Template mode read it as an answer to the open question (the car); with the model, save_profile(pension 0,
study fund 0) was refused, because a zero was grounded only next to the field's own word. Shared logic now: a word
that names both contributions (extensions.contribution_terms) next to a zero cue, or a phrase that says nothing is
deducted (nothing_deducted_phrases), holds both at 0 for ONE confirmation; "לאשר" saves both (0, not missing), "לתקן"
asks them again. A zero for one field, "לא יודע", and values said outright keep their meaning. Both modes, same gate.
"""
from __future__ import annotations

import pytest

from .conftest import make_client

PROFILE = ["קבוצה ב", "שכר בסיס 18000", "אין עמלות ואין בונוס, 2.75 נקודות זיכוי, 2 ילדים"]
CONFIRM = "כדי לוודא שהבנתי: לרשום פנסיה 0% וקרן השתלמות 0%?"


def start(engine, llm=None):
    c = make_client(engine, llm=llm) if llm is not None else make_client(engine)
    cid = c.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = c.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()
    return c, cid, say


def profile(c, cid):
    return c.get(f"/api/conversations/{cid}").json()["profile"]


def confirm(turn):
    field = next(f for f in turn["expects"]["fields"] if f.get("closed"))
    return {"choice": "yes", "field": field["field"]}


def at_contributions(engine, llm=None):
    c, cid, say = start(engine, llm)
    for m in PROFILE:
        say(m)
    return c, cid, say


# ---------- templates (no model) ----------

@pytest.mark.parametrize("text", ["אין הפרשות", "לא יורד לי כלום", "אפס הפרשות", "אין לי הפרשות", "בלי הפרשות בכלל"])
def test_nothing_deducted_is_confirmed_once_for_both_then_saved_as_zero(engine, text):
    c, cid, say = at_contributions(engine)
    turn = say(text)
    assert CONFIRM in turn["reply"] and "לא הצלחתי להבין" not in turn["reply"]
    assert profile(c, cid)["pension_percent"] is None                  # nothing saved before "לאשר"
    after = say(confirm(turn))
    p = profile(c, cid)
    assert (p["pension_percent"], p["study_fund_percent"]) == ("0", "0")
    assert "pension_percent" not in p["unknown_fields"] and "study_fund_percent" not in p["unknown_fields"]
    assert "פנסיה" not in after["reply"].split("\n\n")[-2]                # no loop: the next question is another one
    assert (p["base"], p["commissions"], p["bonus"], p["credit_points"], p["children"]) == ("18000", "0", "0", "2.75", 2)


def test_to_fix_asks_both_again_and_the_values_said_are_saved(engine):
    c, cid, say = at_contributions(engine)
    turn = say("אין הפרשות")
    field = next(f for f in turn["expects"]["fields"] if f.get("closed"))["field"]
    fix = say({"choice": "no", "field": field})
    assert "כמה אחוז יורד לכם לפנסיה ולקרן השתלמות?" in fix["reply"]
    assert profile(c, cid)["pension_percent"] is None
    say("פנסיה 6%, קרן השתלמות 2.5%")
    p = profile(c, cid)
    assert (p["pension_percent"], p["study_fund_percent"]) == ("6", "2.5")


def test_both_zeros_said_outright_ask_only_the_rare_one(engine):
    c, cid, say = at_contributions(engine)
    turn = say("פנסיה 0%, השתלמות 0%")
    assert profile(c, cid)["study_fund_percent"] == "0"                # said for its own field: saved
    assert "לאשר פנסיה של 0%" in turn["reply"]                          # pension 0% is rare: confirmed once
    say(confirm(turn))
    assert profile(c, cid)["pension_percent"] == "0"


def test_one_zero_and_one_value_are_saved_as_said(engine):
    c, cid, say = at_contributions(engine)
    turn = say("אין קרן השתלמות, פנסיה 6%")
    p = profile(c, cid)
    assert (p["pension_percent"], p["study_fund_percent"]) == ("6", "0")
    assert CONFIRM not in turn["reply"]


def test_a_zero_named_for_pension_only_never_reaches_the_study_fund(engine):
    c, cid, say = at_contributions(engine)
    say("אין הפרשה לפנסיה")
    assert profile(c, cid)["study_fund_percent"] is None


def test_not_knowing_is_never_a_zero(engine):
    c, cid, say = at_contributions(engine)
    turn = say("לא יודע כמה יורד לי")
    p = profile(c, cid)
    assert p["pension_percent"] is None and p["study_fund_percent"] is None
    assert CONFIRM not in turn["reply"]


def test_after_results_the_zeros_are_confirmed_saved_and_reach_the_engine(engine):
    c, cid, say = at_contributions(engine)
    say("פנסיה 6%, קרן השתלמות 2.5%")
    say("יש לי קורולה קרוס, משלמת 300")
    first = say("הכי משתלם")
    assert first["results"]
    turn = say("אין הפרשות")                                            # a correction: both values are replaced
    assert CONFIRM in turn["reply"]
    say(confirm(turn))
    v = c.get(f"/api/conversations/{cid}").json()
    assert (v["profile"]["pension_percent"], v["profile"]["study_fund_percent"]) == ("0", "0")
    assert not v["results_stale"] and v["results"]
    today = v["results"][0]["result"]["today"]
    assert float(today["pension"]) == 0 and float(today["study_fund"]) == 0   # the engine got 0, not "missing"
    assert int(today["net_display"]) != int(first["results"][0]["today"]["net_display"])


# ---------- with the model (scripted) ----------

def test_with_the_model_the_pure_answer_is_confirmed_in_code_and_the_model_is_not_asked(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    from app.modules.conversation.public import LLMChain
    fake = FakeLLM()
    fake.turn(call("save_profile", pension_percent=0, study_fund_percent=0), reply="רשמתי.")
    c, cid, say = start(engine, LLMChain([fake]))
    plain = make_client(engine)
    plain.cookies = c.cookies
    for m in PROFILE:                                                   # the setup without the model
        plain.post(f"/api/conversations/{cid}/messages", json={"text": m})
    turn = say("אין הפרשות")
    assert CONFIRM in turn["reply"] and fake.calls == []


def test_with_the_model_a_zero_for_both_in_a_longer_message_is_no_longer_refused(engine):
    # the model's save_profile(pension 0, study fund 0) for "אין הפרשות" was refused ("the message does not say this")
    from app.modules.conversation.llm.fake import FakeLLM, call
    from app.modules.conversation.public import LLMChain
    fake = FakeLLM()
    fake.turn(call("save_profile", pension_percent=0, study_fund_percent=0), reply="רשמתי.")
    c, cid, say = start(engine, LLMChain([fake]))
    plain = make_client(engine)
    plain.cookies = c.cookies
    for m in PROFILE:
        plain.post(f"/api/conversations/{cid}/messages", json={"text": m})
    say("תשמע, אצלי בעבודה הזאת אין הפרשות בכלל, גם לא לקרן")
    tool_results = [m.content for call_ in fake.calls for m in call_.messages if m.role == "tool"]
    assert not any("does not say this" in r for r in tool_results), tool_results
    p = profile(c, cid)
    assert p["study_fund_percent"] == "0"                               # said: saved
    assert p["pension_percent"] in (None, "0")                          # pension 0% is rare: confirmed before saving
