"""A representative's amount is checked against the same ranges as the employee's (QA 22/10, Kiro's finding).

Found: a rep answer was checked only for being a non-negative amount, so a typo such as 350000 for today's copay
reached the calculation. Now the rep API refuses (422, nothing saved) an amount outside the range extensions.yaml
already sets for that value (value_ranges: current_copay, current_use_value, current_maintenance). No range is
invented: a new car's copay has none configured and keeps only the non-negative check."""
from __future__ import annotations

from .conftest import make_client

TOKEN = "rep-secret"
REP = {"X-Rep-Token": TOKEN}
NOA = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
       "2.75 נקודות זיכוי, 2 ילדים")


def waiting_for_copay(engine):
    """Today's copay unknown → a blocking question to the representative."""
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in (NOA, "יש לי קורולה קרוס", "לא יודעת"):
        assert client.post(f"/api/conversations/{cid}/messages", json={"text": text}).status_code == 200
    q = client.get("/api/rep/questions", headers=REP).json()[0]
    assert q["missing_field"] == "CURRENT_COPAY"
    return client, cid, q["id"]


def test_a_copay_typo_is_refused_and_nothing_reaches_the_calculation(engine):
    client, cid, qid = waiting_for_copay(engine)
    r = client.post(f"/api/rep/questions/{qid}/answer", headers=REP, json={"value": "350000"})
    assert r.status_code == 422
    assert "מחוץ לטווח" in r.json()["detail"] and "350000" not in r.json()["detail"]
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["current_car"]["copay"] is None and view["results"] == []
    assert client.get(f"/api/rep/questions/{qid}", headers=REP).json()["status"] == "OPEN"   # still to answer


def test_an_amount_inside_the_range_is_accepted(engine):
    client, cid, qid = waiting_for_copay(engine)
    r = client.post(f"/api/rep/questions/{qid}/answer", headers=REP, json={"value": "350"})
    assert r.status_code == 200
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] in ("350", "350.00")


def test_the_range_edges_come_from_the_configuration(engine):
    from app.modules.rules.public import load_snapshot
    from .conftest import CONFIG
    low, high = load_snapshot(CONFIG).extensions.value_ranges["current_copay"]
    client, _, qid = waiting_for_copay(engine)
    assert client.post(f"/api/rep/questions/{qid}/answer", headers=REP,
                       json={"value": str(high + 1)}).status_code == 422
    assert client.post(f"/api/rep/questions/{qid}/answer", headers=REP, json={"value": str(high)}).status_code == 200
