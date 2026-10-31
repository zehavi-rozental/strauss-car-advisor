"""Small talk, conversation preferences and feedback (browser QA, 30/10).

Live, in template mode: "מה נשמע?" got "לא בטוח שהבנתי את השאלה" and the pay questions; "תשאל כל פעם שאלה אחת" got
"לא הצלחתי להבין את התשובה" (the pace phrase was matched in one word order only); "מה הקשר זה לא תשובה" got "עדיין
חסרים לי…" (feedback on a reply was not recognised at all). The same template is the fallback when the model is off,
fails, or its reply is rejected, so every case is pinned without a model, with a scripted one, and with one down.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FailingLLM, FakeLLM
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.conversation.templates import NOT_UNDERSTOOD
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CONFIG, make_client

SNAP = load_snapshot(CONFIG)
SOCIAL = {line for lines in SNAP.faq.social_replies.values() for line in lines}
FEEDBACK = set(SNAP.faq.feedback_replies)
ROBOTIC = (NOT_UNDERSTOOD, SNAP.faq.clarify, "עדיין חסר")


@pytest.fixture
def client(engine):
    return make_client(engine)


def chat(client, *texts):
    cid = client.post("/api/conversations").json()["conversation_id"]
    turns = [client.post(f"/api/conversations/{cid}/messages", json={"text": t}).json() for t in texts]
    return cid, turns


def first_line(reply: str) -> str:
    return reply.split("\n\n")[0]


def asks(reply: str) -> list[str]:
    """The question lines (the call to action excluded)."""
    return [p for p in reply.split("\n\n") if "?" in p and not p.startswith(("כתבו", "בחרו", "אפשר לכתוב"))]


# ---------- small talk ----------

@pytest.mark.parametrize("text", ["מה נשמע?", "מה המצב", "היי, מה שלומך?", "בוקר טוב", "שלום", "תודה רבה",
                                  "מה קורה אצלכם היום?"])
def test_small_talk_is_answered_in_a_few_words_then_the_open_question(client, text):
    _, [turn] = chat(client, text)
    assert first_line(turn["reply"]) in SOCIAL
    assert not any(r in turn["reply"] for r in ROBOTIC)
    assert "קבוצת הזכאות" in turn["reply"]                 # back to the open question, gently
    assert "!" not in turn["reply"] and turn["expects"]["fields"]   # the persona's style; never a dead end


def test_small_talk_mid_collection_keeps_what_was_saved_and_asks_what_is_next(client):
    cid, [_, turn] = chat(client, "קבוצה ג", "מה נשמע?")
    assert first_line(turn["reply"]) in SOCIAL
    assert "שכר הבסיס" in turn["reply"] and "קבוצת הזכאות" not in turn["reply"]
    assert not any(r in turn["reply"] for r in ROBOTIC)


def test_small_talk_twice_never_gets_the_same_line_and_the_question_is_picked_up(client):
    _, turns = chat(client, "קבוצה ג", "מה נשמע?", "מה נשמע?")
    a, b = first_line(turns[1]["reply"]), first_line(turns[2]["reply"])
    assert a != b and {a, b} <= SOCIAL
    for t in turns[1:]:                                   # the same question, never "still missing"
        assert "שכר הבסיס" in t["reply"] and not any(r in t["reply"] for r in ROBOTIC)
    assert turns[1]["reply"] != turns[2]["reply"]
    assert any("נמשיך מאיפה שעצרנו" in t["reply"] for t in turns[1:])


def test_a_greeting_with_a_value_is_read_as_a_value(client):
    _, [turn] = chat(client, "היי, אני בקבוצה ג")
    assert "רשמתי: קבוצה ג" in turn["reply"]
    assert first_line(turn["reply"]) not in SOCIAL


def test_back_to_the_flow_after_small_talk(client):
    _, [_, turn] = chat(client, "מה נשמע?", "קבוצה ג, שכר בסיס 18,500")
    assert "קבוצה ג" in turn["reply"] and "18,500" in turn["reply"]
    assert not any(r in turn["reply"] for r in ROBOTIC)


# ---------- one question at a time, in any word order; kept ----------

@pytest.mark.parametrize("text", ["תשאל כל פעם שאלה אחת", "בכל פעם רק שאלה אחת בבקשה", "אפשר שאלה אחת כל פעם?",
                                  "דבר אחד בכל פעם", "לאט לאט"])
def test_one_question_at_a_time_in_any_word_order(client, text):
    _, [turn] = chat(client, text)
    assert turn["reply"].startswith("בסדר, אשאל שאלה אחת בכל פעם.")
    assert not any(r in turn["reply"] for r in ROBOTIC)
    [question] = asks(turn["reply"])
    assert "שכר" not in question                         # one field, not group and salary together


def test_the_preference_is_kept_in_the_next_messages(client):
    _, turns = chat(client, "תשאל כל פעם שאלה אחת", "קבוצה ג", "18,500")
    assert [len(asks(t["reply"])) for t in turns[1:]] == [1, 1]
    q2 = asks(turns[2]["reply"])[0]
    assert q2.count(",") <= 1 and "ונקודות" not in q2        # one Q2 field, not the whole list


def test_one_question_said_about_a_question_is_not_a_pace_request(client):
    _, [turn] = chat(client, "יש לי שאלה אחת: מה זה שווי שימוש?")
    assert "שאלה אחת בכל פעם" not in turn["reply"]
    assert "שווי שימוש" in turn["reply"]                    # the question itself is answered


# ---------- style ----------

def test_a_request_for_short_replies_is_acknowledged_and_kept(client, engine):
    _, turns = chat(client, "תכתוב יותר קצר", "קבוצה ג")
    assert turns[0]["reply"].startswith("בסדר, אכתוב קצר ופשוט.")
    assert not any(r in turns[0]["reply"] for r in ROBOTIC)
    for t in turns:
        assert "למשל" not in t["reply"].split("\n\n")[-1]     # the call to action without its example
        assert t["expects"]["placeholder"]                     # the composer keeps the example


# ---------- feedback on a reply ----------

@pytest.mark.parametrize("text", ["מה הקשר זה לא תשובה", "זה לא קשור", "לא ענית לי על מה ששאלתי",
                                  "על מה אתה מדבר?", "זה לא עוזר"])
def test_feedback_is_acknowledged_and_the_question_is_said_in_simpler_words(client, text):
    _, [_, turn] = chat(client, "מה נשמע?", text)
    assert any(line in turn["reply"] for line in FEEDBACK)
    assert "במילים פשוטות" in turn["reply"]
    assert not any(r in turn["reply"] for r in ROBOTIC)


def test_feedback_twice_gets_another_wording(client):
    _, [_, a, b] = chat(client, "מה נשמע?", "מה הקשר", "זה לא תשובה")
    assert first_line(a["reply"]) != first_line(b["reply"])


def test_feedback_respects_one_question_at_a_time(client):
    _, [_, turn] = chat(client, "שאלה אחת בכל פעם", "מה הקשר זה לא תשובה")
    assert "שכר" not in turn["reply"]                        # the explanation names the one open field


def test_i_did_not_understand_is_help_not_feedback(client):
    _, [turn] = chat(client, "לא הבנתי")
    assert not any(line in turn["reply"] for line in FEEDBACK)


# ---------- with a model, and when it is down ----------

def service(engine, llm):
    from app.shared.db import session_factory
    return ConversationService(session_factory(engine), RulesStore(CONFIG), LLMChain([llm]))


def test_with_the_model_down_the_fallback_answers_small_talk_and_feedback(engine):
    svc = service(engine, FailingLLM())
    start, token = svc.start()
    cid = start.conversation_id
    social = svc.send(cid, token, "מה נשמע?")
    feedback = svc.send(cid, token, "מה הקשר זה לא תשובה")
    pace = svc.send(cid, token, "תשאל כל פעם שאלה אחת")
    assert first_line(social.reply) in SOCIAL
    assert any(line in feedback.reply for line in FEEDBACK) and "במילים פשוטות" in feedback.reply
    assert pace.reply.startswith("בסדר, אשאל שאלה אחת בכל פעם.")
    for t in (social, feedback, pace):
        assert not any(r in t.reply for r in ROBOTIC)


def test_a_rejected_model_reply_falls_back_to_the_small_talk_template(engine):
    fake = FakeLLM()
    svc = service(engine, fake)
    start, token = svc.start()
    fake.turn(reply="הכול מעולה!!! 😊 ספרו לי על עצמכם")          # "!" and an emoji: rejected by the guard
    turn = svc.send(start.conversation_id, token, "מה נשמע?")
    assert first_line(turn.reply) in SOCIAL and "!" not in turn.reply
    assert not any(r in turn.reply for r in ROBOTIC)


def test_the_model_is_told_the_preferences(engine):
    fake = FakeLLM()
    svc = service(engine, fake)
    start, token = svc.start()
    svc.send(start.conversation_id, token, "תשאל כל פעם שאלה אחת")
    svc.send(start.conversation_id, token, "תכתוב יותר קצר")
    svc.send(start.conversation_id, token, "קבוצה ג")
    system = [c.messages[0].content for c in fake.calls if c.messages and c.messages[0].role == "system"]
    last = next(s for s in reversed(system) if "STATE" in s or "pace:" in s or "style:" in s)
    assert "pace: the employee asked for ONE question at a time" in last
    assert "style: the employee asked for short, plain replies" in last


# ---------- browser QA, round 2 (30/10): the four messages as written, and phrasings in no list ----------

from app.modules.conversation.templates import ADVICE_FIRST, OPEN_HELP, UNSURE  # noqa: E402

HOW_IT_WORKS = SNAP.faq.get("how_it_works").answer


def light(reply: str) -> bool:
    """Answered, then at most one question, and the call to action carries no example."""
    return len(asks(reply)) <= 1 and "למשל" not in reply.split("\n\n")[-1]


@pytest.mark.parametrize("text", ["תשאל אותי שאלה אחר שאלה", "תשאלי אותי פחות דברים בבת אחת", "אפשר לאט יותר?",
                                  "תשאלו אחד אחד בבקשה", "תשאל בנפרד כל דבר"])
def test_pace_by_features_not_by_exact_sentence(client, text):
    _, [turn] = chat(client, text)
    assert turn["reply"].startswith("בסדר, אשאל שאלה אחת בכל פעם.")
    assert not any(r in turn["reply"] for r in ROBOTIC)
    assert len(asks(turn["reply"])) == 1


def test_ask_me_later_is_not_a_pace_request(client):
    _, [turn] = chat(client, "תשאל אותי על זה אחר כך")
    assert "שאלה אחת בכל פעם" not in turn["reply"]


def test_how_are_you_at_the_start_is_a_conversation_not_a_form(client):
    _, [turn] = chat(client, "מה נשמע?")
    assert first_line(turn["reply"]) in SOCIAL and OPEN_HELP in turn["reply"]
    assert "שכר" not in turn["reply"] and "למשל" not in turn["reply"]


@pytest.mark.parametrize("text", ["איך אתה יודע את כל התשובות?", "על סמך מה אתה ממליץ?", "המערכת שלך אמינה?",
                                  "מאיפה אתה מביא את המידע?"])
def test_a_question_about_the_advisor_is_answered_honestly(client, text):
    _, [turn] = chat(client, text)
    assert turn["reply"].startswith(HOW_IT_WORKS)
    assert light(turn["reply"]) and not any(r in turn["reply"] for r in ROBOTIC)


@pytest.mark.parametrize("text", ["מה אתה אומר שכדאי לי להחליף רכב?", "האם שווה לי לעבור לחשמלי?"])
def test_advice_asked_before_the_basics_explains_and_asks_one_thing(client, text):
    _, [turn] = chat(client, text)
    assert turn["reply"].startswith(ADVICE_FIRST)
    assert "חסרים לי עוד" not in turn["reply"] and light(turn["reply"])


@pytest.mark.parametrize("text", ["אני לא בטוחה שאני רוצה לספר", "רגע, תן לי לחשוב", "בא לי משהו קטן ועירוני"])
def test_a_remark_that_is_no_answer_never_gets_a_failed_answer_line(client, text):
    _, [turn] = chat(client, text)
    # without a model a remark and gibberish look the same: no "failed answer" line, the not-understood flow
    # (simpler words, an example) goes on
    assert NOT_UNDERSTOOD not in turn["reply"] and turn["reply"].startswith(UNSURE)


def test_a_real_answer_the_parser_cannot_read_still_says_so(client):
    _, [turn] = chat(client, "קבוצה ז")                     # a group letter that does not exist: an answer attempt
    assert UNSURE not in turn["reply"]


def test_the_data_and_the_pace_survive_the_whole_detour(client):
    _, turns = chat(client, "קבוצה ג", "מה נשמע?", "18,500", "תשאל אותי שאלה אחר שאלה", "מה הקשר זה לא תשובה",
                    "איך אתה יודע את כל התשובות?", "אין עמלות")
    replies = [t["reply"] for t in turns]
    assert "שכר הבסיס" in replies[1] and OPEN_HELP not in replies[1]     # small talk after data: picked up
    assert all("קבוצת הזכאות" not in r for r in replies[1:])             # the group is never asked again
    assert all(len(asks(r)) <= 1 for r in replies[3:])                     # one question at a time, kept
    assert "רשמתי: אין עמלות" in replies[-1]


# ---------- browser QA, round 2: found by phrasings outside every list (31/10) ----------

def test_a_question_about_the_basis_of_the_advice_is_not_a_ranking_request(client):
    _, [turn] = chat(client, "על מה בעצם מבוססות ההמלצות שלך?")
    assert turn["reply"].startswith(HOW_IT_WORKS) and "החלופות הכי משתלמות" not in turn["reply"]


def test_what_do_you_recommend_is_still_a_ranking_request(client):
    _, [turn] = chat(client, "מה אתה ממליץ לי?")
    assert HOW_IT_WORKS not in turn["reply"]


def test_a_long_pace_request_with_no_value_is_treated_like_a_short_one(client):
    _, [_, _, turn] = chat(client, "קבוצה ג", "18,500", "תעשה לי את זה לאט, שאלה שאלה")
    assert turn["reply"].startswith("בסדר, אשאל שאלה אחת בכל פעם.") and light(turn["reply"])


# ---------- live Gemini QA (31/10): the turn the call cap sent to the templates ----------

def test_switching_to_a_company_car_is_not_a_model_missing_from_the_catalog():
    from app.modules.conversation.understanding import NOT_IN_CATALOG, catalog_answer
    for text in ("האם כדאי לי לעבור לרכב צמוד?", "אפשר להחליף לרכב חשמלי?", "שווה לי לבדוק רכב חברה?"):
        assert catalog_answer(SNAP, text) != NOT_IN_CATALOG, text
    assert catalog_answer(SNAP, "יש לכם רכב פולו?") == NOT_IN_CATALOG       # a model that is not there: still said
    assert catalog_answer(SNAP, "יש לכם דגם גולף?") == NOT_IN_CATALOG


def test_should_i_switch_with_only_the_cars_missing_says_what_it_depends_on_once(client):
    from app.modules.conversation.templates import CHOOSE_TO_COMPARE
    cid = client.post("/api/conversations").json()["conversation_id"]
    send = lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()
    send({"text": "קבוצה ג"})
    send({"fields": {"base_salary": 17000, "commissions": 0, "bonus": 0, "pension_percent": 6,
                     "study_fund_percent": 2.5, "credit_points": 2.25, "children_under_23": 1}})
    assert send({"text": "קורולה קרוס 2025, משלמת 300"})["stage"] == "CHOOSE_OPTIONS"
    for _ in range(2):                                     # the live turn, and the same message again
        reply = send({"text": "האם כדאי לי להחליף את הרכב שלי?"})["reply"]
        assert reply.startswith(CHOOSE_TO_COMPARE)
        assert "עדיין חסר" not in reply and "חסרים לי עוד" not in reply
        assert reply.count("הרכבים להשוואה") == 0 and len(asks(reply)) == 1


def test_today_describes_the_car_before_it_not_the_next_one():
    from app.modules.conversation.fallback import car_roles
    from app.modules.conversation.understanding import named_cars
    text = "תשווה לי בין הקורולה קרוס שיש לי היום לבין ATTO 3"
    roles = car_roles(text, named_cars(text, SNAP.catalog), SNAP.extensions.car_role_cues)
    assert roles == {"CAR-09": "current", "CAR-10": None}
    text = "היום יש לי קורולה קרוס"                                    # nothing before it: still the next car
    assert car_roles(text, named_cars(text, SNAP.catalog), SNAP.extensions.car_role_cues) == {"CAR-09": "current"}


def test_compare_today_s_car_with_an_alternative_named_in_one_message(engine):
    from app.modules.conversation.llm.port import ToolCall
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))
    cid = client.post("/api/conversations").json()["conversation_id"]
    send = lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()
    send({"text": "קבוצה ג"})
    send({"fields": {"base_salary": 17000, "commissions": 0, "bonus": 0, "pension_percent": 6,
                     "study_fund_percent": 2.5, "credit_points": 2.25, "children_under_23": 1}})
    send({"text": "קורולה קרוס 2025, משלמת 300"})
    fake.turn(ToolCall(name="select_options", args={"options": ["ATTO 3"]}, id="c1"), reply="")
    turn = send({"text": "תשווה לי בין הקורולה קרוס שיש לי היום לבין ATTO 3"})
    print(turn["reply"], turn["stage"])
    assert "הרכב שלכם היום, או חלופה" not in turn["reply"]
    assert "זה תלוי בנטו" not in turn["reply"]
    assert turn["stage"] == "PRESENTED" and [r["label"] for r in turn["results"]] == ["ATTO 3 (2025)"]
