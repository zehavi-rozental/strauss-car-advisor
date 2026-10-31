"""The engine badge's source (31/10): what really answers — never a key being configured.

/api/health reports the chain's real state; each turn reports what answered it (null when code alone did).
Neither health nor opening a conversation makes a model call.
"""
from __future__ import annotations

from app.modules.conversation.llm.fake import FailingLLM, FakeLLM
from app.modules.conversation.public import LLMChain

from .conftest import make_client

Q2 = {"base_salary": 17000, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
      "credit_points": 2.25, "children_under_23": 1}


def start(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()


def test_no_model_configured_is_none(engine):
    client = make_client(engine)
    assert client.get("/api/health").json()["engine"] == "none"
    _, send = start(client)
    assert send({"text": "קבוצה ג"})["engine"] == "none"


def test_a_model_is_untried_until_it_answers_and_health_or_a_new_chat_never_calls_it(engine):
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))
    assert client.get("/api/health").json()["engine"] == "untried"
    _, send = start(client)
    client.get("/api/health")
    assert fake.calls == []                                  # no call for health, the page or a new conversation
    fake.turn(reply="רשמתי: קבוצה ג.\nמה שכר הבסיס החודשי שלכם?")
    turn = send({"text": "שלום, אני בקבוצה ג ויש לי שאלה"})   # "אני בקבוצה ג" alone: code saves it, no model
    assert fake.calls and turn["engine"] == "ai"
    assert client.get("/api/health").json()["engine"] == "ai"


def test_a_failing_model_is_limited_in_the_turn_and_in_health(engine):
    client = make_client(engine, llm=LLMChain([FailingLLM()]))
    _, send = start(client)
    turn = send({"text": "שלום, אני בקבוצה ג ויש לי שאלה"})
    assert turn["engine"] == "limited" and turn["reply"]     # the templates answered
    assert client.get("/api/health").json()["engine"] == "limited"


def test_a_form_turn_says_nothing_about_the_engine(engine):
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))
    _, send = start(client)
    send({"text": "קבוצה ג"})
    calls = len(fake.calls)
    turn = send({"fields": Q2})
    assert turn["engine"] is None and len(fake.calls) == calls      # code alone: the badge keeps its state
