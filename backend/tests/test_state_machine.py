"""מכונת המצבים, שאלות חוסמות לנציג, זיהוי הרכב ורשימת החלופות."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, RepReason, Stage
from app.modules.conversation.stage import ConversationState
from app.modules.conversation.tools import available_options
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

# שאלות מקובצות (כמו בתרחיש הרשמי): Q1 תפקיד ושכר בסיס, Q2 רכיבי שכר, הפרשות וילדים
Q2_ANSWER = "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, {children} ילדים"


def service(sessions, llm=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())


def open_questions(svc):
    return svc.cases.list_open()


def talk(svc, messages, start=None):
    turn, token = start or svc.start()
    for m in messages:
        turn = svc.send(turn.conversation_id, token, m)
    return turn, token


def profile(occupation="מנהלת מוצר", children="2"):
    return [f"{occupation}, שכר בסיס 18,500", Q2_ANSWER.format(children=children)]


def test_a_bare_none_to_q2_fills_only_the_fields_where_zero_is_reasonable(sessions):
    """"אין" לשאלה המקובצת: אפס בעמלות, בונוס, קרן השתלמות וילדים; פנסיה ונקודות זיכוי נשאלות שוב."""
    svc = service(sessions)
    turn, token = talk(svc, ["מנהלת מוצר", "18,500", "לא ברור לי"])
    assert turn.stage == Stage.COLLECT_PROFILE
    assert "לא הצלחתי להבין" in turn.reply and "עמלות" in turn.reply
    turn, _ = talk(svc, ["אין"], start=(turn, token))
    p = svc.view(turn.conversation_id, token).state.profile
    assert (p.commissions, p.bonus, p.study_fund_percent, p.children) == (0, 0, 0, 0)
    assert p.pension_percent is None and p.credit_points is None
    assert turn.reply.startswith("רשמתי: אין עמלות, אין בונוס, אין קרן השתלמות, אין ילדים.")
    assert "פנסיה" in turn.reply and "נקודות זיכוי" in turn.reply


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
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["יש לי COROLLA CROSS", "לא יודעת"])
    assert turn.stage == Stage.WAITING_FOR_REP and turn.pending_rep == RepReason.CURRENT_COPAY
    [q] = open_questions(svc)
    assert (q.missing_field.value, q.mode.value, q.answer_type.value) == ("CURRENT_COPAY", "REQUIRED", "MONEY")
    assert q.context == {"group": "ג", "car_label": "COROLLA CROSS (2025)", "catalog_code": "CAR-09",
                         "suggested_amount": "300"}
    assert "שאלתי את נציג התחבורה מה ההשתתפות הנוכחית שלך ב-COROLLA CROSS (2025)" in turn.reply
    state = svc.view(turn.conversation_id, token).state
    assert state.pending_rep.question_ids == [q.id]
    # בזמן ההמתנה: רכב אחר בלי סימן תיקון — שואלים במקום לנחש; אין התקדמות ואין שאלה נוספת לנציג
    turn = svc.send(turn.conversation_id, token, "ATTO 3")
    assert turn.stage == Stage.WAITING_FOR_REP and turn.path == "waiting"
    assert turn.reply.startswith("לא בטוח שהבנתי: ATTO 3 (2025) הוא הרכב שלך היום")
    assert len(open_questions(svc)) == 1
    turn = svc.send(turn.conversation_id, token, "נו?")
    assert turn.reply == "שאלתי את נציג התחבורה ואעדכן כשתגיע תשובה."


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
    svc = service(sessions)
    turn, _ = talk(svc, profile() + ["ZOOMOBILE X", "לא יודע"])
    assert turn.stage == Stage.WAITING_FOR_REP and turn.pending_rep == RepReason.CURRENT_USE_VALUE
    [q] = open_questions(svc)
    assert q.missing_field.value == "CURRENT_USE_VALUE" and q.context == {"group": "ג", "car_label": "ZOOMOBILE X"}


def test_unknown_occupation_asks_group_and_opt_out_goes_to_rep(sessions):
    svc = service(sessions)
    turn, token = talk(svc, ["אסטרונאוטית"])
    assert "לא מצאתי את \"אסטרונאוטית\" בטבלת העיסוקים. זה התפקיד שלך?" in turn.reply   # לא נשמר בלי אישור
    turn, _ = talk(svc, ["כן"], start=(turn, token))
    assert "קבוצת הזכאות" in turn.reply
    turn, _ = talk(svc, ["ג", "18,500", Q2_ANSWER.format(children="2"), "יש לי COROLLA CROSS", "300"], start=(turn, token))
    assert turn.stage == Stage.CHOOSE_OPTIONS and "ויתור על רכב" in turn.reply
    turn, _ = talk(svc, ["ויתור"], start=(turn, token))
    assert turn.stage == Stage.WAITING_FOR_REP and turn.pending_rep == RepReason.OCCUPATION_OPT_OUT
    [q] = open_questions(svc)
    assert (q.missing_field.value, q.mode.value, q.answer_type.value) == ("OCCUPATION_OPT_OUT", "REQUIRED", "BOOLEAN")
    assert q.question_text == "האם עובד בעיסוק הזה רשאי לוותר על רכב?"
    assert q.context == {"group": "ג", "occupation_text": "אסטרונאוטית"}
    assert turn.results[0].result["missing_fields"] == ["OCCUPATION_OPT_OUT"]
    assert "שאלתי את נציג התחבורה אם בעיסוק שלך מותר לוותר על הרכב" in turn.reply


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
    turn, _ = talk(svc, profile() + ["יש לי קורולה קרוס", "300", "טוסון ואטו"])
    assert turn.stage == Stage.PRESENTED
    assert [r.label for r in turn.results] == ["ATTO 3 (2025)", "TUCSON (2025)"]


def test_fallback_names_match_whole_words_only(sessions):
    """"אני רוצה" מכיל את האותיות של "נירו" — אסור שזה ייבחר."""
    turn, _ = talk(service(sessions), profile() + ["יש לי קורולה קרוס", "300", "אני רוצה לבדוק את BYD אטו 3 החשמלי"])
    assert [r.label for r in turn.results] == ["ATTO 3 (2025)"]


# ---------- הרכב הנוכחי ממשפט חופשי ----------

def test_long_sentence_without_a_known_car_asks_for_the_model_again(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["יש לי רכב חברה ישן מאוד ואני בכלל לא בטוחה מה השם שלו"])
    assert turn.stage == Stage.COLLECT_CURRENT_CAR
    assert "לא זיהיתי את דגם הרכב" in turn.reply and "במאגר" not in turn.reply
    assert svc.view(turn.conversation_id, token).state.current.query is None   # לא חיפשנו משפט שלם


def test_known_name_with_year_and_copay_in_one_sentence(sessions):
    """שנה שונה משנת הקטלוג → מאגר רשות המסים לאותה שנה; ההשתתפות נשמרת מאותה הודעה."""
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["יש לי קורולה קרוס 2023 ואני משתתף ב-250 בחודש"])
    assert turn.stage == Stage.CHOOSE_OPTIONS
    current = svc.view(turn.conversation_id, token).state.current
    assert (current.source, current.query, current.reg_year) == ("tax", "COROLLA CROSS", 2023)
    assert (current.use_value, current.copay) == (Decimal("3910"), Decimal("250"))


def test_no_copay_phrase_is_zero(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["קורולה קרוס, אין לי השתתפות"])
    assert turn.stage == Stage.CHOOSE_OPTIONS
    assert svc.view(turn.conversation_id, token).state.current.copay == 0


@pytest.mark.parametrize("text, expected", [
    ("ואני משתתפת היום ב-300 ש\"ח בחודש", Decimal(300)),
    ("השתתפות עצמית 1,250", Decimal(1250)),
    ("אני משתתף 250", Decimal(250)),
    ("אין השתתפות", Decimal(0)),
    ("לא משתתפת בכלל", Decimal(0)),
    ("משתתפת, ויש לי 2 ילדים", None),          # לא חוצים פסיק לשדה אחר
    ("יש לי קורולה קרוס", None),
])
def test_parse_copay(text, expected):
    from app.modules.conversation.parsing import parse_copay
    assert parse_copay(text) == expected


# ---------- אישור הרכב שזוהה, ותיקון שלו ----------

CONFIRM_TAIL = "אם זה לא הרכב שלך, כתבו את הדגם המדויק."


def test_identified_car_is_confirmed_with_label_group_and_drive(sessions):
    turn, _ = talk(service(sessions), profile() + ["יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"])
    assert turn.reply.startswith(f"זיהיתי: COROLLA CROSS (2025), קבוצה ג, היברידי. {CONFIRM_TAIL}")
    assert turn.stage == Stage.CHOOSE_OPTIONS and "איזה רכב מעניין אותך" in turn.reply   # ואז השאלה הבאה


def test_tax_file_car_confirmation_has_no_group(sessions):
    turn, _ = talk(service(sessions), profile() + ["קורולה קרוס 2023"])
    assert turn.reply.startswith(f"זיהיתי: COROLLA CROSS (2023), היברידי. {CONFIRM_TAIL}")


def test_correction_at_the_copay_question_rematches_the_car(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["יש לי קורולה"])
    assert turn.reply.startswith("זיהיתי: COROLLA (2026), קבוצה ב, בנזין.")
    assert svc.view(turn.conversation_id, token).state.current.catalog_code == "CAR-07"

    turn = svc.send(turn.conversation_id, token, "לא, זו קורולה קרוס")
    current = svc.view(turn.conversation_id, token).state.current
    assert current.catalog_code == "CAR-09" and current.copay is None
    assert turn.reply.startswith("זיהיתי: COROLLA CROSS (2025), קבוצה ג, היברידי.")
    assert "מה ההשתתפות החודשית שלך היום" in turn.reply and turn.stage == Stage.COLLECT_CURRENT_CAR


def test_plain_number_at_the_copay_question_is_still_the_copay(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["יש לי קורולה", "300"])
    assert turn.stage == Stage.CHOOSE_OPTIONS
    current = svc.view(turn.conversation_id, token).state.current
    assert (current.catalog_code, current.copay) == ("CAR-07", Decimal(300))
    assert not turn.reply.startswith("זיהיתי")          # הרכב לא השתנה בתור הזה


def test_correction_keeps_the_copay_already_given(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["ZOOMOBILE X"])          # לא נמצא → שאלת שווי שימוש
    turn = svc.send(turn.conversation_id, token, "סליחה, זו קורולה קרוס")
    assert svc.view(turn.conversation_id, token).state.current.catalog_code == "CAR-09"


def test_not_this_car_resets_and_asks_for_the_model(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile() + ["יש לי קורולה"])
    turn = svc.send(turn.conversation_id, token, "זה לא הרכב שלי")
    current = svc.view(turn.conversation_id, token).state.current
    assert current.catalog_code is None and current.source is None
    assert "מה הרכב היום (דגם ושנת רישום)" in turn.reply


# ---------- ניסוח ניטרלי מגדרית ----------

def test_employee_facing_templates_are_gender_neutral():
    """אף תבנית לא מניחה מגדר: לא צורות יחיד ממוגדרות, לא צורות לוכסן (את/ה)."""
    import inspect
    import re

    from app.modules.conversation import templates
    source = inspect.getsource(templates)
    strings = " ".join(re.findall(r'"([^"\n]*[א-ת][^"\n]*)"', source))
    for gendered in ("שתרצי", "תרצה", "אתה", "את/ה", "/ת", "/י", "לא יודע\\", "יודעת", "בחרי", "כתבי"):
        assert gendered not in strings, gendered


def test_unknown_reply_suggested_by_the_bot_is_understood(sessions):
    """הבוט מציע לכתוב "לא ידוע" — והקורא מבין אותו כ"לא יודע/ת"."""
    svc = service(sessions)
    turn, _ = talk(svc, profile() + ["יש לי COROLLA CROSS", "לא ידוע"])
    assert turn.stage == Stage.WAITING_FOR_REP and turn.pending_rep == RepReason.CURRENT_COPAY
