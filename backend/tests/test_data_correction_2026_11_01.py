"""A correction of the employee's own data, asked for in words, at any stage (1/11).

Live QA on 8796: after the results, "אני רוצה להחליף נתון אחד שלא כתבתי נכון מה לעשות?" was offered to the transport
representative. The message was a question with no FAQ entry, the scope layer read it as one about the arrangements,
and when the guard rejected the model's reply the template offered the representative. A correction is now its own
intent (understanding.correction_request): code asks what to correct, or the new value of the field named, saves it
like the correction form and recalculates every result and the option map. Never the representative.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.understanding import correction_request

from .conftest import make_client
from .test_routing_2026_10_31 import at_copay

REP_OFFER = "רוצים שאשאל אותו"


def at_results(client):
    cid, send, _ = at_copay(client)
    send({"choice": "catalog:CAR-07", "field": "copay"})
    turn = send({"text": "הכי משתלם"})
    assert turn["results"], turn["reply"]
    return cid, send, turn


def nets(results):
    return {r["option_id"]: (r["today"]["net_display"], r["alternative"]["net_display"]) for r in results}


@pytest.mark.parametrize("text, fields", [
    ("אני רוצה להחליף נתון אחד שלא כתבתי נכון מה לעשות?", []),     # the live message
    ("טעיתי בנתון", []), ("אני רוצה לשנות משהו", []), ("אפשר לעדכן פרטים?", []),
    ("השכר שהכנסתי לא נכון", ["base_salary"]), ("טעיתי בפנסיה", ["pension_percent"]), ("טעיתי בקבוצה", ["group"]),
    ("הרכב שכתבתי לא נכון", ["car"]),
])
def test_a_correction_is_recognised_in_natural_words(snapshot, text, fields):
    assert correction_request(snapshot, text) == fields


@pytest.mark.parametrize("text", [
    "טעיתי, השכר שלי 20,000",          # the value is in it: the correction itself, read as before
    "אין לי עמלות, טעיתי", "טעיתי, אני בקבוצה ב", "טעיתי, הרכב שלי הוא קורולה",
    "אני רוצה להחליף את הרכב",          # another car: the service itself, never a correction
    "אני רוצה לשנות את הבחירה", "יש טעות בחישוב?", "לא, זה לא נכון", "למה ההשתתפות לא נכונה?",
])
def test_what_is_not_a_correction_request_keeps_its_meaning(snapshot, text):
    assert correction_request(snapshot, text) is None


def test_after_results_a_general_correction_is_asked_never_sent_to_the_representative(engine):
    client = make_client(engine)
    cid, send, _ = at_results(client)
    turn = send({"text": "אני רוצה להחליף נתון אחד שלא כתבתי נכון מה לעשות?"})
    assert REP_OFFER not in turn["reply"] and "נציג" not in turn["reply"]
    assert "מה תרצו לתקן?" in turn["reply"] and "לשנות בחירה" in turn["reply"]
    assert turn["expects"]["link"]["kind"] == "correction_form"
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["rep_questions"] == [] and all(f["field"] != "ask_rep" for f in view["expects"]["fields"])


def test_a_salary_correction_asks_the_value_and_recalculates_every_result_and_the_map(engine):
    client = make_client(engine)
    cid, send, first = at_results(client)
    send({"text": "השווה את כל האפשרויות שלי"})                       # the map is open too
    before_map = client.get(f"/api/conversations/{cid}").json()["option_map"]
    turn = send({"text": "השכר שהכנסתי לא נכון"})
    assert "מה הערך הנכון של שכר בסיס?" in turn["reply"] and "18,000 ₪" in turn["reply"]
    assert REP_OFFER not in turn["reply"]
    after = send({"text": "20000"})
    assert "עדכנתי: שכר בסיס 20,000 ₪ במקום 18,000 ₪" in after["reply"]
    v = client.get(f"/api/conversations/{cid}").json()
    assert v["profile"]["base"] == "20000" and not v["results_stale"]
    old, new = nets(first["results"]), nets([{**r["result"], "option_id": r["option_id"]} for r in v["results"]])
    assert set(old) == set(new) and all(new[o][0] != old[o][0] for o in old)       # every today's net moved
    assert not v["option_map"]["stale"]
    assert v["option_map"]["entries"][0]["result"]["today"]["net_display"] != \
        before_map["entries"][0]["result"]["today"]["net_display"]


def test_a_group_correction_is_saved_through_the_same_gate(engine):
    client = make_client(engine)
    cid, send, _ = at_results(client)
    turn = send({"text": "טעיתי בקבוצה"})
    assert "מה קבוצת הזכאות הנכונה (א–ד)?" in turn["reply"] and "קבוצה א" in turn["reply"]
    send({"text": "ב"})
    assert client.get(f"/api/conversations/{cid}").json()["profile"]["group"] == "ב"


def test_a_car_correction_asks_the_model_again_and_marks_the_results_out_of_date(engine):
    client = make_client(engine)
    cid, send, _ = at_results(client)
    turn = send({"text": "הרכב שכתבתי לא נכון"})
    assert REP_OFFER not in turn["reply"]
    v = client.get(f"/api/conversations/{cid}").json()
    assert v["current_car"]["catalog_code"] is None and v["results_stale"]          # never shown as current
    send({"text": "קורולה קרוס"})
    v = client.get(f"/api/conversations/{cid}").json()
    assert "COROLLA CROSS" in (v["current_car"]["label"] or "")


def test_a_correction_after_the_representative_answered_is_saved_and_recalculated(engine):
    token = "test-rep-token"
    client = make_client(engine, rep_access_token=token)
    cid, send, _ = at_copay(client)
    assert client.post(f"/api/conversations/{cid}/rep-question",
                       json={"question": "מה סכום ההשתתפות שלי?", "intent": "current_copay"}).status_code == 200
    send({"choice": "rep", "field": "copay"})
    send({"text": "הכי משתלם"})
    [q] = client.get("/api/rep/questions?status=OPEN", headers={"X-Rep-Token": token}).json()
    client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "300"}, headers={"X-Rep-Token": token})
    send({"text": "טעיתי בפנסיה"})
    after = send({"text": "6%"})
    assert "עדכנתי: פנסיה 6% במקום 5%" in after["reply"]
    assert client.get(f"/api/conversations/{cid}").json()["results_stale"] is False


def test_the_value_question_survives_a_reload(engine):
    client = make_client(engine)
    cid, send, _ = at_results(client)
    send({"text": "השכר שהכנסתי לא נכון"})
    v = client.get(f"/api/conversations/{cid}").json()                      # a reload: the same question, the link
    assert v["expects"]["link"]["kind"] == "correction_form"
    assert "מה הערך הנכון של שכר בסיס?" in v["messages"][-1]["content"]
    after = send({"text": "19500"})
    assert "עדכנתי: שכר בסיס 19,500 ₪" in after["reply"]


def test_a_message_unrelated_to_the_correction_is_read_as_usual(engine):
    client = make_client(engine)
    cid, send, _ = at_results(client)
    send({"text": "השכר שהכנסתי לא נכון"})
    turn = send({"text": "מה זה שווי שימוש?"})                              # not a value: answered, nothing saved
    assert client.get(f"/api/conversations/{cid}").json()["profile"]["base"] == "18000"
    assert "שווי" in turn["reply"]


def test_with_the_model_the_correction_is_decided_in_code_and_the_model_is_not_asked(engine):
    # live 8796: the model's reply was rejected by a guard and the template offered the representative. A correction
    # is code's: the scripted model below would propose the representative, and it is never called
    from app.modules.conversation.llm.fake import FakeLLM, call
    from app.modules.conversation.public import LLMChain
    plain = make_client(engine)
    cid, _, _ = at_results(plain)
    fake = FakeLLM()
    fake.turn(call("propose_rep_question", question="איך מחליפים נתון?"),
              reply="על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?")
    model = make_client(engine, llm=LLMChain([fake]))
    model.cookies = plain.cookies                          # the same employee, now with a model configured
    turn = model.post(f"/api/conversations/{cid}/messages",
                      json={"text": "אני רוצה להחליף נתון אחד שלא כתבתי נכון מה לעשות?"}).json()
    assert REP_OFFER not in turn["reply"] and "מה תרצו לתקן?" in turn["reply"]
    assert fake.calls == []
    assert model.get(f"/api/conversations/{cid}").json()["rep_questions"] == []
