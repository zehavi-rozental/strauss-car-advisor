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


def test_approximate_salary_goes_to_the_llm_and_is_not_saved(sessions):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    turns, token = talk(svc, ["מנהלת מוצר"])                     # הקורא: עיסוק → שאלת שכר
    assert fake.calls == [] and "שכר הבסיס" in turns[0].reply
    fake.turn(reply=None)                                       # המודל לא שומר כלום
    turn = svc.send(turns[0].conversation_id, token, "שכר הבסיס שלי בערך 18 וחצי")
    assert turn.path == "llm" and fake.calls
    assert svc.view(turn.conversation_id, token).state.profile.base is None   # לא נשמר 18


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
    assert turns[-1].path == "templates" and "לא הצלחתי להבין" in turns[-1].reply
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


@pytest.mark.parametrize("text, expected", [
    ("18,500", "18500"), ("6%", "6"), ("2.75", "2.75"), ("300 ש\"ח", "300"),
    ("בערך 18 וחצי", None), ("כ-18,500", None), ("18 אלף", None), ("18k", None),
    ("שכר הבסיס שלי בערך 18 וחצי", None),            # גם משפט ארוך
    ("1 או 2", None),                                  # שני מספרים
])
def test_single_value_rejects_approximate_or_ambiguous_answers(text, expected):
    from app.modules.conversation.fallback import single_value
    assert single_value(text) == expected


def test_keyword_parser_skips_approximate_amounts(snapshot):
    from app.modules.conversation.parsing import parse_profile_fields
    occ = snapshot.occupations.keys()
    assert parse_profile_fields("שכר הבסיס שלי בערך 18 וחצי", occ) == {}
    assert parse_profile_fields("שכר בסיס כ-18,500, פנסיה 6%", occ) == {"pension_percent": "6"}
