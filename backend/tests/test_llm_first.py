"""LLM_MODE=llm_first (ברירת המחדל): כל הודעה עוברת קודם למודל; הקורא — גיבוי ובדיקה צולבת."""
from __future__ import annotations

from app.modules.conversation.llm.fake import FailingLLM, FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, make_client
from .test_parser_first import NOA, talk


def service(sessions, llm, mode="llm_first"):
    return ConversationService(sessions, RulesStore(CONFIG), llm, llm_mode=mode)


def test_llm_first_calls_the_llm_on_every_turn_of_noa(sessions):
    fake = FakeLLM()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                   pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2))
    fake.turn(call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300))
    fake.turn(call("calculate", options=["ATTO 3"]))
    turns, _ = talk(service(sessions, LLMChain([fake])), NOA)
    asked = {sum(1 for m in c.messages if m.role == "user") for c in fake.calls if c.tools}
    assert asked == {1, 2, 3}                                     # כל הודעה — קודם למודל
    assert [t.path for t in turns] == ["llm", "llm", "llm"]
    assert [t.stage for t in turns] == [Stage.COLLECT_CURRENT_CAR, Stage.CHOOSE_OPTIONS, Stage.PRESENTED]
    assert turns[-1].results[0].result["delta_display"] == "661"


def test_llm_first_with_a_failing_llm_falls_back_to_the_parser(sessions):
    turns, _ = talk(service(sessions, LLMChain([FailingLLM()])), NOA)
    assert [t.path for t in turns] == ["fallback", "fallback", "fallback"]
    assert turns[-1].stage == Stage.PRESENTED
    assert turns[-1].results[0].result["delta_display"] == "661"


def test_llm_and_parser_disagree_so_nothing_is_saved_and_the_bot_asks(sessions):
    fake = FakeLLM().turn(call("save_profile", base_salary=19000))
    turns, token = talk(service(sessions, LLMChain([fake])), ["שכר בסיס 18,500"])
    assert fake.calls
    assert svc_base(sessions, turns[-1], token) is None
    assert "קיבלתי שני ערכים שונים לשכר בסיס. מה הערך הנכון?" in turns[-1].reply


def svc_base(sessions, turn, token):
    return service(sessions, LLMChain()).view(turn.conversation_id, token).state.profile.base


def test_llm_and_parser_agree_so_the_value_is_saved(sessions):
    fake = FakeLLM().turn(call("save_profile", base_salary=18500))
    turns, token = talk(service(sessions, LLMChain([fake])), ["שכר בסיס 18,500"])
    assert turns[-1].path == "llm" and svc_base(sessions, turns[-1], token) == 18500


def test_parser_first_keeps_noa_without_llm_calls(sessions):
    fake = FakeLLM()
    turns, _ = talk(service(sessions, LLMChain([fake]), "parser_first"), NOA)
    assert fake.calls == [] and turns[-1].results[0].result["delta_display"] == "661"


def test_health_shows_the_mode(engine):
    assert make_client(engine).get("/api/health").json()["llm_mode"] == "llm_first"
    assert make_client(engine, llm_mode="parser_first").get("/api/health").json()["llm_mode"] == "parser_first"
