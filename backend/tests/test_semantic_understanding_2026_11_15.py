"""Semantic understanding (15/11): Gemini reads the meaning, code supplies every fact.

Live Gemini understood free wording, and code refused it for want of a literal word: a drive described by meaning
("שלא צריך לתדלק"), a correction back to an earlier value ("התכוונתי לקבוצה הקודמת"), a car in Hebrew letters ("מג 4"),
a ranking request without a ranking word ("אלה שהכי פחות יורידו לי מהמשכורת"). Now:
- a query parameter (the drive filter, the cars to compare, the ranking) is the model's reading; the answer is the
  catalog's / the engine's, and the reply names what was filtered or compared;
- saved data is grounded in the message, or in a value the employee gave earlier in this conversation;
- the model still cannot choose cars on its own: a car it picks must be referred to (by name, in other letters,
  by number, by drive) — small talk and a substituted car stay refused.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.agent import tidy_reply
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.parsing import match_drive, match_group, sounds_like
from app.modules.conversation.public import LLMChain
from app.modules.calculation.public import Drive

from .conftest import make_client

PROFILE = ("שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים, "
           "קבוצה ב")
TOKEN = "rep-test-token"


def start(engine, llm=None):
    client = make_client(engine, llm=LLMChain([llm]) if llm is not None else None, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()["reply"]

    def view():
        return client.get(f"/api/conversations/{cid}").json()

    def open_questions():
        return client.get("/api/rep/questions?status=OPEN", headers={"X-Rep-Token": TOKEN}).json()

    return say, view, open_questions


# ---------- the drive filter: the model's reading, the catalog's cars ----------

def test_a_drive_described_by_meaning_lists_the_catalog_cars_of_that_drive(engine):
    fake = FakeLLM()
    say, _, _ = start(engine, fake)
    say("אני בקבוצה ג")
    fake.turn(call("answer_eligible_cars", drives=["ELECTRIC"]), reply="יש כמה כאלה.")
    reply = say("מתוך מה שמגיע לי, יש משהו שלא צריך לתדלק?")
    assert "5 חשמליים" in reply and "MG4 (2025)" in reply and "PICANTO" not in reply
    assert "נציג" not in reply


def test_several_drives_in_one_question_are_filtered_together_and_named(engine):
    fake = FakeLLM()
    say, _, _ = start(engine, fake)
    say("אני בקבוצה ג")
    fake.turn(call("answer_eligible_cars", drives=["HYBRID", "PLUG_IN"]), reply="הנה.")
    reply = say("ואם אני רוצה משהו שחוסך דלק אבל עדיין עם מנוע?")
    assert "היברידיים או פלאג-אין" in reply
    assert "NIRO" in reply and "PICANTO" not in reply and "MG4" not in reply      # no petrol, no electric


def test_fuel_is_not_a_drive_without_a_model_either(engine):
    say, _, _ = start(engine)
    say("אני בקבוצה ג")
    say("אילו רכבים פתוחים לי?")
    reply = say("ואיזה מהם חוסכים דלק?")
    assert "רכבי בנזין" not in reply               # never the opposite of what was asked; asked or listed, never guessed


def test_a_drive_the_employee_named_still_wins_over_another_reading(engine):
    fake = FakeLLM()
    say, _, _ = start(engine, fake)
    say("אני בקבוצה ג")
    fake.turn(call("answer_eligible_cars", drives=["HYBRID"]))
    reply = say("אילו רכבים חשמליים יש לי?")
    assert "היברידיים" not in reply and "חשמליים" in reply


@pytest.mark.parametrize(("text", "drive"), [("רכב על דלק", None), ("חוסך דלק", None), ("בנזין", Drive.PETROL),
                                              ("רכב על בנזין", Drive.PETROL), ("ההיברידיים", Drive.HYBRID)])
def test_named_only_reads_a_drive_name_never_fuel(text, drive):
    # "דלק" alone never decides a drive in code: "רכב על דלק" and "חוסך דלק" are opposite; the model reads them
    assert match_drive(text, named_only=True) == drive


# ---------- saved data: the message, or a value the employee gave earlier ----------

def test_back_to_the_previous_group_is_grounded_in_the_value_given_before(engine):
    fake = FakeLLM()
    say, view, _ = start(engine, fake)
    say("אני בקבוצה ב")
    say("רגע, בעצם אני בקבוצה ג")
    fake.turn(call("save_profile", eligibility_group="ב"))
    reply = say("עזבי, התכוונתי לקבוצה הקודמת.")
    assert view()["profile"]["group"] == "ב"
    assert "עדכנתי: קבוצה ב במקום ג." in reply


def test_a_group_never_given_in_the_conversation_is_not_saved(engine):
    fake = FakeLLM()
    say, view, _ = start(engine, fake)
    say("אני בקבוצה ב")
    say("רגע, בעצם אני בקבוצה ג")
    fake.turn(call("save_profile", eligibility_group="א"))
    say("עזבי, התכוונתי לקבוצה הקודמת.")
    assert view()["profile"]["group"] == "ג"        # "א" was never said: refused, never invented


@pytest.mark.parametrize(("text", "group"), [("רגע, אני בקבוצה ג", "ג"), ("ובקבוצה ד", "ד"), ("קבוצת זכאות ב", "ב"),
                                              ("אני בקבוצה ב ולא ג", None)])
def test_the_prefix_of_the_word_group_is_not_read_as_a_group(text, group):
    assert match_group(("א", "ב", "ג", "ד"), text) == group


# ---------- the cars to compare and the ranking: the model's reading, the engine's figures ----------

def compared_profile(engine, fake):
    say, view, q = start(engine, fake)
    for text in (PROFILE, "יש לי קורולה קרוס", "300"):
        say(text)
    return say, view


def test_cars_written_in_hebrew_letters_are_the_cars_the_model_picked(engine):
    fake = FakeLLM()
    say, view = compared_profile(engine, fake)
    fake.turn(call("select_options", options=["MG4", "NIRO"]))
    say("בא לי לראות מה קורה אם אני עוברת למג 4 או לנירו")
    assert {r["label"] for r in view()["results"]} == {"MG4 (2025)", "NIRO (2025)"}


def test_small_talk_still_selects_nothing(engine):
    fake = FakeLLM()
    say, view = compared_profile(engine, fake)
    fake.turn(call("select_options", options=["MG4"]))
    say("מה שלומך היום?")
    assert view()["results"] == []


def test_a_ranking_asked_in_other_words_is_ranked_by_the_engine(engine):
    fake = FakeLLM()
    say, view = compared_profile(engine, fake)
    fake.turn(call("rank_options"))
    reply = say("תראי לי את אלה שהכי פחות יורידו לי מהמשכורת.")
    v = view()
    assert len(v["results"]) == 3 and "התוצאות מוצגות בכרטיסים, מהנטו הגבוה לנמוך." in reply
    deltas = [float(r["result"]["delta_display"]) for r in v["results"]]
    assert deltas == sorted(deltas, reverse=True)


@pytest.mark.parametrize(("text", "name", "expected"), [
    ("למג 4 או לנירו", "MG4", True), ("למג 4 או לנירו", "NIRO", True), ("מה עם הטוסון?", "TUCSON", True),
    ("ליאון", "LEON", True), ("יאריס קרוס", "YARIS CROSS HSD", True), ("מאזדה 3", "MAZDA 3", True),
    ("נראה לי שכן", "NIRO", False), ("מה שלומך?", "ATTO 3", False), ("מג 5", "MG4", False),
    ("תבדוק לי את אטו 3", "MG4", False), ("אני רוצה לנסות", "NIRO", False),
])
def test_a_car_name_in_other_letters(text, name, expected):
    assert sounds_like(text, name) is expected


# ---------- continuity: a settled group conflict releases the wait ----------

CONFLICT = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, "
            "2 ילדים, קבוצה ב")


def test_the_copay_is_asked_before_the_wait_for_the_representative(engine):
    say, view, open_q = start(engine)
    say(CONFLICT)
    reply = say("יש לי קורולה קרוס")
    assert view()["stage"] != "WAITING_FOR_REP" and "ההשתתפות" in reply and open_q() == []
    say("300")
    assert view()["today_copay"] == "300" and view()["stage"] == "WAITING_FOR_REP" and len(open_q()) == 1


def test_restating_the_tables_group_cancels_the_question_and_goes_on(engine):
    say, view, open_q = start(engine)
    for text in (CONFLICT, "יש לי קורולה קרוס", "300"):
        say(text)
    reply = say("רגע, אני בקבוצה ג")
    v = view()
    assert "עדכנתי: קבוצה ג במקום ב." in reply and "ביטלתי את השאלה" in reply
    assert open_q() == [] and v["stage"] == "CHOOSE_OPTIONS" and v["profile"]["group"] == "ג"
    say("תשווה בין MG4 ל-NIRO")
    assert len(view()["results"]) == 2


def test_another_group_reopens_the_question_about_it(engine):
    say, view, open_q = start(engine)
    for text in (CONFLICT, "יש לי קורולה קרוס", "300"):
        say(text)
    say("בעצם אני בקבוצה א")
    questions = open_q()
    assert view()["stage"] == "WAITING_FOR_REP" and len(questions) == 1
    assert "קבוצת זכאות א" in str(questions[0])


def test_with_the_model_a_new_group_conflict_is_said_even_when_its_reply_leaves_it_out(engine):
    fake = FakeLLM()
    say, view, _ = start(engine, fake)
    fake.turn(call("save_profile", occupation="מנהלת מוצר", eligibility_group="ב", base_salary="18500"),
              reply="רשמתי: תפקיד מנהלת מוצר, שכר בסיס 18,500 ₪.\nאיזה רכב צמוד יש לכם היום?")
    reply = say("אני מנהלת מוצר בקבוצה ב, שכר בסיס 18,500")
    assert "טבלת העיסוקים" in reply and "נציג התחבורה" in reply


# ---------- the reply's layout ----------

def test_a_reply_never_opens_with_an_empty_line():
    assert tidy_reply("\nמה שכר הבסיס?") == "מה שכר הבסיס?"
    assert tidy_reply("א\n\n\n\nב\n") == "א\n\nב"
