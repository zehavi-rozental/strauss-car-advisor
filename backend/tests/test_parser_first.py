"""הקורא הדטרמיניסטי קודם; מודל השפה רק למה שהקורא לא מבין במלואו."""
from __future__ import annotations

import logging

from app.modules.conversation.llm.fake import FakeLLM, ScriptedProvider, call
from app.modules.conversation.llm.port import LLMError, LLMResponse
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG
from .test_llm_chain import Clock

NOA = [
    "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
    "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש",
    "אני רוצה לבדוק את BYD אטו 3 החשמלי",
]


def service(sessions, llm):
    return ConversationService(sessions, RulesStore(CONFIG), llm)


def talk(svc, messages):
    turn, token = svc.start()
    turns = []
    for m in messages:
        turn = svc.send(turn.conversation_id, token, m)
        turns.append(turn)
    return turns, token


def test_noa_sentences_make_no_llm_calls(sessions, caplog):
    fake = FakeLLM()
    with caplog.at_level(logging.INFO, logger="app.modules.conversation.public"):
        turns, _ = talk(service(sessions, LLMChain([fake])), NOA)
    assert fake.calls == []
    assert [t.stage for t in turns] == [Stage.COLLECT_CURRENT_CAR, Stage.CHOOSE_OPTIONS, Stage.PRESENTED]
    assert all(t.source == "template" and t.path == "parser" for t in turns)
    assert turns[-1].results[0].result["delta_display"] == "661"
    logged = [r.getMessage() for r in caplog.records if r.getMessage().startswith("turn path=")]
    assert len(logged) == 3 and all(m.startswith("turn path=parser total=") for m in logged)


def test_free_car_sentence_goes_to_the_llm(sessions):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    turns, _ = talk(svc, [NOA[0]])
    assert fake.calls == []                                     # הפרופיל — בלי מודל
    fake.turn(reply=None)
    turn = svc.send(turns[0].conversation_id, _, "הרכב שלי זה המשפחתי הלבן של היונדאי")
    assert turn.path == "llm" and fake.calls and "match_current_car" in fake.calls[0].tools


def test_shortened_salary_is_confirmed_not_expanded(sessions):
    """"בערך 18 וחצי" לשכר: לא נשמר 18 ולא 18,500 — הבוט שואל אם התכוונו ל-18,500."""
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    turns, token = talk(svc, ["מנהלת מוצר"])                     # הקורא: עיסוק → שאלת שכר
    assert fake.calls == [] and "שכר הבסיס" in turns[0].reply
    turn = svc.send(turns[0].conversation_id, token, "שכר הבסיס שלי בערך 18 וחצי")
    state = svc.view(turn.conversation_id, token).state
    assert state.profile.base is None and fake.calls == []
    assert turn.reply == "התכוונת ל-18,500 ₪?"
    assert (state.pending_confirmation.field, state.pending_confirmation.value) == ("base_salary", 18500)


def test_number_inside_a_sentence_is_not_an_option_choice(sessions):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    turns, token = talk(svc, NOA[:2])
    assert turns[-1].stage == Stage.CHOOSE_OPTIONS and fake.calls == []
    fake.turn(reply=None)
    turn = svc.send(turns[0].conversation_id, token, "אני רוצה לבדוק 2 רכבים")
    assert turn.path == "llm" and fake.calls
    assert svc.view(turn.conversation_id, token).state.selected == []


def test_bare_numbers_are_list_choices(sessions):
    fake = FakeLLM()
    turns, _ = talk(service(sessions, LLMChain([fake])), NOA[:2] + ["9"])   # 9 = ATTO 3 ברשימה
    assert fake.calls == [] and turns[-1].stage == Stage.PRESENTED
    assert turns[-1].results[0].label == "ATTO 3 (2025)"


def test_template_mode_does_not_guess_from_those_sentences(sessions):
    svc = service(sessions, LLMChain())
    turns, token = talk(svc, ["מנהלת מוצר", "שכר הבסיס שלי בערך 18 וחצי"])
    assert turns[-1].reply == "התכוונת ל-18,500 ₪?"                  # שואלים — לא מרחיבים בשקט
    assert svc.view(turns[-1].conversation_id, token).state.profile.base is None

    turns, token = talk(svc, NOA[:2] + ["אני רוצה לבדוק 2 רכבים"])
    assert turns[-1].stage == Stage.CHOOSE_OPTIONS and "לא הצלחתי להבין" in turns[-1].reply
    assert svc.view(turns[-1].conversation_id, token).state.selected == []


class SlowIntro(ScriptedProvider):
    """חילוץ מהיר (בוחר ATTO 3); הפתיח איטי — מקדם את השעון עד התקרה ונכשל ב-timeout."""

    def __init__(self, clock):
        super().__init__("gemini", "slow-intro")
        self.clock = clock
        self.intro_cap = None

    def complete(self, messages, tools=(), require_tool=False, timeout_s=None):
        self.calls += 1
        if tools:
            return LLMResponse(tool_calls=(call("calculate", options=["ATTO 3"]),))
        self.intro_cap = timeout_s
        self.clock.now += min(5, timeout_s) if timeout_s is not None else 5
        raise LLMError("slow", timeout=True)


def test_slow_intro_is_cut_at_its_budget_and_the_template_result_is_shown(sessions):
    clock = Clock()
    slow = SlowIntro(clock)
    chain = LLMChain([slow], turn_deadline_s=6, intro_budget_s=2.5, clock=clock)
    svc = service(sessions, chain)
    turns, token = talk(svc, NOA[:2])                           # הקורא — בלי מודל
    started = clock.now
    turn = svc.send(turns[0].conversation_id, token, "את החשמלי הסיני בבקשה")   # הקורא לא מבין → המודל
    assert turn.path == "llm" and turn.stage == Stage.PRESENTED
    assert slow.intro_cap == 2.5 and clock.now - started == 2.5
    assert turn.source == "template" and "+661 ₪" in turn.reply


import pytest  # noqa: E402
from decimal import Decimal  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    ("18,500", "18500"), ("6%", "6"), ("2.75", "2.75"), ("300 ש\"ח", "300"),
    ("בערך 18 וחצי", None), ("כ-18,500", None), ("18 אלף", None), ("18k", None),   # single_value: רק מדויק
    ("שכר הבסיס שלי בערך 18 וחצי", None),            # גם משפט ארוך
    ("1 או 2", None),                                  # שני מספרים
])
def test_single_value_rejects_approximate_or_ambiguous_answers(text, expected):
    from app.modules.conversation.fallback import single_value
    assert single_value(text, ["שח", "שקל"]) == expected


def test_keyword_parser_reads_approximate_amounts_and_flags_them(snapshot):
    from app.modules.conversation.parsing import read_profile
    occ = snapshot.occupations.keys()
    r = read_profile("שכר הבסיס שלי בערך 18 וחצי", occ)
    assert r.fields == {"base_salary": "18.5"} and r.approximate == ["base_salary"]
    r = read_profile("שכר בסיס כ-18,500, פנסיה 6%", occ)
    assert r.fields == {"base_salary": "18500", "pension_percent": "6"} and r.approximate == ["base_salary"]
    assert read_profile("שכר בסיס 18 אלף", occ).fields == {"base_salary": "18000"}


# ---------- הודעה על הרכב עם משמעות נוספת → מודל השפה ----------

CROSSOVER = "הרכב שלי זה הקרוסאובר של טויוטה, הקורולה הגבוהה"


def _at_car_question(svc):
    turns, token = talk(svc, [NOA[0]])
    assert turns[-1].stage == Stage.COLLECT_CURRENT_CAR
    return turns[-1].conversation_id, token


def test_car_sentence_with_extra_words_goes_to_the_llm(sessions):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    cid, token = _at_car_question(svc)
    fake.turn(call("match_current_car", model="COROLLA CROSS"), reply=None)
    turn = svc.send(cid, token, CROSSOVER)
    assert turn.path == "llm" and fake.calls
    assert svc.view(cid, token).state.current.catalog_code == "CAR-09"      # המודל הבין


def test_crossover_sentence_is_not_matched_when_the_llm_fails(sessions):
    """קריאה חלקית ("קורולה" בתוך משפט על קרוסאובר) — לא פועלים עליה, גם לא כגיבוי: שואלים שוב."""
    from app.modules.conversation.llm.fake import FailingLLM
    svc = service(sessions, LLMChain([FailingLLM()]))
    cid, token = _at_car_question(svc)
    turn = svc.send(cid, token, CROSSOVER)
    assert turn.path == "fallback"
    assert svc.view(cid, token).state.current.catalog_code is None
    assert "לא זיהיתי את דגם הרכב" in turn.reply


@pytest.mark.parametrize("text", [NOA[1], "קורולה קרוס 2023", "יש לי טוסון"])
def test_plain_car_sentences_stay_on_the_parser(sessions, text):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    cid, token = _at_car_question(svc)
    turn = svc.send(cid, token, text)
    assert turn.path == "parser" and fake.calls == []


def test_leftover_words():
    from pathlib import Path

    from app.modules.conversation.parsing import leftover_words
    from app.modules.rules.public import load_snapshot, matched_name

    snap = load_snapshot(Path(CONFIG))
    fillers = snap.extensions.car_message_fillers
    by_code = {c.catalog_code: c for c in snap.catalog}
    assert leftover_words(CROSSOVER, matched_name(CROSSOVER, by_code["CAR-07"]), fillers) == [
        "הקרוסאובר", "של", "טויוטה", "הגבוהה"]
    assert leftover_words(NOA[1], matched_name(NOA[1], by_code["CAR-09"]), fillers) == []


# ---------- "לא" באמצע ההודעה שולל את הרכב ----------

NEGATED = "זה לא קורולה קרוס"


def test_negated_car_goes_to_the_llm_and_the_parser_matches_nothing(sessions):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    cid, token = _at_car_question(svc)
    fake.turn(reply=None)                       # המודל לא שומר כלום
    turn = svc.send(cid, token, NEGATED)
    assert turn.path == "llm" and fake.calls
    assert svc.view(cid, token).state.current.catalog_code is None


@pytest.mark.parametrize("chain, path", [("failing", "fallback"), ("none", "templates")])
def test_negated_car_has_no_fallback_match(sessions, chain, path):
    from app.modules.conversation.llm.fake import FailingLLM
    svc = service(sessions, LLMChain([FailingLLM()]) if chain == "failing" else LLMChain())
    cid, token = _at_car_question(svc)
    turn = svc.send(cid, token, NEGATED)
    assert turn.path == path
    assert svc.view(cid, token).state.current.catalog_code is None
    assert "לא זיהיתי את דגם הרכב" in turn.reply


# ---------- מספר מחוץ לרשימת החלופות: תשובה מיידית, בלי מודל ----------

OUT_OF_RANGE = "אין חלופה מספר 300. בחרו מספר בין 1 ל-13, או כתבו את שם הרכב."


@pytest.mark.parametrize("text", ["300", "2, 300"])
def test_out_of_range_option_number_is_answered_without_the_llm(sessions, text):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    turns, token = talk(svc, NOA[:2] + [text])
    turn = turns[-1]
    assert fake.calls == [] and turn.path == "parser"
    assert turn.reply.startswith(OUT_OF_RANGE)
    assert "לאילו חלופות להשוות" in turn.reply and "13. ויתור על רכב" in turn.reply
    assert "לא הצלחתי להבין" not in turn.reply
    assert turn.stage == Stage.CHOOSE_OPTIONS
    assert svc.view(turn.conversation_id, token).state.selected == []


def test_in_range_number_still_selects_the_option(sessions):
    fake = FakeLLM()
    turns, _ = talk(service(sessions, LLMChain([fake])), NOA[:2] + ["2"])
    assert fake.calls == [] and turns[-1].stage == Stage.PRESENTED
    assert [r.label for r in turns[-1].results] == ["YARIS CROSS HSD (2025)"]


# ---------- הכלל: פועלים רק על קריאה מלאה ----------

REPEATED_CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"


@pytest.mark.parametrize("chain", ["none", "fake"])
def test_repeated_car_sentence_at_the_options_question_selects_nothing(sessions, chain):
    """"קורולה קרוס" הוא הרכב הנוכחי — לא נבחר "קורולה" שבתוכו, ולא נפתחת שאלה לנציג."""
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]) if chain == "fake" else LLMChain())
    turns, token = talk(svc, NOA[:2])
    if chain == "fake":
        fake.turn(reply=None)                         # המודל לא קורא לכלי
    turn = svc.send(turns[0].conversation_id, token, REPEATED_CAR)
    state = svc.view(turn.conversation_id, token).state
    assert turn.stage == Stage.CHOOSE_OPTIONS and state.selected == [] and turn.results == []
    assert svc.cases.list_open() == []
    assert "לאילו חלופות להשוות" in turn.reply


@pytest.mark.parametrize("text, code", [("אני רוצה לבדוק את אטו 3", "CAR-10"), ("קורולה", "CAR-07"),
                                        ("BYD אטו 3 החשמלי בבקשה", "CAR-10")])
def test_options_read_in_full_select_without_the_llm(sessions, text, code):
    fake = FakeLLM()
    turns, _ = talk(service(sessions, LLMChain([fake])), NOA[:2] + [text])
    assert fake.calls == [] and turns[-1].path == "parser"
    assert [r.option_id for r in turns[-1].results] == [code]


@pytest.mark.parametrize("text", ["MODEL 3",                         # קבוצה ד — לא זמין לנועה (ג)
                                  "אני רוצה לבדוק את אטו 3 הלבן",     # מילה נוספת
                                  "אטו 3 או משהו דומה"])
def test_option_messages_not_read_in_full_select_nothing_without_an_llm(sessions, text):
    svc = service(sessions, LLMChain())
    turns, token = talk(svc, NOA[:2] + [text])
    assert turns[-1].path == "templates" and svc.view(turns[-1].conversation_id, token).state.selected == []


def test_full_profile_with_an_unknown_word_saves_only_labelled_values(sessions):
    svc = service(sessions, LLMChain())
    turns, token = talk(svc, [NOA[0] + ", ואני גם בהריון"])
    turn = turns[-1]
    p = svc.view(turn.conversation_id, token).state.profile
    assert p.occupation is None                       # העיסוק אינו ערך מתויג
    assert (p.base, p.commissions, p.bonus, p.pension_percent) == (18500, 0, 0, 6)
    assert (p.study_fund_percent, p.credit_points, p.children) == (Decimal("2.5"), Decimal("2.75"), 2)
    assert turn.reply.startswith("רשמתי: שכר בסיס 18,500 ₪, אין עמלות, אין בונוס, פנסיה 6%, "
                                 "קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים. אם משהו לא נכון, כתבו את התיקון.")
    assert "מה התפקיד שלך" in turn.reply


@pytest.mark.parametrize("text", ["פנסיה בערך 6, ואני גם בהריון", "פנסיה 6 או 7, ואני גם בהריון",
                                  "לא פנסיה 6, ואני גם בהריון"])
def test_unclear_labelled_values_are_not_saved(sessions, text):
    svc = service(sessions, LLMChain())
    turns, token = talk(svc, ["מנהלת מוצר", text])
    assert svc.view(turns[-1].conversation_id, token).state.profile.pension_percent is None


@pytest.mark.parametrize("text", ["אין לי רכב צמוד אבל יש לי רכב פרטי", "היברידי אבל אולי חשמלי"])
def test_recognised_words_with_extra_meaning_are_not_acted_on(sessions, text):
    svc = service(sessions, LLMChain())
    turns, token = talk(svc, [NOA[0], text])
    state = svc.view(turns[-1].conversation_id, token).state
    assert state.current.source is None and state.current.drive is None


# ---------- ערכים משוערים, סכומים מקוצרים וטווחי סבירות (בקוד, לכל מקור) ----------

def _send_all(svc, messages):
    turn, token = svc.start()
    for m in messages:
        turn = svc.send(turn.conversation_id, token, m)
    return turn, token


def test_official_scenario_sentence_saves_an_approximate_commission(sessions):
    """"עמלות משתנות כ-1,000 ₪" (מהתרחיש הרשמי) נשמר — ומסומן כהערכה, שמופיעה בתוצאה כהנחה."""
    svc = service(sessions, LLMChain())
    turn, token = _send_all(svc, ["מנהלת מוצר", "18,500",
                                  "עמלות משתנות כ-1,000 ₪, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי"])
    p = svc.view(turn.conversation_id, token).state.profile
    assert (p.commissions, p.pension_percent, p.study_fund_percent, p.credit_points) == (
        1000, 6, Decimal("2.5"), Decimal("2.25"))
    assert p.approximate_fields == ["commissions"]
    for m in ["אין", "2", "קורולה קרוס", "300", "אטו 3"]:
        turn = svc.send(turn.conversation_id, token, m)
    assert turn.stage == Stage.PRESENTED
    assert "עמלות: כ-1,000 ₪, לפי ההערכה שלך" in turn.results[0].result["assumptions"]
    assert "עמלות: כ-1,000 ₪, לפי ההערכה שלך" in turn.reply


def test_shortened_salary_needs_a_yes_before_it_is_saved(sessions):
    svc = service(sessions, LLMChain())
    turn, token = _send_all(svc, ["מנהלת מוצר", "בערך 18"])
    assert turn.reply == "התכוונת ל-18,000 ₪?"
    assert svc.view(turn.conversation_id, token).state.profile.base is None
    turn = svc.send(turn.conversation_id, token, "כן")
    p = svc.view(turn.conversation_id, token).state.profile
    assert p.base == 18000 and p.approximate_fields == ["base_salary"]
    assert "עמלות" in turn.reply                                    # השאלה הבאה


def test_no_to_the_confirmation_asks_the_field_again(sessions):
    svc = service(sessions, LLMChain())
    turn, token = _send_all(svc, ["מנהלת מוצר", "18", "לא"])
    s = svc.view(turn.conversation_id, token).state
    assert s.profile.base is None and s.pending_confirmation is None
    assert "מה שכר הבסיס החודשי שלך" in turn.reply


def test_an_llm_that_saves_base_18_is_caught_by_the_range_check(sessions):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    turn, token = _send_all(svc, ["מנהלת מוצר"])
    fake.turn(call("save_profile", base_salary=18))
    turn = svc.send(turn.conversation_id, token, "השכר שלי שמונה עשר")   # הקורא לא מבין → המודל
    assert fake.calls and svc.view(turn.conversation_id, token).state.profile.base is None
    assert turn.reply == "התכוונת ל-18,000 ₪?" and turn.source == "template"


@pytest.mark.parametrize("llm", [False, True])
def test_pension_60_is_rejected_and_the_problem_is_named(sessions, llm):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]) if llm else LLMChain())
    turn, token = _send_all(svc, ["מנהלת מוצר", "18,500", "אין", "אין"])
    if llm:
        fake.turn(call("save_profile", pension_percent=60))
        text = "ההפרשה שלי לפנסיה היא שישים"                       # הקורא לא מבין → המודל
    else:
        text = "60%"
    turn = svc.send(turn.conversation_id, token, text)
    assert svc.view(turn.conversation_id, token).state.profile.pension_percent is None
    assert turn.reply == "60% נראה גבוה מאוד להפרשה לפנסיה. כמה אחוזים מופרשים ממך לפנסיה?"


def test_low_salary_outside_any_reading_names_the_problem(sessions):
    svc = service(sessions, LLMChain())
    turn, token = _send_all(svc, ["מנהלת מוצר", "500,000"])
    assert turn.reply == "500,000 ₪ נראה גבוה מאוד לשכר בסיס חודשי. מה הסכום המלא מהתלוש?"
    assert svc.view(turn.conversation_id, token).state.profile.base is None


def test_other_fields_in_the_same_message_are_still_saved(sessions):
    svc = service(sessions, LLMChain())
    turn, token = _send_all(svc, ["מנהלת מוצר, שכר בסיס 18,500, פנסיה 60%"])
    p = svc.view(turn.conversation_id, token).state.profile
    assert p.base == 18500 and p.pension_percent is None and p.occupation == "מנהל/ת מוצר"
    assert turn.reply.startswith("60% נראה גבוה מאוד להפרשה לפנסיה")
