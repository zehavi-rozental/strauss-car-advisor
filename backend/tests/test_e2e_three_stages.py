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
    cid = start(client)
    say(client, cid, PROFILE)
    say(client, cid, "יש לי קורולה קרוס")        # the car today
    say(client, cid, "לא יודעת")                 # its copay is unknown -> a rep question
    body = say(client, cid, ALT)
    assert body["stage"] == "WAITING_FOR_REP"
    assert body["results"] == []                              # nothing invented while info is missing

    qs = open_questions(client)
    assert len(qs) == 1
    q = qs[0]
    assert q["missing_field"] == "CURRENT_COPAY"
    assert "salary" not in str(q["context"]).lower() and "base" not in q["context"]   # no pay data to the rep

    # the rep answers; the current-car copay is filled and the conversation resumes from the same point
    assert answer(client, q["id"], "250", effective_date="2026-01-01").status_code == 200
    v = view(client, cid)
    assert v["stage"] != "WAITING_FOR_REP"                    # unblocked
    assert v["current_car"]["copay"] == "250"                 # the rep's value was applied
    # choosing the alternative now produces the result (the engine runs, not the LLM)
    body = say(client, cid, ALT)
    assert body["stage"] == "PRESENTED"
    assert body["results"] and body["results"][0]["status"] == "OK"


# ---------------------------------------------------------------------------
# 13: the rep can say "no answer yet" and the conversation keeps waiting (no crash, no invented number)
# ---------------------------------------------------------------------------

def test_rep_has_no_answer_yet_keeps_waiting(client):
    cid = start(client)
    say(client, cid, PROFILE)
    say(client, cid, "יש לי קורולה קרוס")
    say(client, cid, "לא יודעת")
    say(client, cid, ALT)
    [q] = open_questions(client)
    # an empty / "not available" answer is rejected as invalid (422): the question stays open, nothing invented
    resp = answer(client, q["id"], "")
    assert resp.status_code == 422
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
