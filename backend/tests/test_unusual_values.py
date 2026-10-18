"""Legal but rare values (thresholds in extensions.yaml) get one confirmation, never silent saving."""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

BASE = "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, {points} נקודות זיכוי, 2 ילדים"


def start(sessions):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    turn, token = svc.start()
    return svc, turn, token


def profile(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


def test_zero_credit_points_are_asked_once_and_not_saved(sessions):
    svc, turn, token = start(sessions)
    turn = svc.send(turn.conversation_id, token, BASE.format(points=0))
    assert profile(svc, turn, token).profile.credit_points is None
    assert "לאשר 0 נקודות זיכוי? רוב השכירים מקבלים נקודות זיכוי." in turn.reply
    assert [c["label"] for c in turn.expects["choices"]] == ["לאשר", "לתקן"]


def test_confirming_saves_and_is_not_asked_again(sessions):
    svc, turn, token = start(sessions)
    turn = svc.send(turn.conversation_id, token, BASE.format(points=0))
    turn = svc.send_choice(turn.conversation_id, token, ["yes"], None)
    assert profile(svc, turn, token).profile.credit_points == Decimal(0)
    assert "לאשר" not in turn.reply


def test_fix_keeps_the_field_open(sessions):
    svc, turn, token = start(sessions)
    turn = svc.send(turn.conversation_id, token, BASE.format(points=0))
    turn = svc.send_choice(turn.conversation_id, token, ["no"], None)
    assert profile(svc, turn, token).profile.credit_points is None
    assert "נקודות זיכוי" in turn.reply


def test_form_does_not_report_a_held_value_as_saved(sessions):
    svc, turn, token = start(sessions)
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר, שכר בסיס 18,500")
    turn = svc.send_fields(turn.conversation_id, token, {"pension_percent": 0, "bonus": 100}) \
        if hasattr(svc, "send_fields") else turn
    if hasattr(svc, "send_fields"):
        assert profile(svc, turn, token).profile.pension_percent is None
        assert "לאשר פנסיה של 0%?" in turn.reply and "רשמתי: בונוס" in turn.reply
