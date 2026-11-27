"""Finding #9 (27/11): the chat text above the result cards repeated every figure the cards show (each car's net, its
difference, each copay). The approved rule: the cards stay as they are; the text is a short summary of at most three
lines:
  1) where relevant, the direction for all the cars ("בכל 3 הרכבים הנטו שלכם יקטן."), then the car with the highest
     estimated net, that net and its difference from today;
  2) the main reason for that car, then "הפירוט המלא בכרטיסים." (the map: "הפירוט המלא במפת האפשרויות.");
  3) the existing next step, as its own paragraph.
No per-car net list and no copay list. Every figure comes from the engine result the cards show.
Since #10 (27/11) this summary is the text only where the car buttons are not shown; with them, the text
says where the results are and points at the buttons (test_minimal_results_text_2026_11_27.py).
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation import templates as T
from app.modules.conversation.option_map import NET_ONLY, map_reply
from app.modules.conversation.stage import (ConversationState, MapEntry, OptionMap, OptionResult,
                                            PendingConfirmation, Stage)

CARDS = "הפירוט המלא בכרטיסים."


def _item(label, delta, net, why="שווי השימוש גבוה יותר, ולכן המס יגדל."):
    result = {"status": "OK", "reason": "", "delta_display": str(delta), "copay_pending": False, "drivers": [],
              "why": why, "alternative": {"net_display": str(net), "copay": "250"}}
    return OptionResult(option_id=label, label=label, result=result)


def _text(*items, **state):
    return T.results_text(ConversationState(results=list(items), **state))


# ---------------------------------------------------------------- the template itself

def test_all_down_is_the_approved_example():
    t = _text(_item("NIRO (2025)", -1400, 12209), _item("MG4 (2025)", -1163, 12446),
              _item("KONA EV (2026)", -1500, 12109))
    assert t.splitlines() == [
        "בכל 3 הרכבים הנטו שלכם יקטן. MG4 (2025) משאיר את הנטו הגבוה ביותר: 12,446 ₪ בחודש, 1,163 ₪ פחות מהיום.",
        "הסיבה העיקרית: שווי השימוש גבוה יותר, ולכן המס יגדל. " + CARDS]


def test_all_up_two_cars_and_mixed():
    up = _text(_item("A", 100, 11100), _item("B", 300, 11300))
    assert up.splitlines()[0] == "בשני הרכבים הנטו שלכם יגדל. B משאיר את הנטו הגבוה ביותר: 11,300 ₪ בחודש, 300 ₪ יותר מהיום."
    mixed = _text(_item("A", -50, 10950), _item("B", 70, 11070), _item("C", 0, 11000))
    assert mixed.splitlines()[0].startswith("בחלק מהרכבים הנטו שלכם יגדל ובחלק יקטן. B משאיר את הנטו הגבוה ביותר")


def test_one_car_and_no_change():
    one = _text(_item("ATTO 3 (2025)", 661, 11693))
    assert one.splitlines() == ["בדקתי את ATTO 3 (2025). הנטו שלכם יגדל ב-661 ₪ בחודש, ל-11,693 ₪.",
                                "הסיבה העיקרית: שווי השימוש גבוה יותר, ולכן המס יגדל. " + CARDS]
    flat = _text(_item("A", 0, 11000, why=""), _item("B", 0, 11000, why=""))
    assert flat.splitlines() == ["בשני הרכבים הנטו שלכם לא ישתנה.", CARDS]


def test_a_tie_names_every_car_with_the_highest_net():
    t = _text(_item("A", 200, 11200), _item("B", 200, 11200), _item("C", 100, 11100))
    assert "A ו-B משאירים את הנטו הגבוה ביותר: 11,200 ₪ בחודש" in t.splitlines()[0]


def test_no_per_car_list_and_no_copay_list():
    t = _text(_item("NIRO (2025)", 539, 11571), _item("MG4 (2025)", 1013, 12045))
    assert "11,571" not in t and "539" not in t and "NIRO" not in t          # only the car with the highest net
    assert "הנטו החודשי המשוער:" not in t and "השתתפות" not in t and "250" not in t
    assert len(t.splitlines()) <= 2


def test_rank_says_how_many_were_checked_then_the_summary():
    t = _text(_item("A", 100, 11100), _item("B", 300, 11300), _item("C", 200, 11200), ranked_count=13)
    assert t.splitlines() == [
        "בדקתי את כל 13 הרכבים שאתם זכאים להם. B משאיר את הנטו הגבוה ביותר: 11,300 ₪ בחודש, 300 ₪ יותר מהיום.",
        "הסיבה העיקרית: שווי השימוש גבוה יותר, ולכן המס יגדל. " + CARDS]


# ---------------------------------------------------------------- where the car buttons are not shown

def test_without_the_car_buttons_the_summary_stays_and_the_held_value_waits():
    """#10 (27/11) replaced this summary wherever the car buttons are on screen. A value held for confirmation has
    its own buttons and hides them, so the reply keeps the summary (and the held value is its one question)."""
    state = ConversationState(stage=Stage.PRESENTED, results=[_item("NIRO (2025)", 539, 11571),
                                                              _item("MG4 (2025)", 1013, 12045)],
                              pending_confirmation=PendingConfirmation(field="base_salary", value=Decimal("5000"),
                                                                       unusual=True))
    assert not T.car_buttons_shown(state)
    assert T.results_text(state).splitlines() == [
        "בשני הרכבים הנטו שלכם יגדל. MG4 (2025) משאיר את הנטו הגבוה ביותר: 12,045 ₪ בחודש, 1,013 ₪ יותר מהיום.",
        "הסיבה העיקרית: שווי השימוש גבוה יותר, ולכן המס יגדל. " + CARDS]


def test_the_map_without_the_car_buttons_keeps_its_summary():
    m = OptionMap(entries=[MapEntry(option_id="A", label="A", status="full", result={
        "status": "OK", "delta_display": "500", "why": "שווי השימוש נמוך יותר, ולכן המס יקטן.",
        "alternative": {"net_display": "11500"}})])
    reply = map_reply(ConversationState(stage=Stage.WAITING_FOR_REP, option_map=m))
    assert "A משאיר את הנטו הגבוה ביותר: 11,500 ₪ בחודש, 500 ₪ יותר מהיום." in reply
    assert "הפירוט המלא במפת האפשרויות." in reply and NET_ONLY in reply
