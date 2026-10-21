"""Regressions found while stabilising the conversation (template mode, no Gemini):

  - a salary correction after a result is on screen updates the value AND re-runs the calculation.
  - the manufacturer's name next to a model ("קיה נירו") identifies the car.
  - "ויתור על הרכב" (give up the company car) is recognised as opt-out intent, not a car name.
  - "ילד אחד" inside a long sentence extracts the count (1), not just the presence of children.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

PROFILE = ("מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")


def _svc(sessions):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain())


def _state(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


def test_salary_correction_after_result_recomputes(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    for t in [PROFILE.replace("18500", "15000"), "אין לי רכב", "אין", "אטו 3"]:
        turn = svc.send(turn.conversation_id, token, t)
    assert turn.stage == Stage.PRESENTED and turn.results
    run1 = turn.results[0].run_id
    turn = svc.send(turn.conversation_id, token, "טעיתי, השכר שלי 16,000")
    st = _state(svc, turn, token)
    assert st.profile.base == Decimal("16000")           # the value was updated
    assert st.selected == ["CAR-10"]                      # the chosen car is kept
    assert turn.results and turn.results[0].run_id != run1   # recomputed with the new salary


def test_profile_value_correction_mid_flow(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "קבוצה ג, שכר בסיס 15000")
    turn = svc.send(turn.conversation_id, token, "בעצם פנסיה 7%")   # correction of a field already set? not yet
    # pension was not set yet, so this is a normal save, not a correction
    turn = svc.send(turn.conversation_id, token, "טעיתי, השכר שלי 16,000")
    assert _state(svc, turn, token).profile.base == Decimal("16000")


def test_manufacturer_and_model_identify_the_car(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, PROFILE)
    turn = svc.send(turn.conversation_id, token, "יש לי קיה נירו")
    c = _state(svc, turn, token).current
    assert c.source == "catalog" and c.catalog_code == "CAR-05"


def test_opt_out_intent_is_recognised(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, PROFILE)
    turn = svc.send(turn.conversation_id, token, "יש לי קורולה קרוס, משלמת 300")
    turn = svc.send(turn.conversation_id, token, "ויתור על הרכב")
    assert _state(svc, turn, token).selected == ["OPT_OUT"]
    assert "לא הצלחתי להבין" not in turn.reply


def test_one_child_stated_in_words_in_a_long_sentence(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "קבוצה ג, שכר בסיס 18500")
    turn = svc.send(turn.conversation_id, token,
                    "אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2 נקודות זיכוי, יש לי ילד אחד")
    assert _state(svc, turn, token).profile.children == 1
