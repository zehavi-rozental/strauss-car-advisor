"""The bot stays within STRAUSS DRIVE: an unrelated question is redirected politely and never offered to the
transport representative (QA 22/10).

Found: "מה מזג האויר?" (one vav) got "על זה אין לי מידע מדויק… רוצים שאשאל אותו?" with the rep buttons. The scope
check was only a list of off-topic phrases, matched exactly, and any question with no FAQ or catalog answer
defaulted to the representative. Now a question with no answer is offered to the rep only when it is about the
service (`understanding.in_scope`: the domain terms in faq.yaml, the FAQ topics, catalog cars); otherwise one
redirect line. In LLM mode the model's own propose_rep_question is refused for it, in code."""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.conversation.understanding import in_scope, understand
from app.modules.conversation.stage import ConversationState
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CONFIG

SNAP = load_snapshot(CONFIG)
Q1 = "מנהלת מוצר, שכר בסיס 18,500"
REDIRECT = SNAP.faq.redirect

OFF_TOPIC = ["מה מזג האויר?", "מה מזג האוויר?", "מה מזג האויר היום?", "מה השעה?", "מי ניצח במשחק אתמול?",
             "יש לך מתכון לעוגה?", "תספר לי בדיחה?", "מה בירת צרפת?", "איך מכינים קפה?"]
IN_SCOPE_UNCOVERED = ["אפשר לקבל אטו 3 בצבע אחר?", "מתי אפשר להחליף את הרכב?", "האם יש ביטוח מקיף על הרכב?",
                      "מי משלם על הדלק בחופשה?", "אפשר לקבל רכב עם גג נפתח?"]


def service(sessions, fake=None):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]) if fake else LLMChain())


def start(svc, *texts):
    turn, token = svc.start()
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
    return turn, token


def state(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


@pytest.mark.parametrize("question", OFF_TOPIC)
def test_an_off_topic_question_is_not_in_scope(question):
    assert not in_scope(SNAP, question)
    u = understand(ConversationState(), SNAP, question)
    assert u.redirect and not u.unanswered


@pytest.mark.parametrize("question", IN_SCOPE_UNCOVERED)
def test_a_question_about_the_service_with_no_answer_is_in_scope(question):
    assert in_scope(SNAP, question)
    assert understand(ConversationState(), SNAP, question).unanswered          # the representative may know


@pytest.mark.parametrize("question", OFF_TOPIC)
def test_template_mode_redirects_and_never_offers_the_representative(sessions, question):
    svc = service(sessions)
    turn, token = start(svc, Q1, question)
    assert REDIRECT in turn.reply
    assert "נציג" not in turn.reply and "אשאל" not in turn.reply
    assert state(svc, turn, token).unanswered_question is None
    assert not any(f["field"] == "ask_rep" for f in turn.expects.get("fields", []))
    assert state(svc, turn, token).profile.base is not None                     # nothing was lost


def test_the_redirect_says_what_the_service_is_for():
    assert REDIRECT == ("אני כאן כדי לעזור לכם להבין איך בחירת רכב החברה משפיעה על הנטו החודשי שלכם, "
                        "ולהשוות בין האפשרויות לפי הסדרי שטראוס. אפשר לשאול אותי על רכבי החברה, הזכאות "
                        "וההשפעה על השכר.")


@pytest.mark.parametrize("question", IN_SCOPE_UNCOVERED[:2])
def test_template_mode_still_offers_the_representative_for_a_question_about_the_service(sessions, question):
    svc = service(sessions)
    turn, token = start(svc, Q1, question)
    assert "נציג התחבורה" in turn.reply
    assert any(f["field"] == "ask_rep" for f in turn.expects.get("fields", []))


def test_a_faq_question_is_answered_not_redirected(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, "מה זה נקודות זיכוי?")
    assert REDIRECT not in turn.reply and state(svc, turn, token).unanswered_question is None


def test_llm_mode_refuses_the_representative_for_an_off_topic_question(sessions):
    fake = FakeLLM().turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500), reply="רשמתי: שכר בסיס 18,500 ₪.")
    svc = service(sessions, fake)
    turn, token = start(svc, Q1)
    fake.turn(call("propose_rep_question", question="מה מזג האויר?"),
              reply="על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?")
    turn = svc.send(turn.conversation_id, token, "מה מזג האויר?")
    assert state(svc, turn, token).unanswered_question is None                 # the tool call was refused
    assert not any(f["field"] == "ask_rep" for f in turn.expects.get("fields", []))


def test_a_mixed_message_keeps_the_values_and_redirects_only_the_question(sessions):
    svc = service(sessions)
    turn, token = start(svc, "מנהלת מוצר, שכר בסיס 18,500. מה מזג האויר?")
    assert state(svc, turn, token).profile.base is not None
    assert "נציג" not in turn.reply


# ---------- follow-ups and varied wordings stay in scope (review before publishing, 22/10) ----------
# Measured against every question in the test corpora and these follow-ups, old code vs new: only plainly
# off-topic questions and small talk changed from the rep offer to the redirect.
FOLLOW_UPS = ["למה?", "ומה זה?", "מה זה אומר?", "כמה זה יוצא בשנה?", "ומה עם הרכב השני?", "ואם יש לי 3 ילדים?",
              "מה ההבדל ביניהם?", "זה כולל מע\"מ?", "ואיך זה משפיע עליי?", "אז מה עדיף?", "ולמה זה ככה?",
              "אפשר להסביר שוב?", "גם אם אני בחופשת לידה?", "מתי זה נכנס לתוקף?", "ואם אני עוזבת את החברה?",
              "כמה זה עולה לי?", "זה סופי?", "מי מחליט על זה?", "ומה קורה בסוף השנה?", "אפשר לשנות אחר כך?"]
VARIED = ["איזה רכב צמוד יש לכם היום, וכמה משלמים עליו בחודש?",      # one question in two clauses
          "מה הסכום המלא מהתלוש?",                                     # two prefix letters: מ+ה+תלוש
          "התכוונתם ל-18,000 ₪?", "60% זה הרבה?",                       # an amount, a rate
          "מנהל/ת מוצר?"]                                               # a job title


@pytest.mark.parametrize("question", FOLLOW_UPS + VARIED)
def test_follow_ups_and_varied_wordings_are_never_redirected(question):
    u = understand(ConversationState(), SNAP, question)
    assert not u.redirect, question


@pytest.mark.parametrize("question", ["ומה זה?", "למה?", "כמה זה יוצא בשנה?"])
def test_a_follow_up_in_the_conversation_is_not_told_it_is_off_topic(sessions, question):
    svc = service(sessions)
    turn, token = start(svc, Q1, question)
    assert REDIRECT not in turn.reply
