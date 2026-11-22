"""Acceptance finding #2, 22/11: after a confirmed payslip the contributions question still offered the profile form
("או למלא בטופס"). The approved rule: the bot offers a payslip upload once on its own initiative; after a payslip was
confirmed, salary and contribution questions are asked in text only (no upload, no form, no link) unless the employee
asks for a new payslip. Before any payslip nothing changes. Templates only, no model, no real Gemini.
"""
from __future__ import annotations

import dataclasses

from app.modules.conversation.stage import ConversationState
from app.modules.conversation.templates import call_to_action

from .conftest import PAYSLIP_ON, make_client

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


def test_after_a_confirmed_payslip_the_contributions_question_is_text_only(engine):
    say = start(engine)
    say({"fields": NO_CONTRIBUTIONS, "source": "payslip"})
    turn = say("ב")
    assert "הפרשות" in turn["reply"]                                 # the contributions question is the one asked
    assert "בטופס" not in turn["reply"] and "להעלות תלוש" not in turn["reply"]
    assert link(turn) is None and kinds(turn) == []                  # no form, no upload, no other link


def test_a_new_payslip_asked_for_still_brings_the_upload_back(engine):
    say = start(engine)
    say({"fields": NO_CONTRIBUTIONS, "source": "payslip"})
    say("ב")
    again = say("אני רוצה להעלות תלוש חדש")
    assert kinds(again) == ["payslip_upload"] and link(again) == "payslip_upload"


def _state(**kw) -> ConversationState:
    state = ConversationState()
    for k, v in kw.items():
        setattr(state, k, v)
    return state


def test_the_q2_call_to_action_after_a_confirmed_payslip_names_no_form_and_no_upload(snapshot):
    text, offered = call_to_action(_state(payslip_reviewed=True), snapshot, CONTRIBUTIONS)
    assert offered is None and "בטופס" not in text and "להעלות תלוש" not in text


def test_before_any_payslip_nothing_changes(snapshot):
    text, offered = call_to_action(_state(), snapshot, CONTRIBUTIONS)               # the upload is offered, as before
    assert offered == "payslip_upload" and "או להעלות תלוש" in text
    no_upload = dataclasses.replace(snapshot, extensions=snapshot.extensions.model_copy(
        update={"payslip_upload_enabled": False, "payslip_upload_url": ""}))
    text, offered = call_to_action(_state(), no_upload, CONTRIBUTIONS)              # no uploader: the form, as before
    assert offered == "profile_form" and "או למלא בטופס" in text
