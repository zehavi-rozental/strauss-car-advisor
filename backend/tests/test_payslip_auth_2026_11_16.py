"""The payslip extraction belongs to a conversation (16/11).

It used to be POST /api/payslip/extract with no session at all: anyone could spend Gemini Vision calls, limited only per
address. Now it is POST /api/conversations/{id}/payslip/extract, checked like every conversation route before the file
is read and before any model call: no session, an invalid one or someone else's conversation -> 404 (never says which);
then a limit per conversation and the limit per address. Every test uses a recorded fake (no model, nothing sent out)
and counts its calls.
"""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.modules.conversation.public import LLMChain
from app.modules.conversation.models import ConversationRow
from app.modules.conversation.repository import token_hash
from app.modules.payslip.public import PayslipService
from app.shared.settings import Settings

from .conftest import build_engine
from .test_payslip import JPEG, FakeVision

ANSWER = '{"commissions": 1500, "pension_percent": 6}'
FILE = {"file": ("p.jpg", JPEG, "image/jpeg")}


def app_with(vision, **overrides):
    settings = Settings(_env_file=None, llm_provider="none", payslip_enabled=True, **overrides)
    app = create_app(settings, engine=build_engine(), llm=LLMChain())
    app.state.ctx.payslip = PayslipService(app.state.ctx.rules, settings, vision=vision)
    return app


def conversation(client: TestClient) -> str:
    return client.post("/api/conversations").json()["conversation_id"]


def upload(client: TestClient, cid: str):
    return client.post(f"/api/conversations/{cid}/payslip/extract", files=FILE)


# ---------- refused before any model call ----------

def test_without_a_session_nothing_reaches_the_model():
    vision = FakeVision(ANSWER)
    app = app_with(vision)
    cid = conversation(TestClient(app))
    r = upload(TestClient(app), cid)                              # another browser: no cookie at all
    assert r.status_code == 404 and r.json()["detail"] == "השיחה לא נמצאה"
    assert vision.calls == []


def test_an_invalid_session_nothing_reaches_the_model():
    vision = FakeVision(ANSWER)
    app = app_with(vision)
    cid = conversation(TestClient(app))
    stranger = TestClient(app)
    stranger.cookies.set("sca_session", "not-the-token")
    assert upload(stranger, cid).status_code == 404 and vision.calls == []


def test_someone_elses_conversation_nothing_reaches_the_model():
    vision = FakeVision(ANSWER)
    app = app_with(vision)
    owner, other = TestClient(app), TestClient(app)
    cid = conversation(owner)
    conversation(other)                                           # a valid session, for another conversation
    assert upload(other, cid).status_code == 404 and vision.calls == []
    assert upload(owner, cid).status_code == 200 and len(vision.calls) == 1


def test_a_malformed_or_unknown_conversation_id_is_404():
    vision = FakeVision(ANSWER)
    client = TestClient(app_with(vision))
    conversation(client)
    for cid in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        assert upload(client, cid).status_code == 404
    assert vision.calls == []


def test_the_old_open_route_is_gone():
    vision = FakeVision(ANSWER)
    client = TestClient(app_with(vision))
    conversation(client)
    assert client.post("/api/payslip/extract", files=FILE).status_code in (404, 405)
    assert vision.calls == []


def test_no_session_is_refused_even_when_the_feature_is_off():
    """The session comes first: a request without one learns nothing, not even whether uploads are on."""
    app = create_app(Settings(_env_file=None, llm_provider="none"), engine=build_engine(), llm=LLMChain())
    cid = conversation(TestClient(app))
    assert upload(TestClient(app), cid).status_code == 404


# ---------- the limits ----------

def test_the_limit_per_conversation():
    vision = FakeVision(ANSWER)
    client = TestClient(app_with(vision, payslip_conversation_rate_limit_per_minute=2,
                                 payslip_rate_limit_per_minute=100))
    cid = conversation(client)
    assert [upload(client, cid).status_code for _ in range(2)] == [200, 200]
    r = upload(client, cid)
    assert r.status_code == 429 and r.headers["Retry-After"] == "60" and len(vision.calls) == 2
    assert upload(client, conversation(client)).status_code == 200      # another conversation has its own count


def test_the_limit_per_address_still_holds():
    vision = FakeVision(ANSWER)
    client = TestClient(app_with(vision, payslip_conversation_rate_limit_per_minute=100,
                                 payslip_rate_limit_per_minute=1))
    assert upload(client, conversation(client)).status_code == 200
    assert upload(client, conversation(client)).status_code == 429 and len(vision.calls) == 1


def test_refused_requests_do_not_spend_the_owners_limit():
    vision = FakeVision(ANSWER)
    app = app_with(vision, payslip_conversation_rate_limit_per_minute=1, payslip_rate_limit_per_minute=100)
    owner = TestClient(app)
    cid = conversation(owner)
    for _ in range(5):
        assert upload(TestClient(app), cid).status_code == 404    # no session: refused before any limit is counted
    assert upload(owner, cid).status_code == 200


# ---------- the valid flow ----------

def test_the_owner_extracts_and_the_values_reach_the_review():
    vision = FakeVision(ANSWER)
    client = TestClient(app_with(vision))
    r = upload(client, conversation(client))
    assert r.status_code == 200 and r.json()["ok"] is True and len(vision.calls) == 1
    assert {f["field"]: f["value"] for f in r.json()["fields"]}["commissions"] == "1500"


def test_a_session_from_an_email_link_may_upload():
    """A second session issued by the one-time email link (additional_session_token_hashes) opens the same
    conversation, so the employee who came back from the email can still upload."""
    vision = FakeVision(ANSWER)
    app = app_with(vision)
    cid = conversation(TestClient(app))
    with app.state.ctx.sessions() as session, session.begin():
        row = session.scalar(select(ConversationRow))
        row.additional_session_token_hashes = [token_hash("from-the-email-link")]
    returned = TestClient(app)
    returned.cookies.set("sca_session", "from-the-email-link")
    assert upload(returned, cid).status_code == 200 and len(vision.calls) == 1


def test_nothing_of_the_file_is_logged(caplog):
    marker = b"SECRET-PAYSLIP-CONTENT-4242"
    vision = FakeVision(ANSWER)
    app = app_with(vision)
    owner = TestClient(app)
    cid = conversation(owner)
    data = {"file": ("p.jpg", JPEG + marker, "image/jpeg")}
    with caplog.at_level(logging.DEBUG):
        TestClient(app).post(f"/api/conversations/{cid}/payslip/extract", files=data)       # refused
        owner.post(f"/api/conversations/{cid}/payslip/extract", files=data)                 # extracted
    assert "SECRET-PAYSLIP-CONTENT" not in caplog.text and "1500" not in caplog.text
