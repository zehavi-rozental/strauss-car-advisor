"""One number, several fields (5/11): "כמה נקודות זיכוי יש לכם? וכמה ילדים?" → "2" → "גם וגם".

Live QA on the product-excellence branch: the answer was "not understood" twice, and with Gemini save_profile was
refused (the 2 was in the message before). Now the question is held in the state (conversation.value_choice) and the
next message is read against it: buttons, code's reading of the words, else a structured answer of the model that
code validates. A later "בעצם רק ילדים" re-applies the choice and asks the dropped field again.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.llm.port import LLMChain
from app.modules.conversation.value_choice import read_answer

from .conftest import make_client

BOTH = ["credit_points", "children_under_23"]
SETUP = ["קבוצה ב", "שכר בסיס 18000, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%"]   # → credit points + children


def start(engine, llm=None):
    c = make_client(engine, llm=LLMChain([llm])) if llm is not None else make_client(engine)
    cid = c.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = c.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def profile():
        p = c.get(f"/api/conversations/{cid}").json()["profile"]
        return tuple(None if v is None else str(v) for v in (p["credit_points"], p["children"]))
    for m in SETUP:
        say(m)
    return say, profile


def test_a_bare_number_for_a_question_about_two_fields_is_asked_not_refused(engine):
    say, profile = start(engine)
    turn = say("2")
    assert "המספר 2 מתייחס לנקודות הזיכוי או למספר הילדים, או לשניהם?" in turn["reply"]
    assert "לא הצלחתי להבין" not in turn["reply"]
    assert turn["expects"]["field"] == "value_choice"
    assert [c["id"] for c in turn["expects"]["choices"]] == [*BOTH, "all"]
    assert profile() == (None, None)                                    # nothing saved before the answer


@pytest.mark.parametrize("answer, expected", [
    ("גם וגם", ("2", "2")), ("שניהם", ("2", "2")), ("לשניהם", ("2", "2")), ("כן, לשניהם", ("2", "2")),
    ("רק ילדים", (None, "2")), ("ילדים", (None, "2")), ("התכוונתי לנקודות זיכוי", ("2", None)),
    ("נקודות זיכוי", ("2", None)), ("לא לילדים", ("2", None)), ("הראשון", ("2", None)),
])
def test_the_answer_in_words_saves_the_number_to_the_fields_chosen(engine, answer, expected):
    say, profile = start(engine)
    say("2")
    turn = say(answer)
    assert profile() == expected
    assert "רשמתי" in turn["reply"]
    assert "לא הצלחתי" not in turn["reply"] and "לא בטוח שהבנתי" not in turn["reply"]


def test_the_buttons_answer_too(engine):
    say, profile = start(engine)
    say("2")
    say({"choice": "all", "field": "value_choice"})
    assert profile() == ("2", "2")


def test_a_later_correction_moves_the_number_and_asks_the_dropped_field_again(engine):
    say, profile = start(engine)
    say("2")
    say("גם וגם")
    turn = say("בעצם רק ילדים")
    assert profile() == (None, "2")
    assert turn["reply"].splitlines()[0] == "תיקנתי: המספר 2 רשום עכשיו רק למספר הילדים. את נקודות הזיכוי אשאל שוב."
    turn = say("התכוונתי לשניהם")                                       # and back again
    assert profile() == ("2", "2")


def test_a_correction_never_clears_a_value_the_employee_changed_since(engine):
    say, profile = start(engine)
    say("2")
    say("גם וגם")
    say("3 נקודות זיכוי")                                               # a new value, labelled
    assert profile() == ("3", "2")
    say("בעצם רק ילדים")
    assert profile() == ("3", "2")                                       # the 3 is not the number this choice was about


def test_another_number_is_a_new_answer_not_a_choice(engine):
    say, profile = start(engine)
    say("2")
    say("2.25 נקודות זיכוי ו-2 ילדים")
    assert profile() == ("2.25", "2")


def test_none_of_them_saves_nothing(engine):
    say, profile = start(engine)
    say("2")
    turn = say("לא זה ולא זה")
    assert profile() == (None, None)
    assert "לא רשמתי את המספר" in turn["reply"]


def test_a_value_out_of_range_for_one_field_is_not_offered_for_it(engine):
    say, profile = start(engine)
    turn = say("2.5")                                                    # 2.5 children is not a count
    assert turn["expects"]["field"] != "value_choice"


def test_with_the_model_words_code_cannot_read_are_read_by_the_model_and_validated(engine):
    fake = FakeLLM()
    say, profile = start(engine, fake)
    say("2")                                                             # asked by code: no model call
    fake.turn(call("choose_fields", fields=BOTH, answers=True))
    say("תרשום אותו בשני המקומות")
    assert profile() == ("2", "2")
    resolve_calls = [c for c in fake.calls if c.tools == ["choose_fields"]]
    assert len(resolve_calls) == 1 and resolve_calls[0].require_tool
    assert "<<<USER>>>תרשום אותו בשני המקומות<<<END>>>" in resolve_calls[0].messages[-1].content


def test_with_the_model_a_field_outside_the_question_is_refused(engine):
    fake = FakeLLM()
    say, profile = start(engine, fake)
    say("2")
    fake.turn(call("choose_fields", fields=["pension_percent"], answers=True))
    say("תרשום אותו במקום אחר")
    assert profile() == (None, None)


def test_with_the_model_an_answer_that_is_not_one_saves_nothing(engine):
    fake = FakeLLM()
    say, profile = start(engine, fake)
    say("2")
    fake.turn(call("choose_fields", fields=BOTH, answers=False))
    say("רגע, אני צריכה לבדוק משהו")
    assert profile() == (None, None)


def test_code_reads_the_clear_answers_without_a_model_call(engine):
    fake = FakeLLM()
    say, profile = start(engine, fake)
    say("2")
    before = len(fake.calls)
    say("גם וגם")
    assert profile() == ("2", "2") and len(fake.calls) == before


def test_a_question_the_model_asked_itself_is_held_and_answered(engine):
    fake = FakeLLM()
    say, profile = start(engine, fake)
    fake.turn(reply="רק כדי לוודא: 2 הן נקודות הזיכוי או מספר הילדים?")
    turn = say("יש לי 2")
    assert turn["expects"]["field"] == "value_choice"
    say("גם וגם")
    assert profile() == ("2", "2")


def test_read_answer_unit():
    v = Decimal(2)
    assert read_answer("שניהם", BOTH, v) == BOTH
    assert read_answer("לא, רק ילדים", BOTH, v) == ["children_under_23"]
    assert read_answer("לא לנקודות", BOTH, v) == ["children_under_23"]
    assert read_answer("2 לשניהם", BOTH, v) == BOTH                    # the same number said again
    assert read_answer("3 ילדים", BOTH, v) is None                      # another number: a new value
    assert read_answer("אף אחד", BOTH, v) == []
    assert read_answer("מה זה?", BOTH, v) is None


def test_an_answer_not_understood_is_asked_again_in_other_words_then_one_field_at_a_time(engine):
    """Live Gemini 5/11: "תכניס אותו פעמיים" was not resolved and the question was dropped, so the 2 was lost."""
    say, profile = start(engine)
    first = say("2")["reply"]
    again = say("אוקיי")
    assert again["expects"]["field"] == "value_choice"
    assert again["reply"] != first and "לא הייתי בטוח" in again["reply"]
    third = say("אוקיי")
    assert third["expects"]["field"] != "value_choice"           # asked twice: the flow asks one field at a time
    assert profile() == (None, None)


def test_a_question_that_names_a_field_is_never_read_as_the_choice(engine):
    say, profile = start(engine)
    say("2")
    say("מה זה נקודות זיכוי?")
    assert profile() == (None, None)


def test_with_the_model_the_question_on_screen_reaches_the_structured_call(engine):
    fake = FakeLLM()
    say, profile = start(engine, fake)
    say("2")
    fake.turn(call("choose_fields", fields=BOTH, answers=True))
    say("תכניס אותו פעמיים")
    prompt = next(c for c in fake.calls if c.tools == ["choose_fields"]).messages[0].content
    assert "המספר 2 מתייחס לנקודות הזיכוי או למספר הילדים" in prompt
    assert profile() == ("2", "2")
