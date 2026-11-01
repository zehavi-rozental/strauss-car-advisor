"""Today's copay from the representative, applied by the employee in one click (1/11).

The employee asks the representative about today's copay; the representative answers with an amount (a structured
MONEY answer, never read from free text). The amount is offered, not applied: only the employee's confirmation saves
it (marked "rep_confirmed") and recalculates every card. Once applied the offer is gone, so a second click, a reload
or a reopened conversation never applies it twice.
"""
from __future__ import annotations

from .conftest import make_client
from .test_routing_2026_10_31 import at_copay

TOKEN = "test-rep-token"
QUESTION = "אני מבקש/ת לברר מה סכום ההשתתפות החודשי שלי ברכב הנוכחי (COROLLA (2026))."


def setup(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid, send, _ = at_copay(client)
    return client, cid, send


def ask_and_answer(client, cid, send, value="230"):
    r = client.post(f"/api/conversations/{cid}/rep-question", json={"question": QUESTION, "intent": "current_copay"})
    assert r.status_code == 200 and r.json()["status"] == "created", r.text
    send({"choice": "rep", "field": "copay"})
    rep = {"X-Rep-Token": TOKEN}
    [q] = [q for q in client.get("/api/rep/questions?status=OPEN", headers=rep).json()
           if q["missing_field"] == "CURRENT_COPAY"]                     # one conversation per test database
    assert q["answer_type"] == "MONEY"
    assert set(q["context"]) <= {"group", "car_label"}                     # no pay data to the representative
    assert client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": value}, headers=rep).status_code == 200
    return client.get(f"/api/conversations/{cid}").json()


def offer_of(view):
    return next((f for f in view["expects"]["fields"] if f["field"] == "rep_copay"), None)


def test_the_answer_is_offered_not_applied(engine):
    client, cid, send = setup(engine)
    view = ask_and_answer(client, cid, send)
    offer = offer_of(view)
    assert offer is not None and offer["context"]["amount"] == "230" and offer["context"]["car"] == "COROLLA (2026)"
    assert [c["label"] for c in offer["choices"]] == ["עדכון הסכום וחישוב מחדש"]
    assert view["current_car"]["copay"] is None                           # nothing applied by itself
    # the catalog amount the calculation used, and the representative's amount, both said (1/11)
    assert any("230 ₪" in m["content"] and "250 ₪ לפי הקטלוג" in m["content"] for m in view["messages"])


def test_confirming_applies_once_marks_the_source_and_recalculates(engine):
    client, cid, send = setup(engine)
    send({"text": "השתתפות 200"})                                          # the employee's own amount first
    first = send({"text": "הכי משתלם"})["results"]
    view = ask_and_answer(client, cid, send)
    offer = offer_of(view)
    assert offer["context"]["previous"] == "200"
    assert any("יחליף את הסכום הקודם" in m["content"] for m in view["messages"])
    turn = send({"choice": offer["choices"][0]["id"], "field": "rep_copay"})
    assert "עדכנתי לפי תשובת הנציג: השתתפות 230 ₪ במקום 200 ₪" in turn["reply"]
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["current_car"]["copay"] == "230" and view["current_car"]["copay_source"] == "rep_confirmed"
    assert offer_of(view) is None                                          # gone: a reload shows no button
    again = {r["option_id"]: r for r in turn["results"]}
    for r in first:                                                        # every card recalculated: 30 ₪ less today
        assert int(again[r["option_id"]]["today"]["net_display"]) == int(r["today"]["net_display"]) - 30
    second = client.post(f"/api/conversations/{cid}/messages",
                         json={"choice": offer["choices"][0]["id"], "field": "rep_copay"})
    assert second.status_code == 422                                       # a second click applies nothing
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] == "230"


def test_a_later_typed_amount_is_the_employee_s_own_again(engine):
    client, cid, send = setup(engine)
    view = ask_and_answer(client, cid, send)
    send({"choice": offer_of(view)["choices"][0]["id"], "field": "rep_copay"})
    send({"text": "טעיתי, ההשתתפות שלי 240"})
    car = client.get(f"/api/conversations/{cid}").json()["current_car"]
    assert car["copay"] == "240" and car["copay_source"] is None


def test_the_results_say_the_amount_came_from_the_representative():
    from app.modules.conversation.templates import catalog_copays_text
    line = catalog_copays_text([("NIRO (2025)", 250)], 230, "rep_confirmed")
    assert "לפי תשובת הנציג שאישרתם: 230 ₪" in line and "שמסרתם" not in line
    assert "שמסרתם: 230 ₪" in catalog_copays_text([("NIRO (2025)", 250)], 230)
