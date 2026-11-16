"""Questions about data the calculation still needs (16/11).

Root cause: the FAQ matcher picks one entry per clause by its longest alias, so "איך אתה מחשב את הנטו בלי ההשתתפות?"
was the general "how I work" entry (the verb phrase), never the value it asks about; and no verified source said what
can and cannot be calculated without a value, so the model's correct explanation was cut by the claim verifier.
Now the missing-data facts (conversation.data_facts) come from the state and from how the engine uses each value, and
answer such a question in both modes: in the model's prompt, for the claim verifier, and as code's own lines.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.modules.calculation.models import CarState, Employee
from app.modules.calculation.public import evaluate
from app.modules.conversation import data_facts
from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

PROFILE = "קבוצה ב, שכר בסיס 20000, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, אין ילדים"
COPAY_UNKNOWN = [PROFILE, "יש לי קורולה קרוס", "לא ידוע", "לא ידוע"]   # the second "לא ידוע" is recorded (16/11)
CREDIT_UNKNOWN = ["קבוצה ב", "שכר בסיס 20000, אין עמלות ואין בונוס", "פנסיה 6%, קרן השתלמות 2.5%, אין ילדים",
                  "לא יודעת כמה נקודות זיכוי"]


def service(sessions, llm=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())


def at(svc, messages):
    turn, token = svc.start()
    for m in messages:
        turn = svc.send(turn.conversation_id, token, m)
    return turn.conversation_id, token


# ---------- the facts say what the engine does, never more ----------

def _employee(**change):
    base = dict(group="ב", base=D(20000), commissions=D(0), bonus=D(0), pension_rate=D("0.06"),
                study_fund_rate=D("0.025"), credit_points=D("2.25"), eligible_children=0)
    return Employee(**{**base, **change})


def _today(copay=D(300), use_value=D(4070)):
    return CarState(label="COROLLA CROSS (2025)", group="ג", drive="HYBRID", use_value=use_value, copay=copay)


ALT = CarState(label="MG4 (2025)", group="ב", drive="ELECTRIC", use_value=D(2430), copay=D(250))


def test_todays_car_values_move_todays_net_and_the_change_never_a_new_cars_net(snapshot):
    rules = snapshot.calc
    base = evaluate(_employee(), _today(), ALT, rules)
    for today in (_today(copay=D(500)), _today(use_value=D(5000))):
        other = evaluate(_employee(), today, ALT, rules)
        assert other.alternative.net_display == base.alternative.net_display          # the new car's net: same
        assert other.today.net_display != base.today.net_display                      # today's net: moves
        assert other.delta_display != base.delta_display                              # the change: moves
    unknown = evaluate(_employee(), _today(copay=None), ALT, rules)
    assert unknown.status.value == "INCOMPLETE" and unknown.alternative.net_display == base.alternative.net_display
    assert set(data_facts.TODAY_ONLY) >= {"copay", "use_value", "maintenance"}


def test_a_salary_value_is_needed_for_every_net(snapshot):
    result = evaluate(_employee(credit_points=None), _today(), ALT, snapshot.calc)
    assert result.status.value == "INCOMPLETE" and result.alternative is None
    assert "credit_points" in data_facts.EVERY_NET and "base" in data_facts.EVERY_NET


# ---------- in the conversation (no model) ----------

@pytest.mark.parametrize("setup, question, field", [
    (COPAY_UNKNOWN, "איך אתה מחשב את הנטו בלי ההשתתפות?", "copay"),
    (COPAY_UNKNOWN, "ומה אם אני לא יודעת את ההשתתפות?", "copay"),
    (COPAY_UNKNOWN, "אפשר לחשב בלי לדעת כמה אני משלמת על הרכב?", "copay"),
    (COPAY_UNKNOWN, "ואם אני לא אמסור את ההשתתפות, מה תעשה?", "copay"),
    (COPAY_UNKNOWN, "איך מחשבים את הנטו?", "copay"),
    (CREDIT_UNKNOWN, "איך תחשב בלי נקודות הזיכוי?", "credit_points"),
    (CREDIT_UNKNOWN, "מה יקרה אם לא אמצא את נקודות הזיכוי?", "credit_points"),
])
def test_a_question_about_missing_data_gets_the_facts_and_no_representative(sessions, setup, question, field):
    svc = service(sessions)
    cid, token = at(svc, setup)
    turn = svc.send(cid, token, question)
    d = turn.decision
    assert "explain:missing_data" in d["intent"] and data_facts.NAMES[field] in turn.reply
    expected = "הנטו ברכב חדש לא תלוי בו" if field in data_facts.TODAY_ONLY else "בכל אחת מהאפשרויות"
    assert expected in turn.reply and "אין חישוב נטו" in turn.reply and "הערכה" in turn.reply
    assert "נציג" not in d["ask"] and not turn.results                    # answered; nothing calculated, nothing guessed
    assert "לא בטוח שהבנתי" not in turn.reply and turn.reply.count("אין חישוב נטו") == 1
    state = svc.view(cid, token).state
    assert state.current.copay is None if field == "copay" else state.profile.credit_points is None


@pytest.mark.parametrize("setup, question", [
    (COPAY_UNKNOWN, "מה זה שווי שימוש?"),                                 # a term, not a missing value
    ([PROFILE, "יש לי קורולה קרוס, משלמת 300", "MG4"], "איך אתה מחשב את הנטו?"),   # nothing missing
    (COPAY_UNKNOWN, "300"),                                               # an answer, not a question
    (["קבוצה ב", "שכר בסיס 20000"], "מה זה נקודות זיכוי?"),             # a definition of a field not asked yet
    ([], "מאיפה אתה מביא את המידע?"),                                    # the advisor's sources, at the start
    ([], "המערכת שלך אמינה?"),
])
def test_no_missing_data_facts_where_none_is_asked(sessions, setup, question):
    svc = service(sessions)
    cid, token = at(svc, setup)
    turn = svc.send(cid, token, question)
    assert "explain:missing_data" not in turn.decision["intent"] and "הנתון הזה" not in turn.reply


def test_a_value_given_in_the_same_message_is_not_said_to_be_missing(sessions):
    """The facts follow the state after the turn's saves: a value saved in the same message is no longer missing."""
    from app.modules.conversation.llm.fake import call
    fake = FakeLLM()
    plain, svc = service(sessions), service(sessions, LLMChain([fake]))
    cid, token = at(plain, COPAY_UNKNOWN)
    fake.turn(call("save_profile", current_copay=300), reply="")
    turn = svc.send(cid, token, "אני משלמת 300, אז איך מחשבים בלי ההשתתפות?")
    assert svc.view(cid, token).state.current.copay == 300
    assert "הנתון הזה" not in turn.reply and "explain:missing_data" not in turn.decision["intent"]


# ---------- with the model ----------

def test_with_the_model_the_facts_reach_it_and_its_correct_explanation_is_used(sessions):
    fake = FakeLLM()
    plain, svc = service(sessions), service(sessions, LLMChain([fake]))
    cid, token = at(plain, COPAY_UNKNOWN)
    fake.turn(reply="בלי ההשתתפות של היום אי אפשר לחשב כמה הנטו ישתנה, כי היא נכנסת רק לנטו של היום. [[state]]\n"
                    "הנטו ברכב חדש לא תלוי בה. [[state]]\nרוצים לכתוב הערכה?")
    turn = svc.send(cid, token, "איך אתה מחשב את הנטו בלי ההשתתפות?")
    system = next(m.content for m in fake.calls[0].messages if m.role == "system")
    assert "MISSING DATA FACTS" in system and "הנטו ברכב חדש לא תלוי בו" in system
    assert "ואני לא מציב ערך במקומם" not in system                       # code's rule line: said by code only, once
    assert turn.decision["answer_source"] == "model" and "כי היא נכנסת רק לנטו של היום" in turn.reply
    assert "אין חישוב נטו, ואני לא מציב ערך" in turn.reply                # code's rule line, said as is
    assert "רוצים לכתוב הערכה?" not in turn.reply


def test_with_the_model_a_wrong_claim_about_missing_data_falls_back_to_the_facts(sessions):
    fake = FakeLLM()
    plain, svc = service(sessions), service(sessions, LLMChain([fake]))
    cid, token = at(plain, COPAY_UNKNOWN)
    fake.turn(reply="אם ההשתתפות לא ידועה, אחשב לפי 300 ₪ מהקטלוג. [[state]]").verdict([1]).verdict([1])
    turn = svc.send(cid, token, "איך אתה מחשב את הנטו בלי ההשתתפות?")
    assert "אחשב לפי 300" not in turn.reply and "הנטו ברכב חדש לא תלוי בו" in turn.reply


# ---------- the general "how I work" entry is not the answer (8870 smoke, 16/11) ----------

SOURCES = "אני עובד לפי כללי רכב החברה"


@pytest.mark.parametrize("question", ["איך אתה מחשב את הנטו בלי ההשתתפות?", "איך אתה מחשב בלי ההשתתפות"])
def test_a_missing_value_named_after_a_general_verb_gets_its_facts_not_the_advisors_sources(sessions, question):
    """Root cause: in a long question one-word aliases are skipped, so "איך אתה מחשב" (how the advisor works) was the
    clause's FAQ entry; the model was given two explanations and wrote the sources line. The value named is the topic."""
    svc = service(sessions)
    cid, token = at(svc, COPAY_UNKNOWN)
    turn = svc.send(cid, token, question)
    assert "explain:missing_data" in turn.decision["intent"] and "faq:how_it_works" not in turn.decision["intent"]
    assert SOURCES not in turn.reply and "הנטו ברכב חדש לא תלוי בו" in turn.reply


def test_with_the_model_only_the_missing_data_is_given_to_explain(sessions):
    fake = FakeLLM()
    plain, svc = service(sessions), service(sessions, LLMChain([fake]))
    cid, token = at(plain, COPAY_UNKNOWN)
    fake.turn(reply="ההשתתפות של היום נכנסת רק לנטו של היום, ולכן בלעדיה אי אפשר לדעת כמה הנטו ישתנה. [[state]]")
    turn = svc.send(cid, token, "איך אתה מחשב את הנטו בלי ההשתתפות?")
    system = next(m.content for m in fake.calls[0].messages if m.role == "system")
    assert "MISSING DATA FACTS" in system and SOURCES not in turn.reply
    assert turn.decision["answer_source"] == "model" and "נכנסת רק לנטו של היום" in turn.reply


def test_the_sources_question_still_gets_the_sources(sessions):
    svc = service(sessions)
    cid, token = at(svc, COPAY_UNKNOWN)
    turn = svc.send(cid, token, "איך אתה מחשב את הנטו?")
    assert "explain:how_it_works" in turn.decision["intent"] and SOURCES in turn.reply    # no value named: unchanged
