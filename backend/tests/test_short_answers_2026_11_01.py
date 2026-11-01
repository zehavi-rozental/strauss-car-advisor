""""לא", "גם לא", "לא מופרש": a short "no" answers the question on screen (1/11).

Manual QA: the bot asked the pension, "לא מופרש" moved on to the study fund, "גם לא" went back to the pension. Root
cause: a bare number answered the one field asked (state.asked_field), a bare "no" did not: template mode could not
read it, and with the model a zero was grounded only next to the field's own word, so save_profile was refused while
the model moved on in its own words and the field stayed missing. Now one predicate (grounding.bare_zero_answer, words
in extensions.yaml) binds a short "no" to the field asked, in both modes, through the same gates: pension 0% and credit
points 0 are confirmed once, a grouped question is never spread, "לא יודע" stays unknown. On the question that asks
both contributions it is the "אין הפרשות" confirmation. A labelled value replacing a saved one ("הפנסיה שלי 6%")
is a correction without "טעיתי" too, and the results are recalculated.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.grounding import bare_zero_answer, zero_grounded

from .conftest import make_client

PACE = ["תשאל אותי שאלה אחר שאלה", "קבוצה ב", "18000", "אין", "אין רכב", "1500", "אין"]   # → the pension, alone
GROUPED = ["קבוצה ב", "שכר בסיס 18000", "אין עמלות ואין בונוס, 2.75 נקודות זיכוי, 2 ילדים", "אין רכב", "אין",
           "1, 2"]                                                     # → "אילו הפרשות יורדות לכם: פנסיה, קרן השתלמות?"
HOLD_PENSION = "לאשר פנסיה של 0%?"
HOLD_BOTH = "כדי לוודא שהבנתי: לרשום פנסיה 0% וקרן השתלמות 0%?"
NEG = ["לא", "אין", "אפס", "0", "בלי", "ללא"]
ALLOWED = ["גם", "ממש", "בכלל", "כלום", "לי", "יורד", "מופרש", "מפרישים", "מורידים"]


def start(engine, llm=None):
    c = make_client(engine, llm=llm) if llm is not None else make_client(engine)
    cid = c.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = c.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()
    return c, cid, say


def view(c, cid):
    return c.get(f"/api/conversations/{cid}").json()


def button(turn, choice):
    field = next(f for f in turn["expects"]["fields"] if f.get("closed"))
    return {"choice": choice, "field": field["field"]}


def ask_line(turn):
    return [ln for ln in turn["reply"].splitlines() if ln.strip()][-2]   # the question, before the CTA line


def run(say, messages):
    turn = None
    for m in messages:
        turn = say(m)
    return turn


# ---------- the predicate ----------

@pytest.mark.parametrize("text", ["לא", "גם לא", "אין", "אפס", "0", "לא יורד", "לא מופרש", "לא, גם לא", "ממש לא",
                                  "לא מפרישים לי כלום", "בכלל לא"])
def test_a_short_no_is_a_bare_zero(text):
    assert bare_zero_answer(text, NEG, ALLOWED)


@pytest.mark.parametrize("text", ["לא יודע", "לא זוכרת", "למה לא?", "גם לא פנסיה", "אין רכב", "לא, 6%", "יש",
                                  "כן", "לא בטוח שיש לי"])
def test_an_uncertainty_a_question_or_another_word_is_not(text):
    assert not bare_zero_answer(text, NEG, ALLOWED)


def test_a_short_no_grounds_a_zero_only_for_the_field_asked():
    bare = (NEG, ALLOWED)
    assert zero_grounded("study_fund_percent", "גם לא", ["אין"], asked="study_fund_percent", bare=bare)
    assert not zero_grounded("pension_percent", "גם לא", ["אין"], asked="study_fund_percent", bare=bare)
    assert not zero_grounded("study_fund_percent", "גם לא", ["אין"], asked=None, bare=bare)


# ---------- one question at a time (template mode) ----------

@pytest.mark.parametrize("pension_no,yes,fund_no", [
    ("לא מופרש", "מאשר", "גם לא"),                         # the manual QA conversation
    ("לא", "כן", "לא"),
    ("אין", "לאשר", "אין"),
    ("אפס", "מאשרת", "אפס"),
    ("לא יורד", {"choice": "yes"}, "לא יורד"),
    ("0", "כן", "לא מופרש"),
])
def test_pension_then_study_fund_answered_no_each_lands_on_its_own_field(engine, pension_no, yes, fund_no):
    c, cid, say = start(engine)
    asked = run(say, PACE)
    assert "לפנסיה" in ask_line(asked)
    held = say(pension_no)
    assert HOLD_PENSION in held["reply"] and "לא הצלחתי להבין" not in held["reply"]
    assert view(c, cid)["profile"]["pension_percent"] is None           # nothing saved before the confirmation
    fund = say(button(held, "yes") if isinstance(yes, dict) else yes)
    assert view(c, cid)["profile"]["pension_percent"] == "0"
    assert "קרן השתלמות" in ask_line(fund) and "פנסיה" not in ask_line(fund)
    nxt = say(fund_no)
    p = view(c, cid)["profile"]
    assert (p["pension_percent"], p["study_fund_percent"]) == ("0", "0")   # the confirmed pension is kept
    assert "נקודות זיכוי" in ask_line(nxt) and "פנסיה" not in nxt["reply"]   # never back to the pension
    assert (p["commissions"], p["bonus"], p["unknown_fields"]) == ("0", "0", [])


def test_to_fix_asks_the_pension_again_and_the_value_typed_is_saved(engine):
    c, cid, say = start(engine)
    run(say, PACE)
    held = say("לא מופרש")
    fix = say("לתקן")
    assert view(c, cid)["profile"]["pension_percent"] is None
    assert "פנסיה" in ask_line(fix)
    nxt = say("6")
    assert view(c, cid)["profile"]["pension_percent"] == "6"
    assert "קרן השתלמות" in ask_line(nxt)
    assert held["reply"] != fix["reply"]


def test_dont_know_stays_unknown_and_never_becomes_zero(engine):
    c, cid, say = start(engine)
    run(say, PACE)
    say("לא יודע")
    p = view(c, cid)["profile"]
    assert p["pension_percent"] is None and "pension_percent" in p["unknown_fields"]


def test_the_rest_after_the_pension_bound_by_the_short_answers(engine):
    # credit points and children answered "לא" / "אין": credit points 0 is rare and confirmed; children 0 is saved
    c, cid, say = start(engine)
    run(say, [*PACE, "6", "2.5"])
    held = say("אין")
    assert "נקודות זיכוי" in held["reply"] and view(c, cid)["profile"]["credit_points"] is None
    kids = say("כן")
    assert view(c, cid)["profile"]["credit_points"] == "0" and "ילדים" in ask_line(kids)
    say("לא")
    assert view(c, cid)["profile"]["children"] == 0


def test_a_short_no_never_answers_the_base_salary_and_never_assumes_pension_on_a_grouped_question(engine):
    c, cid, say = start(engine)
    say("קבוצה ב")
    say("לא")
    assert view(c, cid)["profile"]["base"] is None
    say("שכר בסיס 18000")                                            # six Q2 fields on screen
    say("לא")                                                        # the settled bare "אין" rule for Q2
    p = view(c, cid)["profile"]
    assert (p["pension_percent"], p["credit_points"]) == (None, None)


# ---------- the question asking both contributions, the results, corrections ----------

@pytest.mark.parametrize("no", ["לא מופרש", "אין", "לא", "לא יורד"])
def test_a_short_no_to_both_contributions_is_one_confirmation_then_the_results(engine, no):
    c, cid, say = start(engine)
    asked = run(say, GROUPED)
    assert "פנסיה" in ask_line(asked) and "קרן השתלמות" in ask_line(asked)
    held = say(no)
    assert HOLD_BOTH in held["reply"]
    done = say("מאשר")
    v = view(c, cid)
    assert (v["profile"]["pension_percent"], v["profile"]["study_fund_percent"]) == ("0", "0")
    assert done["results"] and "PICANTO" in done["reply"]


def test_corrections_after_the_results_are_saved_recalculated_and_keep_the_other_values(engine):
    c, cid, say = start(engine)
    run(say, GROUPED)
    say("לא מופרש")
    first = say("מאשר")["results"]
    today0 = first[0]["today"]["net_display"]
    # a labelled value that replaces a saved one, with or without "טעיתי"
    second = say("הפנסיה שלי 6%")
    assert "לא הצלחתי להבין" not in second["reply"] and "איזה רכבים להשוות" not in second["reply"]
    p = view(c, cid)["profile"]
    assert (p["pension_percent"], p["study_fund_percent"], p["credit_points"], p["children"]) == ("6", "0", "2.75", 2)
    assert second["results"] and second["results"][0]["today"]["net_display"] != today0
    third = say("טעיתי, קרן השתלמות 2.5%")
    p = view(c, cid)["profile"]
    assert (p["pension_percent"], p["study_fund_percent"]) == ("6", "2.5")
    assert third["results"][0]["today"]["net_display"] != second["results"][0]["today"]["net_display"]
    assert not view(c, cid)["results_stale"]


# ---------- with the model (scripted) ----------

def _paced_with_model(engine, fake):
    from app.modules.conversation.public import LLMChain
    c, cid, say = start(engine, LLMChain([fake]))
    plain = make_client(engine)
    plain.cookies = c.cookies
    for m in PACE:                                                     # the setup without the model
        plain.post(f"/api/conversations/{cid}/messages", json={"text": m})
    return c, cid, say


def test_with_the_model_a_short_no_for_the_field_asked_is_not_refused(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("save_profile", pension_percent=0), reply="רשמתי.")
    fake.turn(call("save_profile", study_fund_percent=0), reply="רשמתי.")
    c, cid, say = _paced_with_model(engine, fake)
    held = say("לא מופרש")                                             # the manual QA messages
    assert HOLD_PENSION in held["reply"]                                 # held, asked by code: never skipped
    say("מאשר")
    nxt = say("גם לא")
    results = [m.content for call_ in fake.calls for m in call_.messages if m.role == "tool"]
    assert not any("does not say this" in r for r in results), results
    p = view(c, cid)["profile"]
    assert (p["pension_percent"], p["study_fund_percent"]) == ("0", "0")
    assert "פנסיה" not in nxt["reply"]


def test_with_the_model_a_short_no_is_never_saved_to_another_field(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("save_profile", study_fund_percent=0), reply="רשמתי.")   # the pension is on screen
    c, cid, say = _paced_with_model(engine, fake)
    say("גם לא, ממש לא")
    p = view(c, cid)["profile"]
    assert p["study_fund_percent"] is None


@pytest.mark.parametrize("again", ["לא מופרש", "אין", "באמת אין", "ממש לא מופרש"])
def test_the_zero_said_again_confirms_the_held_zero_and_a_bare_no_still_fixes(engine, again):
    # live Gemini 1/11: with pension 0% held, "לא מופרש" was read as "לא" = "לתקן" and the pension was asked again
    c, cid, say = start(engine)
    run(say, PACE)
    held = say("אין")
    assert HOLD_PENSION in held["reply"]
    fund = say(again)
    assert view(c, cid)["profile"]["pension_percent"] == "0" and "קרן השתלמות" in ask_line(fund)
    c2, cid2, say2 = start(engine)
    run(say2, PACE)
    say2("אין")
    fix = say2("לא")                                                   # a bare "לא" is still "לתקן"
    assert view(c2, cid2)["profile"]["pension_percent"] is None and "פנסיה" in ask_line(fix)
