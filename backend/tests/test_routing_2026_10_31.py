"""Gemini for language, code for certainty (31/10).

A short answer the parser reads in full ("18000", "5%", "אין", "קבוצה א") is saved by code and answered by the
template: no model call (a bare "18000" cost three Gemini calls in the live session). Anything with language in it
— a question, an uncertainty, a request, a remark — goes to the model. The catalog's copay for today's car is offered
as an unverified amount and saved only when the employee confirms it. "אתה באמת Gemini?" is answered honestly.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain
from app.modules.conversation.templates import ENGINE_OFFLINE
from app.modules.rules.public import load_snapshot

from .conftest import CONFIG, make_client

SNAP = load_snapshot(CONFIG)
COROLLA = next(c for c in SNAP.catalog if c.catalog_code == "CAR-07")
Q2 = {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 5, "study_fund_percent": 0,
      "credit_points": 2.75, "children_under_23": 2}


def start(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()


def model_client(engine):
    fake = FakeLLM()
    return fake, make_client(engine, llm=LLMChain([fake]))


@pytest.mark.parametrize("answers", [
    ["קבוצה א", "18000"],
    ["קבוצה א", "18,000 ₪"],
])
def test_simple_answers_are_saved_by_code_without_a_model_call(engine, answers):
    fake, client = model_client(engine)
    cid, send = start(client)
    for text in answers:
        before = len(fake.calls)
        turn = send({"text": text})
        assert len(fake.calls) == before, text            # no model call for a plain answer
        assert turn["source"] == "template" and "רשמתי" in turn["reply"]
    profile = client.get(f"/api/conversations/{cid}").json()["profile"]
    assert profile["group"] == "א" and profile["base"] == "18000"


def test_a_percent_and_a_bare_none_are_code_too(engine):
    fake, client = model_client(engine)
    cid, send = start(client)
    send({"text": "קבוצה א"})
    send({"text": "18000"})
    before = len(fake.calls)
    send({"text": "אין"})                                  # the salary components asked: 0 for those it may mean
    send({"text": "פנסיה 5%"})                             # labelled: saved wherever it is said
    assert len(fake.calls) == before
    profile = client.get(f"/api/conversations/{cid}").json()["profile"]
    assert profile["commissions"] == "0" and profile["pension_percent"] == "5"


@pytest.mark.parametrize("text", [
    "אני לא בטוחה שהסכום נכון",
    "לא מספיק לכתוב באיזה קבוצה אני?",
    "למה אתה צריך את התפקיד שלי?",
    "נו, אז כמה יישאר לי בנטו?",
    "יש לכם רכב אדום כהה?",
    "תשאל אותי שאלה אחת בכל פעם",
    "איזה רכב הכי משתלם לי?",
    "אתה באמת משתמש בג'מיני?",
    "18000 אבל אני לא בטוחה",
])
def test_language_goes_to_the_model(engine, text):
    fake, client = model_client(engine)
    _, send = start(client)
    send({"text": "קבוצה א"})
    before = len(fake.calls)
    send({"text": text})
    assert len(fake.calls) > before, text


def test_without_the_model_the_engine_question_says_this_reply_is_a_template(engine):
    client = make_client(engine)                           # no model configured
    _, send = start(client)
    reply = send({"text": "אתה באמת משתמש בג'מיני?"})["reply"]
    assert ENGINE_OFFLINE in reply and "Gemini" in reply


# ---------- today's copay: the catalog amount, unverified until confirmed ----------

def at_copay(client):
    cid, send = start(client)
    send({"text": "קבוצה א"})
    send({"fields": Q2})
    turn = send({"text": "קורולה 2026"})
    return cid, send, turn


def test_the_catalog_copay_is_offered_with_three_choices_and_never_saved_by_itself(engine):
    client = make_client(engine)
    cid, _, turn = at_copay(client)
    assert f"היא {int(COROLLA.default_copay)} ₪" in turn["reply"] and "סכום כללי" in turn["reply"]
    assert "עשוי להיות שונה" in turn["reply"] and "בחרו אחת משלוש האפשרויות" in turn["reply"]
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] is None
    copay = next(f for f in turn["expects"]["fields"] if f["field"] == "copay")
    assert copay["closed"] and [c["label"] for c in copay["choices"]] == ["זה הסכום שלי", "הסכום שונה",
                                                                          "לברר מול נציג"]
    assert copay["context"] == {"car": "COROLLA (2026)", "catalog_amount": str(COROLLA.default_copay)}   # the draft


def test_another_amount_is_saved_and_recalculates_later(engine):
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    send({"text": "180"})                                  # "הסכום שונה": the client sends the amount typed
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] == "180"
    first = send({"text": "הכי משתלם"})["results"][0]
    turn = send({"text": "טעיתי, ההשתתפות שלי היום 220"})   # changed later in the conversation
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] == "220"
    again = next(r for r in turn["results"] if r["option_id"] == first["option_id"])
    assert int(again["today"]["net_display"]) == int(first["today"]["net_display"]) - 40   # today pays 40 more
    assert again["alternative"]["net_display"] == first["alternative"]["net_display"]      # the new car: unchanged


def test_asking_the_rep_invents_no_amount_and_calculates_nothing_on_it(engine):
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    before = len(client.app.state.ctx.service.cases.list_open())
    turn = send({"choice": "rep", "field": "copay"})       # sent by the client after the inquiry itself was sent
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["current_car"]["copay"] is None and view["current_car"]["copay_unknown"] is True
    assert turn["results"] == [] and len(client.app.state.ctx.service.cases.list_open()) == before


def test_the_button_confirms_the_catalog_copay(engine):
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    send({"choice": "catalog:CAR-07", "field": "copay"})
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] == str(COROLLA.default_copay)


def test_a_typed_yes_confirms_it_and_another_amount_replaces_it(engine):
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    send({"text": "כן"})
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] == str(COROLLA.default_copay)
    client2 = make_client(engine)
    cid2, send2, _ = at_copay(client2)
    send2({"text": "180"})
    assert client2.get(f"/api/conversations/{cid2}").json()["current_car"]["copay"] == "180"


def test_the_card_s_copay_inquiry_is_accepted_and_only_then_the_copay_waits(engine):
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    question = ("אני מבקש/ת לברר מה סכום ההשתתפות החודשי שלי ברכב הנוכחי (COROLLA (2026)). "
                "האם הסכום שבקטלוג הוא הסכום שחל עליי?")
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay_unknown"] is False   # card opened
    r = client.post(f"/api/conversations/{cid}/rep-question", json={"question": question, "intent": "general"})
    assert r.status_code == 200 and r.json()["status"] == "created"
    send({"choice": "rep", "field": "copay"})                # the client, after the send
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["current_car"]["copay"] is None and view["current_car"]["copay_unknown"] is True


def test_the_rep_choice_says_the_copay_is_with_the_rep_not_on_the_payslip(engine):
    from decimal import Decimal
    from app.modules.conversation.templates import copay_with_rep_text
    client = make_client(engine)
    _, send, _ = at_copay(client)
    reply = send({"choice": "rep", "field": "copay"})["reply"]
    # a catalog car: the calculation goes on with the catalog amount, provisionally, and says so (1/11)
    assert reply.startswith(copay_with_rep_text(Decimal(250))) and "בתלוש" not in reply


def test_controlled_today_copay_200_then_300_changes_only_today(engine, tmp_path):
    """Same employee, same car; only today's copay changes (31/10). Every card is recalculated: today's net drops by
    exactly 100, each alternative's own net is unchanged, and each change against today grows by 100."""
    client = make_client(engine)
    cid, send, _ = at_copay(client)
    send({"text": "השתתפות 200"})
    before = {r["option_id"]: r for r in send({"text": "הכי משתלם"})["results"]}
    turn = send({"text": "טעיתי, ההשתתפות שלי היום 300"})
    after = {r["option_id"]: r for r in turn["results"]}
    assert set(after) == set(before) and len(after) >= 2                   # every card recalculated
    lines = []
    for oid, b in before.items():
        a = after[oid]
        assert int(a["today"]["net_display"]) == int(b["today"]["net_display"]) - 100
        assert a["alternative"]["net_display"] == b["alternative"]["net_display"]
        assert int(a["delta_display"]) == int(b["delta_display"]) + 100
        lines.append(f"{b['label']}: today {b['today']['net_display']} -> {a['today']['net_display']}, "
                     f"alt {b['alternative']['net_display']} -> {a['alternative']['net_display']}, "
                     f"delta {b['delta_display']} -> {a['delta_display']}")
    (tmp_path / "copay_200_300.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    assert client.get(f"/api/conversations/{cid}").json()["today_copay"] == "300"


def test_a_percent_or_a_fraction_at_the_car_question_is_never_a_rounded_copay(engine):
    """Found in the acceptance run (31/10): with the car-and-copay question on screen, "5%" (meant for the pension)
    was asked back as "5 ₪ or 5,000 ₪?", and "2.75" as "3 ₪ or 2,750 ₪?"."""
    client = make_client(engine)
    cid, send = start(client)
    send({"text": "קבוצה א"})
    send({"text": "18000"})
    send({"text": "אין"})                                  # Q2 leftovers wait; the car question is on screen
    reply = send({"text": "5%"})["reply"]
    assert "5 ₪" not in reply and "5,000 ₪" not in reply
    assert client.get(f"/api/conversations/{cid}").json()["current_car"]["copay"] is None


def test_a_held_amount_with_a_fraction_is_asked_back_exactly():
    from decimal import Decimal
    from app.modules.conversation.templates import confirm_value_text
    assert confirm_value_text("current_copay", Decimal("2.75"), scale=True) == "התכוונתם ל-2.75 ₪ או ל-2,750 ₪?"
