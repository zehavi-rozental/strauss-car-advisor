"""מכונת המצבים, שאלות חוסמות לנציג, זיהוי הרכב ורשימת החלופות."""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, RepReason, Stage
from app.modules.conversation.stage import ConversationState
from app.modules.conversation.tools import available_options
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, RecordingRep

PROFILE_AFTER_OCCUPATION = ["18,500", "אין", "אין", "6", "2.5", "2.75"]


def service(sessions, llm=None, rep=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain(), rep)


def talk(svc, messages, start=None):
    turn, token = start or svc.start()
    for m in messages:
        turn = svc.send(turn.conversation_id, token, m)
    return turn, token


def profile(occupation="מנהלת מוצר", children="2"):
    return [occupation, *PROFILE_AFTER_OCCUPATION, children]


def test_commissions_must_be_answered_explicitly(sessions):
    svc = service(sessions)
    turn, _ = talk(svc, ["מנהלת מוצר", "18,500", "לא ברור לי"])
    assert turn.stage == Stage.COLLECT_PROFILE
    assert "לא הצלחתי להבין" in turn.reply and "עמלות" in turn.reply
    turn, _ = talk(svc, ["אין"], start=(turn, _))
    assert "בונוס" in turn.reply


def test_llm_without_commissions_stays_in_profile(sessions):
    fake = FakeLLM().turn(call("save_profile", occupation="מנהל מוצר", base_salary=18500, pension_percent=6,
                               study_fund_percent=0, credit_points=2.25, children_under_23=0), reply=None)
    turn, _ = talk(service(sessions, LLMChain([fake])), ["..."])
    assert turn.stage == Stage.COLLECT_PROFILE
    assert "עמלות" in turn.reply   # הניסוח נכשל → תבנית


def test_disallowed_tool_is_ignored(sessions):
    fake = FakeLLM().turn(call("calculate", options=["ATTO 3"]), reply=None)
    turn, _ = talk(service(sessions, LLMChain([fake])), ["תחשב לי ATTO 3"])
    assert turn.stage == Stage.COLLECT_PROFILE and turn.results == []


def test_unknown_copay_opens_rep_question(sessions):
    rep = RecordingRep()
    svc = service(sessions, rep=rep)
    turn, token = talk(svc, profile() + ["COROLLA CROSS", "לא יודעת"])
    assert turn.stage == Stage.WAITING_FOR_REP and turn.pending_rep == RepReason.CURRENT_COPAY
    [(reason, context)] = rep.questions
    assert reason == RepReason.CURRENT_COPAY
    assert context == {"group": "ג", "car": "COROLLA CROSS (2025)", "suggested_amount": "300"}
    # בזמן ההמתנה: אין התקדמות ואין שאלה נוספת לנציג
    turn = svc.send(turn.conversation_id, token, "ATTO 3")
    assert turn.stage == Stage.WAITING_FOR_REP and "נציג" in turn.reply and len(rep.questions) == 1


def test_car_not_found_asks_payslip_use_value(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["ZOOMOBILE X"])
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and "שווי השימוש" in turn.reply
    turn, _ = talk(svc, ["3,500", "היברידי", "250"], start=(turn, token))
    assert turn.stage == Stage.CHOOSE_OPTIONS
    turn, _ = talk(svc, ["ATTO 3"], start=(turn, token))
    [r] = turn.results
    assert r.result["status"] == "OK" and r.result["today"]["use_value"] == "3500"


def test_car_not_found_and_use_value_unknown_opens_rep_question(sessions):
    rep = RecordingRep()
    turn, _ = talk(service(sessions, rep=rep), profile() + ["ZOOMOBILE X", "לא יודע"])
    assert turn.stage == Stage.WAITING_FOR_REP and turn.pending_rep == RepReason.CURRENT_USE_VALUE
    assert rep.questions[0][1] == {"group": "ג", "car": "ZOOMOBILE X", "suggested_amount": None}


def test_unknown_occupation_asks_group_and_opt_out_goes_to_rep(sessions):
    rep = RecordingRep()
    svc = service(sessions, rep=rep)
    turn, token = talk(svc, ["אסטרונאוטית"])
    assert "קבוצת הזכאות" in turn.reply
    turn, _ = talk(svc, ["ג", *PROFILE_AFTER_OCCUPATION, "2", "COROLLA CROSS", "300"], start=(turn, token))
    assert turn.stage == Stage.CHOOSE_OPTIONS and "ויתור על רכב" in turn.reply
    turn, _ = talk(svc, ["ויתור"], start=(turn, token))
    assert turn.stage == Stage.WAITING_FOR_REP and turn.pending_rep == RepReason.OCCUPATION_OPT_OUT
    assert rep.questions[0][0] == RepReason.OCCUPATION_OPT_OUT
    assert turn.results[0].result["missing_fields"] == ["OCCUPATION_OPT_OUT"]


def test_registration_year_mismatch_uses_tax_file(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["COROLLA CROSS 2023"])
    view = svc.view(turn.conversation_id, token)
    assert view.state.current.source == "tax"
    assert view.state.current.use_value == Decimal("3910") and view.state.current.reg_year == 2023


def test_several_tax_matches_let_the_employee_choose(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["FABIA"])
    assert "1. FABIA (2022) NJ32 AMBITION" in turn.reply and "2. FABIA (2022) NJ34 STYLE" in turn.reply
    turn, _ = talk(svc, ["2"], start=(turn, token))
    assert svc.view(turn.conversation_id, token).state.current.use_value == Decimal("2650")


def _options(snapshot, children):
    state = ConversationState()
    state.profile.group, state.profile.occupation, state.profile.children = "ב", "מפתח/ת תוכנה", children
    return {o.id for o in available_options(snapshot, state)}


def test_family_track_options_need_four_children(snapshot):
    assert {"CAR-19", "CAR-20"} <= _options(snapshot, 4)
    assert not {"CAR-19", "CAR-20"} & _options(snapshot, 3)
    assert "CAR-09" not in _options(snapshot, 4)   # קבוצה ג גבוהה מ-ב


def test_mandatory_car_has_no_opt_out(snapshot):
    state = ConversationState()
    state.profile.group, state.profile.occupation = "ב", "נציג/ת מכירות שטח"
    assert "OPT_OUT" not in {o.id for o in available_options(snapshot, state)}


def test_fallback_option_selection_by_hebrew_names(sessions):
    svc = service(sessions)
    turn, _ = talk(svc, profile() + ["קורולה קרוס", "300", "טוסון ואטו"])
    assert turn.stage == Stage.PRESENTED
    assert [r.label for r in turn.results] == ["ATTO 3 (2025)", "TUCSON (2025)"]


def test_fallback_names_match_whole_words_only(sessions):
    """"אני רוצה" מכיל את האותיות של "נירו" — אסור שזה ייבחר."""
    turn, _ = talk(service(sessions), profile() + ["קורולה קרוס", "300", "אני רוצה לבדוק את BYD אטו 3 החשמלי"])
    assert [r.label for r in turn.results] == ["ATTO 3 (2025)"]
