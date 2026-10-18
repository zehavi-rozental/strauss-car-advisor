"""שומר המספרים: מספר שלא בא מהמנוע / מהפרופיל / מהתבנית חוסם את הודעת המודל."""
from __future__ import annotations

from decimal import Decimal

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
Q1_CTA = 'כתבו כאן, למשל: "קבוצה ג, שכר בסיס 18,500".'


def _question_turn(sessions, reply, text=CREDIT_Q):
    """A question at the first step: the plan holds the FAQ answer, the question and the call to action;
    SAY (the scripted reply) writes it, or the template does."""
    fake = FakeLLM().turn(reply=reply)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    return svc.send(turn.conversation_id, token, text)


def test_a_reply_that_covers_the_plan_is_used(sessions):
    reply = ("נקודות זיכוי מקטינות את מס ההכנסה, וכל נקודה שווה 242 ₪ בחודש.\n"
             "ועכשיו: מה הקבוצה או התפקיד שלכם, ומה שכר הבסיס?\n" + Q1_CTA)
    turn = _question_turn(sessions, reply)
    assert turn.source == "llm" and turn.reply == reply


def test_blocked_reply_is_replaced_by_template(sessions):
    turn = _question_turn(sessions, "כל נקודה שווה 250 ₪, אז תרוויחי בערך 700 ₪.\n" + Q1_CTA)
    assert turn.stage == Stage.COLLECT_PROFILE
    assert turn.source == "template" and "700" not in turn.reply
    assert "242" in turn.reply and "תפקיד" in turn.reply and turn.reply.endswith(Q1_CTA)
    with sessions() as s:
        last = s.scalars(select(MessageRow).order_by(MessageRow.seq.desc())).first()
        assert last.guard_blocked is True and last.content == turn.reply


def test_numbers_only_from_the_plan_even_the_employees_own(sessions):
    """SAY may use only the plan's numbers; a number from the employee's message that is not in the
    plan is blocked too (the plan already says everything that was saved)."""
    turn = _question_turn(sessions, "7 שנים, יפה! נקודות זיכוי שוות 242 ₪.\nמה הקבוצה?\n" + Q1_CTA,
                          text="אני בחברה 7 שנים. מה זה נקודות זיכוי?")
    assert turn.source == "template" and "7 שנים" not in turn.reply


def test_a_reply_without_the_call_to_action_is_not_used(sessions):
    turn = _question_turn(sessions, "נקודות זיכוי שוות 242 ₪ כל אחת. מה הקבוצה שלכם?")
    assert turn.source == "template" and turn.reply.endswith(Q1_CTA)


def test_a_reply_that_drops_the_answer_is_not_used(sessions):
    turn = _question_turn(sessions, "מה הקבוצה שלכם ושכר הבסיס, 18,500?\n" + Q1_CTA)
    assert turn.source == "template" and "נקודות זיכוי" in turn.reply


def test_a_button_reply_is_not_phrased(sessions):
    fake = FakeLLM().turn(reply="לא אמור להישלח")
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send_choice(turn.conversation_id, token, ["ג"], field="group")
    assert turn.source == "template" and "שכר הבסיס" in turn.reply
    assert fake.calls == []                                 # a button: no LLM at all


# ---------- results message: always the short template, the LLM is not asked ----------

def test_results_text_is_the_template_and_the_llm_is_not_asked(sessions):
    noa = call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
               pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2)
    fake = FakeLLM().turn(noa)
    fake.turn(call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300))
    fake.turn(call("calculate", options=["ATTO 3"]), reply="חבל, הנטו ירד.")
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    for text in ("הנה הפרטים שלי", "זה הרכב שלי, אותו אחד כמו תמיד", "את החשמלי הסיני בבקשה"):
        turn = svc.send(turn.conversation_id, token, text)
    assert turn.source == "template" and "חבל" not in turn.reply
    assert "הנטו שלכם יגדל ב-661 ₪ בחודש" in turn.reply and len(turn.reply.splitlines()) <= 2
    assert fake.calls[-1].tools                        # the results turn made no phrasing call

def test_a_reply_that_is_only_the_call_to_action_is_not_used(sessions):
    """From the live run: Gemini answered "מה?" with the CTA line alone; the explanation was dropped."""
    fake = FakeLLM().turn(reply=Q1_CTA)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "מה?")
    assert turn.source == "template" and "במילים פשוטות" in turn.reply and turn.reply.endswith(Q1_CTA)
