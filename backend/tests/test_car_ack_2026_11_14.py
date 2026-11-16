"""A car and its copay said together, read by the model: both are said back (14/11).

Live on 8870: "קורולה 2026, משלמת 300" while the contributions question was on screen. Gemini called
match_current_car and save_profile(current_copay=300); code saved both (today's car COROLLA 2026, copay 300). A guard
trimmed the reply to "רשמתי: השתתפות 300 ₪." + a question, and code's own acknowledgement was added only when the word
"רשמתי" was missing, so the car identified was never said: it looked as if it had not been saved. Now every value code
saved is checked on its own, and a car identified this turn that the reply does not name gets code's "זיהיתי" line.
"""
from __future__ import annotations

from app.modules.conversation.agent import unsaid_ack

from .conftest import make_client

FIELDS = {"base_salary": 18000, "commissions": 0, "bonus": 0, "credit_points": 2.75, "children_under_23": 2}


def at_contributions(engine, fake):
    from app.modules.conversation.public import LLMChain
    c = make_client(engine, llm=LLMChain([fake]))
    cid = c.post("/api/conversations").json()["conversation_id"]
    plain = make_client(engine)
    plain.cookies = c.cookies
    for body in ({"text": "קבוצה ב"}, {"fields": FIELDS}):              # the setup without the model
        plain.post(f"/api/conversations/{cid}/messages", json=body)

    def say(text):
        r = c.post(f"/api/conversations/{cid}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        return r.json()
    return c, cid, say


def test_the_car_saved_with_its_copay_is_said_when_the_reply_names_only_the_copay(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("match_current_car", model="קורולה", registration_year=2026), call("save_profile", current_copay=300),
              reply="רשמתי: השתתפות 300 ₪.\nמה אחוז הפנסיה ואחוז קרן ההשתלמות שיורדים לכם מהשכר?")
    c, cid, say = at_contributions(engine, fake)
    turn = say("קורולה 2026, משלמת 300")
    view = c.get(f"/api/conversations/{cid}").json()
    assert view["today_car"] and "COROLLA" in view["today_car"]                 # saved (it always was)
    assert "זיהיתי: COROLLA (2026)" in turn["reply"]                             # and now said
    assert "השתתפות 300 ₪" in turn["reply"]                                      # both, explicitly (acceptance 15/11)
    assert turn["reply"].count("300") == 1                                       # the copay once, never twice
    assert "מה אחוז הפנסיה ואחוז קרן ההשתלמות" not in turn["reply"]      # the question is code's (contract, 16/11)


def test_a_reply_that_names_the_car_in_its_own_words_gets_no_second_line(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("match_current_car", model="קורולה", registration_year=2026), call("save_profile", current_copay=300),
              reply="רשמתי: קורולה 2026, השתתפות 300 ₪.\nכמה אחוז יורד לכם לפנסיה?")
    c, cid, say = at_contributions(engine, fake)
    turn = say("קורולה 2026, משלמת 300")
    # the reply contract (16/11): the car and the copay are said by code, from the state, each once; the model's own
    # acknowledgement is never taken, so there is never a second line for the same car
    assert turn["reply"].count("זיהיתי") == 1 and turn["reply"].count("300") == 1
    assert "רשמתי: קורולה 2026" not in turn["reply"]


def test_a_reply_that_names_only_the_car_gets_the_copay_said_by_code(engine):
    from app.modules.conversation.llm.fake import FakeLLM, call
    fake = FakeLLM()
    fake.turn(call("match_current_car", model="קורולה", registration_year=2026), call("save_profile", current_copay=300),
              reply="זיהיתי את הקורולה שלכם.\nכמה אחוז יורד לכם לפנסיה?")
    c, cid, say = at_contributions(engine, fake)
    turn = say("קורולה 2026, משלמת 300")
    # the reply contract (16/11): code names the car and the copay from the state, once each
    assert "השתתפות 300 ₪" in turn["reply"] and "זיהיתי: COROLLA (2026)" in turn["reply"]
    assert turn["reply"].count("זיהיתי") == 1 and "זיהיתי את הקורולה" not in turn["reply"]


def test_without_a_model_the_same_sentence_shows_the_car_and_the_copay(engine):
    c = make_client(engine)
    cid = c.post("/api/conversations").json()["conversation_id"]
    for body in ({"text": "קבוצה ב"}, {"fields": FIELDS}):
        c.post(f"/api/conversations/{cid}/messages", json=body)
    reply = c.post(f"/api/conversations/{cid}/messages", json={"text": "קורולה 2026, משלמת 300"}).json()["reply"]
    assert "COROLLA (2026)" in reply and "השתתפות 300 ₪" in reply


def test_each_saved_value_is_checked_on_its_own():
    changed = {"current_copay": 300, "pension_percent": 6, "commissions": 0}
    assert unsaid_ack("רשמתי: השתתפות 300 ₪, פנסיה 6%, אין עמלות.", changed) == ""
    left = unsaid_ack("רשמתי: השתתפות 300 ₪.", changed)
    assert "6%" in left and "עמלות" in left and "300" not in left


def test_a_car_named_alone_or_to_compare_at_that_question_is_not_taken_as_today_s_car(engine):
    for text in ("MG4", "אני רוצה לבדוק MG4"):
        c = make_client(engine)
        cid = c.post("/api/conversations").json()["conversation_id"]
        for body in ({"text": "קבוצה ב"}, {"fields": FIELDS}):
            c.post(f"/api/conversations/{cid}/messages", json=body)
        c.post(f"/api/conversations/{cid}/messages", json={"text": text})
        view = c.get(f"/api/conversations/{cid}").json()
        assert view["today_car"] is None and view["today_copay"] is None, text
