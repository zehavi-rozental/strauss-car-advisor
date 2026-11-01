"""A request for the net is the service itself, never a question for the representative (manual QA 31/10).

"אני רוצה לדעת את הנטו" got "על זה אין לי מידע מדויק… רוצים שאשאל אותו?" and an inquiry card. It must start the
calculation path: what is missing is asked, one item at a time, and nothing reaches the representative.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text as sql

from .conftest import make_client

NET_REQUESTS = ["אני רוצה לדעת את הנטו", "כמה יישאר לי ביד?", "איך הרכב ישפיע על המשכורת?",
                "מה יהיה הנטו שלי?", "כמה נטו אקבל?"]
REP_OFFER = ("נציג התחבורה", "לשאול את הנציג", "אין לי מידע מדויק")


@pytest.fixture
def client(engine):
    return make_client(engine)


def chat(client, *texts):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, [client.post(f"/api/conversations/{cid}/messages", json={"text": t}).json() for t in texts]


def open_questions(engine) -> int:
    with engine.connect() as c:
        return c.execute(sql("select count(*) from rep_questions")).scalar_one()


@pytest.mark.parametrize("text", NET_REQUESTS)
def test_a_net_request_at_the_start_asks_for_data_not_the_rep(client, engine, text):
    before = open_questions(engine)
    _, [turn] = chat(client, text)
    print(turn["reply"])
    assert not any(w in turn["reply"] for w in REP_OFFER)
    assert all(f["field"] != "ask_rep" for f in turn["expects"]["fields"])
    assert open_questions(engine) == before


@pytest.mark.parametrize("text", NET_REQUESTS)
def test_a_net_request_mid_collection_asks_the_next_missing_item(client, engine, text):
    before = open_questions(engine)
    _, [_, turn] = chat(client, "קבוצה ג", text)
    print(turn["reply"])
    assert not any(w in turn["reply"] for w in REP_OFFER)
    assert "שכר בסיס" in turn["reply"]                     # the next missing item, one at a time
    assert open_questions(engine) == before


def test_the_offer_alone_never_opens_an_inquiry_only_sending_it_does(client, engine):
    # the healthy path, unchanged: a concrete question no entry covers is offered, previewed, and sent on consent
    before = open_questions(engine)
    cid, [offer] = chat(client, "האם מותר להתקין וו גרירה ברכב החברה?")
    assert "לשאול את הנציג" in str(offer["expects"]) and open_questions(engine) == before
    send = lambda t: client.post(f"/api/conversations/{cid}/messages", json={"text": t}).json()
    preview = send("לשאול את הנציג")
    assert "לשלוח" in preview["reply"] and open_questions(engine) == before      # previewed, not sent
    send("לשלוח")
    assert open_questions(engine) == before + 1                                  # sent only on consent


def test_with_the_model_a_proposed_rep_question_for_a_net_request_is_refused(engine):
    # the live model may propose the representative on its own: code refuses it (no concrete question was found)
    from app.modules.conversation.llm.fake import FakeLLM, call
    from app.modules.conversation.public import LLMChain
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))
    cid = client.post("/api/conversations").json()["conversation_id"]
    fake.turn(call("propose_rep_question", question="אני רוצה לדעת את הנטו"),
              reply="על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה?")
    before = open_questions(engine)
    turn = client.post(f"/api/conversations/{cid}/messages", json={"text": "אני רוצה לדעת את הנטו"}).json()
    assert not any(w in turn["reply"] for w in REP_OFFER)
    assert all(f["field"] != "ask_rep" for f in turn["expects"]["fields"])
    assert open_questions(engine) == before
