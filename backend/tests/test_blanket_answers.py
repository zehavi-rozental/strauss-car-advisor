"""Blanket / ambiguous answers to the grouped Q2 question are never spread silently across the six
fields. Policy (both the parser-only path and the LLM path):

  - one Q2 field on screen            → the answer is that field's.
  - a named field ("לא יודע עמלות")   → only that field.
  - "אף אחד מהם" / "לא יודע כלום"      → an explicit "all of them": every field recorded unknown.
  - a bare "לא ידוע" on the group      → ambiguous: nothing saved, switch to one field at a time.
  - "אין לי כלום" (sweeping zero)      → ambiguous: nothing zeroed, one field at a time.
  - "הכל 5" (one value for all)        → ambiguous: nothing filled, one field at a time.
  - repeating "לא יודע" once the fields are already unknown → acknowledged, never a demand to answer again.
"""
from __future__ import annotations

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

Q2 = ["commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children"]
PROFILE_Q1 = "מנהלת מוצר, שכר בסיס 18,500"


def _state(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


def _tmpl(sessions):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, PROFILE_Q1)
    return svc, turn, token


# ---------- parser-only (template) path ----------

def test_bare_unknown_on_the_group_is_not_spread(sessions):
    svc, turn, token = _tmpl(sessions)
    turn = svc.send(turn.conversation_id, token, "לא ידוע לי")
    st = _state(svc, turn, token)
    assert st.profile.unknown_fields == []
    assert st.pace == "one_at_a_time" and turn.stage == Stage.COLLECT_PROFILE
    assert "עמלות" in turn.reply and "?" in turn.reply       # asks the first field, one at a time


def test_explicit_all_unknown_marks_every_field(sessions):
    svc, turn, token = _tmpl(sessions)
    turn = svc.send(turn.conversation_id, token, "אני לא יודע אף אחד מהם")
    st = _state(svc, turn, token)
    assert set(st.profile.unknown_fields) == set(Q2)
    assert st.pace == "normal" and turn.stage == Stage.COLLECT_CURRENT_CAR


def test_named_unknown_marks_only_that_field(sessions):
    svc, turn, token = _tmpl(sessions)
    turn = svc.send(turn.conversation_id, token, "לא יודעת כמה נקודות זיכוי יש לי")
    st = _state(svc, turn, token)
    assert st.profile.unknown_fields == ["credit_points"] and st.pace == "normal"


def test_nothing_at_all_is_not_zeroed(sessions):
    svc, turn, token = _tmpl(sessions)
    turn = svc.send(turn.conversation_id, token, "אין לי כלום")
    st = _state(svc, turn, token)
    assert st.profile.commissions is None and st.profile.bonus is None
    assert st.pace == "one_at_a_time"


def test_one_value_for_all_is_not_spread(sessions):
    svc, turn, token = _tmpl(sessions)
    turn = svc.send(turn.conversation_id, token, "הכל 5")
    st = _state(svc, turn, token)
    assert st.profile.commissions is None and st.profile.bonus is None
    assert st.pace == "one_at_a_time"


def test_after_the_switch_unknown_answers_one_field(sessions):
    svc, turn, token = _tmpl(sessions)
    svc.send(turn.conversation_id, token, "לא ידוע לי")          # → one at a time, asks commissions
    turn = svc.send(turn.conversation_id, token, "לא יודע")       # answers commissions only
    assert _state(svc, turn, token).profile.unknown_fields == ["commissions"]


def test_repeating_unknown_when_all_are_unknown_is_acknowledged(sessions):
    svc, turn, token = _tmpl(sessions)
    turn = svc.send(turn.conversation_id, token, "אני לא יודע אף אחד מהם")
    turn = svc.send(turn.conversation_id, token, "עדיין לא יודע אף אחד מהם")
    assert "לא הצלחתי להבין" not in turn.reply and turn.results == []


# ---------- LLM path: the model over-applies, the guard refuses ----------

def _llm(sessions, fake):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]), llm_mode="llm_first")
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500), reply="רשמתי.")
    turn = svc.send(turn.conversation_id, token, PROFILE_Q1)
    return svc, turn, token


def test_llm_over_applying_unknown_is_refused(sessions):
    fake = FakeLLM()
    svc, turn, token = _llm(sessions, fake)
    fake.turn(call("save_profile", unknown_fields=list(Q2)), reply="רשמתי שלא ידוע.")
    turn = svc.send(turn.conversation_id, token, "לא ידוע לי")
    st = _state(svc, turn, token)
    assert st.profile.unknown_fields == [] and st.pace == "one_at_a_time"


def test_llm_spreading_one_value_is_refused(sessions):
    fake = FakeLLM()
    svc, turn, token = _llm(sessions, fake)
    fake.turn(call("save_profile", commissions=5, bonus=5, study_fund_percent=5), reply="רשמתי 5.")
    turn = svc.send(turn.conversation_id, token, "הכל 5")
    st = _state(svc, turn, token)
    assert st.profile.commissions is None and st.profile.bonus is None and st.pace == "one_at_a_time"


def test_llm_explicit_all_unknown_is_kept(sessions):
    """When the employee really does say "none of them" the model's all-unknown call stands."""
    fake = FakeLLM()
    svc, turn, token = _llm(sessions, fake)
    fake.turn(call("save_profile", unknown_fields=list(Q2)), reply="רשמתי שלא ידוע.")
    turn = svc.send(turn.conversation_id, token, "אני לא יודע אף אחד מהם")
    assert set(_state(svc, turn, token).profile.unknown_fields) == set(Q2)
