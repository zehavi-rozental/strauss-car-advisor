"""Editing the cars compared in free text after a result (22/10, found by probing the demo flows): add, remove and
replace update the real selection and recalculate, in template mode and with a model; the limit of 3 holds, and
nothing else in the state changes."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM

from .conftest import CONFIG
from .test_rep_policy_2026_10_22 import Chat

PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")
CAR = "יש לי קורולה קרוס, משלמת 300"
MODES = ["template", "llm"]
ATTO, MG4, EV3, PICANTO, YARIS = "CAR-10", "CAR-06", "CAR-11", "CAR-01", "CAR-02"


def chat_with(sessions, mode, *chosen):
    chat = Chat(sessions, FakeLLM() if mode == "llm" else None, config=CONFIG)
    for m in [PROFILE, CAR]:
        chat.say(m)
    chat.choose(list(chosen))
    return chat


def calculated(chat):
    return [r.option_id for r in chat.state.results]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("text", ["תוסיף גם את MG4", "אני רוצה להשוות גם את MG4"])
def test_add_a_car_keeps_the_others_and_recalculates(sessions, mode, text):
    chat = chat_with(sessions, mode, ATTO)
    turn = chat.say(text)
    assert chat.state.selected == [ATTO, MG4] and calculated(chat) == [ATTO, MG4]
    assert "הוספתי MG4 (2025)" in turn.reply and "לא הצלחתי להבין" not in turn.reply
    assert chat.state.profile.base == Decimal("18500")                  # nothing else changes


@pytest.mark.parametrize("mode", MODES)
def test_remove_a_car(sessions, mode):
    chat = chat_with(sessions, mode, ATTO, MG4)
    turn = chat.say("תוריד את MG4")
    assert chat.state.selected == [ATTO] and calculated(chat) == [ATTO]
    assert "הורדתי MG4 (2025)" in turn.reply


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("text", ["במקום אטו 3 תבדוק את EV3", "עזוב את אטו 3, תראה לי את EV3"])
def test_replace_a_car(sessions, mode, text):
    chat = chat_with(sessions, mode, ATTO, MG4)
    chat.say(text)
    assert chat.state.selected == [MG4, EV3] and calculated(chat) == [MG4, EV3]


def test_the_limit_of_three_is_kept_and_said(sessions):
    chat = chat_with(sessions, "template", ATTO, MG4, EV3)
    turn = chat.say("תוסיף גם את פיקנטו")
    assert chat.state.selected == [ATTO, MG4, EV3] and "עד 3 רכבים" in turn.reply


def test_the_last_car_is_not_removed_silently(sessions):
    chat = chat_with(sessions, "template", ATTO)
    turn = chat.say("תוריד את אטו 3")
    assert chat.state.selected == [ATTO] and calculated(chat) == [ATTO] and "היחיד בהשוואה" in turn.reply


def test_a_car_already_compared_or_not_compared_is_said(sessions):
    chat = chat_with(sessions, "template", ATTO, MG4)
    assert "כבר בהשוואה" in chat.say("תוסיף גם את MG4").reply
    assert "לא היה בהשוואה" in chat.say("תוריד את EV3").reply and chat.state.selected == [ATTO, MG4]


def test_a_bare_car_name_still_means_the_cars_to_compare(sessions):
    chat = chat_with(sessions, "template", ATTO)
    chat.say("MG4")
    assert chat.state.selected == [MG4]


# ---------- after a result: a vague message or a request to explain keeps the result (bug C) ----------

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("text", ["אממ", "רגע", "לא הבנתי", "מה זאת אומרת?", "אפשר להסביר שוב?"])
def test_a_vague_message_after_a_result_keeps_it_and_never_asks_to_choose_cars_again(sessions, mode, text):
    chat = chat_with(sessions, mode, ATTO)
    turn = chat.say(text)
    assert chat.state.selected == [ATTO] and calculated(chat) == [ATTO]
    assert "איזה רכבים להשוות" not in turn.reply and "אילו רכבים לבדוק" not in turn.reply
    assert "לא בטוח שהבנתי" in turn.reply or "ATTO 3" in turn.reply   # clarified, or the result explained


# ---------- "to continue later" (bug B) ----------

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("text", ["אפשר להמשיך אחר כך?", "אני אחזור לזה מחר", "בוא נעצור כאן",
                                  "אפשר להמשיך מאיפה שהפסקנו?"])
def test_a_pause_is_answered_with_what_is_kept_and_changes_nothing(sessions, mode, text):
    chat = chat_with(sessions, mode, ATTO)
    before = chat.state.model_dump(exclude={"last_ask", "last_asked", "asked_field", "misses", "links_shown",
                                            "link_now", "unknown_told", "offered_rank"})
    turn = chat.say(text)
    assert "נשמר" in turn.reply and "לרענן את הדף" in turn.reply and "איזה רכבים להשוות" not in turn.reply
    after = chat.state.model_dump(exclude={"last_ask", "last_asked", "asked_field", "misses", "links_shown",
                                           "link_now", "unknown_told", "offered_rank"})
    assert after["profile"] == before["profile"] and after["selected"] == before["selected"]
    assert after["results"] == before["results"] and after["stage"] == before["stage"]
