"""Manual QA 15/11 on 8870: corrections that were said but not saved, the eligible-car follow-up, and the counts.

- "רגע, אני בקבוצה ג" after the copay: the reply said "עדכנתי: קבוצה ג." while the stage gate refused the save and the
  group stayed ב. Now the acknowledgement is built only from what was saved, with the value it replaced, on every
  path (chat, the "הנתונים שלי" form, the model).
- "טעיתי, השכר שלי בעצם 20 אלף" said "רשמתי" (the old value lost); "אני משלמת 400 ולא 300" was not understood.
- "תשווה בין MG4 ל-NIRO" was not read; a comparison now answers with each car's net, the figures the cards show.
- "ומה הם החשמליים?" went to the representative ("ה" before the drive word).
- The map said "13 חלופות" next to "12 רכבים" in the chat: the 13th is "ויתור על רכב", now named as itself.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.parsing import match_drive
from app.modules.conversation.public import LLMChain
from app.modules.conversation.templates import money
from app.modules.calculation.public import Drive
from app.shared.money import to_shekel

from .conftest import make_client

PROFILE_B = ("שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים, "
             "קבוצה ב")


def start(engine, llm=None):
    client = make_client(engine, llm=LLMChain([llm]) if llm is not None else None)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()["reply"]

    def view():
        return client.get(f"/api/conversations/{cid}").json()

    return say, view


def compared(engine, llm=None):
    say, view = start(engine, llm)
    for text in (PROFILE_B, "יש לי קורולה קרוס", "300"):
        say(text)
    return say, view


def nets(view) -> dict[str, str]:
    return {r["label"]: r["result"]["alternative"]["net_display"] for r in view["results"]}


def test_compare_between_x_and_y_is_read_and_answers_with_the_cards_figures(engine):
    say, view = compared(engine)
    reply = say("תשווה בין MG4 ל-NIRO")
    v = view()
    assert {r["label"] for r in v["results"]} == {"MG4 (2025)", "NIRO (2025)"}
    line = next(ln for ln in reply.splitlines() if ln.startswith("הנטו החודשי המשוער:"))
    for label, net in nets(v).items():
        assert f"{label} {money(to_shekel(Decimal(net)))}" in line


def test_a_group_correction_after_the_copay_is_saved_before_it_is_confirmed(engine):
    say, view = compared(engine)
    say("תשווה בין MG4 ל-NIRO")
    reply = say("רגע, אני בקבוצה ג")
    assert view()["profile"]["group"] == "ג"
    assert "עדכנתי: קבוצה ג במקום ב." in reply
    assert reply.count("קבוצה ג") == 1                       # said once
    assert not view()["results_stale"]


def test_a_chat_salary_correction_says_the_old_and_new_value_and_recalculates(engine):
    say, view = compared(engine)
    say("תשווה בין MG4 ל-NIRO")
    before = nets(view())
    reply = say("טעיתי, השכר שלי בעצם 20 אלף")
    v = view()
    assert v["profile"]["base"] == "20000"
    assert "עדכנתי: שכר בסיס 20,000 ₪ במקום 18,500 ₪." in reply
    assert "רשמתי: שכר בסיס" not in reply
    assert not v["results_stale"] and nets(v) != before     # every result recalculated with the new salary


@pytest.mark.parametrize(("text", "saved"), [("אני משלמת 400 ולא 300", "400"), ("אני משלמת 400 ולא 999", "300")])
def test_the_value_replaced_named_after_the_new_one_is_not_a_second_number(engine, text, saved):
    say, view = compared(engine)
    say("תשווה בין MG4 ל-NIRO")
    reply = say(text)
    assert view()["today_copay"] == saved
    if saved == "400":
        assert "עדכנתי: השתתפות 400 ₪ במקום 300 ₪." in reply
    else:                                                      # 999 was never saved: nothing guessed, nothing saved
        assert "עדכנתי" not in reply


def test_a_my_data_edit_is_confirmed_once_in_the_conversation_and_kept_after_a_reload(engine):
    say, view = compared(engine)
    say("תשווה בין MG4 ל-NIRO")
    count = len(view()["messages"])
    reply = say({"fields": {"base_salary": 21000}})
    v = view()
    new = v["messages"][count:]
    assert [m["role"] for m in new] == ["user", "assistant"]   # one confirmation, no duplicate
    assert reply.count("עדכנתי") == 1 and "שכר בסיס 21,000 ₪ במקום 18,500 ₪" in reply
    assert v["profile"]["base"] == "21000" and not v["results_stale"]
    again = view()                                             # a reload: the same data, the same results
    assert again["profile"]["base"] == "21000" and nets(again) == nets(v)


def test_the_map_counts_cars_and_names_the_option_that_is_not_a_car(engine):
    say, view = compared(engine)
    say("רגע, אני בקבוצה ג")
    say("מה הכי משתלם?")
    asked = say("תראה לי את כל האפשרויות")
    counts = view()["option_map"]["counts"]
    cars = counts["cars"]
    assert counts["eligible"] == cars + len(counts["others"])
    assert [o["label"] for o in counts["others"]] == ["ויתור על רכב"]
    assert f"מיפיתי את כל {cars} הרכבים" in asked and "ויתור על רכב" in asked
    assert f"{counts['eligible']} החלופות" not in asked


@pytest.mark.parametrize(("text", "drive"), [
    ("ומה הם החשמליים?", Drive.ELECTRIC), ("ואיזה מהם היברידיים", Drive.HYBRID), ("יש EV?", Drive.ELECTRIC),
    ("every event counts", None), ("מה עם הבנזין", Drive.PETROL), ("plug-in", Drive.PLUG_IN),
])
def test_a_drive_word_with_a_hebrew_prefix_is_read_and_english_needs_the_whole_word(text, drive):
    assert match_drive(text) == drive


Q2_BUT_STUDY_FUND = {"fields": {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 6,
                                "credit_points": 2.25, "children_under_23": 2}}


def test_with_the_model_a_replaced_group_is_said_once_with_the_value_it_replaced(engine):
    fake = FakeLLM()
    say, view = start(engine, fake)
    for body in ("קבוצה ב", "שאלה אחת כל פעם", Q2_BUT_STUDY_FUND):
        say(body)
    # live Gemini 15/11: "רשמתי: קבוצת זכאות ג." (the change and the old value unsaid)
    fake.turn(call("save_profile", eligibility_group="ג"), reply="רשמתי: קבוצת זכאות ג.\nמהו אחוז ההפרשה לקרן השתלמות?")
    reply = say("סליחה שכחתי לציין, התבלבלתי בהתחלה ובעצם הקבוצה שלי היא ג")
    assert view()["profile"]["group"] == "ג"
    assert reply.startswith("עדכנתי: קבוצה ג במקום ב.")
    assert "רשמתי" not in reply and "קרן השתלמות" in reply     # back to the question that was open


def test_with_the_model_a_replaced_salary_is_said_once(engine):
    fake = FakeLLM()
    say, view = start(engine, fake)
    say("קבוצה ב")
    say("שכר בסיס 18,500")
    fake.turn(call("save_profile", base_salary="19000"), reply="רשמתי: שכר בסיס 19,000 ₪.\nיש לכם עמלות?")
    reply = say("סליחה, התבלבלתי בספרות, השכר הוא 19,000")
    assert view()["profile"]["base"] == "19000"
    assert reply.count("19,000") == 1 and "עדכנתי: שכר בסיס 19,000 ₪ במקום 18,500 ₪." in reply


def test_a_correction_before_the_same_question_again_is_not_said_twice(engine):
    # "בעצם 19,000" with several fields on screen: which one is the model's reading (without it, it is asked); the reply
    # is the template's, which put "רשמתי: שכר בסיס 19,000 ₪. עדיין חסרים לי…" under the update line (live 15/11)
    fake = FakeLLM()
    say, _ = start(engine, fake)
    say("קבוצה ב")
    say("שכר בסיס 18,500")
    fake.turn(call("save_profile", base_salary="19000"))
    reply = say("בעצם 19,000")
    assert "עדכנתי: שכר בסיס 19,000 ₪ במקום 18,500 ₪." in reply
    assert "רשמתי: שכר בסיס" not in reply                       # the saved line once, then only what is missing


def test_with_the_model_a_sweeping_value_sent_one_field_per_call_is_not_spread(engine):
    # live Gemini 15/11: "הכל 5" as two calls, one field each; "פנסיה 5%" was saved from a blanket answer
    fake = FakeLLM()
    say, view = start(engine, fake)
    say("קבוצה ב")
    say("שכר בסיס 15,000")
    fake.turn(rounds=[[call("save_profile", pension_percent="5")], [call("save_profile", study_fund_percent="5")]])
    say("הכל 5")
    p = view()["profile"]
    assert [p["commissions"], p["bonus"], p["pension_percent"], p["study_fund_percent"], p["credit_points"],
            p["children"]] == [None] * 6


def test_with_the_model_a_sweeping_value_is_not_spread_when_one_field_is_on_screen(engine):
    # reproduced live 15/11: the model's previous reply asked one field, then "הכל 5" came back as one call with four
    fake = FakeLLM()
    say, view = start(engine, fake)
    say("קבוצה ב")
    say("שאלה אחת כל פעם")
    say("15,000")
    fake.turn(call("save_profile", commissions="5", bonus="5", pension_percent="5", study_fund_percent="5"))
    say("הכל 5")
    p = view()["profile"]
    assert [p["commissions"], p["bonus"], p["pension_percent"], p["study_fund_percent"]] == [None] * 4


def test_with_the_model_same_as_the_pension_is_grounded_only_in_the_saved_pension(engine):
    def flow(pension):
        fake = FakeLLM()
        say, view = start(engine, fake)
        say("קבוצה ב")
        say("שאלה אחת כל פעם")
        say({"fields": {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": pension,
                        "credit_points": 2.25, "children_under_23": 2}})
        fake.turn(call("save_profile", study_fund_percent="2"), reply="רשמתי.")
        say("כמו בפנסיה")
        return view()["profile"]["study_fund_percent"]

    assert flow(2) == "2"            # the model's reading, the number from the saved pension
    assert flow(3) is None           # 2 is neither said nor saved anywhere: refused, never invented
