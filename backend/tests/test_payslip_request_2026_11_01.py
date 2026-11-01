"""A request to upload a payslip opens the uploader at once, at any stage (1/11).

"אני רוצה כעת להעלות תלוש ותראה את הנתונים דבר ראשון" and "אי אפשר להעלות תלוש?" got the group and salary questions
instead (the upload link appeared only with the salary components question, once). Now the request is recognised in
code before the flow question, in both modes, and the reply carries the uploader link; no model call is needed.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import LLMChain
from app.modules.conversation.templates import PAYSLIP_OFFER

from .conftest import make_client


def start(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()


@pytest.mark.parametrize("text", ["אני רוצה כעת להעלות תלוש ותראה את הנתונים דבר ראשון", "אי אפשר להעלות תלוש?",
                                  "אפשר לצרף תלוש שכר?", "אני אעלה את התלוש שלי"])
def test_a_payslip_request_opens_the_uploader_first(engine, text):
    client = make_client(engine)
    _, send = start(client)
    turn = send({"text": text})
    assert turn["reply"].startswith(PAYSLIP_OFFER)
    assert turn["expects"]["link"] == {"kind": "payslip_upload", "label": "להעלות תלוש"}
    assert "קבוצת הזכאות" not in turn["reply"] and "שכר הבסיס" not in turn["reply"]


def test_at_any_stage_and_again_later(engine):
    client = make_client(engine)
    _, send = start(client)
    send({"text": "קבוצה ג"})
    send({"text": "18000"})
    send({"text": "אין"})
    first = send({"text": "רגע, אני רוצה להעלות תלוש"})
    again = send({"text": "אפשר להעלות תלוש שוב?"})
    for turn in (first, again):                                  # every time it is asked, not only once
        assert turn["expects"]["link"]["kind"] == "payslip_upload"


def test_a_question_about_the_payslip_is_not_an_upload_request(engine):
    client = make_client(engine)
    _, send = start(client)
    turn = send({"text": "איפה בתלוש רואים את נקודות הזיכוי?"})
    assert not turn["reply"].startswith(PAYSLIP_OFFER)


def test_with_the_model_the_request_is_code_s_and_the_model_knows_uploads_exist(engine):
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))
    _, send = start(client)
    before = len(fake.calls)
    turn = send({"text": "אני רוצה כעת להעלות תלוש ותראה את הנתונים דבר ראשון"})
    assert len(fake.calls) == before and turn["reply"].startswith(PAYSLIP_OFFER)   # no model call for the action
    send({"text": "בעצם יש לי שאלה על הרכב שלי, כמה זמן לוקח לקבל רכב חדש?"})
    system = next(c.messages[0].content for c in fake.calls if c.messages and c.messages[0].role == "system")
    assert "payslip: the employee may upload a payslip at ANY time" in system
