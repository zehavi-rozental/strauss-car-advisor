"""Finding #6, 24/11 (public site, after the results): "מה אתה אומר? אתה שמח שאתה עוזר לי?" got the sources text and the
results again; "מה מזג האויר?" a redirect and the results again. The approved rule: small talk and off-topic messages
are answered by code with a fixed opening line and ONE short follow-up line that fits where the conversation is (the
pending question, the next step after the results, the representative step waiting). Never the results again, never
the sources text (kept for "על מה אתה מתבסס?"), no saved data changes, no model call.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain

from .conftest import make_client

SMALL = "תודה! אני כאן כדי לעזור לכם לבחור רכב חברה ולהבין את ההשפעה על הנטו."
OFF = "בזה אני לא יכול לעזור. אני עוזר רק בנושאי רכב החברה וההשפעה שלו על הנטו."
SOURCES = "אני עובד לפי כללי רכב החברה"
SMALL_TALK = ["מה אתה אומר? אתה שמח שאתה עוזר לי?", "תודה", "תודה רבה!", "מה נשמע?"]
OFF_TOPIC = ["מה מזג האויר?", "ספר לי בדיחה"]
PROFILE = {"base_salary": 18500, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
           "credit_points": 2.25, "children_under_23": 2}
DATA = ("profile", "current_car", "today_copay", "selected", "results", "data_version", "results_version")


def start(engine, llm=None):
    client = make_client(engine, llm=llm)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def view():
        return client.get(f"/api/conversations/{cid}").json()
    return say, view


def to_results(say):
    say("מנהלת מוצר")
    say({"fields": PROFILE})
    say("יש לי קורולה קרוס 2025 ואני משלמת 300")
    shown = say("אטו 3")
    assert shown["results"] and "התוצאות מוצגות בכרטיסים, מהנטו הגבוה לנמוך." in shown["reply"]
    return shown


def on_screen(view: dict) -> list:
    return [(f["field"], [c["id"] for c in f["choices"]]) for f in view["expects"].get("fields") or []]


def fields(turn: dict) -> list[str]:
    return [f["field"] for f in turn["expects"].get("fields") or []]


# after results the follow-up points at the car buttons by their names, as the results text does (#10, 27/11)
POINTER = 'להשוואת רכבים אחרים לחצו על "לשנות בחירה", ולצפייה בכל הרכבים על "כל הרכבים לפי נטו".'

def lines(reply: str) -> list[str]:
    return [x for x in reply.split("\n") if x.strip()]


def check_aside(say, view, text, opening, follow_up):
    """One message aside: the fixed opening, then exactly the follow-up line; nothing saved, the question's buttons
    as they were, no results and no sources text."""
    before = view()
    turn = say(text)
    after = view()
    assert lines(turn["reply"]) == [opening, follow_up], (text, turn["reply"])
    # two paragraphs, a blank line between them (never "…על הנטו.יש רכיבי שכר…"): in the turn and as stored
    assert turn["reply"] == opening + "\n\n" + follow_up, (text, turn["reply"])
    assert after["messages"][-1]["content"] == turn["reply"], text
    assert SOURCES not in turn["reply"] and "בדקתי את" not in turn["reply"] and not turn["results"], text
    assert {k: after[k] for k in DATA} == {k: before[k] for k in DATA}, text            # no saved data changes
    assert on_screen(after) == on_screen(before), text                                 # the same questions and buttons
    return turn


@pytest.mark.parametrize("text, opening", [*[(t, SMALL) for t in SMALL_TALK], *[(t, OFF) for t in OFF_TOPIC]])
def test_at_the_first_question_the_question_comes_again(engine, text, opening):
    say, view = start(engine)
    check_aside(say, view, text, opening, "מה קבוצת הזכאות שלכם (א-ד), או מה התפקיד שלכם? ומה שכר הבסיס החודשי שלכם?")


@pytest.mark.parametrize("text, opening", [(SMALL_TALK[0], SMALL), (OFF_TOPIC[0], OFF)])
def test_at_the_base_salary_question_the_question_comes_again(engine, text, opening):
    say, view = start(engine)
    say("קבוצה ג")
    check_aside(say, view, text, opening, "מה שכר הבסיס החודשי שלכם (ברוטו)?")


@pytest.mark.parametrize("text, opening", [(SMALL_TALK[0], SMALL), (OFF_TOPIC[0], OFF)])
def test_a_question_with_buttons_keeps_its_buttons(engine, text, opening):
    say, view = start(engine)
    for t in ("קבוצה ג", "18500", "אין"):
        say(t)
    turn = check_aside(say, view, text, opening, 'על מה ה"אין"?')
    assert [c["label"] for c in turn["expects"]["choices"]] == [
        "אין עמלות ואין בונוס", "אין ילדים עד גיל 23", "אין עמלות, בונוס, קרן השתלמות וילדים"]


@pytest.mark.parametrize("text, opening", [(SMALL_TALK[0], SMALL), (SMALL_TALK[1], SMALL), (OFF_TOPIC[0], OFF)])
def test_after_the_results_the_next_step_and_never_the_results_again(engine, text, opening):
    say, view = start(engine)
    to_results(say)
    check_aside(say, view, text, opening, POINTER)       # the car buttons' names, as the results say (#10)


def test_after_the_ranking_the_next_step_is_another_comparison(engine):
    say, view = start(engine)
    to_results(say)
    say("מה הכי משתלם")
    check_aside(say, view, SMALL_TALK[0], SMALL, POINTER)


def test_an_offer_to_ask_the_representative_is_reminded(engine):
    say, view = start(engine)
    to_results(say)
    offer = say("אני רוצה רכב בצבע אדום")
    assert "ask_rep" in fields(offer)
    check_aside(say, view, SMALL_TALK[0], SMALL, 'רוצים שאשאל את נציג התחבורה? בחרו "לשאול את הנציג" או "להמשיך בלי".')


def test_a_draft_for_the_representative_waiting_for_confirmation_is_reminded(engine):
    say, view = start(engine)
    to_results(say)
    say("אני רוצה רכב בצבע אדום")
    draft = say({"choice": "ask_rep", "field": "ask_rep"})
    assert "rep_question" in fields(draft)
    check_aside(say, view, OFF_TOPIC[0], OFF, 'הפנייה לנציג התחבורה מחכה לאישור שלכם: בחרו "לשלוח" או "לבטל".')


def test_an_open_question_with_the_representative_is_the_next_step(engine):
    say, view = start(engine)
    to_results(say)
    say("אני רוצה רכב בצבע אדום")
    say({"choice": "ask_rep", "field": "ask_rep"})
    sent = say({"choice": "send", "field": "rep_question"})
    assert "נציג" in sent["reply"]
    check_aside(say, view, SMALL_TALK[0], SMALL, "השאלה שלכם לנציג התחבורה עדיין פתוחה, והתשובה תגיע לכאן.")


def test_the_sources_text_still_answers_a_real_question_about_them(engine):
    say, _ = start(engine)
    to_results(say)
    assert SOURCES in say("על מה אתה מתבסס?")["reply"]


def test_small_talk_and_off_topic_need_no_model_call(engine):
    fake = FakeLLM()
    say, view = start(engine, llm=LLMChain([fake]))
    say("קבוצה ג")
    before = len(fake.calls)
    for text in (SMALL_TALK[0], OFF_TOPIC[0]):
        turn = say(text)
        assert turn["source"] == "template" and lines(turn["reply"])[0] in (SMALL, OFF)
    assert len(fake.calls) == before
