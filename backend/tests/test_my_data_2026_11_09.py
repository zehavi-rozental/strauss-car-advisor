""""הנתונים שלי" in the hotfix (9/11, ported from feature/integration-to-main): every value with its source and status
(facts_view), one value edited through {fields} with the screen's data version, and everything that depends on it
recalculated in the same turn. A value whose basis changed is said and marked, and old results are never current.
Synthetic data only; the figures are the engine's.
"""
from __future__ import annotations

from decimal import Decimal

from .conftest import make_client

PAY = {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
       "credit_points": 2.25, "children_under_23": 2}


def start(engine, source=None):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def view():
        return client.get(f"/api/conversations/{cid}").json()

    def edit(fields, version=None):
        v = view()
        return client.post(f"/api/conversations/{cid}/messages",
                           json={"fields": fields, "base_version": v["data_version"] if version is None else version})
    return say, view, edit, source


def to_results(engine, source=None):
    say, view, edit, _ = start(engine)
    say("קבוצה ג")
    say({"fields": PAY, **({"source": source} if source else {})})
    say("קורולה 2026")
    say({"choice": "catalog:CAR-07", "field": "copay"})
    say({"choice": "rank", "field": "options"})
    assert view()["stage"] == "PRESENTED"
    return say, view, edit


def item(view, key):
    return next(i for i in view["facts"]["items"] if i["key"] == key)


def nets(view):
    return (view["results"][0]["result"]["today"]["net_display"],
            [r["result"]["alternative"]["net_display"] for r in view["results"]])


def test_the_view_says_every_value_its_source_and_what_is_missing(engine):
    say, view, edit, _ = start(engine)
    say("קבוצה ג")
    say({"fields": {"base_salary": 18000}})
    v = view()
    assert v["facts"]["version"] == v["data_version"] and v["data_version"] >= 1
    assert (item(v, "base_salary")["display"], item(v, "base_salary")["source_label"]) == ("18,000 ₪", "הקלדתם")
    assert item(v, "pension_percent")["status"] == "missing" and "פנסיה" in v["facts"]["missing"]
    assert item(v, "group")["edit"] == "group" and item(v, "current_car")["status"] == "missing"


def test_a_salary_edit_recalculates_today_and_every_car_and_says_what_changed(engine):
    say, view, edit = to_results(engine)
    before = view()
    r = edit({"base_salary": 22000})
    assert r.status_code == 200 and "עדכנתי: שכר בסיס 22,000 ₪ במקום 18,000 ₪" in r.json()["reply"]
    after = view()
    assert nets(after)[0] != nets(before)[0] and nets(after)[1] != nets(before)[1]
    assert not after["results_stale"] and after["data_version"] > before["data_version"]
    assert item(after, "base_salary")["source_label"] == "הקלדתם"


def test_contributions_edit_recalculates(engine):
    say, view, edit = to_results(engine)
    before = nets(view())
    r = edit({"pension_percent": 7, "study_fund_percent": 0})
    assert r.status_code == 200 and "פנסיה 7% במקום 6%" in r.json()["reply"]
    after = view()
    assert nets(after) != before
    assert Decimal(after["results"][0]["result"]["today"]["pension"]) == Decimal(
        after["results"][0]["result"]["today"]["cash_gross"]) * Decimal("0.07")


def test_a_group_edit_is_said_and_the_cars_follow_the_new_group(engine):
    say, view, edit = to_results(engine)
    r = edit({"eligibility_group": "ב"})
    assert r.status_code == 200 and "עדכנתי: קבוצה ב במקום ג" in r.json()["reply"]
    v = view()
    assert v["profile"]["group"] == "ב" and not v["results_stale"]


def test_a_copay_edit_moves_todays_net_only(engine):
    say, view, edit = to_results(engine)
    today_before, cars_before = nets(view())
    r = edit({"current_copay": 300})
    assert r.status_code == 200 and "השתתפות נוכחית 300 ₪ במקום 250 ₪" in r.json()["reply"]
    today_after, cars_after = nets(view())
    assert Decimal(today_before) - Decimal(today_after) == 50 and cars_after == cars_before


def test_another_car_today_is_said_and_its_copay_marked_as_carried(engine):
    say, view, edit = to_results(engine)
    r = edit({"current_car": "CAR-05"})
    reply = r.json()["reply"]
    assert r.status_code == 200 and "NIRO" in reply and "נשארה מהרכב הקודם" in reply
    v = view()
    copay = item(v, "current_copay")
    assert copay["status"] == "assumption" and "נלקחה מהרכב הקודם" in copay["note"]
    assert not v["results_stale"]
    r = edit({"current_copay": 280})                     # the employee's own amount clears the mark
    assert item(view(), "current_copay")["status"] == "known"


def test_no_car_today_makes_the_results_out_of_date_until_the_allowance_is_known(engine):
    say, view, edit = to_results(engine)
    edit({"current_car": "no_car"})
    v = view()
    assert v["results_stale"] and v["stage"] != "PRESENTED"     # never shown as current
    assert item(v, "current_maintenance")["status"] == "missing"
    edit({"current_maintenance": 1500})
    v = view()
    assert not v["results_stale"] and v["stage"] == "PRESENTED"


def test_an_edit_from_an_older_screen_saves_nothing(engine):
    say, view, edit = to_results(engine)
    before = view()
    r = edit({"base_salary": 30000}, version=before["data_version"] - 1)
    assert r.status_code == 409 and "רעננו" in r.json()["detail"]
    assert view()["profile"]["base"] == before["profile"]["base"]


def test_invalid_edits_are_refused_with_their_reason(engine):
    say, view, edit = to_results(engine)
    r = edit({"current_maintenance": 500})                # only without a car
    assert r.status_code == 422 and "current_maintenance" in r.json()["errors"]
    r = edit({"pension_percent": 34})
    assert r.status_code == 422 and "pension_percent" in r.json()["errors"]
    r = edit({"current_car": "NOPE"})
    assert r.status_code == 422 and "current_car" in r.json()["errors"]


def test_after_a_payslip_the_source_is_the_payslip_and_an_edit_replaces_it(engine):
    say, view, edit = to_results(engine, source="payslip")
    assert item(view(), "base_salary")["source_label"] == "מהתלוש, באישורכם"
    edit({"base_salary": 21000})
    v = view()
    assert item(v, "base_salary")["source_label"] == "הקלדתם" and v["profile"]["base"] in ("21000", 21000)


def test_a_value_held_for_confirmation_is_never_shown_as_confirmed(engine):
    say, view, edit, _ = start(engine)
    say("קבוצה ג")
    say({"fields": {"base_salary": 18000}})
    say("אין הפרשות לפנסיה ולקרן השתלמות")         # held: "לרשום פנסיה 0% וקרן השתלמות 0%?"
    v = view()
    assert item(v, "pension_percent")["status"] in ("missing", "held")
    assert item(v, "pension_percent")["value"] is None


def test_an_edit_in_the_middle_keeps_the_conversation_on_its_question(engine):
    say, view, edit, _ = start(engine)
    say("קבוצה ג")
    asked = say("שכר בסיס 18000, אין עמלות ואין בונוס, פנסיה 6%")
    assert "קרן השתלמות" in asked["reply"]
    r = edit({"study_fund_percent": 2.5}).json()
    assert "רשמתי: קרן השתלמות 2.5%" in r["reply"] and "נקודות זיכוי" in r["reply"]
    assert "קרן השתלמות?" not in r["reply"]


def test_a_chat_group_correction_during_q2_returns_to_the_still_missing_field(engine):
    say, view, _, _ = start(engine)
    say("קבוצה ב")
    say("שאלה אחת כל פעם")
    say({"fields": {
        "base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 6,
        "credit_points": 2.25, "children_under_23": 2,
    }})

    reply = say("רגע, אני בקבוצה ג")["reply"]

    assert "עדכנתי: קבוצה ג" in reply
    assert "קרן השתלמות" in reply and "רכב היום" not in reply
    assert view()["profile"]["group"] == "ג"
