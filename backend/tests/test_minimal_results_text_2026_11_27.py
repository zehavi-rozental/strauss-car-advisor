"""Finding #10 (27/11, replaces the #9 summary): the result cards already show the car with the highest net, every
net, the difference and the reason, so the chat text above them repeats none of it. With the car buttons on screen:

  results (a chosen comparison, "הכי משתלם"):
    התוצאות מוצגות בכרטיסים, מהנטו הגבוה לנמוך.
    להשוואת רכבים אחרים לחצו על "לשנות בחירה", ולצפייה בכל הרכבים על "כל הרכבים לפי נטו".
  the map ("כל הרכבים לפי נטו"):
    כל הרכבים מדורגים לפי הנטו החודשי המשוער.
    (the "ויתור על רכב" status and the disclaimer)
    לבחירת רכבים להשוואה לחצו על "<the choose button as it is on screen>", או על "3 המשתלמים ביותר".

Only lines the cards do not hold stay (a car not calculated or not eligible, a copay sent to the representative, a
provisional today's copay), after the first line. The button names come from one source the screen also draws them
from (`car_actions` in the view). Where the buttons are not shown (waiting for the representative, a value held for
confirmation) the #9 summary stays.
"""
from __future__ import annotations

import re

from app.modules.conversation import templates as T
from app.modules.conversation.stage import ConversationState, OptionResult, Stage

from .conftest import make_client

NOA = "שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים, קבוצה ב"
FIRST = "התוצאות מוצגות בכרטיסים, מהנטו הגבוה לנמוך."
POINTER = 'להשוואת רכבים אחרים לחצו על "לשנות בחירה", ולצפייה בכל הרכבים על "כל הרכבים לפי נטו".'
MAP_FIRST = "כל הרכבים מדורגים לפי הנטו החודשי המשוער."
MAP_POINTER_AFTER = 'לבחירת רכבים להשוואה לחצו על "לשנות בחירה", או על "3 המשתלמים ביותר".'
MAP_POINTER_BEFORE = 'לבחירת רכבים להשוואה לחצו על "לבחור רכבים להשוואה", או על "3 המשתלמים ביותר".'
DISCLAIMER = "הדירוג מבוסס על הנטו החודשי המשוער בלבד. התאמת הרכב לצרכים שלכם עשויה להיות שונה."
LABELS = {"choose": "לבחור רכבים להשוואה", "change": "לשנות בחירה", "rank": "3 המשתלמים ביותר",
          "map": "כל הרכבים לפי נטו"}


def _start(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(text):
        r = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        return r.json()["reply"]

    for text in (NOA, "יש לי קורולה קרוס", "300"):
        say(text)
    return say, lambda: client.get(f"/api/conversations/{cid}").json()


def _no_figures(reply: str) -> None:
    assert "₪" not in reply and "הסיבה העיקרית" not in reply and "משאיר את הנטו" not in reply
    assert not re.search(r"\d", reply.replace("3 המשתלמים ביותר", "")), reply


# ---------------------------------------------------------------- each results path

def test_a_chosen_comparison_says_only_where_the_results_are_and_the_buttons(engine):
    say, view = _start(engine)
    reply = say("תשווה בין MG4 ל-NIRO")
    assert len(view()["results"]) == 2
    assert reply == f"{FIRST}\n{POINTER}"
    _no_figures(reply)


def test_best_value_says_the_same(engine):
    say, view = _start(engine)
    reply = say("מה הכי משתלם לי?")
    assert view()["results"] and reply == f"{FIRST}\n{POINTER}"


def test_the_map_before_results_names_the_choose_button_as_it_is_on_screen(engine):
    say, view = _start(engine)
    reply = say("השווה את כל האפשרויות שלי")
    lines = reply.splitlines()
    assert view()["option_map"] is not None and not view()["results"]
    assert lines[0] == MAP_FIRST and lines[-1] == MAP_POINTER_BEFORE
    assert "ויתור על רכב: ממתין למידע, ולכן לא נכלל בדירוג." in lines and DISCLAIMER in lines
    assert "\n\n" not in reply                   # the pointer is the call to action: no second one after it
    _no_figures(reply)


def test_the_map_after_results_points_at_changing_the_selection(engine):
    say, _ = _start(engine)
    say("תשווה בין MG4 ל-NIRO")
    reply = say("השווה את כל האפשרויות שלי")
    assert reply.splitlines()[0] == MAP_FIRST and reply.splitlines()[-1] == MAP_POINTER_AFTER
    _no_figures(reply)


def test_the_after_results_follow_up_of_small_talk_is_the_same_pointer(engine):
    say, _ = _start(engine)
    say("תשווה בין MG4 ל-NIRO")
    assert say("תודה").split("\n\n")[-1] == POINTER
    say, _ = _start(engine)
    say("מה הכי משתלם לי?")
    assert say("תודה").split("\n\n")[-1] == POINTER


def test_the_view_carries_the_button_labels_the_text_points_at(engine):
    _, view = _start(engine)
    assert view()["car_actions"] == LABELS
    # a new conversation draws its buttons from the create answer (it reads no view until a reload)
    assert make_client(engine).post("/api/conversations").json()["car_actions"] == LABELS


# ---------------------------------------------------------------- the lines the cards do not hold

def _item(label, delta, status="OK", reason=""):
    return OptionResult(option_id=label, label=label, result={
        "status": status, "reason": reason, "delta_display": None if delta is None else str(delta),
        "copay_pending": False, "drivers": [], "why": "שווי השימוש נמוך יותר, ולכן המס יקטן.",
        "alternative": {"net_display": "12045", "copay": "250"}})


def test_a_car_not_calculated_is_still_said_after_the_first_line():
    state = ConversationState(stage=Stage.PRESENTED, results=[
        _item("MG4 (2025)", 1013), _item("MODEL 3 (2025)", None, "NOT_ELIGIBLE", reason="הרכב בקבוצה גבוהה משלכם"),
        _item("NIRO (2025)", None, "INCOMPLETE", reason="חסר שכר")])
    assert T.results_text(state).splitlines() == [
        FIRST, "MODEL 3 (2025) לא זמין לכם: הרכב בקבוצה גבוהה משלכם", "NIRO (2025): חסר מידע לחישוב. חסר שכר", POINTER]


def test_without_the_buttons_the_summary_stays():
    """Waiting for the representative: the car buttons are not drawn, so the text keeps the #9 summary."""
    state = ConversationState(stage=Stage.WAITING_FOR_REP, results=[_item("MG4 (2025)", 1013), _item("A", 100)])
    text = T.results_text(state)
    assert FIRST not in text and POINTER not in text
    assert text.splitlines()[0].startswith("בשני הרכבים הנטו שלכם יגדל. MG4 (2025) משאיר את הנטו הגבוה ביותר")
