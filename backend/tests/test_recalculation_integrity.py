"""What the review of 4/11 found around a recalculation: every number was right, what was said about it was not.

P0-1: "עדכנתי: קבוצה א" while the group stayed ג. P0-2: "3 החלופות עם הנטו הגבוה ביותר" and "כל 12 הרכבים" after a change that
made them false. F-03: a chosen car that lost its eligibility just disappeared. F-04: the previous car's copay, said to be
the employee's. Synthetic data only.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import LLMChain

from .conftest import build_engine, make_client

Q2 = {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
      "credit_points": 2.25, "children_under_23": 2}


def start(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()


def at_cars(client, q2=Q2, group="קבוצה ג"):
    cid, send = start(client)
    send({"text": group})
    send({"fields": q2})
    send({"text": "קורולה 2026"})
    send({"choice": "catalog:CAR-07", "field": "copay"})
    return cid, send


def view(client, cid):
    return client.get(f"/api/conversations/{cid}").json()


def rows(v):
    return [(r["option_id"], r["result"]["delta_display"]) for r in v["results"]]


def with_model(client, fake):
    client.app.state.ctx.service.agent.llm = LLMChain([fake])


# ---------- P0-1: a change is reported only after it happened ----------

@pytest.mark.parametrize("model", [False, True])
def test_a_group_correction_after_results_really_changes_the_group(engine, model):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-09", "CAR-01"], "field": "options"})
    if model:
        with_model(client, FakeLLM())       # the same in both modes: code decides, the model is not asked
    turn = send({"text": "טעיתי, אני בקבוצה א"})
    v = view(client, cid)
    assert v["profile"]["group"] == "א"
    assert "עדכנתי: קבוצה א במקום ג" in turn["reply"]
    assert [r["option_id"] for r in v["results"]] == ["CAR-01"]


def test_a_group_correction_while_the_cars_are_asked_is_saved_and_the_question_follows_it(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    turn = send({"text": "טעיתי, אני בקבוצה א"})
    assert view(client, cid)["profile"]["group"] == "א"
    assert "עדכנתי: קבוצה א במקום ג" in turn["reply"] and "בקבוצה א ומטה" in turn["reply"]


def test_a_group_the_role_decides_is_not_changed_and_the_reply_says_why_and_what_can_be_done(engine):
    client = make_client(engine)
    cid, send = at_cars(client, group="אני מנהלת מוצר")
    send({"choices": ["CAR-09"], "field": "options"})
    before = view(client, cid)
    turn = send({"text": "טעיתי, אני בקבוצה א"})
    after = view(client, cid)
    assert after["profile"]["group"] == before["profile"]["group"] == "ג"
    assert rows(after) == rows(before) and after["pending_rep"] is None
    assert "עדכנתי" not in turn["reply"] and "לא שיניתי" in turn["reply"] and "מנהל/ת מוצר" in turn["reply"]
    assert any(f["field"] == "ask_rep" for f in turn["expects"]["fields"])     # the representative, only if asked


def test_a_notice_is_dropped_when_the_save_it_announces_did_not_happen(engine):
    """The same value said again saves nothing new: no recalculation, and nothing reported as changed."""
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"text": "הכי משתלם"})
    before = view(client, cid)
    turn = send({"fields": {"base_salary": 18000}})
    after = view(client, cid)
    assert "עדכנתי" not in turn["reply"] and "הערכים זהים" in turn["reply"]
    assert [r["run_id"] for r in after["results"]] == [r["run_id"] for r in before["results"]]


@pytest.mark.parametrize("text, field, value, words", [
    ("טעיתי, השכר שלי 20,000", "base", "20000", "שכר בסיס 20,000 ₪ במקום 18,000 ₪"),
    ("הפנסיה שלי 5%", "pension_percent", "5", "פנסיה 5% במקום 6"),
    ("טעיתי, 3 נקודות זיכוי", "credit_points", "3", "3 נקודות זיכוי במקום 2.25"),
])
def test_a_value_corrected_in_words_says_what_it_replaced(engine, text, field, value, words):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-05"], "field": "options"})
    turn = send({"text": text})
    assert str(view(client, cid)["profile"][field]) == value
    assert f"עדכנתי: {words}" in turn["reply"]


# ---------- P0-2: every claim about the ranking is true after a change ----------

def test_the_review_case_9000_to_45000_shows_the_real_three_highest(engine):
    client = make_client(engine)
    cid, send = at_cars(client, {**Q2, "base_salary": 9000})
    send({"text": "הכי משתלם"})
    assert "CAR-08" in [r[0] for r in rows(view(client, cid))]     # KONA EV is third at 9,000
    turn = send({"fields": {"base_salary": 45000}})
    kept = rows(view(client, cid))

    fresh = make_client(build_engine())
    cid2, send2 = at_cars(fresh, {**Q2, "base_salary": 45000})
    send2({"text": "הכי משתלם"})
    assert kept == rows(view(fresh, cid2))
    assert "CAR-01" in [r[0] for r in kept] and "CAR-08" not in [r[0] for r in kept]   # PICANTO +946, not KONA EV +936
    assert "3 החלופות עם הנטו המשוער הגבוה ביותר" in turn["reply"] or "הסדר" in turn["reply"]


@pytest.mark.parametrize("q2_children, change, count", [(2, {"children_under_23": 5}, 14)])
def test_the_number_of_cars_checked_follows_the_change(engine, q2_children, change, count):
    client = make_client(engine)
    cid, send = at_cars(client, {**Q2, "children_under_23": q2_children})
    send({"text": "הכי משתלם"})
    turn = send({"fields": change})
    assert f"כל {count} הרכבים" in turn["reply"]
    assert client.app.state.ctx is not None


def test_a_group_going_down_ranks_again_on_the_cars_now_eligible(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"text": "הכי משתלם"})
    turn = send({"text": "טעיתי, אני בקבוצה א"})
    v = view(client, cid)
    assert v["profile"]["group"] == "א" and "כל 4 הרכבים" in turn["reply"]
    assert [r[0] for r in rows(v)] == ["CAR-03", "CAR-01", "CAR-02"]


def test_a_selection_the_employee_made_is_not_replaced_by_a_ranking(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-05", "CAR-12"], "field": "options"})
    send({"fields": {"base_salary": 45000}})
    assert [r[0] for r in rows(view(client, cid))] == ["CAR-05", "CAR-12"]


def test_results_and_data_versions_move_only_with_a_real_change(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-05"], "field": "options"})
    state = lambda: client.app.state.ctx.service.view(cid, client.cookies.get("sca_session")).state    # noqa: E731
    first = state()
    send({"text": "תודה"})
    assert state().data_version == first.data_version and state().results_version == first.results_version
    send({"fields": {"base_salary": 20000}})
    assert state().data_version == first.data_version + 1 and state().results_version == first.results_version + 1


# ---------- F-03: nothing leaves the comparison silently ----------

def test_a_chosen_car_that_lost_its_eligibility_is_named_with_its_rule(engine):
    client = make_client(engine)
    cid, send = at_cars(client, {**Q2, "children_under_23": 4})
    send({"choices": ["CAR-19", "CAR-01"], "field": "options"})
    turn = send({"fields": {"children_under_23": 2}})
    v = view(client, cid)
    assert [r[0] for r in rows(v)] == ["CAR-01"] and v["selected"] == ["CAR-01"]
    assert "CARNIVAL (2025) יצא מההשוואה" in turn["reply"] and "מסלול משפחתי" in turn["reply"]


def test_when_the_only_car_leaves_the_results_are_cleared_and_the_cars_are_asked(engine):
    client = make_client(engine)
    cid, send = at_cars(client, {**Q2, "children_under_23": 4})
    send({"choices": ["CAR-19"], "field": "options"})
    turn = send({"fields": {"children_under_23": 2}})
    v = view(client, cid)
    assert v["results"] == [] and v["selected"] == [] and v["stage"] == "CHOOSE_OPTIONS"
    assert "CARNIVAL (2025) יצא מההשוואה" in turn["reply"] and turn["expects"]["field"] == "options"


def test_a_car_that_became_todays_car_leaves_the_comparison_with_the_reason(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-09", "CAR-01"], "field": "options"})
    with_model(client, FakeLLM().turn(call("match_current_car", model="COROLLA CROSS", reset=True)))
    turn = send({"text": "טעיתי, הרכב שלי הוא בעצם קורולה קרוס של החברה"})
    v = view(client, cid)
    assert v["today_car"].startswith("COROLLA CROSS") and [r[0] for r in rows(v)] == ["CAR-01"]
    assert "COROLLA CROSS (2025) יצא מההשוואה. זה הרכב שלכם היום" in turn["reply"]


# ---------- F-04: the previous car's copay is not the new car's confirmed amount ----------

def test_a_copay_carried_to_another_car_is_marked_and_said(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-01"], "field": "options"})
    with_model(client, FakeLLM().turn(call("match_current_car", model="COROLLA CROSS", reset=True)))
    turn = send({"text": "טעיתי, הרכב שלי הוא בעצם קורולה קרוס של החברה"})
    v = view(client, cid)
    assert v["today_copay"] == "250" and v["today_copay_source"] == "carried"
    assert "מהרכב הקודם" in turn["reply"] and "שמסרתם" not in turn["reply"]


def test_a_new_amount_clears_the_carried_mark(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-01"], "field": "options"})
    with_model(client, FakeLLM().turn(call("match_current_car", model="COROLLA CROSS", reset=True)))
    send({"text": "טעיתי, הרכב שלי הוא בעצם קורולה קרוס של החברה"})
    client.app.state.ctx.service.agent.llm = LLMChain()
    send({"text": "ההשתתפות שלי 300"})
    v = view(client, cid)
    assert v["today_copay"] == "300" and v["today_copay_source"] is None
