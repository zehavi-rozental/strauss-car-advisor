"""R4, 23/11: the reply that asks again for values marked "לא ידוע" offered the payslip upload ("או העלו את התלוש" and
its link) by itself, after the bot's one offer. The approved rule: it offers the upload only when payslip_allowed (the
rule of #3): the bot's one offer, or the employee asked for it. Templates only, no model.
"""
from __future__ import annotations

from .conftest import PAYSLIP_ON, make_client

CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"


def start(engine):
    client = make_client(engine, **PAYSLIP_ON)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def reload():
        return client.get(f"/api/conversations/{cid}").json()
    return say, reload


def kinds(turn):
    return [a["kind"] for a in turn["expects"].get("actions") or []]


def link(turn):
    return (turn["expects"].get("link") or {}).get("kind")


def ask_unknown_again(say):
    first = say("מנהלת מוצר, שכר בסיס 18,500")            # the first salary question: the bot's one offer
    assert kinds(first) == ["payslip_upload"]
    say("אני לא יודע אף אחד מהם")
    say(CAR)
    return say("אטו 3")


def test_the_unknown_values_reply_offers_no_upload_after_the_offer(engine):
    say, reload = start(engine)
    turn = ask_unknown_again(say)
    assert "בתלוש" in turn["reply"]                          # the values asked again (where to find them)
    assert link(turn) is None and kinds(turn) == [] and kinds(reload()) == []
    assert "העלו את התלוש" not in turn["reply"] and "להעלות תלוש" not in turn["reply"]


def test_asked_for_the_upload_is_offered_there_too(engine):
    say, _ = start(engine)
    ask_unknown_again(say)
    asked = say("אני רוצה להעלות תלוש")
    assert kinds(asked) == ["payslip_upload"] and link(asked) == "payslip_upload"
