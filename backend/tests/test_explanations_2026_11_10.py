""""מה זה אומר הפרשות?" is a question, not a missing answer (10/11).

Live on 8870: the bot asked for the contributions, "מה זה אומר הפרשות?" got "הנתונים נחוצים כדי לחשב…" and the data
question again; only "שאלתי מה זה הפרשות" got an explanation. Root cause, three layers:
- no grounded source: no FAQ entry covered "הפרשות", so Gemini's explanation failed the claim verifier (say=guard:claim)
  and the template replaced it;
- the template's reading: a request to explain a term of the employee's own data was classified as "why do you ask"
  (scope "self" → why_ask), and a long one ("מה ההבדל בין ברוטו לנטו?") as a net request;
- no context: "שאלתי מה זה הפרשות" was read as an answer to the question on screen ("לא זיהיתי את דגם הרכב"), and
  "איפה זה בתלוש?" / "לא הבנתי" did not know what "זה" was.
Now: a request to explain (faq.explain_cues) is answered first from the entry it names, a question said again
(faq.requery_cues) is read as the question, and the term explained is the next message's "זה" (state.last_topic).
"""
from __future__ import annotations

import pytest

from app.modules.conversation.understanding import asks_explanation, without_requery

from .conftest import make_client

# 16/11: each salary field on its own, so the contributions come before the car (the pension question is on screen)
SETUP = ["קבוצה ב", "שכר בסיס 18000", "אין עמלות ואין בונוס, 2.75 נקודות זיכוי, 2 ילדים"]
EXPLAINED = "הפרשות הן האחוזים שיורדים לכם מהשכר לחיסכון"
WHY_ASK = "הנתונים נחוצים כדי לחשב"


def at_contributions(engine, llm=None):
    """The contributions question on screen ("אילו הפרשות יורדות לכם: פנסיה, קרן השתלמות?"), set up without a model."""
    plain = make_client(engine)
    cid = plain.post("/api/conversations").json()["conversation_id"]
    for m in SETUP:
        last = plain.post(f"/api/conversations/{cid}/messages", json={"text": m}).json()
    assert "פנסיה" in last["reply"]
    c = plain
    if llm is not None:
        c = make_client(engine, llm=llm)
        c.cookies = plain.cookies

    def say(text):
        r = c.post(f"/api/conversations/{cid}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        return r.json()
    return c, cid, say


def profile(c, cid):
    return c.get(f"/api/conversations/{cid}").json()["profile"]


# ---------- the reading ----------

def test_a_request_to_explain_is_recognised_in_its_common_forms(snapshot):
    for text in ("מה זה אומר הפרשות?", "מה ההבדל בין ברוטו לנטו?", "תסביר לי שווי שימוש", "מה הכוונה השתתפות"):
        assert asks_explanation(snapshot.faq, text), text
    assert not asks_explanation(snapshot.faq, "פנסיה 6%")


def test_a_question_said_again_is_read_without_its_opening_words(snapshot):
    faq = snapshot.faq
    assert without_requery(faq, "שאלתי מה זה הפרשות") == "מה זה הפרשות"
    assert without_requery(faq, "שאלתי, מה זה הפרשות?") == "מה זה הפרשות?"
    assert without_requery(faq, "שאלתי משהו אחר") == "שאלתי משהו אחר"       # a complaint, not a question again
    assert without_requery(faq, "אני רוצה לדעת את הנטו") == "אני רוצה לדעת את הנטו"   # a net request stays one


# ---------- templates (no model) ----------

def test_what_contributions_means_is_explained_first_then_the_question_continues(engine):
    c, cid, say = at_contributions(engine)
    turn = say("מה זה אומר הפרשות?")
    assert turn["reply"].startswith(EXPLAINED) and WHY_ASK not in turn["reply"]
    assert "פנסיה" in turn["reply"].split("\n\n")[1]                    # then one light question, on the same subject
    p = profile(c, cid)
    assert (p["pension_percent"], p["study_fund_percent"]) == (None, None)   # nothing saved, nothing assumed


def test_the_question_said_again_is_answered_never_read_as_an_answer(engine):
    c, cid, say = at_contributions(engine)
    turn = say("שאלתי מה זה הפרשות")
    assert turn["reply"].startswith(EXPLAINED)
    assert "לא הצלחתי להבין" not in turn["reply"] and "לא זיהיתי" not in turn["reply"]


@pytest.mark.parametrize("text", ["מה זה הפרשוצ?", "תסביר לי מה זה הפרשות", "מה המשמעות של הפרשות?"])
def test_wording_and_a_typo_reach_the_same_explanation(engine, text):
    c, cid, say = at_contributions(engine)
    assert say(text)["reply"].startswith(EXPLAINED)


def test_follow_ups_refer_to_the_term_just_explained(engine):
    c, cid, say = at_contributions(engine)
    say("מה זה אומר הפרשות?")
    where = say("איפה זה בתלוש?")
    assert where["reply"].startswith("בתלוש, בניכויים: קרן פנסיה וקרן השתלמות")   # that value, not the payslip in general
    again = say("לא הבנתי")
    assert again["reply"].startswith("במילים פשוטות: כמה אחוזים יורדים מהשכר שלכם לפנסיה ולקרן השתלמות")


def test_after_the_explanation_the_values_are_saved_as_usual(engine):
    c, cid, say = at_contributions(engine)
    say("מה זה אומר הפרשות?")
    say("פנסיה 6%, השתלמות 2.5%")
    p = profile(c, cid)
    assert (p["pension_percent"], p["study_fund_percent"]) == ("6", "2.5")
    say("קורולה 2026, משלמת 300")
    turn = say("MG4")
    assert "הנטו שלכם יגדל" in turn["reply"]                            # the calculation runs, unchanged


def test_gross_and_net_are_explained_not_read_as_a_request_to_calculate(engine):
    c, cid, say = at_contributions(engine)
    turn = say("מה ההבדל בין ברוטו לנטו?")
    assert turn["reply"].startswith("ברוטו הוא השכר לפני ניכויים")
    assert "כדי להשוות, חסרים" not in turn["reply"]


def test_existing_answers_keep_their_meaning(engine):
    c, cid, say = at_contributions(engine)
    assert say("מה זה פנסיה?")["reply"].startswith("פנסיה היא חיסכון לגיל פרישה")   # 16/11: a real explanation
    assert say("למה צריך את זה?")["reply"].startswith(WHY_ASK)        # why we ask stays why we ask
    held = say("אין הפרשות")                                             # nothing deducted: still one confirmation
    assert "לרשום פנסיה 0% וקרן השתלמות 0%?" in held["reply"]


def test_a_catalog_fact_and_an_unverified_property_keep_their_sources(engine):
    c, cid, say = at_contributions(engine)
    copay = say("כמה ההשתתפות של ה-MG4?")["reply"]
    assert "לפי הקטלוג: 250 ₪" in copay                                   # from the catalog, said as general
    roof = say("יש ל-MG4 גג נפתח?")["reply"]
    assert "נציג התחבורה" in roof and "כן" not in roof.split("\n")[0]    # nothing invented: offered to the rep


# ---------- with the model (scripted the way Gemini answers) ----------

def test_with_the_model_a_tagged_explanation_is_grounded_and_shown(engine):
    from app.modules.conversation.llm.fake import FakeLLM
    from app.modules.conversation.public import LLMChain
    fake = FakeLLM()
    fake.turn(reply="הפרשות הן האחוזים שיורדים לכם מהשכר לקרן הפנסיה ולקרן השתלמות, חלק העובד. [[faq:contributions]]\n"
                    "כמה אחוז יורד לכם לפנסיה?")
    c, cid, say = at_contributions(engine, llm=LLMChain([fake]))
    turn = say("מה זה אומר הפרשות?")
    assert turn["reply"].startswith("הפרשות הן האחוזים") and "[[" not in turn["reply"]
    system = next(m.content for m in fake.calls[0].messages if m.role == "system")
    assert 'TOPIC: this message asks about "הפרשות" (FAQ contributions)' in system
    assert "- contributions | הפרשות:" in system                          # the grounded source is in the prompt


def test_with_the_model_a_rejected_reply_falls_back_to_the_explanation_not_to_why_we_ask(engine):
    from app.modules.conversation.llm.fake import FakeLLM
    from app.modules.conversation.public import LLMChain
    fake = FakeLLM()
    fake.turn(reply="הפרשות זה כסף שהמעסיק שומר בשבילכם בבנק. [[faq:contributions]]\nכמה אחוז יורד לכם לפנסיה?")
    fake.verdict([1]).verdict([1])                                        # the claim is unsupported, retry fails too
    c, cid, say = at_contributions(engine, llm=LLMChain([fake]))
    turn = say("מה זה אומר הפרשות?")
    assert turn["reply"].startswith(EXPLAINED) and WHY_ASK not in turn["reply"]
