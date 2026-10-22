"""Data integrity (22/10, root causes M4 / M5 from the diagnosis): a value is saved only when the employee said it as
a fact or confirmed it, a value held for confirmation is never lost or skipped, and a missing amount is never 0.

Live conversation: "ב ושכר 45" held 45,000 for confirmation, but the model asked about commissions instead; then
"אתה יודע האם 45 או 45 אלף?" (a question) saved 45,000 because "45 אלף" appears in it, and the reply said both
"רשמתי: שכר בסיס 45,000 ₪" and "התכוונתם ל-45,000 ₪?"; "כן, אבל היית צריך לשאול אותי קודם" dropped the hold.
In template mode a bare "אין" / "לא" / "אין לי" to the car question was saved as copay 0 before any car was known,
and "אין לי רכב צמוד היום" was not recorded at all.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

NOA = "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"
GROUP_B_PROFILE = ["קבוצה ב, שכר בסיס 18,000",
                   "אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"]


class Chat:
    """One conversation, in template mode or with a scripted model."""

    def __init__(self, sessions, llm: FakeLLM | None = None):
        self.llm = llm
        self.svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([llm]) if llm else LLMChain())
        turn, self.token = self.svc.start()
        self.cid = turn.conversation_id

    def say(self, text: str, *calls, reply: str | None = None):
        if self.llm is not None and (calls or reply is not None):
            self.llm.turn(*calls, reply=reply)
        return self.svc.send(self.cid, self.token, text)

    @property
    def state(self):
        return self.svc.view(self.cid, self.token).state


# ---------- M4: a value held for confirmation is always the question ----------

def test_a_held_value_is_asked_even_when_the_model_asks_something_else(sessions):
    chat = Chat(sessions, FakeLLM())
    turn = chat.say("ב ושכר 45", call("save_profile", eligibility_group="ב", base_salary=45),
                    reply="רשמתי: קבוצה ב.\nהאם יש לכם עמלות או בונוסים נוספים בתלוש?")
    s = chat.state
    assert s.profile.base is None                                   # held, not saved
    assert s.pending_confirmation is not None and s.pending_confirmation.value == Decimal("45000")
    assert "45,000" in turn.reply and "עמלות" not in turn.reply      # the held question, not another one


# ---------- M5: a value in a question is never saved; a save resolves its own hold ----------

def test_a_question_about_a_held_value_never_saves_it(sessions):
    chat = Chat(sessions, FakeLLM())
    chat.say("ב ושכר 45", call("save_profile", eligibility_group="ב", base_salary=45), reply="מה עוד?")
    turn = chat.say("אתה יודע האם 45 או 45 אלף?", call("save_profile", base_salary=45000))
    s = chat.state
    assert s.profile.base is None
    assert s.pending_confirmation is not None and s.pending_confirmation.field == "base_salary"
    assert "רשמתי" not in turn.reply
    assert "45,000" in turn.reply                                    # asked again


@pytest.mark.parametrize("text, value", [("18,000 או 18,500?", 18000), ("אולי 18 או 19 אלף", 18000)])
def test_values_offered_as_alternatives_are_not_saved(sessions, text, value):
    chat = Chat(sessions, FakeLLM())
    chat.say("קבוצה ג", call("save_profile", eligibility_group="ג"), reply="רשמתי: קבוצה ג.\nמה שכר הבסיס החודשי שלכם?")
    chat.say(text, call("save_profile", base_salary=value))
    assert chat.state.profile.base is None


@pytest.mark.parametrize("mode", ["template", "llm"])
def test_saving_the_held_field_resolves_the_hold(sessions, mode):
    chat = Chat(sessions, FakeLLM() if mode == "llm" else None)
    chat.say("ב ושכר 45", *( [call("save_profile", eligibility_group="ב", base_salary=45)] if mode == "llm" else []))
    turn = chat.say("45,000", *([call("save_profile", base_salary=45000)] if mode == "llm" else []))
    s = chat.state
    assert s.profile.base == Decimal("45000")
    assert s.pending_confirmation is None
    assert "התכוונתם" not in turn.reply                              # never "recorded" and "did you mean" together


# ---------- "כן, אבל…": the answer is read, the rest is read too ----------

@pytest.mark.parametrize("text", ["כן, אבל היית צריך לשאול אותי קודם", "נכון, ארבעים וחמש אלף",
                                  "כן כן, ארבעים וחמש אלף", "בטח, 45 אלף"])
def test_yes_with_more_words_confirms_the_held_value(sessions, text):
    chat = Chat(sessions)
    chat.say("ב ושכר 45")
    assert chat.state.pending_confirmation is not None
    chat.say(text)
    s = chat.state
    assert s.profile.base == Decimal("45000")
    assert s.pending_confirmation is None


def test_no_with_more_words_does_not_confirm(sessions):
    chat = Chat(sessions)
    chat.say("ב ושכר 45")
    chat.say("לא, התכוונתי למשהו אחר")
    s = chat.state
    assert s.profile.base is None
    assert s.pending_confirmation is None                            # answered "no": the salary is asked again


def test_another_answer_keeps_the_held_value_and_asks_it_again(sessions):
    chat = Chat(sessions)
    chat.say("ב ושכר 45")
    turn = chat.say("אין עמלות")
    s = chat.state
    assert s.profile.commissions == Decimal("0")                     # the other answer is saved
    assert s.profile.base is None
    assert s.pending_confirmation is not None                        # the held value is not dropped silently
    assert "45,000" in turn.reply


def test_a_salary_correction_after_a_result_is_confirmed_then_recalculated(sessions):
    chat = Chat(sessions)
    for m in (NOA, "יש לי קורולה קרוס", "300", "אטו 3"):
        chat.say(m)
    before = chat.state.results[0].result["delta_display"]
    chat.say("טעיתי, השכר שלי 45")
    assert chat.state.profile.base == Decimal("18500")              # not replaced before it is confirmed
    chat.say("כן")
    s = chat.state
    assert s.profile.base == Decimal("45000")
    assert s.calculated_for == s.selected and s.results[0].result["delta_display"] != before


@pytest.mark.parametrize("text, saved", [("3,000", 3000), ("שלושת אלפים", 3000), ("בטח, 3 אלף", 3000),
                                         ("3 שקלים", 3), ("כן", None), ("3 או 3,000?", None)])
def test_a_small_amount_is_saved_only_once_its_scale_is_said(sessions, text, saved):
    chat = Chat(sessions)
    chat.say("קבוצה ג, שכר בסיס 18,500")
    chat.say("עמלות 3")
    assert chat.state.pending_confirmation is not None and chat.state.pending_confirmation.scale
    turn = chat.say(text)
    s = chat.state
    if saved is None:
        assert s.profile.commissions is None and s.pending_confirmation is not None   # still asked, never guessed
        assert "3,000" in turn.reply
    else:
        assert s.profile.commissions == Decimal(saved) and s.pending_confirmation is None


def test_a_question_about_the_held_value_never_confirms_it(sessions):
    chat = Chat(sessions, FakeLLM())
    chat.say("ב ושכר 45", call("save_profile", eligibility_group="ב", base_salary=45), reply="מה עוד?")
    chat.say("זה 45 אלף?", call("save_profile", base_salary=45000))
    s = chat.state
    assert s.profile.base is None and s.pending_confirmation is not None


def test_yes_with_another_value_saves_both(sessions):
    chat = Chat(sessions)
    chat.say("ב ושכר 45")
    chat.say("כן, ופנסיה 6%")
    s = chat.state
    assert s.profile.base == Decimal("45000") and s.profile.pension_percent == Decimal("6")


def test_yes_with_a_different_amount_is_not_a_confirmation(sessions):
    chat = Chat(sessions)
    chat.say("ב ושכר 45")
    chat.say("כן, 50 אלף")
    assert chat.state.profile.base == Decimal("50000")              # the amount said wins, never 45,000


# ---------- a missing amount is never 0; "no car" is recorded however it is said ----------

@pytest.mark.parametrize("text", ["אין", "לא", "אין לי"])
@pytest.mark.parametrize("mode", ["template", "llm"])
def test_a_bare_no_to_the_car_question_is_never_copay_zero(sessions, text, mode):
    chat = Chat(sessions, FakeLLM() if mode == "llm" else None)
    for m in GROUP_B_PROFILE:
        chat.say(m)
    chat.say(text, *([call("save_profile", current_copay=0)] if mode == "llm" else []))
    s = chat.state
    assert s.current.copay is None                                   # no car known: "אין" may mean "no car"
    assert s.pending_ambiguity is not None                           # asked, never guessed


def test_copay_zero_is_saved_once_the_car_is_known(sessions):
    chat = Chat(sessions)
    for m in GROUP_B_PROFILE:
        chat.say(m)
    chat.say("יש לי נירו")
    chat.say("אין")
    assert chat.state.current.copay == Decimal("0")


@pytest.mark.parametrize("text", ["אין לי רכב צמוד", "אין לי רכב צמוד היום", "אין לי רכב חברה",
                                  "אין לי כרגע רכב", "אין לי רכב של החברה", "בלי רכב צמוד"])
def test_no_car_said_with_its_usual_words_is_recorded(sessions, text):
    chat = Chat(sessions)
    for m in GROUP_B_PROFILE:
        chat.say(m)
    turn = chat.say(text)
    s = chat.state
    assert s.current.source == "none"
    assert s.current.copay is None
    assert "לא זיהיתי" not in turn.reply


def test_has_a_company_car_is_still_a_car(sessions):
    chat = Chat(sessions)
    for m in GROUP_B_PROFILE:
        chat.say(m)
    chat.say("יש לי רכב צמוד")
    s = chat.state
    assert s.current.source is None and s.current.has_car
