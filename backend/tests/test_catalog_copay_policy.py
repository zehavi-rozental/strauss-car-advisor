"""The challenge owner's copay policy (22/10): a new car's copay is the catalog default, used and said with its
source; the representative is asked only with a reason (no catalog amount, the employee's request, an arrangement
that differs) and answers through the same mechanism (recalculation, effective date, refresh, no duplicates)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.cases.public import MissingField, Mode
from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import Stage

from .conftest import CONFIG, make_client
from .test_rep_policy_2026_10_22 import NO_CAR, PROFILE_B, Chat, _no_catalog_copay

TODAY = "יש לי קורולה 2026, משלמת 280"          # CAR-07: catalog 250; the employee pays 280
NIRO = "CAR-05"                                 # catalog 250
CATALOG_LINE = "לפי הקטלוג, ההשתתפות ברכב הזה היא 250 ₪. אם אצלכם סוכם אחרת, אפשר לעדכן אותי ואפנה לנציג התחבורה לבירור."
MODES = ["template", "llm"]


def niro(sessions, mode):
    chat = Chat(sessions, FakeLLM() if mode == "llm" else None, config=CONFIG)
    for m in [*PROFILE_B, TODAY]:
        chat.say(m)
    return chat, chat.choose([NIRO])


@pytest.mark.parametrize("mode", MODES)
def test_1_2_a_catalog_amount_is_used_and_said_with_its_source_without_a_question(sessions, mode):
    chat, turn = niro(sessions, mode)
    r = chat.state.results[0].result
    assert r["status"] == "OK" and r["alternative"]["copay"] == "250"
    assert r["copay_source"] == "catalog_final" and not r["copay_pending"]
    assert CATALOG_LINE in turn.reply and "ממתינ" not in turn.reply and "שלחתי לנציג" not in turn.reply
    assert chat.open() == [] and turn.stage == Stage.PRESENTED


@pytest.mark.parametrize("mode", MODES)
def test_3_todays_copay_is_the_employees_own_amount(sessions, mode):
    chat, _ = niro(sessions, mode)
    assert chat.state.results[0].result["today"]["copay"] == "280"     # never the catalog's 250


@pytest.mark.parametrize("mode", MODES)
def test_4_5_no_catalog_amount_no_number_and_the_rep_is_asked(sessions, tmp_path, mode):
    chat = Chat(sessions, FakeLLM() if mode == "llm" else None, config=_no_catalog_copay(tmp_path, NIRO))
    for m in [*PROFILE_B, TODAY]:
        chat.say(m)
    turn = chat.choose([NIRO])
    r = chat.state.results[0].result
    assert r["status"] != "OK" and r.get("delta_display") is None and r["copay_source"] == "missing"
    assert "חסר לי סכום ההשתתפות" in turn.reply
    [q] = chat.open()
    assert (q.missing_field, q.mode, q.option_id) == (MissingField.NEW_CAR_COPAY, Mode.REQUIRED, NIRO)


@pytest.mark.parametrize("text", ["אני רוצה שנציג התחבורה יאשר את ההשתתפות", "אפשר לברר מול הנציג את ההשתתפות?"])
@pytest.mark.parametrize("mode", MODES)
def test_6_the_employee_asks_for_the_reps_confirmation(sessions, mode, text):
    chat, _ = niro(sessions, mode)
    turn = chat.say(text)
    [q] = chat.open()
    assert (q.missing_field, q.option_id, q.suggested_value) == (MissingField.NEW_CAR_COPAY, NIRO, "250")
    r = chat.state.results[0].result
    assert r["copay_source"] == "catalog" and r["copay_pending"]          # now really waiting
    assert "נציג התחבורה" in turn.reply and "250" in turn.reply


@pytest.mark.parametrize("mode", MODES)
def test_7_a_different_arrangement_with_no_amount_goes_to_the_rep(sessions, mode):
    chat, _ = niro(sessions, mode)
    chat.say("אצלי סוכם אחרת על ההשתתפות, אני לא יודעת כמה")
    assert [q.option_id for q in chat.open()] == [NIRO]


def test_8_the_reps_amount_recalculates_from_the_same_point(engine):
    client = make_client(engine, rep_access_token="t")
    cid = client.post("/api/conversations").json()["conversation_id"]
    for m in [*PROFILE_B, TODAY]:
        client.post(f"/api/conversations/{cid}/messages", json={"text": m})
    before = client.post(f"/api/conversations/{cid}/messages", json={"choices": [NIRO], "field": "options"}).json()
    client.post(f"/api/conversations/{cid}/messages", json={"text": "אני רוצה שנציג התחבורה יאשר את ההשתתפות"})
    [q] = client.get("/api/rep/questions", headers={"X-Rep-Token": "t"}).json()
    assert client.post(f"/api/rep/questions/{q['id']}/answer", headers={"X-Rep-Token": "t"},
                       json={"value": "300", "effective_date": "2026-01-01"}).status_code == 200
    view = client.get(f"/api/conversations/{cid}").json()                  # 11: a fresh read = a refresh
    r = view["results"][0]["result"]
    assert r["copay_source"] == "representative" and r["alternative"]["copay"] == "300"
    assert r["delta_display"] != before["results"][0]["delta_display"] and view["stage"] == "PRESENTED"


def test_9_a_car_taken_out_cancels_its_open_question(sessions):
    chat, _ = niro(sessions, "template")
    chat.say("אני רוצה שנציג התחבורה יאשר את ההשתתפות")
    [q] = chat.open()
    chat.choose(["CAR-01"])
    assert chat.svc.cases.get(q.id).status.value == "CANCELLED" and chat.open() == []


def test_10_asking_twice_opens_one_question(sessions):
    chat, _ = niro(sessions, "template")
    chat.say("אני רוצה שנציג התחבורה יאשר את ההשתתפות")
    chat.say("אני רוצה שנציג התחבורה יאשר את ההשתתפות")
    assert len(chat.open()) == 1


def test_11_the_request_survives_a_refresh_and_a_recalculation(sessions):
    chat, _ = niro(sessions, "template")
    chat.say("אני רוצה שנציג התחבורה יאשר את ההשתתפות")
    chat.say("טעיתי, השכר שלי 20,000")                                  # a recalculation
    r = chat.state.results[0].result                                      # chat.state reads the stored state
    assert r["copay_source"] == "catalog" and r["copay_pending"] and len(chat.open()) == 1


def test_12_a_salary_correction_while_waiting_for_the_rep_is_saved(sessions):
    chat = Chat(sessions, config=CONFIG)
    for m in ["קבוצה ב, שכר בסיס 18,500", "אני מנהלת מוצר",
              "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים", TODAY]:
        chat.say(m)
    assert chat.state.stage == Stage.WAITING_FOR_REP
    turn = chat.say("טעיתי, השכר שלי 15,000")
    assert chat.state.profile.base == Decimal("15000") and "עדכנתי" in turn.reply


@pytest.mark.parametrize("question", ["למה ההשתתפות שונה מהקטלוג?", "מאיפה הסכום של ההשתתפות?", "איך חישבת את ההשתתפות?"])
def test_a_question_about_the_copay_is_answered_from_the_result_not_sent(sessions, question):
    chat, _ = niro(sessions, "template")
    turn = chat.say(question)
    assert chat.open() == [] and "250" in turn.reply and "ברירת המחדל בקטלוג" in turn.reply


def test_a_definition_question_still_gets_the_definition(sessions):
    chat, _ = niro(sessions, "template")
    turn = chat.say("מה זה השתתפות?")
    assert "השתתפות היא הסכום שיורד לכם מהנטו" in turn.reply and chat.open() == []
