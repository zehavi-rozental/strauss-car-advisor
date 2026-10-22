"""ה-API של נציג התחבורה: הרשאה, מה הנציג רואה, אימות תשובות, תשובה אחת בלבד."""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from .conftest import make_client

TOKEN = "rep-secret"
AUTH = {"X-Rep-Token": TOKEN}
NOA = [
    "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
    "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש",
    "אטו 3",
]
QUESTION_KEYS = {"id", "missing_field", "mode", "option_id", "question_text", "answer_type", "suggested_value",
                 "context", "status", "created_at", "answered_at", "answer_value", "effective_date",
                 "answer_note", "answered_by"}


@pytest.fixture
def client(engine):
    return make_client(engine, rep_access_token=TOKEN)


def noa_to_atto(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in NOA:
        client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    [q] = client.get("/api/rep/questions", headers=AUTH).json()
    return cid, q


def test_missing_or_wrong_token_is_401(client):
    for headers in ({}, {"X-Rep-Token": "wrong"}, {"X-Rep-Token": ""}):
        r = client.get("/api/rep/questions", headers=headers)
        assert r.status_code == 401 and r.json()["detail"] == "נדרשת הרשאת נציג"
    assert client.post("/api/rep/questions/x/answer", json={"value": "1"}).status_code == 401


def test_empty_configured_token_closes_the_rep_api(engine):
    client = make_client(engine, rep_access_token="")
    assert client.get("/api/rep/questions", headers={"X-Rep-Token": ""}).status_code == 401


def test_question_json_has_only_what_the_rep_needs(client):
    _, q = noa_to_atto(client)
    assert set(q) == QUESTION_KEYS                                   # בלי conversation_id
    assert set(q["context"]) == {"group", "car_label", "catalog_code", "suggested_amount"}
    text = json.dumps(q, ensure_ascii=False)
    for private in ("18500", "18,500", "2.75", "מנהל", "base", "salary", "pension", "credit"):
        assert private not in text
    assert client.get(f"/api/rep/questions/{q['id']}", headers=AUTH).json() == q


@pytest.mark.parametrize("value, message", [
    ("-50", "הסכום לא יכול להיות שלילי"),
    ("שלוש מאות", "הסכום חייב להיות מספר"),
    (12.345, "עד שתי ספרות אחרי הנקודה"),
])
def test_invalid_amounts_are_422_in_hebrew(client, value, message):
    _, q = noa_to_atto(client)
    r = client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": value}, headers=AUTH)
    assert r.status_code == 422 and r.json()["detail"] == message


def test_bad_effective_date_is_422_in_hebrew(client):
    _, q = noa_to_atto(client)
    r = client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "350", "effective_date": "1/1/27"},
                    headers=AUTH)
    assert r.status_code == 422 and "תאריך" in r.json()["detail"]


def test_answer_once_then_409_and_name_from_header(client):
    _, q = noa_to_atto(client)
    r = client.post(f"/api/rep/questions/{q['id']}/answer",
                    json={"value": "350", "effective_date": "2027-01-01", "note": "לפי חוזה"},
                    headers={**AUTH, "X-Rep-Name": "דנה כהן".encode()})          # UTF-8 גולמי
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["answer_value"], body["effective_date"], body["answered_by"]) == (
        "ANSWERED", "350", "2027-01-01", "דנה כהן")
    again = client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "400"}, headers=AUTH)
    assert again.status_code == 409 and again.json()["detail"] == "השאלה כבר נענתה"
    assert client.get("/api/rep/questions", headers=AUTH).json() == []
    answered = client.get("/api/rep/questions?status=ANSWERED", headers=AUTH).json()
    assert [a["id"] for a in answered] == [q["id"]]


def test_rep_name_url_encoded_and_default(client):
    _, q = noa_to_atto(client)
    r = client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "300"},
                    headers={**AUTH, "X-Rep-Name": quote("יוסי")})
    assert r.json()["answered_by"] == "יוסי"


def test_unknown_question_and_status(client):
    assert client.get("/api/rep/questions/not-a-uuid", headers=AUTH).status_code == 404
    r = client.post("/api/rep/questions/00000000-0000-0000-0000-000000000000/answer", json={"value": "1"},
                    headers=AUTH)
    assert r.status_code == 404 and r.json()["detail"] == "השאלה לא נמצאה"
    assert client.get("/api/rep/questions?status=MAYBE", headers=AUTH).status_code == 422


def test_rep_rate_limit(engine):
    client = make_client(engine, rep_access_token=TOKEN, rep_rate_limit_per_minute=2)
    codes = [client.get("/api/rep/questions", headers=AUTH).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_cancelled_question_is_not_listed(client):
    """העובדת מוסרת כמה היא אמורה לשלם על הרכב החדש: השאלה הפתוחה מתבטלת ומוחלפת בשאלה עם הסכום שמסרה,
    והנציג רואה רק את החדשה."""
    cid, q = noa_to_atto(client)
    client.post(f"/api/conversations/{cid}/messages", json={"text": "באטו אני אשלם 350"})
    [new] = client.get("/api/rep/questions", headers=AUTH).json()
    assert new["id"] != q["id"] and new["context"]["employee_stated_amount"] == "350"
    assert client.get(f"/api/rep/questions/{q['id']}", headers=AUTH).json()["status"] == "CANCELLED"


def test_answer_values_notes_and_names_are_not_logged(client, caplog):
    import logging
    _, q = noa_to_atto(client)
    with caplog.at_level(logging.DEBUG):
        client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "987.65", "note": "SECRET-NOTE"},
                    headers={**AUTH, "X-Rep-Name": "SECRET-NAME"})
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "rep answered question" in logged
    for secret in ("987.65", "SECRET-NOTE", "SECRET-NAME", TOKEN):
        assert secret not in logged


def test_wrong_tokens_count_against_the_rate_limit(engine):
    client = make_client(engine, rep_access_token=TOKEN, rep_rate_limit_per_minute=2)
    wrong = {"X-Rep-Token": "guess"}
    codes = [client.get("/api/rep/questions", headers=wrong).status_code for _ in range(3)]
    assert codes == [401, 401, 429]                      # גם ניחושים נספרים
    assert client.get("/api/rep/questions", headers=AUTH).status_code == 429


def test_rep_api_is_closed_by_default(engine):
    from app.shared.settings import Settings
    assert Settings(_env_file=None).rep_access_token == ""
    client = make_client(engine)                         # בלי REP_ACCESS_TOKEN
    for headers in ({}, {"X-Rep-Token": ""}, {"X-Rep-Token": "demo-rep"}, {"X-Rep-Token": "change-me"}):
        assert client.get("/api/rep/questions", headers=headers).status_code == 401
