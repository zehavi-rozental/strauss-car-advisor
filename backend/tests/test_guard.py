"""שומר המספרים: מספר שלא בא מהמנוע / מהפרופיל / מהתבנית חוסם את הודעת המודל."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.conversation.guard import collect_numbers, is_safe, violations
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.models import MessageRow
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

FACTS = {"delta_display": "661", "today": {"net_display": "11032"}, "alternative": {"net_display": "11693.4"},
         "profile": {"pension_percent": "6", "study_fund_percent": "2.5"}, "label": "ATTO 3 (2025)"}


def test_known_numbers_pass():
    allowed = collect_numbers(FACTS)
    assert is_safe("הנטו יעלה ב-+661 ₪: מ-11,032 ₪ ל-11,693 ₪", allowed)
    assert is_safe("פנסיה 6% וקרן השתלמות 2.5%", allowed)
    assert is_safe("ATTO 3 (2025)", allowed)


def test_invented_number_is_blocked():
    allowed = collect_numbers(FACTS)
    assert violations("כנראה תרוויחי בערך 700 ₪", allowed) == [Decimal(700)]
    assert not is_safe("בשנה זה 7,932 ₪", allowed)        # 661 × 12 — חישוב של המודל
    assert not is_safe("כ-11,700 ₪", allowed)              # עיגול שהמודל המציא


CREDIT_Q = "מה זה נקודות זיכוי?"
ANSWER = "נקודות זיכוי מקטינות את מס ההכנסה, וכל נקודה שווה 242 ₪ בחודש. [[faq:credit_points]]"
ASK = "מה קבוצת הזכאות או התפקיד שלכם, ומה שכר הבסיס?"


def turn_with(sessions, reply, *, retry=None, verdicts=(), text=CREDIT_Q, chain=None, calls=()):
    fake = FakeLLM().turn(*calls, reply=reply, retry=retry)
    for v in verdicts:
        fake.verdict(v)
    svc = ConversationService(sessions, RulesStore(CONFIG), chain(fake) if chain else LLMChain([fake]))
    turn, token = svc.start()
    return svc.send(turn.conversation_id, token, text), fake


# ---------- the LLM writes the reply; the guards decide ----------

def test_a_grounded_reply_is_sent_without_its_source_tags(sessions):
    turn, _ = turn_with(sessions, f"{ANSWER}\n{ASK}")
    assert turn.source == "llm" and turn.say == "llm"
    assert turn.reply == "נקודות זיכוי מקטינות את מס ההכנסה, וכל נקודה שווה 242 ₪ בחודש.\n" + ASK


def test_a_number_not_in_state_faq_or_the_message_is_rejected_then_the_sentence_is_dropped(sessions):
    turn, _ = turn_with(sessions, f"כל נקודה שווה 260 ₪, אז תרוויחו 700 ₪. [[faq:credit_points]]\n{ASK}")
    # without the answer the reply no longer answers the question: the template (with the FAQ answer)
    assert turn.source == "template" and "700" not in turn.reply and "242" in turn.reply


def test_the_retry_gets_the_violation_and_its_answer_is_used(sessions):
    turn, fake = turn_with(sessions, f"כל נקודה שווה 260 ₪. [[faq:credit_points]]\n{ASK}", retry=f"{ANSWER}\n{ASK}")
    assert turn.say == "llm:retry" and "242" in turn.reply
    feedback = [c for c in fake.calls if not c.tools and c.messages[-1].content.startswith("Your reply broke")]
    assert feedback and "number" in feedback[0].messages[-1].content


def test_a_policy_sentence_without_a_source_is_dropped(sessions):
    turn, _ = turn_with(sessions, f"{ANSWER}\nלפי הכללים אתם זכאים לרכב מכל קבוצה.\n{ASK}")
    assert turn.say == "llm:trimmed" and "זכאים" not in turn.reply and "242" in turn.reply


def test_an_unknown_source_tag_is_a_violation(sessions):
    turn, _ = turn_with(sessions, f"{ANSWER}\nזה גם משפיע על הפנסיה. [[faq:no_such_entry]]\n{ASK}")
    assert turn.say == "llm:trimmed" and "הפנסיה" not in turn.reply and "242" in turn.reply


def test_the_claim_verifier_removes_an_unsupported_sentence(sessions):
    turn, _ = turn_with(sessions, f"{ANSWER}\nכל ההפרש מעל התקציב על החברה. [[faq:family_track]]\n{ASK}",
                        verdicts=[[2], [2]])
    assert turn.say == "llm:trimmed" and "ההפרש" not in turn.reply and "242" in turn.reply


def test_out_of_budget_the_verifier_is_skipped_and_only_template_safe_sentences_stay(sessions):
    turn, fake = turn_with(sessions, f"{ANSWER}\nהתקציב של הקבוצה שלכם קבוע. [[state]]\n{ASK}",
                           chain=lambda f: LLMChain([f], turn_deadline_s=0.2, clock=lambda: 100.0))
    assert not [c for c in fake.calls if c.messages and c.messages[0].content.startswith("You verify")]
    assert turn.say == "llm:trimmed" and "התקציב" not in turn.reply and "242" in turn.reply   # only FAQ-tagged stays


def test_a_reply_that_does_not_move_forward_is_not_used(sessions):
    turn, _ = turn_with(sessions, f"{ANSWER}\nיש עוד משהו שתרצו לשאול?")
    assert turn.source == "template" and turn.say == "guard:progress"


@pytest.mark.parametrize("bad, kind", [
    (f"{ANSWER}\nמה הקבוצה שלך ומה שכר הבסיס?", "address"),
    (f"{ANSWER}\n{ASK} 🙂", "emoji"),
    (f"מעולה! {ASK}", "exclamation"),
    (f"לא הצלחתי להבין את התשובה.\n{ASK}", "banned"),
    (f"בשמחה.\n{ANSWER}\n{ASK}", "opener"),
    (f"כתבו את הקבוצה.\nכתבו את שכר הבסיס.", "cta"),
    ("\n".join([ANSWER] * 6 + [ASK]), "length"),
])
def test_shape_violations_fall_back_to_the_template(sessions, bad, kind):
    turn, _ = turn_with(sessions, bad)
    assert turn.source == "template" and kind in turn.say


def test_a_shape_violation_is_fixed_by_the_retry(sessions):
    turn, _ = turn_with(sessions, f"{ANSWER}\nמה הקבוצה שלך?", retry=f"{ANSWER}\n{ASK}")
    assert turn.say == "llm:retry" and "שלך" not in turn.reply


def test_parser_and_llm_numbers_disagree_the_reply_must_ask(sessions):
    turn, _ = turn_with(sessions, "רשמתי: שכר בסיס 19,500 ₪.\nכתבו את הקבוצה שלכם.", text="שכר בסיס 18,500",
                        calls=[call("save_profile", base_salary=19500)])
    assert turn.source == "template" and "conflict" in turn.say


def test_an_outage_on_round_one_uses_the_parser_and_the_template(sessions):
    from app.modules.conversation.llm.fake import FailingLLM

    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([FailingLLM()]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצר, שכר בסיס 18,500")
    assert turn.path == "fallback" and turn.source == "template" and "זיהיתי: מנהל/ת מוצר" in turn.reply


def test_a_button_reply_is_not_phrased(sessions):
    fake = FakeLLM().turn(reply="לא אמור להישלח")
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send_choice(turn.conversation_id, token, ["ג"], field="group")
    assert turn.source == "template" and "שכר הבסיס" in turn.reply
    assert fake.calls == []


# ---------- the two live transcripts (session 3) ----------

SUNROOF = "דבר ראשון אני רוצה לדעת האם יש אפשרות לרכב עם גג נפתח"
COMPLAINT = "אתה בוט שעונה לא לפי השאלה!"


def test_a_question_without_a_question_mark_is_answered_with_the_rep_offer(sessions):
    fake = FakeLLM().turn(call("propose_rep_question", question="האם יש אפשרות לרכב עם גג נפתח"),
                          reply="על גג נפתח אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?")
    fake.turn(call("propose_rep_question", question="האם יש אפשרות לרכב עם גג נפתח"),
              reply="צודקים, סליחה. על גג נפתח אין לי מידע, ואשמח לשאול את נציג התחבורה. לשאול?")
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, SUNROOF)
    assert turn.source == "llm" and turn.reply == "על גג נפתח אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?"
    assert [c["id"] for f in turn.expects["fields"] if f["field"] == "ask_rep" for c in f["choices"]] == ["ask_rep", "skip"]
    turn = svc.send(turn.conversation_id, token, COMPLAINT)
    assert turn.source == "llm" and turn.reply.startswith("צודקים, סליחה.") and "נציג התחבורה" in turn.reply


def test_the_two_transcripts_in_template_mode(sessions):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, SUNROOF)
    assert turn.reply.startswith("על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?")
    assert "לא הצלחתי להבין" not in turn.reply
    turn = svc.send(turn.conversation_id, token, COMPLAINT)
    assert turn.reply.startswith("צודקים, סליחה.") and "נציג התחבורה" in turn.reply
    assert "אני כאן רק כדי לעזור" not in turn.reply


# ---------- results message: the template, the LLM is not asked to write it ----------

def test_results_text_is_the_template_and_the_llm_is_not_asked(sessions):
    noa = call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
               pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2)
    fake = FakeLLM().turn(noa)
    fake.turn(call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300))
    fake.turn(call("select_options", options=["ATTO 3"]), reply="חבל, הנטו ירד.")
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    for text in ("הנה הפרטים שלי", "זה הרכב שלי, אותו אחד כמו תמיד", "את החשמלי הסיני בבקשה"):
        turn = svc.send(turn.conversation_id, token, text)
    assert turn.source == "template" and "חבל" not in turn.reply
    assert "הנטו שלכם יגדל ב-661 ₪ בחודש" in turn.reply and "הסיבה העיקרית" in turn.reply
    assert turn.reply.split("\n\n")[-1].startswith("אפשר לכתוב")