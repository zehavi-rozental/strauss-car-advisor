"""שומר המספרים: מספר שלא בא מהמנוע / מהפרופיל / מהתבנית חוסם את הודעת המודל."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.modules.conversation.guard import collect_numbers, is_safe, violations
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.models import MessageRow
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

FACTS = {"delta_display": "661", "today": {"net_display": "11032"}, "alternative": {"net_display": "11693.4"},
         "profile": {"pension_percent": "6", "study_fund_percent": "2.5"}, "label": "ATTO 3 (2025)"}


def test_known_numbers_pass():
    allowed = collect_numbers(FACTS)
    assert is_safe("הנטו יעלה ב-+661 ₪: מ-11,032 ₪ ל-11,693 ₪", allowed)
    assert is_safe("פנסיה 6% וקרן השתלמות 2.5%", allowed)
    assert is_safe("ATTO 3 (2025)", allowed)


def test_invented_number_is_blocked():
    allowed = collect_numbers(FACTS)
    assert violations("כנראה תרוויחי בערך 700 ₪", allowed) == [Decimal(700)]
    assert not is_safe("בשנה זה 7,932 ₪", allowed)        # 661 × 12 — חישוב של המודל
    assert not is_safe("כ-11,700 ₪", allowed)              # עיגול שהמודל המציא


def test_blocked_reply_is_replaced_by_template(sessions):
    fake = FakeLLM().turn(call("save_profile", occupation="מנהלת מוצר"),
                          reply="מעולה! לפי התפקיד תרוויחי בערך 700 ₪ יותר. מה השכר שלך?")
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר")
    assert turn.stage == Stage.COLLECT_PROFILE
    assert turn.source == "template" and "700" not in turn.reply
    assert "שכר הבסיס" in turn.reply
    with sessions() as s:
        last = s.scalars(select(MessageRow).order_by(MessageRow.seq.desc())).first()
        assert last.guard_blocked is True and last.content == turn.reply


def _phrase_test(sessions, reply):
    fake = FakeLLM().turn(call("save_profile", occupation="מנהלת מוצר"), reply=reply)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    return svc.send(turn.conversation_id, token, "אני מנהלת מוצר כבר 7 שנים")


def test_numbers_from_the_employees_own_message_are_allowed(sessions):
    turn = _phrase_test(sessions, "מנהלת מוצר כבר 7 שנים — מעולה! מה שכר הבסיס שלך?")
    assert turn.source == "llm" and "7 שנים" in turn.reply


def test_other_numbers_are_still_blocked(sessions):
    turn = _phrase_test(sessions, "מנהלת מוצר כבר 8 שנים — מעולה! מה שכר הבסיס שלך?")
    assert turn.source == "template" and "8" not in turn.reply
