"""הפרופיל כולו בהודעה אחת — עם מודל שפה שלא "רוצה" לקרוא לכלי, ובמצב תבניות."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.parsing import parse_profile_fields
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CAR_WORDS, CONFIG, NOA_WORDS, make_client, without_cta

NOA_PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
               "2.75 נקודות זיכוי, 2 ילדים")
# הקורא מבין שכר ופנסיה, אבל השאלה (עיסוק) לא נענתה → לא "מלא" → המודל; בכישלון — הגיבוי
# שכר ופנסיה מתויגים, ועוד מילה שהקורא לא מבין → לא נקרא במלואו → המודל; בגיבוי — רק המתויגים
PARTIAL_PROFILE = "שכר בסיס 18,500 ופנסיה 6%, ואני במשמרות"
NOA_SAVE = call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2)


def test_the_model_saves_in_round_one_and_writes_the_reply_after_the_tool_results(sessions):
    fake = FakeLLM().turn(NOA_SAVE, reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, NOA_WORDS)      # הקורא לא מבין → המודל
    first, second = [c for c in fake.calls if c.tools]
    assert {"save_profile", "match_current_car", "select_options", "propose_rep_question"} <= set(first.tools)
    assert second.messages[-1].role == "tool"                      # round 2 sees what the tools did
    assert turn.stage == Stage.COLLECT_CURRENT_CAR
    assert "לא הצלחתי להבין" not in turn.reply
    p = svc.view(turn.conversation_id, token).state.profile
    assert (p.group, p.base, p.commissions, p.bonus) == ("ג", Decimal(18500), 0, 0)
    assert (p.pension_percent, p.study_fund_percent, p.credit_points, p.children) == (6, Decimal("2.5"),
                                                                                      Decimal("2.75"), 2)


def test_at_most_three_rounds_and_the_user_message_is_delimited(sessions):
    fake = FakeLLM().turn(rounds=[[call("save_profile", base_salary=18500)]] * 5, reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    svc.send(turn.conversation_id, token, "הנה הפרטים שלי")      # (the call is refused: only the rounds are checked)
    rounds = [c for c in fake.calls if c.tools]
    assert len(rounds) == 3
    assert rounds[0].messages[-1].content == "<<<USER>>>\nהנה הפרטים שלי\n<<<END>>>"
    system = rounds[0].messages[0].content
    assert system.index("STYLE") < system.index("FAQ (") < system.index("\nSTATE\nstage:")   # fixed order (caching)


def test_empty_forced_call_is_not_understood(sessions):
    fake = FakeLLM().turn(call("save_profile"), reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "בלה בלה בלה")
    # nothing in "בלה בלה בלה" could answer the question: the neutral not-understood line (browser QA 30/10)
    assert turn.stage == Stage.COLLECT_PROFILE and "לא בטוח שהבנתי" in turn.reply
    assert "לא הצלחתי להבין את התשובה" not in turn.reply


def test_noa_one_message_profile_in_fallback_mode(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]
    body = client.post(f"/api/conversations/{cid}/messages", json={"text": NOA_PROFILE}).json()
    assert body["stage"] == "COLLECT_CURRENT_CAR" and body["source"] == "template"
    client.post(f"/api/conversations/{cid}/messages", json={"text": "יש לי קורולה קרוס"})
    client.post(f"/api/conversations/{cid}/messages", json={"text": "300"})
    body = client.post(f"/api/conversations/{cid}/messages", json={"text": "אטו 3"}).json()
    assert body["stage"] == "PRESENTED" and body["results"][0]["delta_display"] == "661"


@pytest.mark.parametrize("text, expected", [
    (NOA_PROFILE, {"occupation": "מנהל/ת מוצר", "base_salary": "18500", "commissions": 0, "bonus": 0,
                   "pension_percent": "6", "study_fund_percent": "2.5", "credit_points": "2.75",
                   "children_under_23": 2}),
    ("מפתח תוכנה, ברוטו 21000, בלי עמלות ובונוסים, אין קרן השתלמות, נקודות זיכוי 2.25, אין ילדים",
     {"occupation": "מפתח/ת תוכנה", "base_salary": "21000", "commissions": 0, "bonus": 0,
      "study_fund_percent": 0, "credit_points": "2.25", "children_under_23": 0}),
    ("אין עמלות, בונוס 2,000", {"commissions": 0, "bonus": "2000"}),   # סכום מפורש גובר על "אין"
    ("אין עמלות, 6% פנסיה", {"commissions": 0}),                     # לא חוצים פסיק; הפנסיה תישאל
    ("18,500", {}),                                                    # בלי מילת מפתח — שאלה בודדת
    ("לא ברור לי", {}),
])
def test_parse_profile_fields(snapshot, text, expected):
    assert parse_profile_fields(text, snapshot.occupations.keys()) == expected


def test_a_reply_written_with_plain_saves_needs_no_second_round(sessions):
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()

    fake.turn(call("save_profile", occupation="מנהלת מוצר"), reply=None)
    svc.send(turn.conversation_id, token, NOA_WORDS)
    assert len([c for c in fake.calls if c.tools]) == 2           # a tool round, then the reply round


class _FailsFromCall:
    """עוטף FakeLLM ונכשל מהקריאה ה-n (כמו 429 באמצע תור)."""

    name = "flaky"

    def __init__(self, inner, fail_from: int):
        self.inner, self.fail_from, self.n = inner, fail_from, 0

    def complete(self, messages, tools=(), require_tool=False, timeout_s=None):
        self.n += 1
        if self.n >= self.fail_from:
            from app.modules.conversation.llm.port import LLMError
            raise LLMError("HTTP 429")
        return self.inner.complete(messages, tools, require_tool, timeout_s)


def test_provider_failure_after_first_round_keeps_profile(sessions):
    """Round 1 saved the profile, round 2 failed: the template; the parser never reads the same message as a car."""
    fake = FakeLLM().turn(NOA_SAVE, reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([_FailsFromCall(fake, fail_from=2)]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, NOA_WORDS)
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and turn.source == "template"
    current = svc.view(turn.conversation_id, token).state.current
    assert current.query is None and current.source is None        # לא חיפשו "רכב" בשם הפרופיל
    assert "איזה רכב צמוד יש לכם היום" in turn.reply


def test_provider_failure_on_first_round_uses_fallback_parser(sessions):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([_FailsFromCall(FakeLLM(), fail_from=1)]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, PARTIAL_PROFILE)
    assert turn.path == "fallback" and turn.source == "template"
    profile = svc.view(turn.conversation_id, token).state.profile
    assert (profile.base, profile.pension_percent) == (18500, 6)
    assert turn.reply.startswith("רשמתי: שכר בסיס 18,500 ₪, פנסיה 6%. אם משהו לא נכון, כתבו את התיקון.")


def test_a_found_car_is_reported_in_a_second_round(sessions):
    fake = FakeLLM().turn(NOA_SAVE, reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    svc.send(turn.conversation_id, token, NOA_WORDS)
    before = len(fake.calls)
    fake.turn(call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300))
    turn = svc.send(turn.conversation_id, token, CAR_WORDS)
    assert turn.stage == Stage.CHOOSE_OPTIONS
    assert len([c for c in fake.calls[before:] if c.tools]) == 2


def test_turn_deadline_falls_back_to_templates(sessions):
    """כל הספקים איטיים: אחרי 10 שניות (שעון השרשרת) התור עובר לתבניות בלי לנסות עוד רשומות."""
    from .test_llm_chain import Clock, SlowProvider
    from app.modules.conversation.llm.fake import ScriptedProvider

    clock = Clock()
    slow = [SlowProvider("gemini", m, clock, 8) for m in ("a", "b")]
    never = ScriptedProvider("groq", "c")
    chain = LLMChain([*slow, never], turn_deadline_s=10, clock=clock)
    svc = ConversationService(sessions, RulesStore(CONFIG), chain)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, PARTIAL_PROFILE)     # הקורא מבין רק חלק → המודל
    assert turn.source == "template" and turn.path == "fallback"
    profile = svc.view(turn.conversation_id, token).state.profile
    assert (profile.base, profile.pension_percent) == (18500, 6)   # הגיבוי שמר מה שהקורא הבין
    assert never.calls == 0 and clock.now - 1000 == 10


# ---------- המשפטים מהבדיקה החיה, במצב תבניות (בלי מודל שפה) ----------

LIVE_SENTENCES = [
    NOA_PROFILE,
    "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש",
    "אני רוצה לבדוק את BYD אטו 3 החשמלי",
]


def test_live_sentences_reach_the_result_in_template_mode(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]
    stages = []
    for text in LIVE_SENTENCES:
        body = client.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()
        stages.append(body["stage"])
    assert stages == ["COLLECT_CURRENT_CAR", "CHOOSE_OPTIONS", "PRESENTED"]
    [result] = body["results"]
    assert result["label"] == "ATTO 3 (2025)" and result["delta_display"] == "661"
