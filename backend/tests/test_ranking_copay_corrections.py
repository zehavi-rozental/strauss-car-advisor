"""The backend behind "הכי משתלם", the catalog copay pending the representative, and corrections.

Checked against the independent oracle (tests/reference_oracle.py, no engine code), through the API:
- the ranking covers every eligible option (own group and lower, the family track at 4+ children, opting out
  when the occupation allows it), and its top three equal the oracle's;
- a new car is calculated with the catalog copay, marked pending, and one CONFIRM question is opened; the
  representative's answer replaces the amount (a copay is a net deduction: 50 more is exactly 50 less);
- a corrected salary reaches the calculation, and every shown breakdown adds up to its net.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from . import reference_oracle as oracle
from .conftest import make_client

NOA = "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"
TOKEN = "rep-test-token"
D = Decimal


@pytest.fixture
def api(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]

    class Api:
        def say(self, text):
            r = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
            assert r.status_code == 200, r.text
            return r.json()

        def fields(self, values):
            r = client.post(f"/api/conversations/{cid}/messages", json={"fields": values})
            assert r.status_code == 200, r.text
            return r.json()

        def view(self):
            return client.get(f"/api/conversations/{cid}").json()

        def open_questions(self):
            # a fresh database per test: every open question is this conversation's (the rep API never says)
            return client.get("/api/rep/questions?status=OPEN", headers={"X-Rep-Token": TOKEN}).json()

        def answer(self, qid, value):
            return client.post(f"/api/rep/questions/{qid}/answer", json={"value": value},
                               headers={"X-Rep-Token": TOKEN})

    return Api()


def _employee(base=D(18500)) -> oracle.Employee:
    return oracle.Employee(id="noa", name="", occupation="מנהל/ת מוצר", group="ג", base=base, commissions=D(0),
                           bonus=D(0), pension_pct=D(6), study_pct=D("2.5"), points=D("2.75"), children=2,
                           current_car="COROLLA CROSS", current_copay=D(300))


def _oracle_deltas(emp: oracle.Employee) -> dict[str, Decimal]:
    """Every option Noa may choose, computed by the oracle: id -> rounded monthly delta."""
    rules, data = oracle.load_rules(), oracle.load_data()
    order = list(rules.budgets)                        # א, ב, ג, ד
    today = oracle.evaluate(emp, oracle.Situation("today", car=data.cars["COROLLA CROSS"], copay=emp.current_copay), rules)
    out: dict[str, Decimal] = {}
    for car in data.cars.values():
        if car.name == "COROLLA CROSS":
            continue
        family = car.code in rules.family_cars
        if (family and emp.children >= 4) or (not family and order.index(car.group) <= order.index(emp.group)):
            alt = oracle.evaluate(emp, oracle.Situation(car.name, car=car, copay=car.default_copay), rules)
            out[car.code] = oracle.shekel(alt.net) - oracle.shekel(today.net)
    if data.opt_out_allowed[emp.occupation]:
        alt = oracle.evaluate(emp, oracle.Situation("opt out", maintenance=data.occupations[emp.occupation]), rules)
        out["OPT_OUT"] = oracle.shekel(alt.net) - oracle.shekel(today.net)
    return out


def _results(view) -> list[tuple[str, Decimal]]:
    return [(r["option_id"], D(r["result"]["delta_display"])) for r in view["results"]]


def test_best_value_ranks_every_eligible_option_and_matches_the_oracle(api):
    for text in (NOA, "יש לי קורולה קרוס", "300"):
        api.say(text)
    reply = api.say("מה הכי משתלם לי?")["reply"]
    expected = _oracle_deltas(_employee())
    assert len(expected) == 13                                     # 12 cars in groups א-ג (not today's) + opting out
    assert f"בדקתי את כל {len(expected)} הרכבים" in reply
    top = sorted(expected.items(), key=lambda kv: kv[1], reverse=True)[:3]
    assert _results(api.view()) == top
    # each ranked delta equals the same option chosen on its own
    assert all(expected[oid] == delta for oid, delta in _results(api.view()))


def test_a_new_car_uses_the_catalog_copay_pending_one_confirm_question_and_the_answer_replaces_it(api):
    for text in (NOA, "יש לי קורולה קרוס", "300", "קורולה"):
        api.say(text)
    [row] = api.view()["results"]
    assert row["option_id"] == "CAR-07"
    result = row["result"]
    assert result["copay_pending"] is True
    assert D(result["alternative"]["copay"]) == D(250)            # COROLLA's catalog default copay
    expected = _oracle_deltas(_employee())["CAR-07"]
    assert D(result["delta_display"]) == expected

    [q] = api.open_questions()
    assert (q["missing_field"], q["mode"], D(q["suggested_value"])) == ("NEW_CAR_COPAY", "CONFIRM", D(250))
    assert set(q["context"]) == {"group", "car_label", "catalog_code", "suggested_amount"}   # no salary
    api.say("קורולה")                                              # choosing it again opens no second question
    assert len(api.open_questions()) == 1

    assert api.answer(q["id"], "300").status_code == 200
    [row] = api.view()["results"]
    assert row["result"]["copay_pending"] is False
    assert D(row["result"]["alternative"]["copay"]) == D(300)
    assert D(row["result"]["delta_display"]) == expected - 50       # a net deduction: exactly 50 less
    assert api.open_questions() == []


def test_the_representative_confirming_the_catalog_amount_keeps_the_numbers_and_clears_pending(api):
    for text in (NOA, "יש לי קורולה קרוס", "300", "קורולה"):
        api.say(text)
    before = api.view()["results"][0]["result"]["delta_display"]
    [q] = api.open_questions()
    api.answer(q["id"], "250")
    after = api.view()["results"][0]["result"]
    assert (after["delta_display"], after["copay_pending"]) == (before, False)


def test_a_corrected_salary_from_the_form_reaches_the_calculation(api):
    for text in (NOA, "יש לי קורולה קרוס", "300", "אטו 3"):
        api.say(text)
    assert D(api.view()["results"][0]["result"]["delta_display"]) == D(661)
    api.fields({"base_salary": 16000})
    view = api.view()
    assert view["profile"]["base"] == "16000"
    assert D(view["results"][0]["result"]["delta_display"]) == _oracle_deltas(_employee(D(16000)))["CAR-10"]


@pytest.mark.xfail(strict=True, reason=(
    "OPEN BUG (conversation module, Kiro): after a result, in template mode, 'טעיתי, השכר 16,000' is taken as the "
    "'you made a mistake' audit cue and nothing is saved, and 'שכר בסיס 16,000' is not read at all. The form "
    "correction works (test above). strict: this fails as soon as the bug is fixed, so the mark gets removed."))
def test_a_salary_correction_typed_after_the_result_reaches_the_calculation(api):
    for text in (NOA, "יש לי קורולה קרוס", "300", "אטו 3"):
        api.say(text)
    api.say("סליחה, טעיתי, שכר הבסיס שלי 16,000")
    view = api.view()
    assert view["profile"]["base"] == "16000"
    assert view["results"], "the result was recalculated, not dropped"
    assert D(view["results"][0]["result"]["delta_display"]) == _oracle_deltas(_employee(D(16000)))["CAR-10"]


def test_every_shown_breakdown_adds_up_to_its_net(api):
    for text in (NOA, "יש לי קורולה קרוס", "300", "מה הכי משתלם לי?"):
        api.say(text)
    for row in api.view()["results"]:
        for side in ("today", "alternative"):
            b = {k: D(str(v)) for k, v in row["result"][side].items() if k not in ("label",)}
            deductions = (b["income_tax"] + b["national_insurance"] + b["health"] + b["pension"] + b["study_fund"]
                          + b["copay"] + b["family_share"])
            assert b["net"] == b["cash_gross"] - deductions, (row["option_id"], side)
            assert b["net_display"] == oracle.shekel(b["net"])
        r = row["result"]
        assert D(r["delta_display"]) == D(r["alternative"]["net_display"]) - D(r["today"]["net_display"])
