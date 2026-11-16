"""Today's copay, the approved business rule (16/11). Once today's car is known its copay is a closed question:
1. "זה הסכום שלי" saves the catalog amount (catalog_confirmed) and the flow goes on;
2. another amount ("הסכום שונה", the form, or in words, also after results) is saved and the result recalculated;
3. "לברר מול נציג": the inquiry is opened by the client before the choice is sent (test_routing_2026_10_31); the copay
   stays unknown, no catalog amount is shown as the employee's, and no net is calculated;
4. a typed "לא ידוע": nothing is saved; the first time the ways to go on are given, the second time (same car) it is
   recorded as unknown and the flow goes on — never the same choices over and over.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

PROFILE = ["מנהלת מוצר, שכר בסיס 18,500",
           "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"]
CAR = "יש לי קורולה קרוס"          # COROLLA CROSS (2025), CAR-09, catalog copay 300


def service(sessions, llm=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())


def at_copay(svc):
    turn, token = svc.start()
    cid = turn.conversation_id
    for m in [*PROFILE, CAR]:
        turn = svc.send(cid, token, m)
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and turn.expects["field"] == "copay"
    return cid, token, turn


def current(svc, cid, token):
    return svc.view(cid, token).state.current


# ---------- 1. the catalog amount, confirmed ----------

def test_confirming_the_catalog_amount_saves_it_and_goes_on(sessions):
    svc = service(sessions)
    cid, token, _ = at_copay(svc)
    turn = svc.send_choice(cid, token, ["catalog:CAR-09"], "copay")
    c = current(svc, cid, token)
    assert (c.copay, c.copay_source, c.copay_unknown) == (300, "catalog_confirmed", False)
    assert turn.stage == Stage.CHOOSE_OPTIONS and "רשמתי: השתתפות 300 ₪" in turn.reply
    turn = svc.send_choice(cid, token, ["CAR-10"], "options")
    assert turn.stage == Stage.PRESENTED and turn.results[0].result["delta_display"] == "661"


# ---------- 2. another amount: saved, and a result is recalculated ----------

def test_another_amount_is_asked_exactly_and_saved(sessions):
    svc = service(sessions)
    cid, token, _ = at_copay(svc)
    turn = svc.send_choice(cid, token, ["other"], "copay")
    assert current(svc, cid, token).copay is None and "הסכום המדויק" in turn.reply      # nothing saved yet
    turn = svc.send(cid, token, "350")
    assert current(svc, cid, token).copay == 350 and turn.stage == Stage.CHOOSE_OPTIONS


def test_a_changed_amount_after_results_is_saved_and_recalculated(sessions):
    svc = service(sessions)
    cid, token, _ = at_copay(svc)
    svc.send_choice(cid, token, ["catalog:CAR-09"], "copay")
    first = svc.send_choice(cid, token, ["CAR-10"], "options").results[0].result["delta_display"]
    turn = svc.send(cid, token, "בעצם ההשתתפות שלי 350")                  # in words
    assert current(svc, cid, token).copay == 350 and "350 ₪ במקום 300 ₪" in turn.reply
    in_words = turn.results[0].result["delta_display"]
    assert turn.stage == Stage.PRESENTED and in_words != first
    turn = svc.send_fields(cid, token, {"current_copay": 400}, None, None)   # the form ("הנתונים שלי")
    assert current(svc, cid, token).copay == 400 and turn.stage == Stage.PRESENTED
    assert turn.results[0].result["delta_display"] not in (first, in_words)


# ---------- 3. the representative: no unverified amount as a fact, no net ----------

def test_the_rep_choice_keeps_the_copay_unknown_and_calculates_nothing(sessions):
    svc = service(sessions)
    cid, token, _ = at_copay(svc)
    turn = svc.send_choice(cid, token, ["rep"], "copay")
    c = current(svc, cid, token)
    assert c.copay is None and c.copay_provisional is None and c.copay_unknown
    assert "נציג התחבורה" in turn.reply and "300" not in turn.reply          # no catalog amount as the employee's
    turn = svc.send_choice(cid, token, ["CAR-10"], "options")
    assert not turn.results and turn.stage != Stage.PRESENTED and "300 ₪" not in turn.reply


# ---------- 4. "לא ידוע": the ways to go on, then recorded — never a loop ----------

def test_a_typed_unknown_gives_the_ways_to_go_on_and_saves_nothing(sessions):
    svc = service(sessions)
    cid, token, before = at_copay(svc)
    turn = svc.send(cid, token, "לא יודעת")
    c = current(svc, cid, token)
    assert c.copay is None and not c.copay_unknown and c.copay_provisional is None    # nothing saved
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and turn.expects["field"] == "copay"
    assert "לברר מול נציג" in turn.reply and "בתלוש השכר" in turn.reply and 'שוב "לא ידוע"' in turn.reply
    assert "לא הצלחתי להבין" not in turn.reply and svc.cases.list_open() == []


def test_a_second_unknown_is_recorded_and_the_flow_goes_on(sessions):
    svc = service(sessions)
    cid, token, _ = at_copay(svc)
    first = svc.send(cid, token, "לא יודעת")
    second = svc.send(cid, token, "לא יודעת")
    c = current(svc, cid, token)
    assert c.copay is None and c.copay_unknown                                  # recorded as unknown, no amount
    assert second.stage == Stage.CHOOSE_OPTIONS and second.reply != first.reply
    assert "רשמתי שזה לא ידוע" in second.reply and svc.cases.list_open() == []
    turn = svc.send_choice(cid, token, ["CAR-10"], "options")                  # no net without it, asked again
    assert not turn.results and "ההשתתפות ברכב של היום" in turn.reply
    turn = svc.send(cid, token, "300")
    assert turn.stage == Stage.PRESENTED and turn.results


def test_unknown_never_repeats_the_same_reply(sessions):
    svc = service(sessions)
    cid, token, _ = at_copay(svc)
    replies = [svc.send(cid, token, "לא ידוע").reply for _ in range(4)]
    assert all(a != b for a, b in zip(replies, replies[1:]))
    assert sum("לברר מול נציג" in r for r in replies) == 1                     # the choices are given once
    assert current(svc, cid, token).copay is None


def test_another_car_gets_the_ways_to_go_on_again(sessions):
    svc = service(sessions)
    cid, token, _ = at_copay(svc)
    svc.send(cid, token, "לא יודעת")
    turn = svc.send(cid, token, "בעצם יש לי MG4")
    assert current(svc, cid, token).label == "MG4 (2025)" and turn.expects["field"] == "copay"
    turn = svc.send(cid, token, "לא יודעת")                                    # the first for this car
    assert not current(svc, cid, token).copay_unknown and "לברר מול נציג" in turn.reply


@pytest.mark.parametrize("text", ["אין", "לא", "אין לי"])
def test_a_bare_no_to_the_car_alone_is_asked_never_a_car_and_never_copay_zero(sessions, text):
    """The car is asked alone (16/11): a bare "לא" was matched to LEON (2025) and "אין" was not understood. Before any
    car is known it may mean "no car" or "copay 0": the employee is asked (NO_CAR_OR_ZERO), nothing is saved."""
    svc = service(sessions)
    turn, token = svc.start()
    cid = turn.conversation_id
    for m in PROFILE:
        svc.send(cid, token, m)
    turn = svc.send(cid, token, text)
    c, s = current(svc, cid, token), svc.view(cid, token).state
    assert c.source is None and c.label is None and c.copay is None and not c.has_car
    assert s.pending_ambiguity is not None and "אין לכם רכב צמוד היום" in turn.reply


def test_the_parser_and_the_model_in_one_turn_count_as_one_unknown(sessions):
    """llm_first: the parser reads "לא ידוע" and the model also calls save_profile(current_copay_unknown) in the same
    turn: still the first time — nothing recorded, the ways to go on are given."""
    plain = service(sessions)
    cid, token, _ = at_copay(plain)
    fake = FakeLLM().turn(call("save_profile", current_copay_unknown=True), reply=None)
    svc = service(sessions, LLMChain([fake]))
    turn = svc.send(cid, token, "לא ידוע")
    assert not current(svc, cid, token).copay_unknown and turn.stage == Stage.COLLECT_CURRENT_CAR
