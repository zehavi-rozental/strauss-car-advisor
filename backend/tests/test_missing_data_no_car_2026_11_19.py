"""Missing data with no company car, the same mechanisms as with a car (19/11; template mode, no model, fake mail).

Root cause: with no company car, "לא ידוע" for the car-maintenance allowance was only a deferral (deferred_fields), so
the question came straight back after the cars were chosen — and at once when "הכי משתלם" was asked before — and a
"לא ידוע" again was "not understood". The allowance is now an unknown status like today's copay (stage.current_unknown):
not asked again at once, completed once in the end-of-flow step, never 0 by itself, no net without it. Like the copay it
is a closed question (the payslip's amount to confirm, another amount, the representative; without a payslip amount
nothing is suggested), the representative is asked only through the inquiry card (an explicit send), and the
representative's answer completes the value and recalculates. The car path is checked alongside.
"""
from __future__ import annotations

from .conftest import make_client

TOKEN = "test-rep-token"
REP = {"X-Rep-Token": TOKEN}
PROFILE = {"base_salary": 18500, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
           "credit_points": 2.75, "children_under_23": 2}
NOT_UNDERSTOOD = "לא הצלחתי להבין"
MAINTENANCE_QUESTION = "האם אתם מקבלים היום אחזקת רכב"


def start(engine, payslip_maintenance=None, payslip=False):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def view():
        return client.get(f"/api/conversations/{cid}").json()

    say("קבוצה ג")
    if payslip or payslip_maintenance is not None:
        body = {"fields": PROFILE, "source": "payslip"}
        if payslip_maintenance is not None:
            body["payslip_maintenance"] = payslip_maintenance
        say(body)
    else:
        say({"fields": PROFILE})
    return client, cid, say, view


def field(turn, name):
    return next((f for f in turn["expects"]["fields"] if f["field"] == name), None)


def choice_ids(turn, name):
    return [c["id"] for c in field(turn, name)["choices"]]


def to_maintenance(say):
    turn = say("אין לי רכב צמוד היום")
    assert MAINTENANCE_QUESTION in turn["reply"] or "אחזקת רכב" in turn["reply"], turn["reply"]
    return turn


# ---------- 1. the closed question: with and without the payslip's amount ----------

def test_without_a_payslip_nothing_is_suggested(engine):
    _, _, say, view = start(engine)
    turn = to_maintenance(say)
    assert field(turn, "maintenance")["closed"] and choice_ids(turn, "maintenance") == ["other", "none", "rep"]
    assert view()["current_car"]["maintenance"] is None and "₪" not in turn["reply"].split("\n\n")[1]


def test_a_payslip_line_is_offered_to_confirm_change_or_ask(engine):
    _, _, say, view = start(engine, payslip_maintenance=1200)
    turn = to_maintenance(say)
    assert "זיהיתי אחזקת רכב של 1,200 ₪" in turn["reply"]
    assert choice_ids(turn, "maintenance") == ["payslip", "other", "rep"]
    assert field(turn, "maintenance")["context"]["payslip_amount"] == "1200"
    assert view()["current_car"]["maintenance"] is None                    # offered, never saved by itself
    done = say({"choice": "payslip", "field": "maintenance"})
    assert done["stage"] == "CHOOSE_OPTIONS" and view()["current_car"]["maintenance"] == "1200"
    item = next(i for i in view()["facts"]["items"] if i["key"] == "current_maintenance")
    assert item["source"] == "payslip"


def test_a_payslip_without_the_line_says_so_and_invents_nothing(engine):
    _, _, say, view = start(engine, payslip=True)
    turn = to_maintenance(say)
    assert "לא זיהיתי שורה כזו" in turn["reply"] and "לא אניח סכום" in turn["reply"]
    assert choice_ids(turn, "maintenance") == ["other", "none", "rep"]


def test_payslip_maintenance_only_with_a_reviewed_payslip(engine):
    client, cid, _, _ = start(engine)
    r = client.post(f"/api/conversations/{cid}/messages", json={"fields": {"bonus": 0}, "payslip_maintenance": 900})
    assert r.status_code == 422


def test_none_and_another_amount(engine):
    _, _, say, view = start(engine)
    to_maintenance(say)
    other = say({"choice": "other", "field": "maintenance"})
    assert "כמה אחזקת רכב אתם מקבלים" in other["reply"] and view()["current_car"]["maintenance"] is None
    say("1,500")
    assert view()["current_car"]["maintenance"] == "1500"
    _, _, say2, view2 = start(engine)
    to_maintenance(say2)
    turn = say2({"choice": "none", "field": "maintenance"})
    assert view2()["current_car"]["maintenance"] == "0" and turn["stage"] == "CHOOSE_OPTIONS"


def test_a_typed_none_is_a_confirmed_zero(engine):
    _, _, say, view = start(engine)
    to_maintenance(say)
    turn = say("אין")
    assert view()["current_car"]["maintenance"] == "0" and turn["stage"] == "CHOOSE_OPTIONS"


# ---------- 2-5. "לא ידוע": a status, not asked again at once, completed at the end, never a net without it ----------

def test_unknown_is_a_status_completed_at_the_end_with_an_estimate(engine):
    _, _, say, view = start(engine)
    to_maintenance(say)
    turn = say("לא ידוע")
    car = view()["current_car"]
    assert car["maintenance"] is None and car["maintenance_unknown"] is True       # never 0
    assert turn["stage"] == "CHOOSE_OPTIONS" and MAINTENANCE_QUESTION not in turn["reply"]   # not asked again now
    assert "לברר מול נציג" in turn["reply"] and "הערכה" in turn["reply"]           # the ways on, for this field
    end = say("אטו 3")
    assert end["results"] == [] and end["stage"] == "COLLECT_PROFILE"              # no net without it
    assert "חסר לי עוד סכום אחזקת הרכב" in end["reply"]
    assert field(end, "maintenance") is not None                                   # the same closed choices
    again = say("לא ידוע")                                                         # still unknown: a status, no loop
    assert NOT_UNDERSTOOD not in again["reply"] and again["results"] == []
    assert again["reply"].split("\n\n")[0] != end["reply"].split("\n\n")[1]       # never the same text twice
    done = say("בערך 1000")
    assert done["stage"] == "PRESENTED" and done["results"]
    assert "סימנתי כהערכה" in done["reply"]
    item = next(i for i in view()["facts"]["items"] if i["key"] == "current_maintenance")
    assert item["status"] == "estimated"


def test_a_ranking_asked_before_is_kept_and_the_value_is_not_asked_again_at_once(engine):
    _, _, say, view = start(engine)
    say("הכי משתלם")
    to_maintenance(say)
    turn = say("לא ידוע")
    # recorded and nothing else is left: a status line with the ways on, not the question again
    assert turn["results"] == [] and "החישוב מחכה רק לסכום אחזקת הרכב" in turn["reply"]
    assert MAINTENANCE_QUESTION not in turn["reply"]
    done = say("אין")
    assert done["stage"] == "PRESENTED" and len(done["results"]) == 3            # the ranking ran when it could


# ---------- 7. the representative: an explicit send, then the answer completes and recalculates ----------

QUESTION = "אני מבקש/ת לברר מה סכום אחזקת הרכב החודשי שאני מקבל/ת."


def test_the_rep_choice_alone_opens_nothing(engine):
    client, cid, say, view = start(engine)
    to_maintenance(say)
    say({"choice": "rep", "field": "maintenance"})
    assert client.get("/api/rep/questions?status=OPEN", headers=REP).json() == []   # only the card's send opens one


def test_the_representatives_answer_completes_the_value_and_recalculates(engine):
    client, cid, say, view = start(engine, payslip_maintenance=900)
    say("הכי משתלם")
    to_maintenance(say)
    r = client.post(f"/api/conversations/{cid}/rep-question", json={"question": QUESTION, "intent": "current_maintenance"})
    assert r.status_code == 200 and r.json()["status"] == "created", r.text
    assert r.json()["missing_field"] == "MAINTENANCE_AMOUNT"
    turn = say({"choice": "rep", "field": "maintenance"})
    assert "נברר מול נציג התחבורה" in turn["reply"] and turn["results"] == []
    assert "rep" not in choice_ids(turn, "maintenance")                            # asked once: not offered again
    assert "שאלתי את נציג התחבורה" in turn["reply"]                                 # the end step says it is on its way
    again = client.post(f"/api/conversations/{cid}/rep-question",
                        json={"question": QUESTION, "intent": "current_maintenance"})
    assert again.json()["status"] == "exists"                                      # never a second inquiry
    [q] = client.get("/api/rep/questions?status=OPEN", headers=REP).json()
    assert q["missing_field"] == "MAINTENANCE_AMOUNT" and set(q["context"]) <= {"group", "car_label"}
    assert client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "1100"}, headers=REP).status_code == 200
    v = view()
    assert v["current_car"]["maintenance"] == "1100" and v["current_car"]["maintenance_unknown"] is False
    assert len(v["results"]) == 3 and v["stage"] == "PRESENTED"                    # recalculated, the ranking done
    assert any("אחזקת הרכב שלכם היא 1,100 ₪" in m["content"] for m in v["messages"])


def test_a_maintenance_inquiry_needs_no_car_today(engine):
    client, cid, say, _ = start(engine)
    say("קורולה קרוס 2025")
    r = client.post(f"/api/conversations/{cid}/rep-question", json={"question": QUESTION, "intent": "current_maintenance"})
    assert r.status_code in (200, 422) and r.json().get("missing_field") != "MAINTENANCE_AMOUNT"


# ---------- the car path, the same statuses ----------

def test_car_path_unknown_copay_with_a_ranking_is_a_status_not_asked_again(engine):
    _, _, say, view = start(engine)
    say("הכי משתלם")
    say("קורולה קרוס 2025")
    say("לא ידוע")                                       # the ways on (the approved 16/11 copay rule), nothing saved
    turn = say("לא ידוע")                                # recorded as unknown
    assert view()["current_car"]["copay_unknown"] is True and turn["results"] == []
    assert "החישוב מחכה רק להשתתפות ברכב של היום" in turn["reply"]
    assert "אולי כבר מצאתם" not in turn["reply"]       # not the question again in the same reply
    assert NOT_UNDERSTOOD not in say("לא ידוע")["reply"]
    done = say("300")
    assert done["stage"] == "PRESENTED" and len(done["results"]) == 3


def test_car_path_copay_with_the_rep_is_said_as_waiting(engine):
    client, cid, say, view = start(engine)
    say("קורולה קרוס 2025")
    r = client.post(f"/api/conversations/{cid}/rep-question",
                    json={"question": "מה ההשתתפות שלי ברכב הנוכחי?", "intent": "current_copay"})
    assert r.json()["status"] == "created"
    say({"choice": "rep", "field": "copay"})
    turn = say("אטו 3")
    assert turn["results"] == [] and "שאלתי את נציג התחבורה" in turn["reply"]
    assert "אולי כבר מצאתם" not in turn["reply"]
