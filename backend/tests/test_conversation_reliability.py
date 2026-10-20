"""Regression tests for the conversation-reliability work (feature/conversation-reliability).

These reproduce the reported bugs and lock in the intended behaviour. They are DETERMINISTIC:
they run in template mode (no LLM) or with a scripted FakeLLM. They do NOT prove the quality of
the live Gemini conversation — that is verified separately (see TEST_PLAN notes and manual steps).

Test kinds are marked in each test's docstring:
  [unit]        a pure function.
  [integration] a full turn through ConversationService (the API's entry point), template mode.
  [fake-llm]    a full turn with a scripted FakeLLM (the LLM path, deterministic).

Tests that reproduce a CURRENT bug are marked xfail(strict=True): they fail today and must turn
green once the fix lands (a strict xfail that starts passing fails the suite, so the marker must
be removed with the fix — that is intentional).
"""
from __future__ import annotations

import pytest

from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, CTA_STARTS

PROFILE_Q1 = "מנהלת מוצר, שכר בסיס 18,500"


def service(sessions, llm=None, config=CONFIG):
    return ConversationService(sessions, RulesStore(config), llm or LLMChain())


def talk(svc, texts):
    turn, token = svc.start()
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
    return turn, token


def profile(svc, turn, token):
    return svc.view(turn.conversation_id, token).state.profile


def lines(reply: str) -> list[str]:
    return [ln.strip() for ln in reply.replace("\n\n", "\n").splitlines() if ln.strip()]


# ===========================================================================
# BUG 1 — a duplicate sentence in one bubble
# ===========================================================================

def test_no_duplicate_line_in_one_reply(sessions):
    """[integration] A bare number that only repeats the compound Q2 question must not produce the same
    'רשמתי… עדיין חסרים לי' line twice in one reply. Reproduced with: base, then two bare numbers that
    do not bind to a single field, so the grouped question repeats."""
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE_Q1, "1500", "כן 1500", "15000"])
    body = lines(turn.reply)
    # count how many lines mention both "רשמתי" and "חסר" — the compound progress line
    progress = [ln for ln in body if "רשמתי" in ln and "חסר" in ln]
    assert len(progress) <= 1, f"the progress line appears {len(progress)} times:\n{turn.reply}"
    assert len(body) == len(set(body)), f"duplicate line in one bubble:\n{turn.reply}"


# ===========================================================================
# BUG 2 — deferring a mandatory field ("I'll give the salary later")
# ===========================================================================

def test_defer_marks_the_field_and_skips_it(sessions):
    """[integration] 'את השכר אבדוק אחר כך' marks the base salary deferred, and the next reply does not
    ask for the base again (it asks the other open field). Deterministic (template mode)."""
    svc = service(sessions)
    turn, token = talk(svc, ["קבוצה ג", "את השכר אבדוק אחר כך, תשאל אותי בינתיים דברים אחרים"])
    state = svc.view(turn.conversation_id, token).state
    assert state.profile.group == "ג"                      # the group was saved on the first turn
    assert "base" in state.deferred_fields                 # the base salary is postponed, not dropped
    assert "נחזור" in turn.reply                            # acknowledged as deferred ("נחזור ל… בהמשך")
    # the next question (the last line) asks for something other than the base salary
    question_line = [ln for ln in turn.reply.splitlines() if ln.strip()][-1]
    assert "שכר" not in question_line, f"the question still asks for the salary:\n{question_line}"


def test_deferred_base_comes_back_when_nothing_else_is_askable(sessions):
    """[integration] Once every other field is supplied, a deferred mandatory field comes back (it is
    required for the calculation) and no net is shown until it is given."""
    svc = service(sessions)
    turn, token = talk(svc, [
        "קבוצה ג",
        "את השכר אבדוק אחר כך",
        "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
        "יש לי קורולה קרוס, משלמת 300",
        "אטו 3",
    ])
    # the base salary is the only thing left: it must be asked, and there is no result yet
    assert turn.results == []
    assert "שכר" in turn.reply                              # the deferred base comes back


def test_no_calculation_without_mandatory_base(sessions):
    """[integration] No net is shown while the mandatory base salary is missing (defence in depth; this
    holds today and must keep holding after the deferral fix)."""
    svc = service(sessions)
    turn, token = talk(svc, [
        "אני בקבוצה ג, את השכר אבדוק אחר כך",
        "יש לי קורולה קרוס, משלמת 300",
        "אטו 3",
    ])
    assert turn.results == []
    assert turn.stage != Stage.PRESENTED


# ===========================================================================
# BUG 3 — correction / the 1500 → כן 1500 → 15000 sequence
# ===========================================================================

def test_labeled_value_then_correction_keeps_one_value(sessions):
    """[integration] A labeled value, then a corrected labeled value ("עמלות 15000"), must leave exactly
    one up-to-date value — not two conflicting ones. This is the common correction form."""
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE_Q1, "עמלות 1500", "עמלות 15000"])
    p = profile(svc, turn, token)
    assert str(p.commissions) == "15000", f"commissions ended as {p.commissions}"


def test_pension_correction_keeps_one_value(sessions):
    """[integration] 'פנסיה 6%' then 'פנסיה 7%' leaves pension at 7 (correction applied)."""
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE_Q1, "פנסיה 6%", "פנסיה 7%"])
    assert str(profile(svc, turn, token).pension_percent) == "7"


def test_correction_of_group(sessions):
    """[integration] 'טעיתי, אני בקבוצה ד' updates the group to ד when the group was given directly (no
    occupation). An occupation-determined group is intentionally not changed this way (the rules own it)."""
    svc = service(sessions)
    turn, token = talk(svc, ["קבוצה ג", "טעיתי, אני בקבוצה ד"])
    assert profile(svc, turn, token).group == "ד"


def test_group_correction_rejected_when_occupation_owns_it(sessions):
    """[integration] When the occupation determines the group, a manual group change is NOT applied — the
    occupations table is the source of truth (documents the rule)."""
    svc = service(sessions)
    turn, token = talk(svc, ["מנהלת מוצר, שכר בסיס 18,500"])
    g = profile(svc, turn, token).group
    turn = svc.send(turn.conversation_id, token, "טעיתי, אני בקבוצה ד")
    assert profile(svc, turn, token).group == g            # unchanged: the role owns the group


# ===========================================================================
# BUG 4 — payslip upload button not visible in the normal flow
# ===========================================================================

def test_payslip_link_offered_during_salary_collection(sessions):
    """[integration] The built-in uploader is enabled in the shipped config, so the Q2 salary step offers
    the 'להעלות תלוש' link (the employee can upload instead of typing)."""
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE_Q1])
    assert turn.expects["link"] == {"kind": "payslip_upload", "label": "להעלות תלוש"}


def test_payslip_link_in_salary_collection_when_enabled(sessions, tmp_path):
    """[integration] With the built-in uploader enabled, the link should be offered while collecting the
    salary profile (Q2), so the employee can choose to upload instead of typing."""
    import shutil
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    ext = config / "extensions.yaml"
    ext.write_text(ext.read_text(encoding="utf-8").replace("payslip_upload_enabled: false",
                                                           "payslip_upload_enabled: true"),
                   encoding="utf-8")
    svc = service(sessions, config=config)
    turn, token = talk(svc, [PROFILE_Q1])        # now on Q2 (salary components)
    assert turn.expects["link"] == {"kind": "payslip_upload", "label": "להעלות תלוש"}


# ===========================================================================
# SIDE / PROFESSIONAL QUESTIONS — behaviour that must be preserved/added
# ===========================================================================

def test_off_topic_keeps_state_and_redirects(sessions):
    """[integration] An off-topic question does not change the profile or the stage, and gets a polite
    redirect. (Documents current behaviour.)"""
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE_Q1, "מה מזג האוויר מחר?"])
    p = profile(svc, turn, token)
    assert p.base is not None and turn.stage == Stage.COLLECT_PROFILE      # nothing lost
    assert "רכב" in turn.reply                                            # redirect mentions the car advisor
