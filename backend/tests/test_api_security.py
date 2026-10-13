"""עוגיית שיחה, אורך הודעה, הגבלת קצב, ת"ז לא נשמרת, ונקודות הקצה הקטנות."""
from __future__ import annotations

from sqlalchemy import select

from app.modules.conversation.models import ConversationRow, MessageRow

from .conftest import make_client


def _start(client):
    r = client.post("/api/conversations")
    assert r.status_code == 201
    return r.json()["conversation_id"]


def test_cookie_is_httponly_and_token_not_stored(engine, sessions):
    client = make_client(engine)
    r = client.post("/api/conversations")
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
    token = r.cookies["sca_session"]
    with sessions() as s:
        row = s.scalars(select(ConversationRow)).one()
        assert row.session_token_hash != token and len(row.session_token_hash) == 64


def test_wrong_or_missing_cookie_is_404(engine):
    owner = make_client(engine)
    cid = _start(owner)
    stranger = make_client(engine)
    assert stranger.get(f"/api/conversations/{cid}").status_code == 404
    stranger.cookies.set("sca_session", "guess", path="/api")
    assert stranger.post(f"/api/conversations/{cid}/messages", json={"text": "שלום"}).status_code == 404
    assert owner.get("/api/conversations/not-a-uuid").status_code == 404
    assert owner.get(f"/api/conversations/{cid}").status_code == 200


def test_message_length_limit(engine):
    client = make_client(engine, max_message_chars=50)
    cid = _start(client)
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "א" * 51})
    assert r.status_code == 422 and "ארוכה" in r.json()["detail"]
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "   "}).status_code == 422


def test_rate_limits(engine):
    client = make_client(engine, rate_limit_per_minute=2, create_rate_limit_per_minute=2)
    _start(client)
    cid = _start(client)          # העוגייה עכשיו של השיחה השנייה
    assert client.post("/api/conversations").status_code == 429
    codes = [client.post(f"/api/conversations/{cid}/messages", json={"text": "מנהלת מוצר"}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]


def test_conversation_message_cap(engine):
    client = make_client(engine, max_messages_per_conversation=1)
    cid = _start(client)
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "מנהלת מוצר"}).status_code == 200
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "18500"}).status_code == 429


def test_id_number_is_redacted(engine, sessions):
    client = make_client(engine)
    cid = _start(client)
    client.post(f"/api/conversations/{cid}/messages", json={"text": "מנהלת מוצר, ת\"ז 123456782"})
    with sessions() as s:
        contents = " ".join(m.content for m in s.scalars(select(MessageRow)))
        state = str(s.scalars(select(ConversationRow)).one().state)
    assert "123456782" not in contents and "123456782" not in state
    assert "[הוסר]" in contents


def test_health_catalog_occupations(engine):
    client = make_client(engine)
    health = client.get("/api/health").json()
    assert health["db"] is True and health["llm"] == "templates"
    assert health["rules_version"] == "mock-2026-10-12"
    catalog = client.get("/api/catalog").json()
    assert len(catalog) == 20 and {"code": "CAR-10", "label": "ATTO 3 (2025)", "group": "ג", "drive": "ELECTRIC",
                                   "use_value": "3130", "default_copay": "300"} in catalog
    occupations = client.get("/api/occupations").json()
    assert len(occupations) == 14 and all("maintenance" not in o for o in occupations)
