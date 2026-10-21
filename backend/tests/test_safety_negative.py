"""Failure-seeking / safety tests. Each one tries to make the system do the WRONG thing and asserts
it does not: a value put on the wrong field, a stale result shown after an input changed, a blanket
answer spread across fields, data leaking between conversations, an unapproved amount shown as final,
and a 9-digit id never echoed back.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

FULL = ("מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
        "2.75 נקודות זיכוי, 2 ילדים")


def _svc(sessions, llm=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain(), llm_mode="llm_first" if llm else "parser_first")


def _state(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


# ---------- a bare value is never remapped to a field that was not asked ----------

def test_bare_value_is_not_remapped_to_a_wrong_field(sessions):
    """The pension question is on screen; the model wrongly returns the number as base_salary. The guard
    refuses it — a bare value answers only the field being asked."""
    fake = FakeLLM()
    svc = _svc(sessions, LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(call("save_profile", eligibility_group="ג", base_salary=18500), reply="רשמתי.")
    turn = svc.send(turn.conversation_id, token, "קבוצה ג, שכר בסיס 18500")
    # walk to a single pension question
    fake.turn(call("save_profile", commissions=0, bonus=0), reply="רשמתי.")
    turn = svc.send(turn.conversation_id, token, "אין עמלות ואין בונוס")
    before = _state(svc, turn, token).profile.base
    # the pension question is on screen; the model mis-assigns "6" to base_salary
    fake.turn(call("save_profile", base_salary=6), reply="רשמתי.")
    turn = svc.send(turn.conversation_id, token, "6")
    after = _state(svc, turn, token).profile
    assert after.base == before          # base was NOT overwritten by a bare value meant for another field


# ---------- a changed input invalidates the old result (never stale) ----------

def test_a_changed_value_invalidates_the_previous_result(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    for t in [FULL, "אין לי רכב", "אין", "אטו 3"]:
        turn = svc.send(turn.conversation_id, token, t)
    assert turn.stage == Stage.PRESENTED
    run1 = turn.results[0].run_id
    turn = svc.send(turn.conversation_id, token, "טעיתי, השכר שלי 16,000")
    # the old run is gone; a fresh calculation replaced it
    assert turn.results and turn.results[0].run_id != run1
    assert _state(svc, turn, token).profile.base == Decimal("16000")


# ---------- a blanket answer never spreads (defended at the model layer too) ----------

def test_model_zero_spread_is_refused(sessions):
    fake = FakeLLM()
    svc = _svc(sessions, LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500), reply="רשמתי.")
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר, שכר בסיס 18500")
    # the model zeroes several fields for a sweeping "אין לי כלום"
    fake.turn(call("save_profile", commissions=0, bonus=0, study_fund_percent=0), reply="רשמתי אפס.")
    turn = svc.send(turn.conversation_id, token, "אין לי כלום")
    p = _state(svc, turn, token).profile
    assert p.commissions is None and p.bonus is None and p.study_fund_percent is None


# ---------- no leakage between conversations ----------

def test_two_conversations_do_not_share_state(sessions):
    svc = _svc(sessions)
    a, atok = svc.start()
    b, btok = svc.start()
    svc.send(a.conversation_id, atok, "קבוצה ג, שכר בסיס 18500")
    svc.send(b.conversation_id, btok, "קבוצה ב, שכר בסיס 9000")
    pa = _state(svc, a, atok).profile
    pb = _state(svc, b, btok).profile
    assert (pa.group, pa.base) == ("ג", Decimal("18500"))
    assert (pb.group, pb.base) == ("ב", Decimal("9000"))


# ---------- an unknown copay is never shown as a final net (waits for the rep) ----------

def test_unknown_copay_does_not_produce_a_final_net(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    for t in [FULL, "יש לי קורולה קרוס", "לא יודע"]:    # copay unknown -> a rep question
        turn = svc.send(turn.conversation_id, token, t)
    assert turn.stage == Stage.WAITING_FOR_REP and turn.results == []


# ---------- a 9-digit id number is never echoed back ----------

def test_id_number_is_not_echoed(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "קבוצה ג, שכר בסיס 18500, ת\"ז 123456789")
    assert "123456789" not in turn.reply
