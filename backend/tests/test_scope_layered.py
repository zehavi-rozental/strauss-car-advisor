"""Layered scope of a question nothing else answers (QA 22/10, P0): conversations through the real service.

Measured before: 52% of off-topic questions were offered to the representative and 31% of in-scope ones were
told they are off topic (PR #3's rules: a number meant in scope, the whole message counted). Now
(`understanding.question_scope`, lists in faq.yaml): off topic → the redirect line; the employee's own pay → the
calculation; a concrete question about the cars or the arrangements → the representative may answer it; a
follow-up → the question on screen explained; truly unclear → one line asking to say it again, no buttons, never
the representative. A number alone is never a signal. In LLM mode the model cannot reach the representative for
a question code did not find concrete (`propose_rep_question` refused).

Also: the pace cue "לאט" is matched as a whole word, so "לאטו 3" (a catalog car) no longer switches the
conversation to one question at a time."""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.conversation.stage import ConversationState
from app.modules.conversation.understanding import says_phrase, understand
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CONFIG

SNAP = load_snapshot(CONFIG)
Q1 = "מנהלת מוצר, שכר בסיס 18,500"
Q2 = "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"
CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"
REDIRECT, CLARIFY = SNAP.faq.redirect, SNAP.faq.clarify
PACE_ONE = "בסדר, אשאל שאלה אחת בכל פעם."


def service(sessions, fake=None):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]) if fake else LLMChain())


def start(svc, *texts):
    turn, token = svc.start()
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
    return turn, token


def state(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


def offers_rep(turn) -> bool:
    return any(f["field"] in ("ask_rep", "rep_question") for f in turn.expects.get("fields", []))


def read(text):
    return understand(ConversationState(last_ask="מה קבוצת הזכאות שלכם?"), SNAP, text)


# ---------- single messages: each layer ----------

@pytest.mark.parametrize("text", ["כמה זה 20% מ-50?", "כמה עולה אייפון 15?", "מה שער הדולר היום, 3.7?",
                                  "מי זכה ב-2022 במונדיאל?", "מה הריבית של המשכנתא 4%?"])
def test_a_number_in_an_unrelated_question_never_reaches_the_representative(text):
    u = read(text)
    assert not u.unanswered


@pytest.mark.parametrize("text", ["אני מרוויחה יותר או פחות?", "כמה נשאר לי ביד בסוף?", "זה יעלה לי יותר מעכשיו?",
                                  "מה ההשפעה על הכיס שלי?", "מה הכי זול?"])
def test_a_question_about_my_own_net_is_answered_by_the_calculation(text):
    u = read(text)
    assert (u.compare or u.faq_ids) and not u.unanswered and not u.redirect     # the calculation, or the FAQ on net


@pytest.mark.parametrize("text", ["מי משלם על הביטוח?", "אפשר לבחור צבע?", "מה קורה בתאונה?", "יש משהו עם 7 מקומות?",
                                  "אפשר לקבל אטו 3 בצבע אחר?", "מה עם חופשת לידה?", "אני יכולה לקבל קיה?"])
def test_a_concrete_question_about_the_cars_or_the_arrangements_may_go_to_the_representative(text):
    u = read(text)
    assert u.unanswered or u.faq_ids or u.catalog                         # the FAQ / catalog first, else the rep


@pytest.mark.parametrize("text", ["ולמה?", "כלומר?", "והשני?", "ומה זה?", "בחודש או בשנה?", "אפשר דוגמה?"])
def test_a_follow_up_is_explained_never_redirected_or_sent(text):
    u = read(text)
    assert u.help and not u.unanswered and not u.redirect


@pytest.mark.parametrize("text", ["מה זה השתתפות?", "מה זה שווי שימוש?", "מה זה נקודות זיכוי?"])
def test_a_faq_question_with_a_follow_up_word_is_still_answered_from_the_faq(text):
    assert read(text).faq_ids


@pytest.mark.parametrize("text", ["מה הפנסייה?", "כמה הנטוו שלי?", "מה זה שווי שימש?", "מה עם המשכורת שלי?"])
def test_typos_and_spoken_hebrew_are_never_redirected(text):
    u = read(text)
    assert not u.redirect


@pytest.mark.parametrize("text", ["יש תקופת התחייבות?", "צריך חתימה של המנהל?", "מה הנוהל אצלכם?"])
def test_a_truly_unclear_question_gets_the_clarify_line_and_never_the_representative(text):
    u = read(text)
    assert u.clarify and not u.unanswered and not u.redirect


@pytest.mark.parametrize("text", ["שאלה לנציג: מי ניצח במשחק?", "תעביר לנציג התחבורה: מה מזג האויר?",
                                  "תשאל את הנציג מי ראש הממשלה", "מה מניית שטראוס עושה היום?",
                                  "נציג, איך מכינים קפה?"])
def test_naming_the_representative_or_strauss_does_not_open_the_gate(text):
    assert not read(text).unanswered


@pytest.mark.parametrize("text", ["300", "כן", "אין", "2", "18,500", "קורולה קרוס"])
def test_short_answers_are_not_questions(text):
    u = read(text)
    assert not (u.unanswered or u.redirect or u.clarify)


def test_shirut_is_not_shir():
    assert not read("מה השירות שלכם לגבי הרכב?").redirect           # "שיר" (a song) is not "שירות"


# ---------- conversations (template mode) ----------

def test_an_unrelated_question_mid_conversation_keeps_the_flow_and_never_offers_the_rep(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, Q2, "כמה זה 20% מ-50?")
    assert not offers_rep(turn) and state(svc, turn, token).unanswered_question is None
    assert state(svc, turn, token).profile.credit_points is not None             # nothing lost


def test_a_mixed_message_saves_the_values_and_redirects_only_the_question(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, "פנסיה 6%, ומי ניצח במשחק?")
    assert str(state(svc, turn, token).profile.pension_percent) == "6"
    assert REDIRECT in turn.reply and not offers_rep(turn)


def test_a_mixed_message_with_a_car_saves_the_car(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, Q2, "יש לי קורולה קרוס, ואיך מכינים פיצה?")
    assert state(svc, turn, token).current.label and not offers_rep(turn)


def test_unclear_then_said_again_concretely_reaches_the_representative(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, "יש תקופת התחייבות?")
    assert CLARIFY in turn.reply and not offers_rep(turn)
    turn = svc.send(turn.conversation_id, token, "יש תקופת התחייבות על הרכב?")
    assert offers_rep(turn) and "נציג התחבורה" in turn.reply


def test_yes_to_the_clarify_line_does_not_open_a_question_to_the_representative(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, "יש תקופת התחייבות?")
    turn = svc.send(turn.conversation_id, token, "כן")
    assert not offers_rep(turn) and state(svc, turn, token).unanswered_question is None


def test_a_net_question_before_the_data_says_what_is_missing_not_the_representative(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, "אני מרוויחה יותר או פחות?")
    assert "חסרים לי עוד" in turn.reply and not offers_rep(turn)


def test_a_follow_up_after_the_results_is_not_sent_to_the_representative(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, Q2, CAR, "אטו 3")
    assert state(svc, turn, token).results
    for text in ("ולמה?", "מה זה אומר בשבילי?"):
        turn = svc.send(turn.conversation_id, token, text)
        assert not offers_rep(turn) and REDIRECT not in turn.reply


# ---------- LLM mode: the gate in code ----------

def llm_after_q1(sessions):
    fake = FakeLLM().turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500),
                          reply="רשמתי: שכר בסיס 18,500 ₪.")
    svc = service(sessions, fake)
    turn, token = start(svc, Q1)
    return svc, fake, turn, token


@pytest.mark.parametrize("text", ["יש תקופת התחייבות?", "כמה זה 20% מ-50?", "שאלה לנציג: מי ניצח במשחק?"])
def test_llm_mode_cannot_offer_the_representative_for_an_unclear_or_unrelated_question(sessions, text):
    svc, fake, turn, token = llm_after_q1(sessions)
    fake.turn(call("propose_rep_question", question=text),
              reply="על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?")
    turn = svc.send(turn.conversation_id, token, text)
    assert state(svc, turn, token).unanswered_question is None and not offers_rep(turn)


def test_llm_mode_still_offers_the_representative_for_a_concrete_question(sessions):
    svc, fake, turn, token = llm_after_q1(sessions)
    fake.turn(call("propose_rep_question", question="אפשר לבחור את צבע הרכב?"),
              reply="על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?")
    turn = svc.send(turn.conversation_id, token, "אפשר לבחור את צבע הרכב?")
    assert state(svc, turn, token).unanswered_question


# ---------- "לאטו 3" is a car, not "slowly" ----------

@pytest.mark.parametrize("text,slow", [("לאטו 3", False), ("אטו 3", False), ("יש לי אטו 3, ורוצה לבדוק לאטו 3", False),
                                       ("הפלאטפורמה הזאת נוחה", False), ("לאט לאט בבקשה", True),
                                       ("שאלה אחת כל פעם", True), ("אפשר ולאט?", True)])
def test_pace_cues_are_whole_words(text, slow):
    assert says_phrase(text, SNAP.extensions.pace_cues) is slow


def test_a_catalog_car_named_with_a_prefix_does_not_switch_the_pace(sessions):
    svc = service(sessions)
    turn, token = start(svc, Q1, Q2, CAR, "יש לאטו 3 צבעים נוספים?")
    assert PACE_ONE not in turn.reply and state(svc, turn, token).pace == "normal"
    turn = svc.send(turn.conversation_id, token, "לאט לאט בבקשה")
    assert state(svc, turn, token).pace == "one_at_a_time"
