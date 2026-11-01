""""אני לא זוכרת, תציג לי אפשרויות ואזהה": a request for the cars is not "no car" (1/11).

Manual QA on 6c22c50, with Gemini: at the question about today's car, the model guessed match_current_car(no_car) for
that message; code refused the guess (no words for it) but then raised its own question "אין לי רכב צמוד / יש רכב,
ההשתתפות 0", which replaced the reply. Without the model the message was "לא בטוח שהבנתי". Root causes: (1) a refused
model guess was turned into code's clarification, so the model's understanding never reached the employee; (2) a
request to see the cars, or not knowing today's model, had no intent at the car question. Now a refused guess is only
refused (the ambiguity comes only from the employee's words), and the request is answered in code from the real
catalog (today's car: every catalog car; the alternatives: the eligible ones, numbered like the selection card).
Nothing is saved or assumed; the financial values still missing are still asked.
"""
from __future__ import annotations

import pytest

from .conftest import make_client

PROFILE = ["קבוצה ב", "שכר בסיס 18000",
           "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"]
ASK_TODAY = "איזה מהם הרכב הצמוד שלכם היום?"


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


def catalog_labels(c):
    return {car["label"] for car in c.get("/api/catalog").json()}


def closed(turn):
    return [f["field"] for f in turn["expects"].get("fields", []) if f.get("closed")]


def assert_nothing_assumed(c, cid, turn):
    assert view(c, cid)["today_car"] is None                           # no car recorded, no "no car"
    assert "ambiguity" not in closed(turn)                              # never "no car, or copay 0?"
    assert "אין לי רכב צמוד" not in turn["reply"] and "ההשתתפות 0" not in turn["reply"]
    assert "לא הצלחתי להבין" not in turn["reply"] and "לא בטוח שהבנתי" not in turn["reply"]


@pytest.mark.parametrize("text", [
    "אני לא זוכרת, תציג לי אפשרויות ואזהה",                          # the manual QA message
    "לא זוכרת איזה רכב",
    "לא זוכר את הדגם, תן לי רשימה",
    "תראה לי רשימה של הרכבים",
    "אפשר לראות את הדגמים?",
    "תציגי לי את האופציות",
])
def test_at_todays_car_the_catalog_is_shown_and_nothing_is_assumed(engine, text):
    c, cid, say = start(engine)
    for m in PROFILE:
        say(m)
    turn = say(text)
    assert ASK_TODAY in turn["reply"]
    shown = {ln[2:] for ln in turn["reply"].splitlines() if ln.startswith("- ")}
    assert shown == catalog_labels(c)                                   # the real catalog, every car, nothing else
    assert_nothing_assumed(c, cid, turn)
    assert "car" in [f["field"] for f in turn["expects"]["fields"]]      # the question stays open


def test_a_car_picked_from_the_list_is_recognized_and_its_copay_is_still_asked(engine):
    c, cid, say = start(engine)
    for m in PROFILE:
        say(m)
    say("אני לא זוכרת, תציג לי אפשרויות ואזהה")
    turn = say("זה הנירו")
    assert "זיהיתי: NIRO (2025)" in turn["reply"]
    assert "copay" in [f["field"] for f in turn["expects"]["fields"]]   # a missing amount is asked, never assumed


def test_at_the_alternatives_the_list_is_numbered_like_the_card_and_the_numbers_select(engine):
    c, cid, say = start(engine)
    for m in [*PROFILE, "יאריס קרוס, משלמת 200"]:
        say(m)
    turn = say("תראה לי רשימה")
    numbered = [ln for ln in turn["reply"].splitlines() if ln[:1].isdigit()]
    card = [ch["label"] for f in turn["expects"]["fields"] if f["field"] == "options" for ch in f["choices"]]
    assert [ln.split(". ", 1)[1].split(" — ")[0] for ln in numbered] == card
    picked = say("1, 4")
    assert {r["label"] for r in picked["results"]} == {card[0], card[3]}


@pytest.mark.parametrize("text,expect", [
    ("אין רכב", "רשמתי: אין לכם רכב צמוד היום."),                    # still "no car"
    ("יש לי קורולה קרוס ומשלמת עליו אני לא זוכרת כמה", "COROLLA CROSS"),   # an unknown copay, not a list
])
def test_other_answers_at_todays_car_keep_their_meaning(engine, text, expect):
    c, cid, say = start(engine)
    for m in PROFILE:
        say(m)
    turn = say(text)
    assert expect in turn["reply"] and ASK_TODAY not in turn["reply"]


def test_a_ranking_request_at_the_alternatives_still_ranks(engine):
    c, cid, say = start(engine)
    for m in [*PROFILE, "יאריס קרוס, משלמת 200"]:
        say(m)
    turn = say("הכי משתלם")
    assert turn["results"] and "אלה הרכבים שאתם זכאים להם" not in turn["reply"]


# ---------- with the model (scripted) ----------

def _with_model(engine, fake):
    from app.modules.conversation.public import LLMChain
    c, cid, say = start(engine, LLMChain([fake]))
    plain = make_client(engine)
    plain.cookies = c.cookies
    for m in PROFILE:                                                   # the setup without the model
        plain.post(f"/api/conversations/{cid}/messages", json={"text": m})
    return c, cid, say


@pytest.mark.parametrize("text", [
    "אני לא זוכרת, תציג לי אפשרויות ואזהה",
    "אין לי מושג מה הדגם, יש איזה רשימה לבחור ממנה?",               # no word of the fallback list: the model decides
    "שכחתי איך קוראים לאוטו, תעזור לי למצוא",
])
def test_with_the_model_the_model_decides_and_code_shows_the_real_catalog(engine, text):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("show_cars"), reply="איזה מהם הרכב הצמוד שלכם היום?")
    c, cid, say = _with_model(engine, fake)
    turn = say(text)
    assert fake.calls                                                   # free text goes to the model, not to a word list
    shown = {ln[2:] for ln in turn["reply"].splitlines() if ln.startswith("- ")}
    assert shown == catalog_labels(c)                                   # code's list: every real car, nothing invented
    assert turn["reply"].rstrip().endswith("?")                        # the model's question closes the reply
    assert_nothing_assumed(c, cid, turn)


def test_with_the_model_a_list_request_phrased_as_a_question_is_never_offered_to_the_representative(engine):
    # live Gemini 1/11: "יש איזה רשימה לבחור ממנה?" was scope "org"; the guard trimmed the model's line and the template
    # added "רוצים שאשאל את נציג התחבורה?" under the list that already answered it
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("show_cars"), reply="הנה הרשימה! בחרו.", retry="הנה הרשימה! בחרו.")   # broken shape, twice
    c, cid, say = _with_model(engine, fake)
    turn = say("אין לי מושג מה הדגם, יש איזה רשימה לבחור ממנה?")
    assert {ln[2:] for ln in turn["reply"].splitlines() if ln.startswith("- ")} == catalog_labels(c)
    assert "נציג" not in turn["reply"] and "ask_rep" not in closed(turn)
    assert ASK_TODAY in turn["reply"] and "עדיין חסרים" not in turn["reply"]   # the list's question, not a recap
    assert_nothing_assumed(c, cid, turn)


def test_with_the_model_its_wrong_no_car_guess_is_refused_and_nothing_is_assumed(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("match_current_car", no_car=True),                # the live model's guess, 1/11
              reply="לא נורא, נמצא אותו יחד.\nאיזה רכב צמוד יש לכם היום?")
    c, cid, say = _with_model(engine, fake)
    turn = say("אני לא זוכרת, תציג לי אפשרויות ואזהה")
    assert_nothing_assumed(c, cid, turn)
    assert "car" in [f["field"] for f in turn["expects"]["fields"]]


def test_with_the_model_a_refused_no_car_guess_never_becomes_codes_no_car_question(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("match_current_car", no_car=True),
              reply="בסדר, אפשר לבדוק ולחזור אליי. איזה רכב צמוד יש לכם היום?")
    c, cid, say = _with_model(engine, fake)
    turn = say("רגע, אני צריכה לבדוק את זה במשרד")
    results = [m.content for call_ in fake.calls for m in call_.messages if m.role == "tool"]
    assert fake.calls and results                                       # the model was asked, its call refused
    assert_nothing_assumed(c, cid, turn)


def test_the_employees_own_ambiguous_words_still_raise_the_question(engine):
    c, cid, say = start(engine)
    for m in PROFILE:
        say(m)
    turn = say("אינני משתתף")
    assert "ambiguity" in closed(turn)                                  # "אינני משתתף": asked, as ruled
