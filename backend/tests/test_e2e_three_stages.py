"""End-to-end acceptance: the three stages work together through the HTTP API (template mode, no LLM).

These prove the JOURNEY, not single functions returning 200: conversation → (representative when info is
missing) → calculation → result + comparison data → change an alternative → recompute. Everything goes
through the real endpoints and the real engine; numbers come from the engine, never hardcoded.

Requirement 13 scenarios covered here deterministically:
  1  the official Strauss script to the comparison
  4  missing info → a rep question is created, answered, the comparison updates
 12  a rep question is created and answered
 13  the rep answers "no answer yet"
 15  calculation and the comparison table data
 16  add/replace an alternative and recompute
 19  a mandatory field missing → no invented result
"""
from __future__ import annotations

import pytest

from .conftest import make_client

TOKEN = "e2e-rep"
AUTH = {"X-Rep-Token": TOKEN}

# the official-script employee (Noa): one message with the full profile, then the current car, then an
# electric alternative. Matches the reference employee E-001 whose net is checked by app.verify (12/12).
PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")
CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"
ALT = "אטו 3"


@pytest.fixture
def client(engine):
    return make_client(engine, rep_access_token=TOKEN)


def start(client):
    return client.post("/api/conversations").json()["conversation_id"]


def say(client, cid, text):
    return client.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()


def view(client, cid):
    return client.get(f"/api/conversations/{cid}").json()


def open_questions(client):
    return client.get("/api/rep/questions", headers=AUTH).json()


def answer(client, qid, value, **extra):
    return client.post(f"/api/rep/questions/{qid}/answer", json={"value": value, **extra}, headers=AUTH)


# ---------------------------------------------------------------------------
# 1 + 15: the full journey to a result with a comparison, numbers from the engine
# ---------------------------------------------------------------------------

def test_full_journey_to_result_and_comparison(client):
    cid = start(client)
    say(client, cid, PROFILE)
    say(client, cid, CAR)
    body = say(client, cid, ALT)

    assert body["stage"] == "PRESENTED"
    assert body["results"], "no result produced"
    r = body["results"][0]
    assert r["label"].startswith("ATTO 3")
    # the comparison fields the table renders, straight from the engine ScenarioResult
    assert r["status"] == "OK"
    assert r["today"]["net_display"] and r["alternative"]["net_display"]
    assert r["delta_display"] is not None
    assert r["today"]["use_value"] is not None and r["alternative"]["use_value"] is not None
    assert r["alternative"]["copay"] is not None          # the employee's participation
    # the electric refund shows up for the electric alternative
    assert r["electric_refund"] is not None or float(r["alternative"]["electric_gross_up"]) != 0


# ---------------------------------------------------------------------------
# 4 + 12 + 15: missing info → rep question → answer → the comparison updates
# ---------------------------------------------------------------------------

def test_missing_copay_goes_to_rep_then_result_updates(client):
    """The new car's copay: calculated now with the catalog amount, said so, sent to the representative; the
    representative's amount recalculates the comparison from the same point (C1, 22/10)."""
    cid = start(client)
    say(client, cid, PROFILE)
    say(client, cid, CAR)
    body = say(client, cid, ALT)
    assert body["stage"] == "PRESENTED"
    [before] = body["results"]
    assert before["copay_source"] == "catalog_final" and before["copay_pending"] is False
    assert open_questions(client) == []                      # the catalog amount is used (22/10)
    say(client, cid, "אני רוצה שנציג התחבורה יאשר את ההשתתפות")                # the employee asks: now the representative is asked

    [q] = open_questions(client)
    assert q["missing_field"] == "NEW_CAR_COPAY" and q["option_id"] == "CAR-10"
    assert q["context"]["suggested_amount"] == before["alternative"]["copay"]
    assert "salary" not in str(q["context"]).lower() and "base" not in q["context"]   # no pay data to the rep

    assert answer(client, q["id"], "350", effective_date="2026-01-01").status_code == 200
    [after] = view(client, cid)["results"]
    assert after["result"]["alternative"]["copay"] == "350"
    assert after["result"]["copay_source"] == "representative" and after["result"]["copay_pending"] is False
    assert after["result"]["delta_display"] != before["delta_display"]       # the engine ran again
    assert open_questions(client) == []


def test_an_unknown_copay_today_is_asked_from_the_employee_not_the_rep(client):
    """Rule A (22/10): today's copay is the employee's own figure. "לא יודעת" is recorded, nothing goes to the
    representative and nothing is calculated until the employee gives it."""
    cid = start(client)
    say(client, cid, PROFILE)
    say(client, cid, "יש לי קורולה קרוס")
    say(client, cid, "לא יודעת")
    body = say(client, cid, ALT)
    assert body["results"] == [] and open_questions(client) == []
    assert body["stage"] != "WAITING_FOR_REP"
    body = say(client, cid, "ההשתתפות שלי היום 300")
    assert body["stage"] == "PRESENTED" and body["results"][0]["status"] == "OK"


# ---------------------------------------------------------------------------
# 13: the rep can say "no answer yet" and the conversation keeps waiting (no crash, no invented number)
# ---------------------------------------------------------------------------

CONFLICT = ["קבוצה ב, שכר בסיס 18,500", "אני מנהלת מוצר",
            "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים", CAR]


def test_rep_has_no_answer_yet_keeps_waiting(client):
    """The one blocking wait left: a stated group that contradicts the occupations table (C4)."""
    cid = start(client)
    for text in CONFLICT:
        say(client, cid, text)
    [q] = open_questions(client)
    assert q["missing_field"] == "ELIGIBILITY_GROUP"
    # an empty or not-a-group answer is rejected (422): the question stays open, nothing invented
    assert answer(client, q["id"], "").status_code == 422
    assert answer(client, q["id"], "עוד לא יודע").status_code == 422
    v = view(client, cid)
    assert v["stage"] == "WAITING_FOR_REP" and v["results"] == []
    assert len(open_questions(client)) == 1                   # still open, not duplicated


# ---------------------------------------------------------------------------
# 16: change the alternative and recompute
# ---------------------------------------------------------------------------

def test_change_alternative_recomputes(client):
    cid = start(client)
    say(client, cid, PROFILE)
    say(client, cid, CAR)
    first = say(client, cid, ALT)
    assert first["results"][0]["label"].startswith("ATTO 3")

    # the employee switches to a different eligible alternative through the selection (as the UI does:
    # a structured {choices} message, exactly what the SelectionCard sends). EV3 = CAR-11.
    second = client.post(f"/api/conversations/{cid}/messages",
                         json={"choices": ["CAR-11"], "field": "options"}).json()
    labels = [r["label"] for r in (second["results"] or view(client, cid)["results"])]
    assert any("EV3" in lbl.upper() or "EV 3" in lbl.upper() for lbl in labels), labels


# ---------------------------------------------------------------------------
# 19: a mandatory field missing → no invented result
# ---------------------------------------------------------------------------

def test_no_result_while_mandatory_field_missing(client):
    cid = start(client)
    # defer the base salary, give everything else, pick a car and an alternative
    say(client, cid, "קבוצה ג")
    say(client, cid, "את השכר אבדוק אחר כך")
    say(client, cid, "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים")
    say(client, cid, CAR)
    body = say(client, cid, ALT)
    # the base salary is mandatory for the net: no result until it is supplied
    assert body["results"] == []
    assert body["stage"] != "PRESENTED"
