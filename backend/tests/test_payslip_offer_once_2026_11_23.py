"""Acceptance finding #3, 23/11: the payslip card ("להעלות תלוש שכר") came with every reply until a payslip was confirmed.
The approved rule: the bot offers it once, on its own initiative, at the first salary question (not at the greeting and
not at the group question), and again on a reload of that reply. After that no button and no "או להעלות תלוש" unless
the employee asks for it. Fixes #1 and #2 (22/11) stay. Templates only, no model, no real Gemini.
"""
from __future__ import annotations

from .conftest import PAYSLIP_ON, make_client

CAR = "יש לי טויוטה קורולה 2024 ומשלם 500"


def start(engine):
    client = make_client(engine, **PAYSLIP_ON)
    opened = client.post("/api/conversations").json()
    cid = opened["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        assert r.status_code == 200, r.text
        return r.json()

    def reload():
        r = client.get(f"/api/conversations/{cid}")
        assert r.status_code == 200, r.text
        return r.json()
    return opened, say, reload


def kinds(turn):
    return [a["kind"] for a in turn["expects"].get("actions") or []]


def link(turn):
    return (turn["expects"].get("link") or {}).get("kind")


def test_no_card_at_the_greeting_or_the_group_question(engine):
    opened, say, reload = start(engine)
    assert kinds(opened) == []
    group_q = say(CAR)
    assert "קבוצת הזכאות" in group_q["reply"]
    assert kinds(group_q) == [] and kinds(reload()) == []


def test_the_card_is_offered_once_at_the_base_salary_question_and_on_its_reload(engine):
    _, say, reload = start(engine)
    say(CAR)
    base_q = say("ג")
    assert "שכר הבסיס" in base_q["reply"]
    assert kinds(base_q) == ["payslip_upload"]
    assert kinds(reload()) == ["payslip_upload"]          # a reload of the offer reply shows it again
    assert kinds(reload()) == ["payslip_upload"]


def test_after_the_offer_no_button_and_no_text(engine):
    _, say, reload = start(engine)
    say(CAR)
    say("ג")
    for text in ("18500", "לא יודע", "אין", "6"):
        turn = say(text)
        assert kinds(turn) == [], (text, turn["expects"].get("actions"))
        assert link(turn) is None, (text, link(turn))     # no upload link and no form link either
        assert "להעלות תלוש" not in turn["reply"] and "העלו את התלוש" not in turn["reply"], text
        assert kinds(reload()) == [], text


def test_asking_for_an_upload_brings_it_back(engine):
    _, say, _ = start(engine)
    say(CAR)
    say("ג")
    say("18500")
    asked = say("אני רוצה להעלות תלוש")
    assert kinds(asked) == ["payslip_upload"] and link(asked) == "payslip_upload"


def test_after_a_confirmed_payslip_the_questions_stay_text_only(engine):
    _, say, reload = start(engine)
    say(CAR)
    say("ג")
    turn = say({"fields": {"base_salary": 18500, "commissions": 0, "bonus": 0, "credit_points": 2.75,
                           "children_under_23": 2}, "source": "payslip"})
    assert "הפרשות" in turn["reply"]
    assert kinds(turn) == [] and link(turn) is None and kinds(reload()) == []
    assert "להעלות תלוש" not in turn["reply"] and "בטופס" not in turn["reply"]
