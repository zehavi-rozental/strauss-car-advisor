"""Focused fixes (20/10): field binding, plausibility, catalog grounding, car ambiguity, year coverage,
pace preference, single request, greeting, links. Each test checks the saved state (the tool-call layer)
and the visible reply."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.agent import asked_field, bare_value
from app.modules.conversation.guards import check
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.conversation.stage import ConversationState
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CONFIG

NO_PENSION = "מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"
NO_COMMISSIONS = "מנהלת מוצר, שכר בסיס 18,500, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"
PROFILE = ("מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")


def service(sessions, fake=None):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]) if fake else LLMChain())


def start(svc, *texts):
    turn, token = svc.start()
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
    return turn, token


def state(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


# ---------- 1. field binding ----------

def test_bare_34_after_the_pension_question_cannot_become_the_copay(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = start(svc, NO_PENSION)
    assert "פנסיה" in turn.reply and state(svc, turn, token).asked_field == "pension_percent"
    fake.turn(call("save_profile", current_copay=34))          # the model files "34" under the wrong field
    turn = svc.send(turn.conversation_id, token, "34")
    s = state(svc, turn, token)
    assert s.current.copay is None and s.profile.pension_percent is None     # nothing saved, nothing remapped
    assert "34% גבוה מהטווח שהמערכת מאפשרת לפנסיה. כמה אחוז יורד לכם בפועל?" in turn.reply
    assert "רשמתי" not in turn.reply


def test_labelled_values_are_saved_together_even_with_a_field_on_screen(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = start(svc, NO_PENSION)
    fake.turn(call("save_profile", pension_percent=6, current_copay=300))
    turn = svc.send(turn.conversation_id, token, "פנסיה 6%, ומשלמת 300 על הרכב")
    assert state(svc, turn, token).profile.pension_percent == 6


def test_asked_field_binds_only_a_single_missing_field():
    s = ConversationState()
    assert asked_field(s, "רשמתי: פנסיה 6%.\n\nכמה נקודות זיכוי יש לכם?") == "credit_points"
    assert asked_field(s, "יש לכם עמלות או בונוס?") is None              # two fields: no binding
    assert bare_value("34") and bare_value("6%") and bare_value("בערך 300 ₪") and not bare_value("פנסיה 6%")


# ---------- 2. plausibility ----------

def test_small_commissions_are_asked_3_or_3000_and_never_multiplied_silently(sessions):
    svc = service(sessions)
    turn, token = start(svc, NO_COMMISSIONS)
    assert "עמלות" in turn.reply
    turn = svc.send(turn.conversation_id, token, "3")
    assert "התכוונתם ל-3 ₪ או ל-3,000 ₪?" in turn.reply
    assert [c["id"] for c in turn.expects["choices"]] == ["small", "thousands"]
    assert state(svc, turn, token).profile.commissions is None               # held, not saved
    turn = svc.send_choice(turn.conversation_id, token, ["thousands"], field="confirmation")
    assert state(svc, turn, token).profile.commissions == 3000 and "3,000" in turn.reply


def test_ranges_come_from_extensions_yaml():
    ranges = load_snapshot(CONFIG).extensions.value_ranges
    assert ranges["pension_percent"] == (0, 7) and ranges["study_fund_percent"] == (0, Decimal("2.5"))
    assert ranges["credit_points"] == (0, 12) and ranges["children_under_23"] == (0, 15)


# ---------- 3. catalog grounding ----------

def test_catalog_question_is_answered_from_the_catalog_not_the_representative(sessions):
    svc = service(sessions)
    turn, token = start(svc, "יש לכם קורולה?")
    assert "COROLLA (2026), קבוצה ב, בנזין" in turn.reply and "COROLLA CROSS (2025), קבוצה ג, היברידי" in turn.reply
    assert "נציג" not in turn.reply and state(svc, turn, token).unanswered_question is None


def test_a_model_not_in_the_catalog_is_said_plainly(sessions):
    svc = service(sessions)
    turn, _ = start(svc, "יש לכם דגם פולקסווגן גולף?")
    assert "לא מצאתי את הדגם הזה בקטלוג הרכבים הזמין במערכת." in turn.reply


def test_catalog_tag_is_valid_only_on_a_sentence_naming_a_catalog_car():
    snap = load_snapshot(CONFIG)
    kwargs = dict(snapshot=snap, allowed={Decimal(2026), Decimal(3)}, tools_called=(), fields=[],
                  offer_rep=False, waiting_answer=False, conflicts=[])
    ok = check("COROLLA (2026) נמצאת בקבוצה ב. [[catalog]]\nמה קבוצת הזכאות שלכם?", **kwargs)
    bad = check("זה רכב מצוין עם בגאז' גדול. [[catalog]]\nמה קבוצת הזכאות שלכם?", **kwargs)
    assert not [v for v in ok.violations if v.kind == "source"]
    assert [v for v in bad.violations if v.kind == "source"]


def test_the_catalog_is_in_the_cached_prompt_prefix(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    start(svc, "קבוצה ג ושכר בסיס 18,000")      # read in full but not a bare answer: the model writes the reply
    system = fake.calls[0].messages[0].content
    catalog = system.index("CATALOG (the Strauss")
    assert system.index("FAQ (the only source") < catalog < system.index("the rule-based reader")
    assert "COROLLA CROSS" in system and "family track" in system


# ---------- 4. car ambiguity and year coverage ----------

def test_korolla_matches_two_catalog_cars_and_asks_with_buttons(sessions):
    svc = service(sessions)
    turn, token = start(svc, PROFILE, "יש לי קורולה")
    assert [c["label"] for c in turn.expects["choices"]][:2] == ["COROLLA (2026)", "COROLLA CROSS (2025)"]
    assert "COROLLA (2026) או COROLLA CROSS (2025)" in turn.reply
    turn = svc.send_choice(turn.conversation_id, token, ["2"], field="car_choice")
    s = state(svc, turn, token)
    assert (s.current.source, s.current.catalog_code) == ("catalog", "CAR-09")


def test_a_year_outside_the_tax_file_states_its_coverage_and_never_invents_a_value(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = start(svc, PROFILE)
    fake.turn(call("match_current_car", model="FABIA", registration_year=2000))
    turn = svc.send(turn.conversation_id, token, "יש לי פביה 2000")
    s = state(svc, turn, token)
    assert s.current.use_value is None and s.current.coverage == "2022–2023"     # the test tax file
    assert "משנות 2022–2023" in turn.reply and "שווי השימוש" in turn.reply


# ---------- 5. pace ----------

def test_pace_preference_survives_stage_transitions(sessions):
    svc = service(sessions)
    turn, token = start(svc, "שאלה אחת כל פעם בבקשה")
    assert turn.reply.startswith("בסדר, אשאל שאלה אחת בכל פעם.")
    assert state(svc, turn, token).pace == "one_at_a_time"
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר, שכר בסיס 18,500")
    ask = turn.reply.split("\n\n")[-2]                         # the question (the last line is the CTA)
    assert "עמלות" in ask and "פנסיה" not in ask and "בונוס" not in ask
    # the built-in uploader (if enabled) may be offered on Q2; the manual profile-form link is not
    assert (turn.expects["link"] is None or turn.expects["link"]["kind"] == "payslip_upload")
    turn = svc.send(turn.conversation_id, token, "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
                                                 "2.75 נקודות זיכוי, 2 ילדים")
    assert turn.stage == Stage.COLLECT_CURRENT_CAR
    assert "כמה משלמים" not in turn.reply                       # the car first, the copay on its own
    assert state(svc, turn, token).pace == "one_at_a_time"


def test_multi_field_question_is_a_guard_violation_at_one_at_a_time_pace():
    snap = load_snapshot(CONFIG)
    kwargs = dict(snapshot=snap, allowed=set(), tools_called=(), fields=["pension_percent", "credit_points"],
                  offer_rep=False, waiting_answer=False, conflicts=[])
    text = "כמה אחוז יורד לפנסיה וכמה נקודות זיכוי יש לכם?"
    assert any(v.kind == "multi" for v in check(text, one_field=True, **kwargs).violations)
    assert not any(v.kind == "multi" for v in check(text, **kwargs).violations)


# ---------- 6-8. one request, greeting, links ----------

def test_greeting_invites_group_car_and_interest_salary_later(sessions):
    """The warmer opening invites the eligibility group, the current car and a car of interest, and says
    salary comes later. It must still name the eligibility group and make clear salary is deferred-able."""
    turn, _ = service(sessions).start()
    r = turn.reply
    assert "קבוצת הזכאות" in r                     # the Strauss-required first datum
    assert "רכב חברה" in r or "רכב" in r           # invites the current car
    assert "השכר" in r and "בהמשך" in r            # salary is explicitly left for later
    assert "יחד או בהדרגה" in r                     # any order, gradually


def test_the_form_link_is_offered_once(sessions):
    """With the built-in uploader enabled (config), the Q2 link is the payslip uploader; it is offered once
    and a reload keeps it. (Before the uploader was enabled this was the profile-form link.)"""
    svc = service(sessions)
    turn, token = start(svc, "מנהלת מוצר, שכר בסיס 18,500")
    assert turn.expects["link"]["kind"] == "payslip_upload" and "להעלות תלוש" in turn.reply
    assert svc.view(turn.conversation_id, token).expects["link"]["kind"] == "payslip_upload"  # a reload keeps it
    turn = svc.send(turn.conversation_id, token, "מה זה נקודות זיכוי?")
    assert turn.expects["link"] is None and "בטופס" not in turn.reply   # the Q2 link is offered only once


def test_options_question_carries_no_second_offer(sessions):
    svc = service(sessions)
    turn, _ = start(svc, PROFILE, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש")
    assert "אפשר גם להציג" not in turn.reply and turn.reply.count("?") == 1


def test_a_saved_field_is_never_asked_again(sessions):
    svc = service(sessions)
    turn, token = start(svc, NO_PENSION, "פנסיה 6%")
    assert state(svc, turn, token).profile.pension_percent == 6
    for text in ("יש לי קורולה קרוס", "300"):
        assert "פנסיה" not in turn.reply.split("רשמתי")[0] + turn.reply.rsplit("\n\n", 2)[-2]
        turn = svc.send(turn.conversation_id, token, text)
    assert "פנסיה" not in turn.reply


# ---------- 9. honest complaints ----------

def test_already_answered_complaint_is_answered_from_the_state_without_a_false_apology(sessions):
    svc = service(sessions)
    turn, token = start(svc, NO_PENSION, "כבר כתבתי לך")
    assert "בדקתי: עד עכשיו רשמתי" in turn.reply and "18,500" in turn.reply
    assert "סליחה" not in turn.reply and "פנסיה" in turn.reply           # still asks what is really missing
    assert state(svc, turn, token).profile.pension_percent is None


# ---------- eval follow-up: final result, copay, answered what was asked ----------

LONG = ("אני מנהלת מוצר בשטראוס, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
        "2.75 נקודות זיכוי, 2 ילדים. יש לי קורולה קרוס 2025 ואני משתתפת ב-300 ש\"ח בחודש, ואני רוצה לבדוק את אטו 3")


@pytest.mark.parametrize("model, year", [("COROLLA CROSS", None), ("COROLLA", 2025), ("קורולה", 2025), ("קורולה", None),
                                         ("טויוטה קורולה קרוס", 2025), ("Toyota Corolla Cross", None)])
def test_one_long_message_reaches_the_reference_net_whatever_the_model_calls_the_car(sessions, model, year):
    fake = FakeLLM().turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                               pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2,
                               current_copay=300),
                          call("match_current_car", model=model, registration_year=year),
                          call("select_options", options=["ATTO 3"]))
    svc = service(sessions, fake)
    turn, token = start(svc, LONG)
    s = state(svc, turn, token)
    assert turn.stage == Stage.PRESENTED and s.current.catalog_code == "CAR-09" and s.current.copay == 300
    assert turn.results[0].result["delta_display"] == "661"                   # the same net as Noa's reference


def test_an_ambiguous_name_the_employee_typed_is_still_asked(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = start(svc, PROFILE)
    fake.turn(call("match_current_car", model="קורולה"))
    turn = svc.send(turn.conversation_id, token, "יש לי קורולה")
    assert len(state(svc, turn, token).current.choices) == 2 and turn.expects["field"] == "car_choice"


def test_copay_zero_is_never_saved_while_no_car_or_zero_is_open(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = start(svc, PROFILE)
    fake.turn(call("save_profile", current_copay=0))
    turn = svc.send(turn.conversation_id, token, "אינני משתתף היום")
    s = state(svc, turn, token)
    assert s.current.copay is None and s.pending_ambiguity == "no_car_or_zero_copay"
    assert [c["id"] for c in turn.expects["choices"]]                          # asked with buttons


def test_check_call_drops_a_copay_only_under_the_no_car_ambiguity():
    from app.modules.conversation.agent import Agent
    from app.modules.conversation.llm.port import ToolCall
    from app.modules.conversation.tools import ToolContext
    from app.modules.conversation.understanding import Understanding
    snap = load_snapshot(CONFIG)
    ctx = ToolContext(snapshot=snap, find_vehicle=None, state=ConversationState())
    zero = ToolCall("save_profile", {"current_copay": 0}, "x")
    assert Agent._check_call(ctx, zero, "אינני משתתף", Understanding(ambiguity="no_car_or_zero_copay")) is None
    ctx = ToolContext(snapshot=snap, find_vehicle=None, state=ConversationState())
    kept = Agent._check_call(ctx, zero, "יש לי קורולה קרוס ואני לא משלמת עליה", Understanding())
    assert kept is not None and kept.args["current_copay"] == 0                 # a car named: copay 0 is real


def test_out_of_range_bare_value_is_named_even_when_the_llm_just_reasks(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = start(svc, NO_PENSION)
    fake.turn(call("save_profile", pension_percent=34), reply="מה אחוז ההפרשה לפנסיה (חלק העובד)?")
    turn = svc.send(turn.conversation_id, token, "34")
    assert "34% גבוה מהטווח שהמערכת מאפשרת לפנסיה. כמה אחוז יורד לכם בפועל?" in turn.reply
    assert state(svc, turn, token).profile.pension_percent is None


def test_catalog_answer_is_restored_when_a_guard_trims_it():
    from app.modules.conversation.agent import _answers_catalog
    snap = load_snapshot(CONFIG)
    answer = "כן, בקטלוג יש: COROLLA (2026), קבוצה ב, בנזין; COROLLA CROSS (2025), קבוצה ג, היברידי."
    assert not _answers_catalog("מה קבוצת הזכאות שלכם?", [answer], snap)          # trimmed: code prepends it
    assert _answers_catalog("יש קורולה בקבוצה ב. מה קבוצת הזכאות שלכם?", [answer], snap)
    assert _answers_catalog("הדגם לא בקטלוג.", ["לא מצאתי את הדגם הזה בקטלוג הרכבים הזמין במערכת."], snap)


def test_catalog_answer_is_restored_end_to_end_when_a_guard_trims_the_llm_sentence(sessions):
    # as in the live run (llm:trimmed): the catalog sentence carries an invented price, the number guard drops
    # it, the rest still passes, and the code's catalog answer is put first
    fake = FakeLLM().turn(reply="שאלה טובה. COROLLA (2026) עולה 150,000 ₪. [[catalog]]\nמה קבוצת הזכאות שלכם?")
    svc = service(sessions, fake)
    turn, _ = start(svc, "יש לכם קורולה?")
    assert (turn.source, turn.say) == ("llm", "llm:trimmed")
    assert turn.reply.startswith("כן, בקטלוג יש: COROLLA (2026), קבוצה ב, בנזין; COROLLA CROSS (2025)")
    assert "150,000" not in turn.reply and turn.reply.endswith("מה קבוצת הזכאות שלכם?")


# ---------- manual-test bugs: a question mid-collection, a request about the conversation, a looping question ----------

Q2_FULL = ("יש רכיבי שכר נוספים — עמלות, בונוסים? ואילו הפרשות יורדות לכם: קרן השתלמות? "
           "כמה נקודות זיכוי יש לכם? וכמה ילדים עד גיל 23?")
SAVED_LINE = "רשמתי: שכר בסיס 18,500 ₪, פנסיה 5%."
MISSING_LINE = "עדיין חסרים לי: עמלות, בונוס, קרן השתלמות, נקודות זיכוי וילדים."
HALF = "מנהלת מוצר, שכר בסיס 18,500, פנסיה 5%"


def llm_at_q2(sessions):
    """An LLM-led conversation after the first message, with the compound salary question on screen."""
    fake = FakeLLM().turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500, pension_percent=5),
                          reply=f"{SAVED_LINE}\n{Q2_FULL}")
    svc = service(sessions, fake)
    turn, token = start(svc, HALF)
    assert Q2_FULL in turn.reply
    return svc, fake, turn, token


def test_bug1_a_car_question_mid_collection_is_acknowledged_in_template_mode(sessions):
    svc = service(sessions)
    turn, token = start(svc, HALF, "אפשר לעבור לקורולה?")
    assert "בקטלוג יש: COROLLA (2026), קבוצה ב, בנזין; COROLLA CROSS (2025), קבוצה ג, היברידי." in turn.reply
    assert "נחזור לזה אחרי שנסיים לאסוף את הפרטים" in turn.reply
    assert "נציג" not in turn.reply and state(svc, turn, token).unanswered_question is None   # not the rep
    # and the collection goes on: one light question, never the whole list of missing fields (browser QA 30/10)
    assert MISSING_LINE not in turn.reply and "עמלות" in turn.reply
    question = turn.reply.split("\n\n")[-2]
    assert "עמלות" in question and not any(w in question for w in ("בונוס", "פנסיה", "נקודות זיכוי", "ילדים"))


def test_bug1_the_llm_reply_that_only_re_asks_is_replaced_by_the_acknowledgement(sessions):
    svc, fake, turn, token = llm_at_q2(sessions)
    fake.turn(reply=Q2_FULL, retry=Q2_FULL)                 # the live failure: the flow question, nothing else
    turn = svc.send(turn.conversation_id, token, "אפשר לעבור לקורולה?")
    assert "בקטלוג יש: COROLLA (2026)" in turn.reply and "נחזור לזה אחרי שנסיים לאסוף את הפרטים" in turn.reply
    assert Q2_FULL not in turn.reply and "נציג" not in turn.reply


def test_bug1_a_question_nothing_covers_is_offered_to_the_rep_not_ignored(sessions):
    svc, fake, turn, token = llm_at_q2(sessions)
    fake.turn(reply=Q2_FULL, retry=Q2_FULL)
    turn = svc.send(turn.conversation_id, token, "אפשר לקבל אטו 3 בצבע אחר?")      # colour: only the rep knows
    assert "נציג התחבורה" in turn.reply and Q2_FULL not in turn.reply
    assert state(svc, turn, token).unanswered_question == "אפשר לקבל אטו 3 בצבע אחר?"


def test_bug1_root_cause_a_compound_flow_question_is_not_an_answer():
    snap = load_snapshot(CONFIG)
    kwargs = dict(snapshot=snap, allowed={Decimal(23)}, tools_called=(), fields=["commissions"], offer_rep=False,
                  waiting_answer=False, conflicts=[], must_answer=True)
    assert any(v.kind == "unanswered" for v in check(Q2_FULL, **kwargs).violations)       # four "?" sentences
    assert any(v.kind == "unanswered" for v in check(f"{SAVED_LINE}\n{Q2_FULL}", **kwargs).violations)
    answered = check(f"כל נקודה שווה סכום קבוע בחודש.\n{Q2_FULL}", **kwargs)
    assert not any(v.kind == "unanswered" for v in answered.violations)


def test_bug2_all_questions_at_once_is_explained_and_never_goes_to_the_rep_in_template_mode(sessions):
    svc = service(sessions)
    turn, token = start(svc, HALF, "תן לי את כל השאלות בפעם אחת, אמלא הכל ביחד")
    assert "אני שואל בשלבים כדי שלא יחסר דבר, אבל אפשר לענות על הכול בהודעה אחת." in turn.reply
    assert "נציג" not in turn.reply and state(svc, turn, token).unanswered_question is None
    assert turn.expects["link"]["kind"] == "profile_form" and "או למלא בטופס" in turn.reply   # honoured: the form
    assert MISSING_LINE in turn.reply


def test_bug2_the_llm_cannot_send_a_request_about_the_conversation_to_the_rep(sessions):
    svc, fake, turn, token = llm_at_q2(sessions)
    fake.turn(call("propose_rep_question", question="תן לי את כל השאלות בפעם אחת"),
              reply="על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?")
    turn = svc.send(turn.conversation_id, token, "תן לי את כל השאלות בפעם אחת, אמלא הכל ביחד")
    assert state(svc, turn, token).unanswered_question is None           # the tool call was refused
    assert "נציג" not in turn.reply and "אני שואל בשלבים" in turn.reply


def test_bug2_all_at_once_cancels_a_one_at_a_time_pace(sessions):
    svc = service(sessions)
    turn, token = start(svc, "שאלה אחת כל פעם", HALF)
    assert state(svc, turn, token).pace == "one_at_a_time"
    turn = svc.send(turn.conversation_id, token, "תן לי את כל השאלות בפעם אחת")
    assert state(svc, turn, token).pace == "normal"


def test_bug3_a_retry_of_the_compound_question_says_what_is_saved_and_what_is_missing(sessions):
    svc = service(sessions)
    turn, token = start(svc, HALF)
    assert Q2_FULL in turn.reply
    for text in ("בלה בלה", "משהו אחר"):
        turn = svc.send(turn.conversation_id, token, text)
        assert f"{SAVED_LINE} {MISSING_LINE}" in turn.reply and Q2_FULL not in turn.reply
    p = state(svc, turn, token).profile                                  # nothing saved or required differently
    assert (p.base, p.pension_percent, p.commissions) == (18500, 5, None)
    assert [f for f in ("commissions", "bonus", "study_fund_percent", "credit_points", "children")
            if getattr(p, f) is None] == ["commissions", "bonus", "study_fund_percent", "credit_points", "children"]


def test_bug3_an_llm_that_repeats_the_compound_question_word_for_word_is_replaced(sessions):
    svc, fake, turn, token = llm_at_q2(sessions)
    for text in ("בלה בלה", "משהו אחר"):
        fake.turn(reply=Q2_FULL, retry=Q2_FULL)
        turn = svc.send(turn.conversation_id, token, text)
        assert (turn.source, turn.say) == ("template", "guard:repeat")
        assert f"{SAVED_LINE} {MISSING_LINE}" in turn.reply and Q2_FULL not in turn.reply


def test_bug3_a_new_question_after_an_answer_is_not_a_repeat(sessions):
    svc, fake, turn, token = llm_at_q2(sessions)
    fake.turn(call("save_profile", commissions=0, bonus=0),
              reply="רשמתי: אין עמלות, אין בונוס.\nואילו הפרשות יורדות לכם: קרן השתלמות? כמה נקודות זיכוי יש לכם? "
                    "וכמה ילדים עד גיל 23?")
    turn = svc.send(turn.conversation_id, token, "אין עמלות ואין בונוס")
    assert turn.say == "llm" and "אין עמלות, אין בונוס" in turn.reply
