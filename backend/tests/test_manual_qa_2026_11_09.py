"""Manual QA 9/11, the comparison flow with a value the employee does not know. Reproduced on production's code (main,
the same screen and settings, with Gemini) before any fix; it began with 0e83851 (22/10), where an unknown car value
stopped holding the car stage but the ranking was not told.

- "3 המשתלמים ביותר" with maintenance "לא יודעת" (no car) or an unknown copay: the ranking ran without the value,
  failed, and the request was dropped; the button alternated between "עדיין חסר לי: הרכבים להשוואה" and the cars
  question. Now the request is kept, the value is asked with why and where, and the ranking follows the answer.
- Three cars chosen with that value unknown: the first maintenance question came back word for word ("אפשר גם לבדוק
  בהמשך" included). Now it says what is missing and where it is on the payslip.
- "אין הפרשות לפנסיה ולקרן השתלמות" while the car was asked: read as a car model, and lost.
- "לא יודעת כמה אני משלמת" while today's copay was asked: "not understood".
Synthetic data only.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.llm.port import LLMChain

from .conftest import make_client

PROFILE = {"base_salary": 18500, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
           "credit_points": 2.75, "children_under_23": 2}
RANK = {"choice": "rank", "field": "options"}
LOOP = "עדיין חסר לי: הרכבים להשוואה"


def start(engine, llm=None):
    client = make_client(engine, llm=LLMChain([llm])) if llm is not None else make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def view():
        return client.get(f"/api/conversations/{cid}").json()
    return say, view


def no_car_maintenance_unknown(say):
    say("קבוצה ב")
    say({"fields": PROFILE})
    say("אין לי רכב צמוד היום")
    turn = say("אני לא יודעת")                       # the maintenance allowance: put off to the end
    assert turn["stage"] == "CHOOSE_OPTIONS"
    return turn


def offers_rank(turn) -> bool:
    return any(f["field"] == "options" and any(q.get("id") == "rank" for q in (f.get("quick_replies") or []))
               for f in turn["expects"]["fields"])


def test_the_rank_button_keeps_the_request_and_asks_the_missing_value_with_where_it_is(engine):
    say, view = start(engine)
    asked = no_car_maintenance_unknown(say)
    assert offers_rank(asked)
    turn = say(RANK)
    # 19/11: the allowance is an unknown value now, completed in the end-of-flow step (as today's unknown copay)
    assert LOOP not in turn["reply"] and turn["stage"] == "COLLECT_PROFILE"
    assert "ביקשתם את החלופות הכי משתלמות" in turn["reply"]
    assert "חסר לי עוד סכום אחזקת הרכב" in turn["reply"] and "שורת אחזקת רכב" in turn["reply"]
    assert "לבדוק בהמשך" not in turn["reply"]          # put off once already: the end is now
    assert not offers_rank(turn)                        # the request is recorded: no button to press again
    done = say("אין")
    assert done["stage"] == "PRESENTED" and len(view()["results"]) == 3
    assert "בדקתי את כל" in done["reply"]


def test_three_cars_chosen_with_the_value_unknown_ask_it_once_with_why_then_calculate(engine):
    say, view = start(engine)
    asked_first = no_car_maintenance_unknown(say)
    turn = say({"choices": ["CAR-02", "CAR-03", "CAR-05"], "field": "options"})
    assert "בחרתם להשוות" in turn["reply"] and "חסר לי עוד סכום אחזקת הרכב" in turn["reply"]
    assert "אפשר גם לבדוק בהמשך" not in turn["reply"]
    assert turn["reply"] != asked_first["reply"]
    done = say("1,200")
    assert done["stage"] == "PRESENTED"
    assert sorted(r["option_id"] for r in view()["results"]) == ["CAR-02", "CAR-03", "CAR-05"]


def test_the_rank_button_with_an_unknown_copay_waits_for_it_and_never_loops(engine):
    say, view = start(engine)
    say("קבוצה ג")
    say({"fields": PROFILE})
    say("קורולה 2026")
    say("לא יודעת")
    turn = say(RANK)
    assert LOOP not in turn["reply"] and turn["stage"] != "CHOOSE_OPTIONS" and not offers_rank(turn)
    assert view()["results"] == []                   # nothing calculated on a guessed copay
    say({"choice": "catalog:CAR-07", "field": "copay"})
    assert len(view()["results"]) == 3


def test_with_complete_data_the_rank_button_is_unchanged(engine):
    say, view = start(engine)
    say("קבוצה ג")
    say({"fields": PROFILE})
    say("קורולה 2026")
    say({"choice": "catalog:CAR-07", "field": "copay"})
    turn = say(RANK)
    assert turn["stage"] == "PRESENTED" and len(view()["results"]) == 3


@pytest.mark.parametrize("text", ["אין הפרשות לפנסיה ולקרן השתלמות", "אין פנסיה ואין קרן השתלמות",
                                  "בלי פנסיה ובלי קרן השתלמות"])
def test_both_contributions_none_while_the_car_is_asked_are_confirmed_and_saved(engine, text):
    say, view = start(engine)
    say("קבוצה ב")
    say({"fields": {k: v for k, v in PROFILE.items() if k not in ("pension_percent", "study_fund_percent")}})
    turn = say(text)
    assert "לרשום פנסיה 0% וקרן השתלמות 0%" in turn["reply"] and "לא זיהיתי את דגם הרכב" not in turn["reply"]
    say("לאשר")
    p = view()["profile"]
    assert (Decimal(p["pension_percent"]), Decimal(p["study_fund_percent"])) == (0, 0)


@pytest.mark.parametrize("text", ["יש לי פנסיה ואין קרן השתלמות", "אין הפרשה לפנסיה, קרן השתלמות 2.5%",
                                  "לא יודעת לגבי פנסיה וקרן השתלמות"])
def test_one_contribution_or_not_knowing_is_never_both_zero(engine, text):
    say, view = start(engine)
    say("קבוצה ב")
    say({"fields": {k: v for k, v in PROFILE.items() if k not in ("pension_percent", "study_fund_percent")}})
    turn = say(text)
    assert "לרשום פנסיה 0% וקרן השתלמות 0%" not in turn["reply"]


@pytest.mark.parametrize("text", ["לא יודעת כמה אני משלמת", "אני לא זוכרת כמה ההשתתפות"])
def test_not_knowing_todays_copay_in_words_is_recorded_not_misread(engine, text):
    say, view = start(engine)
    say("קבוצה ג")
    say({"fields": PROFILE})
    say("קורולה 2026")
    turn = say(text)                 # 16/11: first the ways to go on, nothing saved; the second time it is recorded
    assert "לברר מול נציג" in turn["reply"] and "לא בטוח שהבנתי" not in turn["reply"]
    assert view()["today_copay"] is None and not view()["current_car"]["copay_unknown"]
    turn = say(text)
    assert "רשמתי שזה לא ידוע" in turn["reply"] and "לא בטוח שהבנתי" not in turn["reply"]
    assert view()["today_copay"] is None


def test_with_the_model_a_typed_ranking_request_is_kept_while_a_value_is_unknown(engine):
    say, view = start(engine, FakeLLM())
    no_car_maintenance_unknown(say)
    turn = say("הכי משתלם")
    assert turn["stage"] == "COLLECT_PROFILE" and LOOP not in turn["reply"] and view()["results"] == []
    done = say("1,200")
    assert done["stage"] == "PRESENTED" and len(view()["results"]) == 3
