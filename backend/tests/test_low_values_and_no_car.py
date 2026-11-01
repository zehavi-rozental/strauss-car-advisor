"""(1) An implausibly low amount ("עמלות 15 ₪") is held for "15 ₪ or 15,000 ₪?" for every monetary field, never
reported as recorded, one held value at a time; (2) "אין רכב" said again is acknowledged first and the
maintenance question is re-explained, never a field the employee already answered."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.guards import check
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.llm.port import LLMError
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.conversation.stage import ConversationState
from app.modules.conversation.tools import ToolContext, run_tool
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CONFIG, NOA_WORDS

SNAP = load_snapshot(CONFIG)
HALF = "מנהלת מוצר, שכר בסיס 18,500, פנסיה 5%"
NOA = ("מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
       "2.75 נקודות זיכוי, 2 ילדים")


def service(sessions, fake=None):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]) if fake else LLMChain())


def talk(svc, *messages):
    turn, token = svc.start()
    for m in messages:
        turn = svc.send(turn.conversation_id, token, m)
    return turn, token, svc.view(turn.conversation_id, token).state


# ====================== 1. low amounts ======================

@pytest.mark.parametrize("message", ["עמלות 15 ₪", "עמלות 15", "עמלות 15 שקל"])
def test_a_low_commissions_figure_is_held_and_not_reported_as_recorded(sessions, message):
    turn, _, s = talk(service(sessions), HALF, message)
    assert s.profile.commissions is None
    assert s.pending_confirmation.field == "commissions" and s.pending_confirmation.scale
    assert "התכוונתם ל-15 ₪ או ל-15,000 ₪?" in turn.reply
    assert "רשמתי: עמלות" not in turn.reply and "עמלות 15" not in turn.reply.replace("או ל-15,000", "")
    assert [c["id"] for c in turn.expects["choices"]] == ["small", "thousands"]


class _Down:
    """A provider that is down (the outage path: the parser reads what it can)."""
    name = "down"

    def complete(self, *args, **kwargs):
        raise LLMError("down")


@pytest.mark.parametrize("chain", [LLMChain, lambda: LLMChain([_Down()])], ids=["templates", "llm-down"])
def test_a_partly_read_sentence_never_reports_the_held_amount_as_recorded(sessions, chain):
    """The live report: "עמלות 15 ₪" looked saved because the reply said "רשמתי: עמלות 15 ₪, …" while also asking
    15 or 15,000. The parser's notice was written before the check; only what was saved is acknowledged now."""
    svc = ConversationService(sessions, RulesStore(CONFIG), chain())
    turn, token, s = talk(svc, HALF, "קרן השתלמות 2.5% ועוד עמלות 15 ₪ כל חודש")
    assert s.profile.commissions is None and s.profile.study_fund_percent == Decimal("2.5")
    assert "רשמתי: קרן השתלמות 2.5%." in turn.reply
    assert "רשמתי: עמלות" not in turn.reply and "עמלות 15" not in turn.reply
    assert "התכוונתם ל-15 ₪ או ל-15,000 ₪?" in turn.reply


@pytest.mark.parametrize("message", ["אין בונוס, עמלות 15 ₪", "בונוס 2,000 ועמלות 15 ₪"])
def test_a_complete_sentence_with_a_saved_and_a_held_value_acknowledges_only_the_saved_one(sessions, message):
    turn, _, s = talk(service(sessions), HALF, message)
    assert s.profile.commissions is None and s.profile.bonus in (0, 2000)
    assert "רשמתי: עמלות" not in turn.reply and "עמלות 15" not in turn.reply


def test_the_small_amount_the_employee_confirms_is_saved_and_not_asked_again(sessions):
    svc = service(sessions)
    turn, token, _ = talk(svc, HALF, "עמלות 15 ₪")
    turn = svc.send_choice(turn.conversation_id, token, ["small"], field="confirmation")
    s = svc.view(turn.conversation_id, token).state
    assert s.profile.commissions == 15 and s.pending_confirmation is None      # no loop on the same question
    assert "15,000" not in turn.reply and "רשמתי: עמלות 15 ₪" in turn.reply


def test_the_thousands_amount_the_employee_confirms_is_saved(sessions):
    svc = service(sessions)
    turn, token, _ = talk(svc, HALF, "עמלות 15 ₪")
    turn = svc.send_choice(turn.conversation_id, token, ["thousands"], field="confirmation")
    assert svc.view(turn.conversation_id, token).state.profile.commissions == 15000


def test_the_amount_can_also_be_typed_as_the_answer(sessions):
    svc = service(sessions)
    _, _, s = talk(svc, HALF, "עמלות 15 ₪", "15,000")
    assert s.profile.commissions == 15000


def test_two_held_values_in_one_message_keep_the_first_question_and_the_second_is_asked_again(sessions):
    svc = service(sessions)
    turn, token, s = talk(svc, HALF, "עמלות 15 ₪, בונוס 4 ₪")
    assert s.pending_confirmation.field == "commissions"                       # not replaced by the bonus
    assert s.profile.commissions is None and s.profile.bonus is None
    assert "רשמתי" not in turn.reply and "15 ₪ או ל-15,000 ₪" in turn.reply
    turn = svc.send_choice(turn.conversation_id, token, ["thousands"], field="confirmation")
    s = svc.view(turn.conversation_id, token).state
    assert s.profile.commissions == 15000 and s.profile.bonus is None and "בונוס" in turn.reply   # asked again


def test_the_model_saving_a_low_commissions_figure_is_held_too(sessions):
    fake = FakeLLM().turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500, pension_percent=5))
    svc = service(sessions, fake)
    turn, token = svc.start()
    svc.send(turn.conversation_id, token, HALF)
    fake.turn(call("save_profile", commissions=15))
    turn = svc.send(turn.conversation_id, token, "עמלות 15 ₪")
    s = svc.view(turn.conversation_id, token).state
    assert s.profile.commissions is None and s.pending_confirmation.scale
    assert "רשמתי: עמלות" not in turn.reply


@pytest.mark.parametrize("field, value", [
    ("commissions", 15), ("bonus", 15), ("current_use_value", 4), ("current_maintenance", 4), ("current_copay", 3),
])
def test_every_monetary_field_asks_before_taking_a_shorthand_amount(field, value):
    ctx = ToolContext(snapshot=SNAP, find_vehicle=lambda *_: None, state=ConversationState())
    ctx.state.current.query, ctx.state.current.source = "x", "stated"
    result = run_tool(ctx, "save_profile", {field: value})
    assert result["ok"] and not result["saved"]
    p = ctx.state.pending_confirmation
    assert (p.field, p.value, p.scale) == (field, Decimal(value), True)


@pytest.mark.parametrize("field, value", [
    ("commissions", 100), ("bonus", 2000), ("current_use_value", 100), ("current_copay", 10), ("current_copay", 0),
    ("commissions", 0),
])
def test_ordinary_and_zero_amounts_are_saved_without_the_question(field, value):
    ctx = ToolContext(snapshot=SNAP, find_vehicle=lambda *_: None, state=ConversationState())
    ctx.state.current.query, ctx.state.current.source = "x", "stated"
    result = run_tool(ctx, "save_profile", {field: value})
    assert result["saved"] == [field] and ctx.state.pending_confirmation is None


@pytest.mark.parametrize("args, held", [
    ({"pension_percent": 0}, "pension_percent"),            # a rare value: asked "לאשר / לתקן"
    ({"credit_points": 0}, "credit_points"),
    ({"children_under_23": 9}, "children_under_23"),
])
def test_percentages_and_counts_use_the_unusual_value_confirmation_instead(args, held):
    ctx = ToolContext(snapshot=SNAP, find_vehicle=lambda *_: None, state=ConversationState())
    run_tool(ctx, "save_profile", args)
    assert ctx.state.pending_confirmation.field == held and ctx.state.pending_confirmation.unusual


@pytest.mark.parametrize("args", [{"pension_percent": 34}, {"study_fund_percent": 10}, {"credit_points": 225},
                                  {"children_under_23": 16}])
def test_percentages_and_counts_outside_their_range_are_rejected(args):
    ctx = ToolContext(snapshot=SNAP, find_vehicle=lambda *_: None, state=ConversationState())
    result = run_tool(ctx, "save_profile", args)
    assert result["saved"] == [] and ctx.range_problems


# ====================== 2. "אין רכב" said again ======================

def test_no_car_is_acknowledged_when_said(sessions):
    turn, _, s = talk(service(sessions), NOA, "אין רכב")
    assert s.current.source == "none" and turn.reply.startswith("רשמתי: אין לכם רכב צמוד היום.")
    assert "אחזקת רכב" in turn.reply


def test_no_car_said_again_is_acknowledged_first_and_the_maintenance_question_is_re_explained(sessions):
    turn, _, s = talk(service(sessions), NOA, "אין רכב", "כתבתי שאין לי רכב")
    assert turn.reply.startswith("רשמתי: אין לכם רכב צמוד היום.")             # acknowledged first
    assert "אחזקת רכב היא תשלום חודשי בתלוש למי שאין לו רכב צמוד" in turn.reply    # the FAQ explanation
    assert "במילים פשוטות: כמה אחזקת רכב מופיעה בתלוש שלכם בכל חודש." in turn.reply   # the question, simply
    assert "לא הצלחתי להבין" not in turn.reply
    assert not any(w in turn.reply for w in ("עמלות", "בונוס", "פנסיה", "קרן השתלמות", "ילדים"))   # nothing stale
    assert (s.current.source, s.current.maintenance, s.stage) == ("none", None, Stage.COLLECT_CURRENT_CAR)


def test_the_maintenance_answer_after_the_repeat_completes_the_flow(sessions):
    svc = service(sessions)
    turn, token, _ = talk(svc, NOA, "אין רכב", "כתבתי שאין לי רכב")
    turn = svc.send(turn.conversation_id, token, "1,500")
    s = svc.view(turn.conversation_id, token).state
    assert s.current.maintenance == 1500 and s.stage == Stage.CHOOSE_OPTIONS


def test_the_model_cannot_go_back_to_a_field_the_employee_already_answered(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                   pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2), reply=None)
    svc.send(turn.conversation_id, token, NOA_WORDS)
    turn = svc.send(turn.conversation_id, token, "אין רכב")
    stale = "כמה עמלות יש לכם בחודש?"                           # commissions were answered ("בלי עמלות")
    fake.turn(reply=stale, retry=stale)
    turn = svc.send(turn.conversation_id, token, "כתבתי שאין לי רכב")
    assert turn.source == "template" and "stale" in turn.say
    assert turn.reply.startswith("רשמתי: אין לכם רכב צמוד היום.") and "עמלות" not in turn.reply
    assert "אחזקת רכב" in turn.reply


def test_no_car_said_early_by_the_model_is_acknowledged(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500, pension_percent=5), reply=None)
    svc.send(turn.conversation_id, token, "מנהלת מוצר, שכר שמונה עשר וחצי אלף, פנסיה חמישה אחוז")
    # the parser does not read "אין רכב" while the salary question is on screen: the model does (not a simple answer)
    fake.turn(call("match_current_car", no_car=True), reply=None)
    turn = svc.send(turn.conversation_id, token, "אין רכב")
    s = svc.view(turn.conversation_id, token).state
    assert s.current.source == "none" and "רשמתי: אין לכם רכב צמוד היום." in turn.reply


def test_stale_guard_flags_a_question_about_a_saved_field_but_not_an_acknowledgement():
    kwargs = dict(snapshot=SNAP, allowed={Decimal(0)}, tools_called=(), fields=["maintenance"], offer_rep=False,
                  waiting_answer=False, conflicts=[])
    ask = "כתבו את סכום האחזקה מהתלוש."
    assert any(v.kind == "stale" for v in check(f"כמה עמלות יש לכם בחודש?\n{ask}", closed=["commissions"], **kwargs).violations)
    assert any(v.kind == "stale" for v in check(f"כמה ילדים יש לכם?\n{ask}", closed=["children"], **kwargs).violations)
    ok = check(f"רשמתי: אין עמלות.\nכמה אחזקת רכב מופיעה בתלוש?\n{ask}", closed=["commissions"], **kwargs)
    assert not any(v.kind == "stale" for v in ok.violations)
    assert not any(v.kind == "stale" for v in check(f"כמה עמלות יש לכם?\n{ask}", **kwargs).violations)
