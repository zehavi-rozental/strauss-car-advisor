"""Acceptance finding 22/11: after a confirmed payslip, the contributions question still said "או להעלות תלוש" and showed
the "להעלות תלוש שכר" button (the Q2 call to action checked only the configuration). The one rule is
actions.payslip_allowed: once a payslip was confirmed the upload is not offered again, unless the employee asks for a
new payslip. Templates only, no model, no real Gemini.
"""
from __future__ import annotations

from app.modules.conversation.stage import ConversationState
from app.modules.conversation.templates import call_to_action

from .conftest import PAYSLIP_ON, make_client

# what the synthetic payslip of the manual acceptance gave: no pension and no study fund on it
NO_CONTRIBUTIONS = {"base_salary": 18500, "commissions": 0, "bonus": 0, "credit_points": 2.75, "children_under_23": 2}
CONTRIBUTIONS = ["pension_percent", "study_fund_percent"]


def start(engine):
    client = make_client(engine, **PAYSLIP_ON)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()
    return say


def kinds(turn):
    return [a["kind"] for a in turn["expects"].get("actions") or []]


def link(turn):
    return (turn["expects"].get("link") or {}).get("kind")


# ---------- 1. the acceptance case, along the flow ----------

def test_no_upload_offer_in_the_contributions_question_after_a_confirmed_payslip(engine):
    say = start(engine)
    say({"fields": NO_CONTRIBUTIONS, "source": "payslip"})           # confirmed before the group, as in acceptance
    turn = say("ב")
    assert "הפרשות" in turn["reply"]                                 # the contributions question is the one asked
    assert "להעלות תלוש" not in turn["reply"]
    assert link(turn) != "payslip_upload" and kinds(turn) == []


# ---------- 2. asking for a new payslip still brings the upload back ----------

def test_asking_for_a_new_payslip_after_that_brings_the_upload_back(engine):
    say = start(engine)
    say({"fields": NO_CONTRIBUTIONS, "source": "payslip"})
    say("ב")
    again = say("אני רוצה להעלות תלוש חדש")
    assert kinds(again) == ["payslip_upload"] and link(again) == "payslip_upload"


# ---------- 3. the Q2 call to action itself ----------

def _state(**kw) -> ConversationState:
    state = ConversationState()
    for k, v in kw.items():
        setattr(state, k, v)
    return state


def test_the_q2_call_to_action_offers_the_upload_only_when_allowed(snapshot):
    offer = {"payslip_offered": True, "payslip_offer_now": True}                       # 23/11: the bot's one offer
    before = call_to_action(_state(**offer), snapshot, CONTRIBUTIONS)                # no payslip yet: offered
    assert before[1] == "payslip_upload" and "או להעלות תלוש" in before[0]

    reviewed = call_to_action(_state(payslip_reviewed=True), snapshot, CONTRIBUTIONS)
    assert reviewed[1] != "payslip_upload" and "להעלות תלוש" not in reviewed[0]

    asked = call_to_action(_state(payslip_reviewed=True, payslip_requested=True), snapshot, CONTRIBUTIONS)
    assert asked[1] == "payslip_upload"                                              # asked for: offered again
