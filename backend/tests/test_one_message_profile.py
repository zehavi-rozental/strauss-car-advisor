"""הפרופיל כולו בהודעה אחת — עם מודל שפה שלא "רוצה" לקרוא לכלי, ובמצב תבניות."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.parsing import parse_profile_fields
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, make_client

NOA_PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
               "2.75 נקודות זיכוי, 2 ילדים")
NOA_SAVE = call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2)


def test_forced_tool_mode_saves_profile_when_model_prefers_text(sessions):
    """כמו Gemini בבדיקה החיה: בלי חיוב הוא עונה בטקסט. עם mode ANY הוא קורא לכלי."""
    fake = FakeLLM().turn(NOA_SAVE, forced_only=True, reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, NOA_PROFILE)

    assert fake.calls[0].require_tool is True and fake.calls[0].tools == ["save_profile"]
    assert turn.stage == Stage.COLLECT_CURRENT_CAR
    assert "לא הצלחתי להבין" not in turn.reply
    p = svc.view(turn.conversation_id, token).state.profile
    assert (p.group, p.base, p.commissions, p.bonus) == ("ג", Decimal(18500), 0, 0)
    assert (p.pension_percent, p.study_fund_percent, p.credit_points, p.children) == (6, Decimal("2.5"),
                                                                                      Decimal("2.75"), 2)


def test_only_first_round_of_collecting_stages_is_forced(sessions):
    fake = FakeLLM().turn(NOA_SAVE, reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    svc.send(turn.conversation_id, token, NOA_PROFILE)
    extraction = [c for c in fake.calls if c.tools]
    assert [c.require_tool for c in extraction] == [True, False]   # הסבב השני חופשי לסיים

    fake.turn(call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300))
    svc.send(turn.conversation_id, token, "קורולה קרוס, 300")
    fake.turn(call("calculate", options=["ATTO 3"]))
    svc.send(turn.conversation_id, token, "אטו 3")
    choose = [c for c in fake.calls if "calculate" in c.tools]
    assert choose and not any(c.require_tool for c in choose)       # בחירת חלופות לא כפויה


def test_empty_forced_call_is_not_understood(sessions):
    fake = FakeLLM().turn(call("save_profile"), reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "מה זה בכלל?")
    assert turn.stage == Stage.COLLECT_PROFILE and "לא הצלחתי להבין" in turn.reply


def test_noa_one_message_profile_in_fallback_mode(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]
    body = client.post(f"/api/conversations/{cid}/messages", json={"text": NOA_PROFILE}).json()
    assert body["stage"] == "COLLECT_CURRENT_CAR" and body["source"] == "template"
    client.post(f"/api/conversations/{cid}/messages", json={"text": "קורולה קרוס"})
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


def test_second_round_only_when_the_stage_moved(sessions):
    """שתי קריאות לתור רגיל (חילוץ + ניסוח) — מכסות חינמיות של ספקים נגמרות מהר."""
    fake = FakeLLM().turn(call("save_profile", occupation="מנהלת מוצר"), reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    svc.send(turn.conversation_id, token, "מנהלת מוצר")
    assert len([c for c in fake.calls if c.tools]) == 1


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
    """סבב 1 שמר את הפרופיל, סבב 2 נכשל: אסור שהגיבוי יפרש את אותה הודעה כשם רכב."""
    fake = FakeLLM().turn(NOA_SAVE, reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([_FailsFromCall(fake, fail_from=2)]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, NOA_PROFILE)
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and turn.source == "template"
    current = svc.view(turn.conversation_id, token).state.current
    assert current.query is None and current.source is None        # לא חיפשו "רכב" בשם הפרופיל
    assert "איזה רכב צמוד" in turn.reply


def test_provider_failure_on_first_round_uses_fallback_parser(sessions):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([_FailsFromCall(FakeLLM(), fail_from=1)]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, NOA_PROFILE)
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and turn.source == "template"


def test_found_car_does_not_trigger_a_second_round(sessions):
    """רכב נמצא → בחירת חלופות: העובד עוד לא בחר, אין טעם בסבב נוסף."""
    fake = FakeLLM().turn(NOA_SAVE, reply=None)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    svc.send(turn.conversation_id, token, NOA_PROFILE)
    before = len(fake.calls)
    fake.turn(call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300))
    turn = svc.send(turn.conversation_id, token, "קורולה קרוס, 300")
    assert turn.stage == Stage.CHOOSE_OPTIONS
    assert len([c for c in fake.calls[before:] if c.tools]) == 1


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
    turn = svc.send(turn.conversation_id, token, NOA_PROFILE)
    assert turn.source == "template" and turn.stage == Stage.COLLECT_CURRENT_CAR   # הפרסר בגיבוי
    assert never.calls == 0 and clock.now - 1000 == 10
