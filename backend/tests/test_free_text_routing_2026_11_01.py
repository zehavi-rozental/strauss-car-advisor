"""Free wording with the model: code checks it, never replaces its understanding with a wrong template (1/11).

Found by live Gemini scenarios (scripted here the way the live model answered):
- "קרן השתלמות? גם את זה אין לי, החברה לא מפרישה" to the study-fund question: the model called save_profile(0) three
  times and grounding refused it (the zero cue was not next to the field's word: "את זה"), so the reply was an FAQ
  explanation and nothing was saved. A zero cue now grounds a zero for the field asked when the message names no
  other field ("אין נקודות זיכוי וילדים" still is not "no children").
- "רגע, אתה יודע אם ירד גשם מחר?": a guard trimmed the model's answer and left only the call to action. An off-topic
  question must be answered like any question (must_answer), so the reply keeps the redirect line.
"""
from __future__ import annotations

from app.modules.conversation.grounding import zero_grounded

from .conftest import make_client

FULL = {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
        "credit_points": 2.75, "children_under_23": 2}
CUES = ["אין", "בלי", "ללא", "אפס"]


def with_model(engine, fake, fields):
    from app.modules.conversation.public import LLMChain
    c = make_client(engine, llm=LLMChain([fake]))
    cid = c.post("/api/conversations").json()["conversation_id"]
    plain = make_client(engine)
    plain.cookies = c.cookies
    for body in ({"text": "קבוצה ב"}, {"fields": fields}):              # the setup without the model
        plain.post(f"/api/conversations/{cid}/messages", json=body)

    def say(text):
        r = c.post(f"/api/conversations/{cid}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        return r.json()
    return c, cid, say


def test_a_zero_cue_about_the_question_on_screen_grounds_that_field_only():
    text = "קרן השתלמות? גם את זה אין לי, החברה לא מפרישה"
    assert zero_grounded("study_fund_percent", text, CUES, asked="study_fund_percent")
    assert not zero_grounded("study_fund_percent", text, CUES, asked=None)
    assert not zero_grounded("children_under_23", "אין לי נקודות זיכוי", CUES, asked="children_under_23")
    assert not zero_grounded("children_under_23", "אין נקודות זיכוי וילדים", CUES, asked="children_under_23")


def test_with_the_model_a_no_that_refers_back_to_the_study_fund_question_is_saved(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("save_profile", study_fund_percent=0), reply="איזה רכב צמוד יש לכם היום?")   # the live reply: no ack
    c, cid, say = with_model(engine, fake, {k: v for k, v in FULL.items() if k != "study_fund_percent"})
    turn = say("קרן השתלמות? גם את זה אין לי, החברה לא מפרישה")
    p = c.get(f"/api/conversations/{cid}").json()["profile"]
    assert (p["study_fund_percent"], p["pension_percent"]) == ("0", "6")
    assert "כתבו אין" not in turn["reply"]                             # not the FAQ explanation of an answered field
    assert "קרן השתלמות" in turn["reply"].splitlines()[0]             # said (code's ack when the model left it out)


def test_with_the_model_a_zero_for_another_field_than_the_one_asked_is_still_refused(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("save_profile", children_under_23=0), reply="רשמתי: אין ילדים.\nאיזה רכב צמוד יש לכם היום?")
    c, cid, say = with_model(engine, fake, {k: v for k, v in FULL.items() if k != "children_under_23"})
    say("אין לי נקודות זיכוי בכלל")
    assert c.get(f"/api/conversations/{cid}").json()["profile"]["children"] is None


def test_with_the_model_an_off_topic_question_is_answered_not_only_the_call_to_action(engine):
    from app.modules.conversation.llm.fake import FakeLLM
    fake = FakeLLM()
    fake.turn(reply="כתבו איזה דגם צמוד יש לכם כעת?")                  # the live reply after trimming
    c, cid, say = with_model(engine, fake, FULL)
    turn = say("רגע, אתה יודע אם ירד גשם מחר?")
    assert len([ln for ln in turn["reply"].splitlines() if ln.strip()]) > 2          # more than the question
    assert "רכבי החברה" in turn["reply"] or "רכב החברה" in turn["reply"]   # what the advisor helps with
    assert c.get(f"/api/conversations/{cid}").json()["today_car"] is None


def test_what_a_company_car_is_is_answered_from_the_faq_never_offered_to_the_representative(engine):
    # live Gemini 1/11: nothing grounded the model's explanation, the verifier rejected it and the template offered the
    # representative; the explanation is now in faq.yaml (company_car), so it is grounded in both modes
    c = make_client(engine)
    cid = c.post("/api/conversations").json()["conversation_id"]
    for body in ({"text": "קבוצה ב"}, {"fields": FULL}):
        c.post(f"/api/conversations/{cid}/messages", json=body)
    turn = c.post(f"/api/conversations/{cid}/messages",
                  json={"text": "לא הבנתי מה אתה רוצה ממני, מה זה רכב צמוד בכלל?"}).json()
    assert "רכב שהחברה נותנת לכם לשימוש" in turn["reply"] and "נציג" not in turn["reply"]
    assert c.get(f"/api/conversations/{cid}").json()["today_car"] is None
    said = c.post(f"/api/conversations/{cid}/messages", json={"text": "יש לי רכב צמוד"}).json()
    assert "רשמתי שיש לכם רכב צמוד" in said["reply"]                # a statement, not a question about the term


def test_with_the_model_a_message_code_read_in_full_is_only_phrased_never_changes_the_state(engine):
    # live Gemini 1/11: "תשאל אותי שאלה אחר שאלה" (code's pace request) was phrased by the model, which also called
    # save_profile(defer_fields=[pension]) and asked the study fund; the next "לא מופרש" then went to the study fund
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("save_profile", defer_fields=["pension_percent"]),
              reply="בסדר, נחזור לזה בהמשך.\nכמה אחוזים מופרשים מכם לקרן השתלמות?")
    fields = {k: v for k, v in FULL.items() if k not in ("pension_percent", "study_fund_percent")}
    c, cid, say = with_model(engine, fake, fields)
    say("תשאל אותי שאלה אחר שאלה")
    state = c.get(f"/api/conversations/{cid}").json()
    assert state["profile"]["pension_percent"] is None and "השתלמות" not in str(state.get("deferred_fields", ""))
    for call_ in fake.calls:
        assert not call_.tools if hasattr(call_, "tools") else True                 # no tools offered
    plain = make_client(engine)
    plain.cookies = c.cookies
    held = plain.post(f"/api/conversations/{cid}/messages", json={"text": "לא מופרש"}).json()
    assert "לאשר פנסיה של 0%?" in held["reply"]                        # bound to the pension, as asked by code
