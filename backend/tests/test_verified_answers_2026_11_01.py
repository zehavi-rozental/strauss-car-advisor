"""Verified answers first (1/11): a fact the company catalog holds is answered from it; the net difference between two
calculated cars comes from the engine's results; only what nothing verified holds is offered to the representative.
Both modes: the answer is code's (the model is not asked to know it)."""
from __future__ import annotations

import pytest

from .conftest import make_client
from .test_inquiry_record import _client, _code, _compare

OFFER = "רוצים שאשאל אותו"


def said(c, cid, text):
    return c.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()


@pytest.mark.parametrize("question, field", [
    ("כמה עולה NIRO?", "list_price"), ("מה שווי השימוש של NIRO?", "use_value"),
    ("מה היצרן של NIRO?", "manufacturer"), ("NIRO חשמלי?", None), ("באיזו קבוצה NIRO?", None),
])
def test_a_fact_the_catalog_holds_is_answered_from_it(engine, snapshot, question, field):
    car = next(c for c in snapshot.catalog if c.model_name == "NIRO")
    c = _client(engine)
    cid = _compare(c, [car.catalog_code])
    turn = said(c, cid, question)
    assert OFFER not in turn["reply"] and all(f["field"] != "ask_rep" for f in turn["expects"]["fields"])
    if field == "list_price":
        assert f"{int(car.list_price):,} ₪" in turn["reply"]
    elif field == "use_value":
        assert f"{int(car.use_value):,} ₪" in turn["reply"]
    elif field == "manufacturer":
        assert car.manufacturer in turn["reply"]
    else:
        assert "NIRO" in turn["reply"]


def test_what_the_catalog_does_not_hold_is_still_offered(engine, snapshot):
    c = _client(engine)
    cid = _compare(c, [_code(snapshot, "NIRO")])
    turn = said(c, cid, "יש ל-NIRO גג נפתח?")
    assert any(f["field"] == "ask_rep" for f in turn["expects"]["fields"])


def test_the_net_difference_of_two_calculated_cars_comes_from_the_results(engine, snapshot):
    niro, dolphin = _code(snapshot, "NIRO"), _code(snapshot, "DOLPHIN")
    c = _client(engine)
    cid = _compare(c, [niro, dolphin])
    results = {r["option_id"]: r["result"] for r in c.get(f"/api/conversations/{cid}").json()["results"]}
    gap = abs(int(results[dolphin]["delta_display"]) - int(results[niro]["delta_display"]))
    turn = said(c, cid, "מה ההבדל בנטו בין NIRO ל-DOLPHIN?")
    assert f"ההפרש ביניהם: {gap:,} ₪ בחודש" in turn["reply"]
    assert f"{int(results[niro]['delta_display']):,} ₪" in turn["reply"]
    assert "איזה רכבים להשוות" not in turn["reply"] and OFFER not in turn["reply"]


def test_with_the_model_the_difference_is_the_engine_s_not_the_model_s(engine, snapshot):
    from app.modules.conversation.llm.fake import FakeLLM
    from app.modules.conversation.public import LLMChain
    niro, dolphin = _code(snapshot, "NIRO"), _code(snapshot, "DOLPHIN")
    plain = _client(engine)
    cid = _compare(plain, [niro, dolphin])
    results = {r["option_id"]: r["result"] for r in plain.get(f"/api/conversations/{cid}").json()["results"]}
    gap = abs(int(results[dolphin]["delta_display"]) - int(results[niro]["delta_display"]))
    fake = FakeLLM()
    fake.turn(reply="ההבדל הוא בערך 300 ₪ לטובת NIRO.")                     # an invented figure
    model = make_client(engine, llm=LLMChain([fake]), rep_access_token="rep-record")
    model.cookies = plain.cookies
    turn = said(model, cid, "מה ההבדל בנטו בין NIRO ל-DOLPHIN?")
    assert f"ההפרש ביניהם: {gap:,} ₪ בחודש" in turn["reply"]
    assert "300 ₪ לטובת NIRO" not in turn["reply"]
