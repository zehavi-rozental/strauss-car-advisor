"""expects עם כל תשובה, חיפוש רכב, ובחירות מובנות שהקוד מיישם בלי הקורא ובלי מודל השפה."""
from __future__ import annotations

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain

from .conftest import make_client

PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")


def new(client):
    body = client.post("/api/conversations").json()
    return body["conversation_id"], body


def post(client, cid, **payload):
    return client.post(f"/api/conversations/{cid}/messages", json=payload)


def say(client, cid, *texts):
    body = None
    for text in texts:
        r = post(client, cid, text=text)
        assert r.status_code == 200, r.text
        body = r.json()
    return body


def ids(expects, key="choices"):
    return [c["id"] for c in expects[key]]


# ---------- expects בכל שלב ----------

def test_expects_follows_the_state_machine(engine):
    client = make_client(engine)
    cid, body = new(client)
    e = body["expects"]                                            # Q1: קבוצה או תפקיד, ושכר בסיס
    assert [f["field"] for f in e["fields"]] == ["occupation", "group", "base"]
    assert (e["field"], e["type"]) == ("occupation", "choice") and "מנהל/ת מוצר" in ids(e)
    assert ids(e["fields"][1]) == ["א", "ב", "ג", "ד"]

    e = say(client, cid, "מנהלת מוצר")["expects"]                  # רק מה שחסר
    assert (e["field"], e["type"], e["quick_replies"]) == ("base", "number", [])
    assert [f["field"] for f in e["fields"]] == ["base"]
    e = say(client, cid, "18,500")["expects"]                       # Q2
    assert [f["field"] for f in e["fields"]] == ["commissions", "bonus", "pension_percent", "study_fund_percent",
                                                  "credit_points", "children"]
    assert ids(e["fields"][0], "quick_replies") == ["none"]
    e = say(client, cid, "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים")["expects"]
    assert [f["field"] for f in e["fields"]] == ["car", "copay", "options"]          # Q3
    assert (e["field"], e["type"], ids(e, "quick_replies")) == ("car", "car_search", ["no_car"])
    e = say(client, cid, "יש לי קורולה קרוס")["expects"]
    assert [f["field"] for f in e["fields"]] == ["copay", "options"]
    assert (e["field"], ids(e, "quick_replies")) == ("copay", ["none", "unknown"])
    e = say(client, cid, "300")["expects"]
    assert (e["field"], e["type"], e["max"], ids(e, "quick_replies")) == ("options", "multi_choice", 3, ["rank"])
    atto = next(c for c in e["choices"] if c["id"] == "CAR-10")
    assert atto == {"id": "CAR-10", "label": "ATTO 3 (2025)", "group": "ג", "drive": "ELECTRIC"}
    assert "CAR-09" not in ids(e)                                  # הרכב הנוכחי אינו חלופה


def test_expects_for_confirmation_variants_drive_and_waiting(engine):
    client = make_client(engine)
    cid, _ = new(client)
    e = say(client, cid, "מנהלת מוצר", "בערך 18")["expects"]
    assert (e["field"], ids(e)) == ("confirmation", ["yes", "no"])

    cid, _ = new(client)
    e = say(client, cid, PROFILE, "FABIA")["expects"]
    assert (e["field"], ids(e)) == ("car_choice", ["1", "2"]) and "2,400 ₪" in e["choices"][0]["label"]

    cid, _ = new(client)
    e = say(client, cid, PROFILE, "טויוטה מיסטרי")["expects"]
    assert (e["field"], ids(e, "quick_replies")) == ("use_value", ["unknown"])
    e = say(client, cid, "3000")["expects"]
    assert (e["field"], ids(e)) == ("drive", ["PETROL", "HYBRID", "PLUG_IN", "ELECTRIC"])

    cid, _ = new(client)
    body = say(client, cid, PROFILE, "יש לי קורולה", "לא יודעת")
    assert body["stage"] == "WAITING_FOR_REP" and body["expects"]["type"] == "none"
    e = say(client, cid, "אטו 3")["expects"]
    assert (e["field"], ids(e)) == ("clarification", ["mine", "alternative"])
    assert client.get(f"/api/conversations/{cid}").json()["expects"] == e
    assert client.get(f"/api/conversations/{cid}/updates").json()["expects"] == e


# ---------- בחירות מובנות ----------

def test_choices_select_exactly_the_option_without_the_llm(engine):
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))             # llm_first (ברירת המחדל)
    cid, _ = new(client)
    say(client, cid, PROFILE, "יש לי קורולה קרוס", "300")
    before = len(fake.calls)
    r = post(client, cid, choices=["CAR-10"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(fake.calls) == before                              # בלי מודל השפה
    assert [x["label"] for x in body["results"]] == ["ATTO 3 (2025)"]
    assert body["results"][0]["delta_display"] == "661"
    messages = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["content"] for m in messages if m["role"] == "user"][-1] == "ATTO 3 (2025)"


def test_invalid_or_too_many_choices_are_rejected(engine):
    client = make_client(engine)
    cid, _ = new(client)
    say(client, cid, PROFILE, "יש לי קורולה קרוס", "300")
    r = post(client, cid, choices=["CAR-99"])
    assert r.status_code == 422 and r.json()["detail"] == "הבחירה אינה מהאפשרויות שהוצעו"
    r = post(client, cid, choices=["CAR-09"])                      # הרכב הנוכחי — לא הוצע
    assert r.status_code == 422
    r = post(client, cid, choices=["CAR-01", "CAR-02", "CAR-03", "CAR-04"])
    assert r.status_code == 422 and r.json()["detail"] == "אפשר לבחור עד 3 אפשרויות"
    r = post(client, cid, choices=["rank", "CAR-01"])
    assert r.status_code == 422
    assert post(client, cid, text="1", choice="CAR-01").status_code == 422
    assert post(client, cid).status_code == 422
    assert client.get(f"/api/conversations/{cid}").json()["selected"] == []


def test_a_choice_for_another_question_is_rejected(engine):
    client = make_client(engine)
    cid, _ = new(client)
    assert post(client, cid, choice="CAR-10").status_code == 422     # שאלת התפקיד
    body = post(client, cid, choice="מנהל/ת מוצר").json()
    assert body["expects"]["field"] == "base"


def test_quick_replies_and_confirmation_buttons(engine):
    client = make_client(engine)
    cid, _ = new(client)
    body = post(client, cid, choice="מנהל/ת מוצר").json()
    body = say(client, cid, "18")
    assert body["expects"]["field"] == "confirmation"
    body = post(client, cid, choice="yes").json()
    state = client.get(f"/api/conversations/{cid}").json()["profile"]
    assert state["base"] == "18000" and state["approximate_fields"] == []
    assert body["expects"]["field"] == "commissions"
    r = post(client, cid, choice="none")                            # "אין" מוצע לכמה שדות ב-Q2
    assert r.status_code == 422 and r.json()["detail"] == "יש לציין לאיזה שדה הבחירה"
    body = post(client, cid, choice="none", field="commissions").json()
    assert client.get(f"/api/conversations/{cid}").json()["profile"]["commissions"] == "0"
    fields = [f["field"] for f in body["expects"]["fields"]]       # Q2 נענתה בחלקה: מה שנשאר עובר ל-Q3
    assert fields[:3] == ["car", "copay", "options"] and "bonus" in fields


def test_unknown_copay_quick_reply_waits_for_the_rep(engine):
    client = make_client(engine)
    cid, _ = new(client)
    say(client, cid, PROFILE)
    assert post(client, cid, choice="CAR-09").status_code == 422   # ב-Q3 גם חלופה מציעה את CAR-09
    body = post(client, cid, choice="CAR-09", field="car").json()
    assert body["expects"]["field"] == "copay"
    body = post(client, cid, choice="unknown").json()
    assert body["stage"] == "WAITING_FOR_REP"


def test_no_car_quick_reply(engine):
    client = make_client(engine)
    cid, _ = new(client)
    say(client, cid, PROFILE)
    body = post(client, cid, choice="no_car").json()
    assert body["expects"]["field"] == "maintenance"


# ---------- חיפוש רכב ----------

def test_search_puts_the_catalog_first(engine):
    client = make_client(engine)
    found = client.get("/api/catalog/search", params={"q": "קורולה"}).json()
    assert [f["id"] for f in found[:2]] == ["CAR-07", "CAR-09"]
    assert client.get("/api/catalog/search", params={"q": "ק"}).json() == []
    assert client.get("/api/catalog/search").json() == []


def test_search_reaches_the_tax_file_and_a_tax_choice_sets_that_car(engine):
    client = make_client(engine)
    found = client.get("/api/catalog/search", params={"q": "FABIA"}).json()
    assert {f["id"] for f in found} == {"tax:2026:2022:1:00413:00500", "tax:2026:2022:2:00413:00501"}
    style = next(f for f in found if f["id"].endswith("00501"))
    assert style["label"] == "FABIA (2022) NJ34 STYLE" and style["source"] == "tax"

    cid, _ = new(client)
    say(client, cid, PROFILE)
    r = post(client, cid, choice=style["id"])
    assert r.status_code == 200, r.text
    assert r.json()["expects"]["field"] == "copay"
    car = client.get(f"/api/conversations/{cid}").json()["current_car"]
    assert (car["source"], car["label"], car["use_value"]) == ("tax", "FABIA (2022)", "2650")
    messages = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["content"] for m in messages if m["role"] == "user"][-1] == "FABIA (2022) NJ34 STYLE"


def test_an_unknown_tax_id_is_rejected(engine):
    client = make_client(engine)
    cid, _ = new(client)
    say(client, cid, PROFILE)
    assert post(client, cid, choice="tax:2026:2022:1:00413:99999").status_code == 422
    assert post(client, cid, choice="tax:nonsense").status_code == 422
