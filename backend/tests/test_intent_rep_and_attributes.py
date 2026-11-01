"""What the employee means (stage 5 of the 29/10 plan, C1-C4), in both reading paths.

A colour, a sunroof, equipment or stock is a property the catalog does not hold: never read as a model name, never
answered "yes, it is in the catalog" (which reads as yes to the colour), but offered to the representative with the
employee's own question. A request for the representative ("אני רוצה לדבר עם נציג", "היית צריך לשאול את הנציג") is
the offer at once, with the open question — never "not understood" and never back to the pay questions. A question
naming a car on screen is about its real subject, not the copay; two cars compared get their catalog facts, and no
copay moves. Template mode is checked with no model; the LLM path with the scripted FakeLLM (code paths: what code
forces into or refuses from the model's turn) — the live Gemini path is not exercised here.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import LLMChain
from app.modules.conversation.understanding import NOT_IN_CATALOG
from .conftest import make_client

TOKEN = "rep-intent"
AUTH = {"X-Rep-Token": TOKEN}
PROFILE_ONE = "מנהלת מוצר, שכר בסיס 18500"
PROFILE_REST = "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"
CAR = "יש לי קורולה קרוס, משלמת 300"
PAY_WORDS = ("עמלות", "פנסיה", "נקודות זיכוי", "שכר")


def _client(engine, llm=None):
    return make_client(engine, rep_access_token=TOKEN, **({"llm": LLMChain([llm])} if llm else {}))


def _say(c, cid, text):
    r = c.post(f"/api/conversations/{cid}/messages", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def _offer(turn):
    return next((f for f in turn["expects"]["fields"] if f["field"] == "ask_rep"), None)


def _start(c, *messages):
    cid = c.post("/api/conversations").json()["conversation_id"]
    for m in messages:
        _say(c, cid, m)
    return cid


def _results(c, cid):
    return {r["option_id"]: r["result"] for r in c.get(f"/api/conversations/{cid}").json()["results"]}


def _code(snapshot, name):
    return next(c.catalog_code for c in snapshot.catalog if name in c.model_name)


# ---------- template mode (no model) ----------

@pytest.mark.parametrize("question", ["יש לכם רכב אדום?", "יש לכם את NIRO באדום?", "באילו צבעים מגיע NIRO?",
                                      "יש גג נפתח ב-NIRO?"])
def test_a_property_question_is_offered_to_the_rep_never_answered_from_the_catalog(engine, question):
    c = _client(engine)
    cid = _start(c, PROFILE_ONE, PROFILE_REST, CAR)                 # before results

    turn = _say(c, cid, question)

    assert NOT_IN_CATALOG not in turn["reply"]
    assert "כן, בקטלוג יש" not in turn["reply"]                     # never reads as yes to the colour
    assert _offer(turn)["question"] == question                    # offered with the employee's own words


@pytest.mark.parametrize("question", ["באילו צבעים מגיע NIRO?", "יש גג נפתח ב-NIRO?", "יש לכם את NIRO באדום?"])
def test_after_results_a_property_question_about_a_car_on_screen_is_not_a_copay_question(engine, snapshot, question):
    c = _client(engine)
    cid = _start(c, PROFILE_ONE, PROFILE_REST, CAR, "NIRO")
    before = _results(c, cid)
    assert before                                                   # results on screen

    turn = _say(c, cid, question)

    assert "ההשתתפות היא" not in turn["reply"] and "כן, בקטלוג יש" not in turn["reply"]
    assert _offer(turn)["question"] == question
    assert _results(c, cid) == before                               # nothing in the calculation moved


@pytest.mark.parametrize("request_text", ["היית צריך לשאול את הנציג", "אני רוצה לדבר עם נציג", "תשאל את הנציג"])
def test_a_request_for_the_rep_offers_the_open_question_and_asks_nothing_about_pay(engine, request_text):
    c = _client(engine)
    cid = _start(c, PROFILE_ONE)
    _say(c, cid, "יש לכם רכב אדום?")

    turn = _say(c, cid, request_text)

    assert "לא הצלחתי להבין" not in turn["reply"]
    # the question still open, not the request: offered (the card opens with it), or — the offer being open,
    # "תשאל" is its yes — straight to the preview before sending
    offer = _offer(turn)
    if offer is not None:
        assert offer["question"] == "יש לכם רכב אדום?"
    else:
        assert "יש לכם רכב אדום?" in turn["reply"] and "לשלוח" in turn["reply"]
    assert not any(w in turn["reply"] for w in PAY_WORDS)           # never back to the pay questions this turn


def test_a_request_for_the_rep_with_no_question_yet_asks_what_to_ask(engine):
    c = _client(engine)
    cid = _start(c, PROFILE_ONE, PROFILE_REST, CAR)
    turn = _say(c, cid, "אני רוצה לדבר עם נציג")
    assert "לא הצלחתי להבין" not in turn["reply"] and "כתבו מה תרצו לשאול" in turn["reply"]
    assert _offer(turn)["question"] == ""                           # the card opens empty, ready to type
    follow = _say(c, cid, "לשאול")                                   # typed instead of the card
    assert "מה תרצו לשאול את נציג התחבורה" in follow["reply"] and "פרטיות" not in follow["reply"]


def test_a_request_after_skipping_is_still_offered_and_after_sending_is_not_doubled(engine):
    c = _client(engine)
    cid = _start(c, PROFILE_ONE, PROFILE_REST, CAR, "NIRO")
    _say(c, cid, "יש גג נפתח ב-NIRO?")
    _say(c, cid, "להמשיך בלי")
    turn = _say(c, cid, "אני רוצה לדבר עם נציג")                     # an explicit request overrides the skip
    assert _offer(turn)["question"] == "יש גג נפתח ב-NIRO?"
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "יש גג נפתח ב-NIRO?", "intent": "general"})
    assert r.json()["status"] == "created"
    again = _say(c, cid, "תשאל את הנציג")                            # nothing open any more: what to ask
    assert _offer(again)["question"] == ""
    records = [m for m in c.get(f"/api/conversations/{cid}").json()["messages"] if m["source"] == "rep_inquiry"]
    assert len(records) == 1 and len(c.get("/api/rep/questions", headers=AUTH).json()) == 1


def test_two_cars_compared_get_catalog_facts_and_no_copay_moves(engine, snapshot):
    c = _client(engine)
    cid = _start(c, PROFILE_ONE, PROFILE_REST, CAR, "NIRO ו-DOLPHIN")
    before = _results(c, cid)

    turn = _say(c, cid, "מה ההבדל בין NIRO ל-DOLPHIN?")

    assert "לפי הקטלוג:" in turn["reply"] and "NIRO" in turn["reply"] and "DOLPHIN" in turn["reply"]
    assert "ההשתתפות היא" not in turn["reply"]
    assert _results(c, cid) == before
    assert c.get("/api/rep/questions", headers=AUTH).json() == []   # nothing opened
    # the same comparison may go to the representative as a general question; no copay moves
    general = c.post(f"/api/conversations/{cid}/rep-question",
                     json={"question": "מה ההבדל בין NIRO ל-DOLPHIN?", "intent": "general"})
    assert general.status_code == 200 and general.json()["missing_field"] == "GENERAL_QUESTION"
    assert _results(c, cid) == before
    # and a copay inquiry mixing the two stays refused (stage 1)
    mixed = c.post(f"/api/conversations/{cid}/rep-question",
                   json={"question": "מה ההבדל בהשתתפות בין NIRO ל-DOLPHIN?", "intent": "copay_verification",
                         "option_id": _code(snapshot, "NIRO")})
    assert mixed.status_code == 422


def test_a_question_about_the_result_itself_is_still_answered_from_the_result(engine):
    """The narrowing never loses C5: "למה החישוב זמני?" is still about the result on screen."""
    c = _client(engine)
    cid = _start(c, PROFILE_ONE, PROFILE_REST, CAR, "NIRO")
    turn = _say(c, cid, "למה ההשתתפות ב-NIRO לפי הקטלוג?")
    assert _offer(turn) is None and "NIRO" in turn["reply"]


# ---------- the LLM path (scripted FakeLLM: what code does around the model's turn) ----------

def test_llm_path_a_colour_question_is_not_forced_to_not_in_catalog_and_may_be_offered(engine):
    fake = FakeLLM()
    c = _client(engine, fake)
    cid = _start(c)
    fake.turn(call("propose_rep_question", question="יש לכם רכב אדום?"),
              reply="על צבעים אין לי מידע. רוצים שאשאל את נציג התחבורה?")
    turn = _say(c, cid, "יש לכם רכב אדום?")
    assert NOT_IN_CATALOG not in turn["reply"]       # before: code prepended it to whatever the model said
    offer = _offer(turn)
    assert offer is not None and offer["question"] == "יש לכם רכב אדום?"   # the offer was not refused


def test_llm_path_a_request_for_the_rep_is_decided_in_code_without_the_model(engine):
    fake = FakeLLM()
    c = _client(engine, fake)
    cid = _start(c)
    fake.turn(reply="שלום")                                          # anything scripted for the first turn
    _say(c, cid, PROFILE_ONE)
    calls = len(fake.calls)
    turn = _say(c, cid, "היית צריך לשאול את הנציג")
    assert len(fake.calls) == calls                                  # no model call for this turn
    assert "לא הצלחתי להבין" not in turn["reply"] and _offer(turn) is not None
    assert not any(w in turn["reply"] for w in PAY_WORDS)
