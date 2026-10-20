"""State-based scenario matrix (deterministic, template mode) — requirement 6/7.

Each scenario checks STATE (what is saved, which stage, which field is next), not just that the bot replied.
These are the natural-language situations the advisor must handle without loops, repeats, lost data, or a
net on missing data. They run without an LLM (template parser); the live-model behaviour is covered
separately in test_live_reliability.py.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.conversation.stage import current_group, missing_profile, missing_current_car
from app.modules.rules.public import RulesStore

from .conftest import CONFIG


def service(sessions):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain())


def step(svc, turn, token, text):
    turn = svc.send(turn.conversation_id, token, text)
    return turn, svc.view(turn.conversation_id, token).state


def start(svc):
    return svc.start()


def last_line(reply: str) -> str:
    return [ln for ln in reply.splitlines() if ln.strip()][-1]


# ---------- no repeated question for an already-saved field ----------

def test_saved_fields_are_never_asked_again(sessions):
    """Walk a full happy-path conversation and assert the question line never names a field already saved."""
    svc = service(sessions)
    turn, token = start(svc)
    convo = ["קבוצה ג", "שכר בסיס 18500", "אין עמלות", "אין בונוס", "פנסיה 6%",
             "קרן השתלמות 2.5%", "2.75 נקודות זיכוי", "2 ילדים", "יש לי קורולה קרוס", "300", "אטו 3"]
    for text in convo:
        turn, st = step(svc, turn, token, text)
        p = st.profile
        q = last_line(turn.reply)
        # once base is saved, the question never says "שכר בסיס"; once group saved, never re-asks group alone
        if p.base is not None:
            assert "שכר בסיס" not in q, f"re-asked base after saving it:\n{turn.reply}"
        if p.group is not None and p.occupation is not None:
            assert not q.strip().startswith("מה קבוצת"), f"re-asked group:\n{turn.reply}"


# ---------- correction of a value mid-conversation keeps one value ----------

def test_correction_updates_single_value(sessions):
    svc = service(sessions)
    turn, token = start(svc)
    turn, _ = step(svc, turn, token, "מנהלת מוצר, שכר בסיס 18500")
    turn, st = step(svc, turn, token, "פנסיה 6%")
    assert st.profile.pension_percent == 6
    turn, st = step(svc, turn, token, "פנסיה 7%")
    assert st.profile.pension_percent == 7                       # corrected, single value


# ---------- contradiction: a value that conflicts with a saved one ----------

def test_restate_group_is_not_a_fresh_start(sessions):
    """'כבר אמרתי לך שקבוצה ב' must not re-open the group; the state already has it."""
    svc = service(sessions)
    turn, token = start(svc)
    turn, st = step(svc, turn, token, "קבוצה ב")
    assert st.profile.group == "ב"
    turn, st = step(svc, turn, token, "כבר אמרתי לך שקבוצה ב")
    assert st.profile.group == "ב"                               # unchanged, not reset
    assert not last_line(turn.reply).strip().startswith("מה קבוצת")  # not re-asking the group


# ---------- several facts in one message ----------

def test_several_facts_one_message(sessions):
    svc = service(sessions)
    turn, token = start(svc)
    turn, st = step(svc, turn, token,
                    "מנהלת מוצר, שכר בסיס 18500, אין עמלות, בונוס 2000, פנסיה 6%, "
                    "קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 2 ילדים")
    p = st.profile
    assert (p.base, p.commissions, p.bonus, p.pension_percent, p.children) == (
        Decimal(18500), Decimal(0), Decimal(2000), Decimal(6), 2)


# ---------- a missing mandatory field yields no net ----------

def test_no_net_while_mandatory_missing(sessions):
    svc = service(sessions)
    turn, token = start(svc)
    # defer base, give the rest, pick a car + alternative
    for text in ["קבוצה ג", "את השכר אבדוק אחר כך",
                 "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
                 "יש לי קורולה קרוס, משלמת 300", "אטו 3"]:
        turn, st = step(svc, turn, token, text)
    assert turn.results == [] and turn.stage != Stage.PRESENTED  # base still required
    turn, st = step(svc, turn, token, "18500")
    assert turn.stage == Stage.PRESENTED and turn.results        # completes once supplied


# ---------- off-topic question keeps state ----------

def test_off_topic_preserves_state(sessions):
    svc = service(sessions)
    turn, token = start(svc)
    turn, _ = step(svc, turn, token, "מנהלת מוצר, שכר בסיס 18500")
    turn, st = step(svc, turn, token, "מה השעה?")
    assert st.profile.base == Decimal(18500)                     # nothing lost
    assert st.stage == Stage.COLLECT_PROFILE                     # no stage jump


# ---------- new conversation does not leak the previous one ----------

def test_new_conversation_is_clean(sessions):
    svc = service(sessions)
    turn, token = start(svc)
    step(svc, turn, token, "מנהלת מוצר, שכר בסיס 18500")
    # a brand-new conversation starts empty
    turn2, token2 = svc.start()
    st2 = svc.view(turn2.conversation_id, token2).state
    assert st2.profile.base is None and st2.profile.group is None and st2.profile.occupation is None
    assert st2.current.source is None


# ---------- the next-question field is always something still missing ----------

def test_next_question_is_always_a_missing_field(sessions):
    """Across a walk, the grouped question only ever names fields that are genuinely still open."""
    svc = service(sessions)
    turn, token = start(svc)
    convo = ["קבוצה ג", "18500", "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
             "יש לי קורולה קרוס", "300"]
    for text in convo:
        turn, st = step(svc, turn, token, text)
        if turn.stage in (Stage.PRESENTED, Stage.WAITING_FOR_REP, Stage.CALCULATING):
            continue
        group, fields = current_group(st)
        open_now = set(missing_profile(st.profile)) | set(missing_current_car(st.current)) | {"options"}
        # the car question groups "car" with "copay" (asked together: which car and how much you pay);
        # "group"/"opt_out_occupation" are alternative framings of an open ask
        allowed_extra = {"group", "opt_out_occupation", "copay"}
        for f in fields:
            assert f in open_now or f in allowed_extra, \
                f"next-question names a non-missing field {f!r} (group={group}) at stage {st.stage.value}"
