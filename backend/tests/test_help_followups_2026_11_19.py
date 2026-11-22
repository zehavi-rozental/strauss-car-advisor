"""Help and follow-up questions at any stage, and the screen's action contract (19/11; templates and a fake model, no
real Gemini, fake mail).

Root cause (8870, 19/11): "מעלה תלוש" was not read (the upload request needed a verb from a list), the model then
answered that a button "שמופיע אצלכם" existed although no action was on the screen, and "איפה הכפתור?" got "לא בטוח
שהבנתי" twice: since c204812 (the reply contract) the model is not asked when code reaches a weak decision (unclear,
"explain again"), and nothing kept what the last reply offered. Now:
  - `expects.actions` (conversation/actions.py) is the only source of the persistent buttons; the payslip upload is
    there whenever a salary value is missing, from the base salary on;
  - the last reply's context (topic, field asked, actions offered) resolves a follow-up first;
  - a question or a message about an action's topic points to the real action, carried by the reply;
  - with a model, a weak decision lets it explain, and a sentence naming a control the screen lacks is refused.
The field asked and every value already given stay as they were.
"""
from __future__ import annotations

from app.modules.conversation.actions import HELP, PAYSLIP_UPLOAD
from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain

from .conftest import PAYSLIP_ON, make_client

PROFILE = {"base_salary": 18500, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
           "credit_points": 2.75, "children_under_23": 2}
WHERE = HELP[PAYSLIP_UPLOAD].split(".")[0]          # "הכפתור "להעלות תלוש" נמצא מתחת להודעות, מעל תיבת הכתיבה"
NOT_UNDERSTOOD = ("לא הצלחתי להבין", "לא בטוח שהבנתי")


def start(engine, llm=None, payslip=True):
    extra = PAYSLIP_ON if payslip else {}
    client = make_client(engine, llm=LLMChain([llm]) if llm is not None else None, **extra)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def view():
        return client.get(f"/api/conversations/{cid}").json()
    return say, view


def kinds(turn):
    return [a["kind"] for a in turn["expects"].get("actions") or []]


def link(turn):
    return (turn["expects"].get("link") or {}).get("kind")


def understood(turn):
    return not any(x in turn["reply"] for x in NOT_UNDERSTOOD)


# ---------- the 8870 conversation of 19/11, without a model ----------

def test_the_reported_conversation_points_to_the_real_upload_and_keeps_the_question(engine):
    say, view = start(engine)
    first = say("ב")
    assert kinds(first) == [PAYSLIP_UPLOAD]                       # on the screen from the base salary on
    for text in ("מעלה תלוש", "איפה הכפתור?", "אני שואלת איפה הכפתור"):
        turn = say(text)
        assert understood(turn), (text, turn["reply"])
        assert WHERE in turn["reply"] and link(turn) == PAYSLIP_UPLOAD        # the action itself, with the words
        assert "שכר בסיס" in turn["reply"] or "שכר הבסיס" in turn["reply"]   # the open question stays
        v = view()
        assert v["profile"]["group"] == "ב" and v["profile"]["base"] is None and v["stage"] == "COLLECT_PROFILE"
    assert say("18,500")["stage"] == "COLLECT_PROFILE" and view()["profile"]["base"] == "18500"


def test_how_to_upload_is_answered_with_the_action(engine):
    say, _ = start(engine)
    say("ב")
    turn = say("איך מעלים תלוש?")
    assert WHERE in turn["reply"] and link(turn) == PAYSLIP_UPLOAD and understood(turn)


def test_a_follow_up_after_a_reply_about_the_action_is_about_it(engine):
    say, _ = start(engine)
    say("ב")
    say("מעלה תלוש")
    for text in ("איך עושים את זה?", "לא הבנתי"):
        turn = say(text)
        assert WHERE in turn["reply"] and link(turn) == PAYSLIP_UPLOAD, (text, turn["reply"])


def test_a_follow_up_after_a_data_question_is_about_that_question(engine):
    say, _ = start(engine)
    say("ב")
    turn = say("איך עושים את זה?")                     # nothing about an action before: the salary question, simpler
    assert WHERE not in turn["reply"] and "שכר בסיס" in turn["reply"]


def test_an_unrelated_unclear_question_is_not_turned_into_the_upload(engine):
    say, _ = start(engine)
    say("קבוצה ג, שכר בסיס 18,500")                  # the next reply offers the upload only next to the Q2 question
    turn = say("יש תקופת התחייבות?")
    assert WHERE not in turn["reply"] and "לא בטוח שהבנתי" in turn["reply"]


# ---------- the contract: the action is on the screen only when it can be used ----------

def test_no_upload_action_once_the_salary_is_complete_and_never_an_invented_button(engine):
    say, view = start(engine)
    say("קבוצה ג")
    turn = say({"fields": PROFILE})
    assert kinds(turn) == [] and view()["expects"]["actions"] == []
    turn = say("איפה הכפתור?")
    assert "להעלות תלוש" not in turn["reply"] and link(turn) is None and kinds(turn) == []
    assert view()["profile"]["base"] == "18500"                   # nothing lost


def test_the_view_carries_the_same_actions(engine):
    say, view = start(engine)
    say("ב")
    assert [a["kind"] for a in view()["expects"]["actions"]] == [PAYSLIP_UPLOAD]
    assert view()["expects"]["actions"][0]["label"] == "להעלות תלוש שכר"


def test_help_at_every_collecting_stage_keeps_the_field_and_the_data(engine):
    """Q1 (base salary), Q2 (a salary item), today's car: a help question never empties a value or changes the field."""
    for steps, field_word in ((["ב"], "שכר"), (["ב", "18,500"], "עמלות"),
                              (["ב", {"fields": PROFILE}], "רכב")):
        say, view = start(engine)
        for s in steps:
            say(s)
        before = view()
        for text in ("איך עושים את זה?", "איפה זה?", "מה זה אומר?"):
            turn = say(text)
            assert understood(turn), (steps, text, turn["reply"])
            assert field_word in turn["reply"], (steps, text, turn["reply"])
            after = view()
            assert after["profile"] == before["profile"] and after["stage"] == before["stage"]


# ---------- with a (fake) model: the c204812 regression and the screen guard ----------

ANSWER = "את תקופת ההתחייבות קובע ההסכם שלכם עם החברה, ונציג התחבורה יכול לפרט."


def test_with_the_model_an_unclear_question_is_explained_not_bounced(engine):
    # the model's answer alone, and as Gemini writes it (the answer and the next question): replayed on 9198531 (4/11)
    # the full reply was used; on c204812 / b1bc54e both got "לא בטוח שהבנתי" — the regression this guards
    for reply in (ANSWER, ANSWER + "\n\nיש לכם עמלות חודשיות? אם כן, כמה בחודש?"):
        fake = FakeLLM()
        say, view = start(engine, fake)
        say("קבוצה ג, שכר בסיס 18,500")
        fake.turn(reply=reply)
        turn = say("יש תקופת התחייבות?")
        assert "לא בטוח שהבנתי" not in turn["reply"] and "ההסכם" in turn["reply"]
        assert "עמלות" in turn["reply"]                          # code's next question stays
        assert view()["profile"]["base"] == "18500"


def test_with_the_model_a_button_the_screen_lacks_is_refused(engine):
    fake = FakeLLM()
    say, view = start(engine, fake)
    say("קבוצה ג")
    say({"fields": PROFILE})                                    # no action on the screen now
    fake.turn(reply="לחצו על הכפתור \"להעלות תלוש\" שמופיע אצלכם.")
    turn = say("יש תקופת התחייבות?")
    assert "להעלות תלוש" not in turn["reply"] and "כפתור" not in turn["reply"]
    assert kinds(turn) == []


def test_with_the_model_the_reported_conversation_needs_no_model_for_the_action(engine):
    fake = FakeLLM()
    say, _ = start(engine, fake)
    say("ב")
    for text in ("מעלה תלוש", "איפה הכפתור?"):
        turn = say(text)
        assert WHERE in turn["reply"] and link(turn) == PAYSLIP_UPLOAD and understood(turn)


# ---------- the server's real switch (19/11): the payslip extraction off -> nothing offered, nothing that answers 503 ----------

def test_with_the_extraction_off_no_button_no_link_and_no_claim(engine):
    say, view = start(engine, payslip=False)
    first = say("ב")
    assert kinds(first) == [] and link(first) is None
    for text in ("מעלה תלוש", "איפה הכפתור?", "אני רוצה להעלות תלוש"):
        turn = say(text)
        assert kinds(turn) == [] and link(turn) is None, (text, turn["expects"])
        assert WHERE not in turn["reply"] and "לחצו על \"להעלות תלוש\"" not in turn["reply"], (text, turn["reply"])
    assert "לא זמינה" in turn["reply"]                              # an explicit request: said plainly (PAYSLIP_UNAVAILABLE)
    v = view()
    assert v["expects"]["actions"] == [] and v["profile"]["group"] == "ב" and v["profile"]["base"] is None


def test_with_the_extraction_off_the_salary_question_offers_the_form_not_the_upload(engine):
    say, _ = start(engine, payslip=False)
    turn = say("קבוצה ג, שכר בסיס 18,500")                        # Q2: the link that used to be "או להעלות תלוש"
    assert link(turn) != "payslip_upload" and "להעלות תלוש" not in turn["reply"]


def test_with_the_extraction_on_the_salary_question_offers_the_upload(engine):
    say, _ = start(engine)
    turn = say("קבוצה ג, שכר בסיס 18,500")
    # the reply's link and the screen's action are the same one (the client draws it once: persistentActions)
    assert link(turn) == "payslip_upload" and kinds(turn) == ["payslip_upload"]
