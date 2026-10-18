"""Live test 4: one form of address, requests answered, no verbatim repeats, examples of missing fields
only, results wording, rank header, the waiting line after a result."""
from __future__ import annotations

from app.modules.conversation.agent import SINGULAR_RE
from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.rules.public import RulesStore

from .conftest import CONFIG
from .test_no_dead_ends import run

Q1_CTA = 'כתבו כאן, למשל: "קבוצה ג, שכר בסיס 18,500".'
PROFILE = ["מנהלת מוצר, שכר בסיס 18,500",
           "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
           "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"]


def service(sessions, llm=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())


def talk(svc, texts):
    turn, token = svc.start()
    replies = [turn.reply]
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
        replies.append(turn.reply)
    return turn, token, replies


# ---------- 1. one form of address ----------

def test_template_replies_never_use_the_singular(sessions):
    for _, turn in run(sessions):
        assert not SINGULAR_RE.search(turn.reply), turn.reply
    _, _, replies = talk(service(sessions), PROFILE + ["אטו 3"])
    assert not any(SINGULAR_RE.search(r) for r in replies), replies


def test_a_phrased_reply_in_the_singular_or_with_an_opener_is_not_used(sessions):
    singular = "נקודות זיכוי מקטינות את המס, כל נקודה שווה 242 ₪.\nמה הקבוצה שלך ומה שכר הבסיס?\n" + Q1_CTA
    opener = "שמנו לב שנקודות זיכוי שוות 242 ₪.\nמה הקבוצה שלכם ושכר הבסיס?\n" + Q1_CTA
    for reply, reason in ((singular, "coverage:address"), (opener, "coverage:opener")):
        svc = service(sessions, LLMChain([FakeLLM().turn(reply=reply)]))
        turn, token = svc.start()
        turn = svc.send(turn.conversation_id, token, "מה זה נקודות זיכוי?")
        assert turn.say == reason and turn.source == "template"


# ---------- 2. a request the bot cannot do yet; no verbatim repeat ----------

def test_compare_before_the_profile_says_what_is_missing(sessions):
    turn, _, _ = talk(service(sessions), ["מנהלת מוצר, שכר בסיס 18,500", "תשווה בינהם"])
    assert "כדי להשוות, חסרים לי עוד: עמלות, בונוס" in turn.reply and "הרכב שלכם היום" in turn.reply
    assert "לא הצלחתי להבין" not in turn.reply


def test_the_same_question_is_never_repeated_verbatim(sessions):
    svc = service(sessions)
    turn, token, _ = talk(svc, ["מנהלת מוצר, שכר בסיס 18,500"])
    asks = [turn.reply.split("\n\n")[-2]]
    for text in ("מה זה נקודות זיכוי?", "מי רואה את הנתונים שלי?", "מה זה שווי שימוש?"):
        turn = svc.send(turn.conversation_id, token, text)
        asks.append(turn.reply.split("\n\n")[-2])
    assert all(a != b for a, b in zip(asks, asks[1:])), asks


# ---------- 3. the example names only what is still missing ----------

def test_the_example_lists_only_missing_fields(sessions):
    turn, _, _ = talk(service(sessions), ["מנהלת מוצר, שכר בסיס 18,500, פנסיה 6%"])     # Q2 without the pension
    cta = turn.reply.split("\n\n")[-1]
    assert cta == 'אפשר לכתוב כאן, למשל: "אין עמלות, בונוס 2,000, קרן השתלמות 2.5%", או למלא בטופס.'
    assert "פנסיה" not in turn.expects["placeholder"]


# ---------- 5. rank: how many were ranked ----------

def test_rank_says_how_many_cars_were_ranked(sessions):
    turn, _, _ = talk(service(sessions), PROFILE + ["מה הכי משתלם לי?"])
    assert turn.results and turn.reply.startswith("בדקתי את כל ")
    assert "הרכבים שאתם זכאים להם. אלה 3 המשתלמים ביותר:" in turn.reply
