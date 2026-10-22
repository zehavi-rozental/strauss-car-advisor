"""מודול השאלות לנציג: פתיחה בלי כפילויות, הקשר מצומצם, אימות תשובות, תשובה אחת בלבד."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.modules.cases.public import (
    ALLOWED_CONTEXT_KEYS,
    AnswerInvalid,
    AnswerType,
    CasesService,
    MissingField,
    Mode,
    QuestionAlreadyAnswered,
    QuestionNotFound,
    QuestionRequest,
    Status,
)

CONV = str(uuid.uuid4())


def copay_request(option_id="CAR-10", **context):
    ctx = {"group": "ג", "car_label": "ATTO 3 (2025)", "catalog_code": "CAR-10", "suggested_amount": "300"}
    ctx.update(context)
    return QuestionRequest(MissingField.NEW_CAR_COPAY, Mode.CONFIRM, AnswerType.MONEY,
                           "מה ההשתתפות החודשית של העובד ב-ATTO 3 (2025)? לפי הקטלוג: 300 ₪", ctx,
                           suggested_value="300", option_id=option_id)


def open_one(cases, sessions, request, conversation_id=CONV):
    with sessions() as s, s.begin():
        return cases.open_question(s, conversation_id, request)


def test_open_question_does_not_duplicate(sessions):
    cases = CasesService(sessions)
    first = open_one(cases, sessions, copay_request())
    second = open_one(cases, sessions, copay_request())
    assert first.id == second.id and len(cases.list_open()) == 1
    other_option = open_one(cases, sessions, copay_request(option_id="CAR-11"))
    assert other_option.id != first.id and len(cases.list_open()) == 2


def test_after_an_answer_a_new_question_can_open(sessions):
    cases = CasesService(sessions)
    first = open_one(cases, sessions, copay_request())
    cases.answer(first.id, "300")
    again = open_one(cases, sessions, copay_request())
    assert again.id != first.id and again.status == Status.OPEN


def test_context_keys_are_only_the_allowed_list(sessions):
    assert ALLOWED_CONTEXT_KEYS == {"group", "car_label", "catalog_code", "suggested_amount", "occupation_text",
                                    "stated_group", "table_group", "employee_stated_amount"}
    cases = CasesService(sessions)
    for forbidden in ({"base_salary": "18500"}, {"message": "שלום"}, {"id_number": "123456782"}):
        with pytest.raises(ValueError):
            open_one(cases, sessions, copay_request(**forbidden))
    with pytest.raises(ValueError):                       # רק טקסט — לא מבנים מקוננים
        open_one(cases, sessions, copay_request(group={"salary": 18500}))
    view = open_one(cases, sessions, copay_request())
    assert set(view.context) <= ALLOWED_CONTEXT_KEYS


@pytest.mark.parametrize("value, message", [
    ("-5", "הסכום לא יכול להיות שלילי"),
    ("abc", "הסכום חייב להיות מספר"),
    ("12.345", "עד שתי ספרות אחרי הנקודה"),
    ("", "הסכום חייב להיות מספר"),
])
def test_money_answer_validation_in_hebrew(sessions, value, message):
    cases = CasesService(sessions)
    q = open_one(cases, sessions, copay_request())
    with pytest.raises(AnswerInvalid, match=message):
        cases.answer(q.id, value)
    assert cases.get(q.id).status == Status.OPEN          # תשובה לא תקינה לא נשמרת


def test_answer_is_stored_once_and_a_second_answer_conflicts(sessions):
    cases = CasesService(sessions)
    events = []
    cases.subscribe(events.append)
    q = open_one(cases, sessions, copay_request())
    answered = cases.answer(q.id, "350.50", effective_date=date(2027, 1, 1), note="לפי חוזה", answered_by="דנה")
    assert (answered.status, answered.answer_value, answered.effective_date) == (
        Status.ANSWERED, "350.50", date(2027, 1, 1))
    assert answered.answered_by == "דנה" and answered.version == 1
    with pytest.raises(QuestionAlreadyAnswered):
        cases.answer(q.id, "400")
    assert [e.value for e in events] == ["350.50"]        # האירוע פורסם פעם אחת
    assert events[0].conversation_id == CONV and events[0].option_id == "CAR-10"


def test_boolean_and_date_answers(sessions):
    cases = CasesService(sessions)
    q = open_one(cases, sessions, QuestionRequest(
        MissingField.OCCUPATION_OPT_OUT, Mode.REQUIRED, AnswerType.BOOLEAN,
        "האם עובד בעיסוק הזה רשאי לוותר על רכב?", {"group": "ג", "occupation_text": "אסטרונאוטית"}))
    with pytest.raises(AnswerInvalid, match="כן או לא"):
        cases.answer(q.id, "אולי")
    assert cases.answer(q.id, "כן").answer_value == "true"


def test_cancel_for_conversation_and_unknown_question(sessions):
    cases = CasesService(sessions)
    q = open_one(cases, sessions, copay_request())
    with sessions() as s, s.begin():
        assert cases.cancel_for_conversation(s, CONV) == 1
    assert cases.get(q.id).status == Status.CANCELLED and cases.list_open() == []
    with pytest.raises(QuestionAlreadyAnswered):
        cases.answer(q.id, "300")
    with pytest.raises(QuestionNotFound):
        cases.get(str(uuid.uuid4()))
    with pytest.raises(QuestionNotFound):
        cases.get("not-a-uuid")


def test_a_failing_subscriber_does_not_lose_the_answer(sessions):
    cases = CasesService(sessions)

    def broken(event):
        raise RuntimeError("boom")

    cases.subscribe(broken)
    q = open_one(cases, sessions, copay_request())
    cases.answer(q.id, "300")
    assert cases.get(q.id).status == Status.ANSWERED


def test_lost_insert_race_reuses_the_open_question_and_keeps_the_transaction(sessions, monkeypatch):
    """תור מקביל פתח את אותה שאלה בין הבדיקה להוספה: האינדקס הייחודי דוחה, ה-savepoint מתגלגל
    אחורה, השאלה הקיימת מוחזרת — והטרנזקציה של התור ממשיכה."""
    cases = CasesService(sessions)
    winner = open_one(cases, sessions, copay_request())
    real = CasesService._open_row
    calls = {"n": 0}

    def stale(session, conv, field, option_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(session, conv, field, option_id)

    monkeypatch.setattr(CasesService, "_open_row", staticmethod(stale))
    with sessions() as s, s.begin():
        again = cases.open_question(s, CONV, copay_request())
        other = cases.open_question(s, CONV, copay_request(option_id="CAR-11"))   # אותה טרנזקציה ממשיכה
    assert again.id == winner.id
    assert {q.option_id for q in cases.list_open()} == {"CAR-10", "CAR-11"}
