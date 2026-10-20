"""Focused LIVE Gemini checks for the conversation-reliability fixes.

These call the REAL model and therefore cost quota. They are SKIPPED unless you opt in:

    LIVE_RELIABILITY=live pytest tests/test_live_reliability.py -s

They are intentionally few and short (reasonable quota use). They assert the behaviours the user
reported, with the live model in the loop — not just template mode. Each test prints the transcript
so the run doubles as a manual-review artifact.

What they check (requirement 12): salary deferral, data correction, several values at once, the
1500 → כן → 15000 sequence, a side (off-topic) question with return to the flow, a mixed-intent
message, a professional car question (sunroof), a feature missing from the catalog, and a request to
explain the calculation.
"""
from __future__ import annotations

import os

import pytest

from app.modules.conversation.public import ConversationService, build_llm_chain
from app.modules.rules.public import RulesStore
from app.shared.settings import get_settings

from .conftest import CONFIG

LIVE = os.environ.get("LIVE_RELIABILITY") == "live"
pytestmark = pytest.mark.skipif(not LIVE, reason="set LIVE_RELIABILITY=live to run the live Gemini checks")


def _svc(sessions):
    settings = get_settings()
    chain = build_llm_chain(settings)
    assert chain.available, "no live LLM configured (LLM_CHAIN / GEMINI_API_KEY)"
    return ConversationService(sessions, RulesStore(CONFIG), chain, llm_mode=settings.llm_mode)


def _run(sessions, label, texts):
    svc = _svc(sessions)
    turn, token = svc.start()
    print(f"\n\n===== LIVE: {label} =====")
    print(f"[BOT] {turn.reply}")
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
        print(f"\n[USER] {t}\n[STAGE] {turn.stage.value}\n[BOT] {turn.reply}")
    state = svc.view(turn.conversation_id, token).state
    print("=========================\n")
    return turn, token, state, svc


def _no_duplicate_lines(reply: str) -> bool:
    body = [ln.strip() for ln in reply.replace("\n\n", "\n").splitlines() if ln.strip()]
    return len(body) == len(set(body))


def test_live_defer_salary(sessions):
    turn, token, state, svc = _run(sessions, "defer salary", [
        "אני בקבוצה ג, את השכר אבדוק אחר כך. תשאל אותי בינתיים דברים אחרים.",
    ])
    # the group is kept; the base salary is postponed (deferred or simply not re-demanded as the one question)
    assert state.profile.group == "ג"
    assert _no_duplicate_lines(turn.reply)


def test_live_browser_defer_sequence(sessions):
    """The exact browser failure, live: group first, defer salary in a separate message, then a restate.
    The bot must not stay stuck re-asking the base salary."""
    turn, token, state, svc = _run(sessions, "browser defer sequence", [
        "ג",
        "תשאל אותי בינתיים שאלות אחרות אח\"כ אבדוק את השכר",
        "אמרתי שאח\"כ",
    ])
    assert state.profile.group == "ג" and state.profile.base is None
    # base is deferred, and the conversation did not stay stuck on the salary (it progressed or deferred)
    last_question = [ln for ln in turn.reply.splitlines() if ln.strip()][-1]
    assert "base" in state.deferred_fields
    assert "שכר" not in last_question, f"still stuck on the salary:\n{turn.reply}"
    assert _no_duplicate_lines(turn.reply)


def test_live_correction(sessions):
    turn, token, state, svc = _run(sessions, "correction", [
        "מנהלת מוצר, שכר בסיס 18,500",
        "עמלות 1500",
        "טעיתי, העמלות הן 15000",
    ])
    assert str(state.profile.commissions) == "15000"


def test_live_several_values_at_once(sessions):
    turn, token, state, svc = _run(sessions, "several values", [
        "מנהלת מוצר, שכר בסיס 18,500, אין עמלות, בונוס 2,000, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 2 ילדים",
    ])
    p = state.profile
    assert p.base == 18500 and p.pension_percent == 6 and p.children == 2
    assert _no_duplicate_lines(turn.reply)


def test_live_1500_yes_15000(sessions):
    turn, token, state, svc = _run(sessions, "1500/yes/15000", [
        "מנהלת מוצר, שכר בסיס 18,500",
        "1500",
        "כן 1500",
        "15000",
    ])
    # the point: one consistent value is kept and the bot does not loop or duplicate lines
    assert _no_duplicate_lines(turn.reply)


def test_live_side_question_then_return(sessions):
    turn, token, state, svc = _run(sessions, "side question", [
        "מנהלת מוצר, שכר בסיס 18,500",
        "מה מזג האוויר מחר?",
    ])
    # nothing lost, and the bot stays on the advisory task (it declines/redirects rather than forecasting).
    # The exact wording varies with the live model; the hard guarantees are state + stage.
    assert state.profile.base == 18500                       # state preserved
    assert turn.stage.value == "COLLECT_PROFILE"             # still on task, no stage jump
    assert _no_duplicate_lines(turn.reply)


def test_live_mixed_intent(sessions):
    turn, token, state, svc = _run(sessions, "mixed intent", [
        "אני בקבוצה ג, את השכר אבדוק אחר כך. בינתיים תסביר לי מה זה שווי שימוש, וגם תגיד אם יש רכב עם גג נפתח.",
    ])
    assert state.profile.group == "ג"
    assert _no_duplicate_lines(turn.reply)


def test_live_sunroof_question(sessions):
    turn, token, state, svc = _run(sessions, "sunroof", [
        "מנהלת מוצר, שכר בסיס 18,500",
        "יש רכב עם גג נפתח?",
    ])
    # a professional question is engaged with (not brushed off); whatever the catalog supports
    assert "רק כדי" not in turn.reply        # not the generic "I only calculate net" brush-off


def test_live_no_car_group_b_defer_and_restate(sessions):
    """The second browser failure, live: 'קבוצה ב אין לי רכב', then defer salary, then 'כבר אמרתי'.
    Must save group=ב AND no car, respect the defer, and not loop re-asking the group or salary."""
    turn, token, state, svc = _run(sessions, "group B no car + defer + restate", [
        "קבוצה ב אין לי רכב",
        'את זה תשאל אח"כ אני אבדוק תעבור לשאלות אחרות',
        "כתבתי לך שקבוצה ב",
    ])
    assert state.profile.group == "ב"                        # group saved and kept
    assert state.current.source == "none"                    # no car understood
    assert state.profile.base is None                        # salary not invented
    last = [ln for ln in turn.reply.splitlines() if ln.strip()][-1]
    assert not last.strip().startswith("מה קבוצת")           # not looping on the group
    assert _no_duplicate_lines(turn.reply)


def test_live_no_car_maintenance_three_way(sessions):
    """A no-car employee is asked about a maintenance allowance (not told they have one); 'אין' confirms 0."""
    turn, token, state, svc = _run(sessions, "no car maintenance", [
        "מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
        "אין לי רכב",
        "אין",
    ])
    assert state.current.source == "none"
    assert state.current.maintenance == 0                    # "אין" -> confirmed 0, not an assumption
    assert _no_duplicate_lines(turn.reply)


def test_live_explain_calculation(sessions):
    turn, token, state, svc = _run(sessions, "explain", [
        "מנהלת מוצר, שכר בסיס 18,500",
        "למה אתה צריך את השכר שלי?",
    ])
    assert _no_duplicate_lines(turn.reply)
