"""A repeated payslip upload, and car properties the catalog cannot verify (19/11; templates and a fake model, fake mail,
no real Gemini).

1. The upload is on the screen before a payslip is confirmed, hidden after the confirmation, and back when the employee
   asks for a new payslip or to update the salary details; a new payslip shows each change before it replaces anything,
   and a confirmed change recalculates the results.
2. Root cause: only a QUESTION about a car property ("יש בצבע אדום?") reached the representative path; a REQUEST ("אני
   רוצה רכב בצבע אדום") was read as a failed answer, so nothing was kept and "אין לך איך לשאול נציג על זה?" opened an
   empty inquiry (or an older question). Now a request for a property the catalog cannot verify (faq.attribute_terms) is
   the same as asking about it: said as unverified, the representative offered, the follow-up sends exactly it, once.
"""
from __future__ import annotations

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain
from app.modules.conversation.templates import ATTRIBUTE_UNVERIFIED, PAYSLIP_REPLACE_OFFER

from .conftest import PAYSLIP_ON, make_client

TOKEN = "test-rep-token"
REP = {"X-Rep-Token": TOKEN}
PROFILE = {"base_salary": 18500, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
           "credit_points": 2.75, "children_under_23": 2}
OPTIONS_ASK = "איזה רכבים להשוות"


def start(engine, llm=None):
    client = make_client(engine, llm=LLMChain([llm]) if llm is not None else None, rep_access_token=TOKEN, **PAYSLIP_ON)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def view():
        return client.get(f"/api/conversations/{cid}").json()
    return client, cid, say, view


def kinds(turn):
    return [a["kind"] for a in turn["expects"].get("actions") or []]


def link(turn):
    return (turn["expects"].get("link") or {}).get("kind")


def ask_rep(turn):
    return next((f for f in turn["expects"]["fields"] if f["field"] == "ask_rep"), None)


def at_the_cars(say):
    say("קבוצה ג")
    say({"fields": PROFILE, "source": "payslip"})
    say("אין לי רכב")
    return say("אין")                                     # no maintenance allowance: the alternatives are asked


# ---------- 1. the repeated payslip ----------

def test_the_upload_is_hidden_after_a_confirmed_payslip_and_back_on_request(engine):
    _, _, say, view = start(engine)
    assert kinds(say("קבוצה ג")) == ["payslip_upload"]                         # before: clear and available
    confirmed = say({"fields": PROFILE, "source": "payslip"})
    assert kinds(confirmed) == [] and view()["expects"]["actions"] == []        # after the confirmation: hidden
    again = say("אני רוצה להעלות תלוש חדש")
    assert kinds(again) == ["payslip_upload"] and link(again) == "payslip_upload"
    assert PAYSLIP_REPLACE_OFFER.split(".")[0] in again["reply"]
    assert "כל שינוי" in again["reply"] and "אחרי שתאשרו" in again["reply"]   # nothing replaced before approval
    assert view()["profile"]["base"] == "18500"                                # the request itself changes nothing


def test_a_message_about_a_new_payslip_or_updating_the_salary_brings_it_back(engine):
    for text in ("מעלה תלוש חדש", "אני רוצה לעדכן את נתוני השכר"):
        _, _, say, _ = start(engine)
        say("קבוצה ג")
        say({"fields": PROFILE, "source": "payslip"})
        turn = say(text)
        assert kinds(turn) == ["payslip_upload"], (text, turn["reply"])
    assert link(turn) == "correction_form"                                     # "הנתונים שלי" / correction stays


def test_a_new_payslip_after_results_replaces_on_confirmation_and_recalculates(engine):
    _, _, say, view = start(engine)
    at_the_cars(say)
    first = say("אטו 3")["results"][0]
    say("אני רוצה להעלות תלוש חדש")
    turn = say({"fields": {"base_salary": 20000}, "source": "payslip"})        # the reviewed, confirmed change
    assert "20,000 ₪ במקום 18,500 ₪" in turn["reply"]
    v = view()
    assert v["profile"]["base"] == "20000" and v["results_stale"] is False
    after = v["results"][0]
    assert after["result"]["today"]["net_display"] != first["today"]["net_display"]   # recalculated
    assert v["expects"]["actions"] == []                                       # confirmed: hidden again


# ---------- 2. car properties the catalog cannot verify ----------

def test_a_colour_request_is_unverified_and_offers_the_representative_not_the_cars_question(engine):
    _, _, say, view = start(engine)
    at_the_cars(say)
    turn = say("אני רוצה רכב בצבע אדום")
    assert ATTRIBUTE_UNVERIFIED in turn["reply"]
    assert OPTIONS_ASK not in turn["reply"]                                    # the way to handle it first
    assert ask_rep(turn)["question"] == "אני רוצה רכב בצבע אדום"              # the draft is the request itself
    assert "תלוי" not in turn["reply"] and "אין אפשרות" not in turn["reply"]   # no stock claim, no "impossible"


def test_a_sunroof_question_and_request_take_the_same_path(engine):
    for text in ("יש רכב עם גג נפתח?", "אני רוצה רכב עם גג נפתח"):
        _, _, say, _ = start(engine)
        at_the_cars(say)
        turn = say(text)
        assert ask_rep(turn) is not None and ask_rep(turn)["question"] == text, (text, turn["reply"])


def test_asking_for_the_representative_after_it_drafts_the_original_request(engine):
    _, _, say, view = start(engine)
    at_the_cars(say)
    say("יש רכב עם גג נפתח?")                       # an older question
    say("אטו 3")                                    # something else in between
    say("אני רוצה רכב בצבע אדום")                   # the latest request
    turn = say("אין לך איך לשאול נציג על זה?")
    assert "אני רוצה רכב בצבע אדום" in turn["reply"] and "גג נפתח" not in turn["reply"]
    assert ask_rep(turn)["question"] == "אני רוצה רכב בצבע אדום"
    assert "כתבו מה תרצו לשאול" not in turn["reply"]                           # never asked to write it again


def test_sent_only_on_confirmation_once_and_the_cars_go_on_while_waiting(engine):
    client, cid, say, view = start(engine)
    at_the_cars(say)
    say("אני רוצה רכב בצבע אדום")
    assert client.get("/api/rep/questions?status=OPEN", headers=REP).json() == []   # nothing sent by itself
    body = {"question": "אני רוצה רכב בצבע אדום", "intent": "general"}
    first = client.post(f"/api/conversations/{cid}/rep-question", json=body)
    assert first.status_code == 200 and first.json()["status"] == "created", first.text
    second = client.post(f"/api/conversations/{cid}/rep-question", json=body)
    assert second.json()["status"] == "exists"                                  # never twice
    assert len(client.get("/api/rep/questions?status=OPEN", headers=REP).json()) == 1
    turn = say("אטו 3")                                                         # the cars go on meanwhile
    assert turn["results"] and view()["stage"] == "PRESENTED"
    assert len(client.get("/api/rep/questions?status=OPEN", headers=REP).json()) == 1   # the inquiry is kept


def test_cancelling_sends_nothing_and_comes_back_to_the_cars(engine):
    client, _, say, view = start(engine)
    at_the_cars(say)
    say("אני רוצה רכב בצבע אדום")
    turn = say({"choice": "skip", "field": "ask_rep"})                         # "להמשיך בלי"
    assert OPTIONS_ASK in turn["reply"] and ask_rep(turn) is None
    assert client.get("/api/rep/questions?status=OPEN", headers=REP).json() == []


def test_with_the_model_the_property_is_never_answered_with_an_invented_claim(engine):
    fake = FakeLLM()
    _, _, say, _ = start(engine, fake)
    at_the_cars(say)
    fake.turn(reply="הצבעים תלויים במלאי, ואין אפשרות לבחור צבע מראש.")      # what the model said on 8897
    turn = say("אני רוצה רכב בצבע אדום")
    assert "תלויים במלאי" not in turn["reply"] and "אין אפשרות" not in turn["reply"]
    assert ATTRIBUTE_UNVERIFIED in turn["reply"] and ask_rep(turn) is not None
