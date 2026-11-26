"""Today's copay asked from the representative: no net on an unverified amount (16/11, replaces the provisional
catalog amount of 1/11).

Same employee, same salary, same cars throughout: (1) the inquiry is sent; (2) the copay stays unknown: no catalog
amount is used or shown as the employee's, and nothing is calculated; (3) the representative answers an amount;
(4) it is offered, never applied by itself; (5-7) confirming calculates: today's net is the independent oracle's for
that amount; (8) a reload shows no button and nothing is applied twice.
"""
from __future__ import annotations

from decimal import Decimal as D

from .conftest import make_client
from .reference_oracle import Employee, Situation, evaluate, load_data, load_rules, shekel
from .test_routing_2026_10_31 import at_copay

TOKEN = "test-rep-token"
RULES, DATA = load_rules(), load_data()
EMP = Employee(id="t", name="synthetic", occupation="", group="א", base=D(18000), commissions=D(0), bonus=D(0),
               pension_pct=D(5), study_pct=D(0), points=D("2.75"), children=2, current_car="", current_copay=D(0))


def oracle_today(copay: int) -> int:
    return int(shekel(evaluate(EMP, Situation(label="today", car=DATA.cars["COROLLA"], copay=D(copay)), RULES).net))


def nets(results):
    return {r["option_id"]: (int(r["today"]["net_display"]), int(r["alternative"]["net_display"]),
                             int(r["delta_display"])) for r in results}


def test_no_net_until_the_rep_answers_and_the_employee_confirms(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid, send, _ = at_copay(client)                              # COROLLA (2026): catalog copay 250
    view = lambda: client.get(f"/api/conversations/{cid}").json()

    # 1. the inquiry, then the choice the client sends after it
    r = client.post(f"/api/conversations/{cid}/rep-question",
                    json={"question": "מה סכום ההשתתפות שלי ברכב הנוכחי?", "intent": "current_copay"})
    assert r.status_code == 200 and r.json()["status"] == "created"
    turn = send({"choice": "rep", "field": "copay"})
    assert "לא אחשב נטו ולא אציב סכום משלי" in turn["reply"] and "250" not in turn["reply"]
    car = view()["current_car"]
    assert car["copay"] is None and car["copay_source"] is None and car["copay_unknown"] is True
    assert car["copay_provisional"] is None and view()["today_copay_provisional"] is None

    # 2. a ranking request waits: nothing is calculated on an unverified amount
    first = send({"text": "הכי משתלם"})
    assert first["results"] == [] and "250" not in first["reply"]

    # 3-4. the representative answers 300: offered; nothing applied by itself
    rep = {"X-Rep-Token": TOKEN}
    [q] = [q for q in client.get("/api/rep/questions?status=OPEN", headers=rep).json()
           if q["missing_field"] == "CURRENT_COPAY"]
    assert client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "300"}, headers=rep).status_code == 200
    v = view()
    offer = next(f for f in v["expects"]["fields"] if f["field"] == "rep_copay")
    assert offer["choices"][0]["label"] == "עדכון הסכום וחישוב מחדש"
    assert offer["context"] == {"car": "COROLLA (2026)", "amount": "300", "previous": None, "provisional": None}
    assert v["current_car"]["copay"] is None and v["results"] == []          # nothing applied by itself

    # 5-7. confirm: the ranking asked for earlier is calculated, today's net exactly as the oracle says
    turn = send({"choice": offer["choices"][0]["id"], "field": "rep_copay"})
    after = nets(turn["results"])
    assert after and {t for t, _, _ in after.values()} == {oracle_today(300)}
    assert all("today_copay_provisional" not in r for r in turn["results"])
    car = view()["current_car"]
    assert car["copay"] == "300" and car["copay_source"] == "rep_confirmed" and car["copay_provisional"] is None

    # 8. a reload: no button; a second click applies nothing
    assert all(f["field"] != "rep_copay" for f in view()["expects"]["fields"])
    again = client.post(f"/api/conversations/{cid}/messages",
                        json={"choice": offer["choices"][0]["id"], "field": "rep_copay"})
    assert again.status_code == 422 and view()["current_car"]["copay"] == "300"


def test_the_rep_text_assumes_nothing():
    from app.modules.conversation.templates import COPAY_WITH_REP, copay_with_rep_text
    assert copay_with_rep_text(None) == COPAY_WITH_REP and "לא אציב סכום משלי" in COPAY_WITH_REP
    assert not any(ch.isdigit() for ch in COPAY_WITH_REP)


def test_the_same_car_matched_again_keeps_the_inquiry_state_and_never_an_amount(engine):
    """Found in the browser on 8795 (1/11): the model matched today's car again on "2.75 נקודות זיכוי", the car was
    rebuilt, the provisional amount was lost and the comparison was blocked again."""
    from app.modules.conversation.llm.fake import FakeLLM, call
    from app.modules.conversation.public import LLMChain
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))
    cid, send, _ = at_copay(client)
    send({"choice": "rep", "field": "copay"})
    car = lambda: client.get(f"/api/conversations/{cid}").json()["current_car"]
    assert car()["copay_unknown"] and car()["copay_provisional"] is None
    fake.turn(call("match_current_car", model="קורולה 2026"), reply="רשמתי.")
    send({"text": "2.75 נקודות זיכוי, והרכב שלי הוא קורולה 2026"})
    assert car()["catalog_code"] == "CAR-07" and car()["copay_unknown"]                    # the same car: kept
    assert car()["copay"] is None and car()["copay_provisional"] is None                   # never an amount
    fake.turn(call("match_current_car", model="מאזדה 3", reset=True), reply="רשמתי.")
    send({"text": "טעיתי, הרכב שלי הוא מאזדה 3"})
    assert car()["catalog_code"] != "CAR-07" and car()["copay"] is None and car()["copay_provisional"] is None


def test_after_confirming_the_results_say_the_amount_is_the_representative_s(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid, send, _ = at_copay(client)
    client.post(f"/api/conversations/{cid}/rep-question", json={"question": "מה ההשתתפות שלי?", "intent": "current_copay"})
    send({"choice": "rep", "field": "copay"})
    send({"text": "הכי משתלם"})
    rep = {"X-Rep-Token": TOKEN}
    [q] = [q for q in client.get("/api/rep/questions?status=OPEN", headers=rep).json()
           if q["missing_field"] == "CURRENT_COPAY"]
    client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "300"}, headers=rep)
    offer = next(f for f in client.get(f"/api/conversations/{cid}").json()["expects"]["fields"]
                 if f["field"] == "rep_copay")
    reply = send({"choice": offer["choices"][0]["id"], "field": "rep_copay"})["reply"]
    assert "עדכנתי לפי תשובת הנציג: השתתפות 300 ₪" in reply and "שמסרתם" not in reply


def test_the_three_copay_states_name_their_source_and_offer_no_second_check(engine):
    """(1) a confirmed catalog amount, (2) a provisional catalog amount while the rep is asked, (3) the rep's answer
    confirmed — each with its own source (since #9, 27/11, on the card above the comparison, not in the results
    text); once confirmed, no provisional mark and no offer to check it again."""
    # (1) through the API: "זה הסכום שלי" — confirmed, marked so, nothing provisional, no copay question left
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    send({"choice": "catalog:CAR-07", "field": "copay"})
    v = client.get(f"/api/conversations/{cid}").json()
    assert v["today_copay"] == "250" and v["today_copay_source"] == "catalog_confirmed"
    assert v["today_copay_provisional"] is None
    assert all(f["field"] not in ("copay", "rep_copay") for f in v["expects"]["fields"])
    results = send({"text": "הכי משתלם"})["results"]
    assert results and all("today_copay_provisional" not in r for r in results)


def test_view_and_updates_name_todays_car_for_the_card_above_the_comparison(engine):
    """The card above the comparison (1/11) names today's car: the same label in the view and in /updates."""
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    send({"choice": "catalog:CAR-07", "field": "copay"})
    v = client.get(f"/api/conversations/{cid}").json()
    assert v["today_car"] and "COROLLA" in v["today_car"]
    u = client.get(f"/api/conversations/{cid}/updates?after=0").json()
    assert u["today_car"] == v["today_car"]
