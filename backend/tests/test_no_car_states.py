"""The three distinct no-car situations, kept apart (deterministic, template mode):

  1. employee WITH a company car                      -> CarState (use value, copay)
  2. employee with NO car and NO maintenance allowance -> source="none", maintenance=0 (confirmed by "אין")
  3. employee with NO car who DOES get an allowance     -> source="none", maintenance=<amount>
  plus "אבדוק בהמשך" -> maintenance unknown, never assumed 0, no net shown.

Requirements enforced here: never assume maintenance=0 silently; "לא" (אין) is a confirmed 0; a deferred /
unknown allowance does not block choosing alternatives but does block a numeric net; the opt-out alternative
is a separate concept driven by the occupations table, not by this question.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

# a full salary profile so the flow reaches the car question deterministically
PROFILE = ("מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")


def service(sessions):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain())


def talk(svc, texts):
    turn, token = svc.start()
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
    return turn, token


def car(svc, turn, token):
    return svc.view(turn.conversation_id, token).state.current


# ---------- state 2: no car, no allowance ("אין") -> confirmed 0 ----------

def test_no_car_no_allowance_is_confirmed_zero(sessions):
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE, "אין לי רכב", "אין"])
    c = car(svc, turn, token)
    assert c.source == "none"
    assert c.maintenance == 0 and c.maintenance_unknown is False      # a confirmed 0, not an assumption
    assert turn.stage == Stage.CHOOSE_OPTIONS                          # proceeds to choosing alternatives


# ---------- state 3: no car, receives an allowance (amount) ----------

def test_no_car_with_allowance_amount(sessions):
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE, "אין לי רכב", "1500"])
    c = car(svc, turn, token)
    assert c.source == "none" and c.maintenance == Decimal("1500")
    assert turn.stage == Stage.CHOOSE_OPTIONS


# ---------- "אבדוק בהמשך": unknown, never 0, does not block options, blocks the net ----------

def test_no_car_allowance_unknown_does_not_block_options_but_blocks_net(sessions):
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE, "אין לי רכב", "לא ידוע"])
    c = car(svc, turn, token)
    assert c.source == "none"
    assert c.maintenance is None                                       # unknown, NOT assumed 0
    assert "maintenance" in svc.view(turn.conversation_id, token).state.deferred_fields
    assert turn.stage == Stage.CHOOSE_OPTIONS                          # alternatives are still choosable
    # pick an alternative: the deferred allowance comes back before any net is shown (never an assumed 0)
    turn = svc.send(turn.conversation_id, token, "אטו 3")
    assert turn.results == []                                          # no net on an unverified baseline
    assert "אחזקת רכב" in turn.reply                                   # the allowance question returns


# ---------- the maintenance question is the clear 3-way, not "how much is on your payslip" ----------

def test_maintenance_question_is_a_three_way_choice(sessions):
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE, "אין לי רכב"])
    reply = turn.reply
    # it asks whether they receive an allowance, and offers amount / none / later — it does NOT presume one
    assert "אחזקת רכב" in reply
    assert "אין" in reply and ("לא ידוע" in reply or "בהמשך" in reply)


# ---------- state 1 unaffected: an employee WITH a car still works ----------

def test_employee_with_car_unaffected(sessions):
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE, "יש לי קורולה קרוס, משלמת 300", "אטו 3"])
    assert turn.stage == Stage.PRESENTED and turn.results
    assert turn.results[0].result["status"] == "OK"                    # a real net, as before


def test_group_and_no_car_in_one_natural_sentence(sessions):
    """[integration] Template mode (no Gemini): a single natural sentence that states BOTH the eligibility
    group AND 'no company car' is parsed as both facts — the bug from the local port-10000 session."""
    for msg in ["אני בקבוצה ב ואין לי רכב היום", "קבוצה ב אין לי רכב",
                "אני בקבוצה ג ואין לי רכב חברה",
                "אני בקבוצה ב ואין ליר רכב היום"]:   # last one has a light typo ("ליר") — tolerated
        svc = service(sessions)
        turn, token = svc.start()
        turn = svc.send(turn.conversation_id, token, msg)
        c = svc.view(turn.conversation_id, token).state
        assert c.profile.group in ("ב", "ג"), f"group not saved for: {msg!r}"
        assert c.current.source == "none", f"no-car not saved for: {msg!r}"


# ---------- deferring the maintenance question continues, then it comes back before the net ----------

def test_no_car_defer_maintenance_comes_back(sessions):
    svc = service(sessions)
    # "אבדוק בהמשך" defers the allowance; the employee can still pick a car, but the allowance question
    # comes back before any net. Answering it then ("אין" = no allowance -> 0) completes the calculation.
    turn, token = talk(svc, [PROFILE, "אין לי רכב", "לא ידוע", "אטו 3"])
    assert turn.results == [] and "אחזקת רכב" in turn.reply   # brought back, no net yet
    turn = svc.send(turn.conversation_id, token, "אין")      # no allowance after all -> confirmed 0
    c = car(svc, turn, token)
    assert c.maintenance == 0 and c.maintenance_unknown is False
    assert turn.stage == Stage.PRESENTED and turn.results[0].result["status"] == "OK"
