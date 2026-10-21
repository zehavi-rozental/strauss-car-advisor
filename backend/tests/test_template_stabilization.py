"""Template-mode (no Gemini) stabilization matrix — the backup path when the LLM is off.

Every test drives ConversationService with an empty LLMChain (pure parser + templates) and asserts STATE
(what was saved, which stage), not just that the bot replied. These probe natural Hebrew phrasings — including
ones not in the original examples — to surface deterministic-parser gaps. A failing/weak case is documented
with xfail(strict) so it turns green only when truly fixed.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

FULL_Q2 = "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"


def svc(sessions):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain())


def run(sessions, msgs):
    s = svc(sessions)
    turn, token = s.start()
    for m in msgs:
        turn = s.send(turn.conversation_id, token, m)
    return s, turn, token


def st(s, turn, token):
    return s.view(turn.conversation_id, token).state


# ---------- eligibility group, natural phrasings ----------

@pytest.mark.parametrize("msg,group", [
    ("קבוצה ג", "ג"), ("אני בקבוצה ב", "ב"), ("בקבוצת זכאות א", "א"),
    ("הקבוצה שלי היא ד", "ד"), ("ג", "ג"), ("אני בקבוצה ג בלבד", "ג"),
])
def test_group_phrasings(sessions, msg, group):
    s, turn, token = run(sessions, [msg])
    assert st(s, turn, token).profile.group == group, f"group not read from: {msg!r}"


# ---------- car situations ----------

def test_has_company_car_named(sessions):
    s, turn, token = run(sessions, ["מנהלת מוצר, שכר בסיס 18500", FULL_Q2, "יש לי קורולה קרוס"])
    assert st(s, turn, token).current.catalog_code == "CAR-09"


def test_no_company_car(sessions):
    s, turn, token = run(sessions, ["מנהלת מוצר, שכר בסיס 18500", FULL_Q2, "אין לי רכב"])
    assert st(s, turn, token).current.source == "none"


def test_group_and_no_car_one_sentence(sessions):
    s, turn, token = run(sessions, ["אני בקבוצה ב ואין לי רכב"])
    c = st(s, turn, token)
    assert c.profile.group == "ב" and c.current.source == "none"


# ---------- several facts in one message ----------

def test_full_profile_one_message(sessions):
    s, turn, token = run(sessions, [
        "מנהלת מוצר, שכר בסיס 18500, אין עמלות, בונוס 2000, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 2 ילדים"])
    p = st(s, turn, token).profile
    assert (p.base, p.bonus, p.pension_percent, p.children) == (Decimal(18500), Decimal(2000), Decimal(6), 2)


# ---------- corrections ----------

def test_correct_a_labeled_value(sessions):
    s, turn, token = run(sessions, ["מנהלת מוצר, שכר בסיס 18500", "פנסיה 6%", "פנסיה 7%"])
    assert st(s, turn, token).profile.pension_percent == 7


def test_correct_the_group_before_occupation(sessions):
    s, turn, token = run(sessions, ["קבוצה ג", "טעיתי, אני בקבוצה ד"])
    assert st(s, turn, token).profile.group == "ד"


# ---------- deferral ----------

def test_defer_salary_then_other_questions(sessions):
    s, turn, token = run(sessions, ["קבוצה ג", "את השכר אבדוק אחר כך"])
    state = st(s, turn, token)
    assert state.profile.group == "ג" and "base" in state.deferred_fields
    assert "שכר" not in [ln for ln in turn.reply.splitlines() if ln.strip()][-1]


# ---------- short answers ----------

def test_bare_number_answers_the_asked_field(sessions):
    s, turn, token = run(sessions, ["מנהלת מוצר", "18500"])   # base was asked
    assert st(s, turn, token).profile.base == Decimal(18500)


def test_none_for_commissions(sessions):
    s, turn, token = run(sessions, ["מנהלת מוצר, שכר בסיס 18500", "אין עמלות"])
    assert st(s, turn, token).profile.commissions == 0


# ---------- no net while mandatory data is missing ----------

def test_no_net_without_base(sessions):
    s, turn, token = run(sessions, ["קבוצה ג", "את השכר אבדוק אחר כך", FULL_Q2,
                                    "יש לי קורולה קרוס, משלמת 300", "אטו 3"])
    assert turn.results == [] and turn.stage != Stage.PRESENTED


# ---------- full journey, template mode, to a result ----------

def test_full_journey_template_mode(sessions):
    s, turn, token = run(sessions, ["מנהלת מוצר, שכר בסיס 18500", FULL_Q2,
                                    "יש לי קורולה קרוס, משלמת 300", "אטו 3"])
    assert turn.stage == Stage.PRESENTED and turn.results
    assert turn.results[0].result["status"] == "OK"


# ---------- off-topic keeps state ----------

def test_off_topic_keeps_state(sessions):
    s, turn, token = run(sessions, ["מנהלת מוצר, שכר בסיס 18500", "מה השעה?"])
    assert st(s, turn, token).profile.base == Decimal(18500) and turn.stage == Stage.COLLECT_PROFILE


# ---------- no infinite "לא הבנתי" loop ----------

def test_no_endless_not_understood(sessions):
    s, t, tok = run(sessions, ["בלהבלה גיבריש", "עוד גיבריש"])
    # a not-understood reply still offers a way forward (expects is not empty — no dead end)
    assert s.view(t.conversation_id, tok).expects["fields"]
