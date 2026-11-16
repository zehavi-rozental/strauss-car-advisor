"""Manual check on 8870 (16/11): a partial answer skipped the study fund, an explanation was a tautology, the
waiting question changed after it, cars chosen too early got no cards and no word why, and one value was
acknowledged twice.

Root causes:
- one value ("5" to "פנסיה וקרן השתלמות?") set `q2_answered`, so every other Q2 field moved behind the car question;
  now the leftovers move only when most of the grouped question was answered in one message (the official scenario);
- the FAQ entries said "זה האחוז שיורד לכם…" and nothing more, so a real explanation by the model had no grounded source
  and the claim verifier replaced it; the entries now say what the term is and what it means for the calculation, in
  the engine's terms, with numbers from strauss-rules.json;
- a turn that gave no data asked the group's first field, and the model's own words could ask another; now the field
  the employee was asked before the turn is asked again, and a model question about another field is replaced;
- values saved by `_fill_gaps` were acknowledged by their own line and again by the state-diff acknowledgement.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.conversation.templates import COMPARE_WAITS
from app.modules.rules.public import RulesStore

from .conftest import CONFIG


def service(sessions, llm=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())


def at_q2(svc):
    turn, token = svc.start()
    cid = turn.conversation_id
    svc.send(cid, token, "קבוצה ב")
    svc.send(cid, token, "שכר בסיס 20000")
    return cid, token


# ---------- 1. a partial answer never skips a field ----------

def test_one_value_keeps_the_rest_of_q2_before_the_car(sessions):
    svc = service(sessions)
    cid, token = at_q2(svc)
    turn = svc.send(cid, token, "פנסיה 5%")
    assert turn.stage == Stage.COLLECT_PROFILE
    assert "קרן השתלמות" in turn.reply and "חסר עוד" not in turn.reply and "רכב" not in turn.reply.split("\n")[-1]
    p = svc.view(cid, token).state.profile
    assert p.pension_percent == 5 and p.study_fund_percent is None


def test_a_minority_answer_keeps_the_contributions_on_q2(sessions):
    svc = service(sessions)
    cid, token = at_q2(svc)
    turn = svc.send(cid, token, "אין עמלות ואין בונוס")
    assert turn.stage == Stage.COLLECT_PROFILE and "חסר עוד" not in turn.reply
    assert "פנסיה" in turn.reply and "קרן השתלמות" in turn.reply


def live_shape(svc):
    """As in the live conversation: commissions and bonus with the salary, then credit points and children; the
    contributions question ("פנסיה, קרן השתלמות?") is on screen."""
    turn, token = svc.start()
    cid = turn.conversation_id
    svc.send(cid, token, "קבוצה ב")
    svc.send(cid, token, "שכר בסיס 20000, אין עמלות ואין בונוס")
    turn = svc.send(cid, token, "2.25 נקודות זיכוי, אין ילדים")         # 2 of 4: the contributions stay
    assert turn.stage == Stage.COLLECT_PROFILE and "פנסיה" in turn.reply and "קרן השתלמות" in turn.reply
    return cid, token


def test_one_contribution_saved_then_only_the_other_is_asked_and_none_is_zero(sessions):
    svc = service(sessions)
    cid, token = live_shape(svc)
    turn = svc.send(cid, token, "5")                # the contributions pair asked, no model: asked again, never guessed
    p = svc.view(cid, token).state.profile
    assert p.pension_percent is None and p.study_fund_percent is None and turn.stage == Stage.COLLECT_PROFILE
    turn = svc.send(cid, token, "פנסיה 5%")
    p = svc.view(cid, token).state.profile
    assert p.pension_percent == 5 and p.study_fund_percent is None
    assert turn.stage == Stage.COLLECT_PROFILE and "קרן השתלמות" in turn.reply and "פנסיה" not in turn.reply.split(
        "\n\n")[-2]                                            # the question names only what is still missing
    turn = svc.send(cid, token, "אין")                        # "אין" to the study fund question alone: 0, said
    p = svc.view(cid, token).state.profile
    assert p.study_fund_percent == 0 and p.pension_percent == 5
    assert "אין קרן השתלמות" in turn.reply and turn.stage == Stage.COLLECT_CURRENT_CAR


def test_not_knowing_is_recorded_as_unknown_never_a_value(sessions):
    svc = service(sessions)
    cid, token = live_shape(svc)
    svc.send(cid, token, "פנסיה 6%")
    svc.send(cid, token, "לא יודעת כמה קרן השתלמות")
    p = svc.view(cid, token).state.profile
    assert p.study_fund_percent is None and "study_fund_percent" in p.unknown_fields


def test_each_field_has_its_own_status_no_majority_rule(sessions):
    """4 of 6 answered: the other two are asked next, one at a time, before the car — never moved along as a group."""
    svc = service(sessions)
    cid, token = at_q2(svc)
    turn = svc.send(cid, token, "אין עמלות, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי")
    assert turn.stage == Stage.COLLECT_PROFILE and "חסר עוד" not in turn.reply
    asked = [ln for ln in turn.reply.split("\n") if "?" in ln][-1]
    assert "בונוס" in asked and "פנסיה" not in asked and "ילדים" not in asked
    turn = svc.send(cid, token, "אין בונוס")
    asked = [ln for ln in turn.reply.split("\n") if "?" in ln][-1]
    assert "ילדים" in asked and turn.stage == Stage.COLLECT_PROFILE
    turn = svc.send(cid, token, "2 ילדים")
    assert turn.stage == Stage.COLLECT_CURRENT_CAR


def test_part_of_the_question_answered_is_never_a_pass(sessions):
    svc = service(sessions)
    cid, token = at_q2(svc)
    svc.send(cid, token, "אין עמלות, אין בונוס, פנסיה 6%, 2.25 נקודות זיכוי")   # 4 values: no pass
    st = svc.view(cid, token).state
    assert not st.deferred_fields and not st.passed_over
    turn = svc.send(cid, token, "אין ילדים")     # the study fund was asked; another value given: counted once, asked again
    assert "קרן השתלמות" in turn.reply
    st = svc.view(cid, token).state
    assert not st.deferred_fields and st.passed_over == {"study_fund_percent": 1}


def test_a_field_passed_over_twice_is_postponed_said_and_never_assumed():
    """The rule itself, on a state: the study fund asked, the answer gives something else (today's car, with the model)."""
    from decimal import Decimal

    from app.modules.conversation.agent import _Before, _pass_over
    from app.modules.conversation.stage import ConversationState, hidden_deferred

    state = ConversationState()
    p = state.profile
    p.group, p.base, p.commissions, p.bonus, p.pension_percent = "ב", Decimal(20000), 0, 0, Decimal(6)
    p.credit_points, p.children = Decimal("2.25"), 0
    state.asked_line = "כמה אחוזים מופרשים מכם לקרן השתלמות?"
    before = _Before.of(state)
    state.current.copay = Decimal(300)                    # not what was asked: passed over once
    assert _pass_over(state, before) == "" and state.passed_over == {"study_fund_percent": 1}
    before = _Before.of(state)
    state.current.copay = Decimal(250)                    # again: postponed, and said
    assert _pass_over(state, before) == "בסדר, נחזור לקרן ההשתלמות כשנצטרך את זה לחישוב."
    assert state.deferred_fields == ["study_fund_percent"] and p.study_fund_percent is None
    assert hidden_deferred(state) == {"study_fund_percent"}     # other things to ask first (today's car)
    state.current.source, state.current.maintenance = "none", Decimal(0)
    state.selected = ["CAR-06"]                           # nothing else left, cars chosen: it comes back for the calculation
    assert hidden_deferred(state) == set()


# ---------- 4 known, 2 unknown: back to them for the calculation, an estimate, a late exact value ----------

def test_four_known_two_unknown_estimate_then_exact(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    cid = turn.conversation_id
    svc.send(cid, token, "קבוצה ב")
    svc.send(cid, token, "שכר בסיס 20000, אין עמלות ואין בונוס")
    svc.send(cid, token, "פנסיה 6%, אין ילדים")                              # 4 known
    svc.send(cid, token, "לא יודעת כמה קרן השתלמות")
    turn = svc.send(cid, token, "לא יודעת כמה נקודות זיכוי")
    p = svc.view(cid, token).state.profile
    assert set(p.unknown_fields) == {"study_fund_percent", "credit_points"}
    assert p.study_fund_percent is None and p.credit_points is None          # nothing guessed, no 0
    assert turn.stage == Stage.COLLECT_CURRENT_CAR                           # the conversation goes on
    assert "נקודות זיכוי" not in [ln for ln in turn.reply.split("\n") if "?" in ln][-1]   # not asked again at once
    turn = svc.send(cid, token, "קורולה קרוס 2025, משלמת 300")
    assert turn.stage == Stage.CHOOSE_OPTIONS
    # the calculation needs them: asked again gently, once, with the estimate offered; no net, no default
    turn = svc.send_choice(cid, token, ["CAR-06", "CAR-05"], "options")
    assert not turn.results and "אולי כבר מצאתם" in turn.reply and "הערכה" in turn.reply
    turn = svc.send(cid, token, "עדיין לא יודעת")
    assert not turn.results and "אין נטו אמין" in turn.reply and "לא אנחש ולא אציב 0" in turn.reply
    assert "קרן השתלמות" in turn.reply and "נקודות זיכוי" in turn.reply       # everything it waits for is named
    # an estimate the employee gives: recorded as one, said as one
    turn = svc.send(cid, token, "קרן השתלמות בערך 2.5%")
    st = svc.view(cid, token).state
    assert st.profile.study_fund_percent == 2.5 and "study_fund_percent" in st.profile.approximate_fields
    assert "סימנתי כהערכה: קרן השתלמות כ-2.5%" in turn.reply and turn.reply.count("2.5%") == 1
    turn = svc.send(cid, token, "2.25 נקודות זיכוי")
    assert turn.stage == Stage.PRESENTED and len(turn.results) == 2
    assert all(any("קרן השתלמות: כ-2.5%, לפי ההערכה שלכם" in a for a in r.result["assumptions"]) for r in turn.results)
    estimated_net = turn.results[0].result["alternative"]["net_display"]
    # later, the exact value: replaces the estimate, said once, recalculated, no longer marked
    turn = svc.send(cid, token, "קרן השתלמות 2%")
    st = svc.view(cid, token).state
    assert st.profile.study_fund_percent == 2 and "study_fund_percent" not in st.profile.approximate_fields
    assert turn.reply.count("עדכנתי") == 1 and turn.results
    assert not any("לפי ההערכה שלכם" in a for r in turn.results for a in r.result["assumptions"])
    assert int(turn.results[0].result["alternative"]["net_display"]) == int(estimated_net) + 100   # 0.5% of 20,000


def test_with_the_model_an_estimate_is_recorded_as_one(sessions):
    from app.modules.conversation.llm.port import ToolCall
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    plain = service(sessions)
    turn, token = plain.start()
    cid = turn.conversation_id
    for text in ("קבוצה ב", "שכר בסיס 20000, אין עמלות ואין בונוס", "פנסיה 6%, 2.25 נקודות זיכוי, אין ילדים"):
        plain.send(cid, token, text)
    fake.turn(ToolCall("save_profile", {"study_fund_percent": "2.5"}, "s1"),
              reply="רשמתי: קרן השתלמות 2.5%.\nמה הדגם של הרכב הצמוד שלכם?")
    turn = svc.send(cid, token, "בערך 2.5% קרן השתלמות")
    p = svc.view(cid, token).state.profile
    assert p.study_fund_percent == 2.5 and "study_fund_percent" in p.approximate_fields
    assert "סימנתי כהערכה: קרן השתלמות כ-2.5%" in turn.reply and turn.reply.count("2.5%") == 1


# ---------- 2. explanations: grounded, and back to the waiting field ----------

def test_the_study_fund_is_explained_with_its_meaning_for_the_calculation(snapshot):
    entry = snapshot.faq.get("study_fund_percent")
    assert entry.answer.startswith("קרן השתלמות היא חיסכון")
    assert "יורד מהנטו" in entry.answer and "לא משתנה כשמחליפים רכב" in entry.answer
    assert "מהשכר (בסיס, עמלות ובונוס)" in entry.answer                       # contributionsBase: gross
    pension = snapshot.faq.get("pension_percent").answer
    assert "35%" in pension and "232.75" in pension                          # 35% × 7% × 9,500, from the rules


def test_after_an_explanation_the_waiting_field_is_asked_again(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    cid = turn.conversation_id
    for text in ("קבוצה ב", "שכר בסיס 20000, אין עמלות ואין בונוס", "2.25 נקודות זיכוי, אין ילדים"):
        svc.send(cid, token, text)                       # pension and study fund wait
    view = svc.view(cid, token).state
    assert view.asked_field in (None, "pension_percent")
    turn = svc.send(cid, token, "מה זה קרן השתלמות?")
    assert "קרן השתלמות היא חיסכון" in turn.reply
    assert svc.view(cid, token).state.profile.study_fund_percent is None      # a question is never an answer


@pytest.mark.parametrize("asked", ["מה אחוז ההפרשה שלכם לקרן השתלמות?",
                                   "אילו הפרשות יורדות לכם: פנסיה וקרן השתלמות? כמה נקודות זיכוי יש לכם?"])
def test_with_the_model_an_explanation_never_changes_the_waiting_question(sessions, asked):
    """Gemini explains in its words and then asks about the study fund; the pension was waiting: the explanation stays,
    the question is code's."""
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    plain = service(sessions)
    turn, token = plain.start()
    cid = turn.conversation_id
    for text in ("קבוצה ב", "שכר בסיס 20000, אין עמלות ואין בונוס", "2.25 נקודות זיכוי, אין ילדים"):
        plain.send(cid, token, text)                     # the pension and the study fund wait
    fake.turn(reply="קרן השתלמות היא חיסכון נפרד מהפנסיה, שמפרישים אליו מהשכר בכל חודש. [[faq:study_fund_percent]]\n"
                    + asked)
    turn = svc.send(cid, token, "מה זה קרן השתלמות?")
    assert "קרן השתלמות היא חיסכון" in turn.reply                       # the model's explanation stays
    questions = [ln for ln in turn.reply.split("\n") if "?" in ln]
    assert len(questions) == 1                                          # one question, never two
    question = questions[0]
    assert "פנסיה" in question and "השתלמות" not in question           # the waiting field, not the one explained
    assert svc.view(cid, token).state.asked_field == "pension_percent"


# ---------- 3. no cards before the data, and why ----------

def test_cars_chosen_while_a_value_is_unknown_get_no_cards_and_the_reason(sessions):
    svc = service(sessions)
    cid, token = at_q2(svc)
    svc.send(cid, token, "אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי")
    svc.send(cid, token, "לא יודעת כמה ילדים")
    svc.send(cid, token, "יש לי קורולה קרוס, משלמת 300")
    turn = svc.send_choice(cid, token, ["CAR-06", "CAR-05"], "options")
    assert not turn.results and "ילדים" in turn.reply and "אולי כבר מצאתם" in turn.reply
    assert COMPARE_WAITS not in turn.reply                      # the reason is said once, by the unknown question
    turn = svc.send(cid, token, "אין ילדים")
    assert turn.stage == Stage.PRESENTED and len(turn.results) == 2
    for r in turn.results:                                      # the chat names each car's net from the same result
        assert f"{int(r.result['alternative']['net_display']):,}" in turn.reply


# ---------- 4. a correction: saved, said once, recalculated ----------

def test_a_copay_correction_is_said_once_and_recalculated(sessions):
    svc = service(sessions)
    cid, token = at_q2(svc)
    svc.send(cid, token, "אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, אין ילדים")
    svc.send(cid, token, "יש לי קורולה קרוס, משלמת 300")
    before = svc.send_choice(cid, token, ["CAR-06"], "options").results[0].result
    turn = svc.send(cid, token, "ההשתתפות שלי 250 ולא 300")
    assert turn.reply.count("250 ₪ במקום 300 ₪") == 1 and turn.reply.count("עדכנתי") == 1
    after = turn.results[0].result
    assert after["today"]["copay"] == "250" and int(after["today"]["net_display"]) == int(before["today"]["net_display"]) + 50


def test_a_gap_value_is_acknowledged_once_with_the_model(sessions):
    """The model saves the credit points and names the children too; code saves the children (a gap): one line."""
    from app.modules.conversation.llm.port import ToolCall
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    turn, token = svc.start()
    cid = turn.conversation_id
    plain = service(sessions)
    plain.send(cid, token, "קבוצה ב")
    plain.send(cid, token, "שכר בסיס 20000, אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%")
    fake.turn(ToolCall("save_profile", {"credit_points": "2.25"}, "c1"),
              reply="רשמתי: 2.25 נקודות זיכוי, אין ילדים.\nמה הדגם של הרכב הצמוד שלכם?")
    turn = svc.send(cid, token, "2.25 נקודות זיכוי, אין ילדים")
    p = svc.view(cid, token).state.profile
    assert p.credit_points == 2.25 and p.children == 0
    assert turn.reply.count("אין ילדים") == 1
