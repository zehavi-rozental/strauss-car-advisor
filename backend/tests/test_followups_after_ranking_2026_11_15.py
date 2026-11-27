"""Follow-ups after a ranking or a comparison (8870, 15/11).

"ולמה דווקא הראשון?" was answered with every car's copay (the estimate's explanation) when Gemini's reply was
rejected, and "ומה שלושת הכי משתלמים?" opened with that paragraph too. Now a "why" about a car gets the engine's
net, change and main reason for the car meant (named, its place on the list, else the leader); the copay paragraph
is for a question about the estimate itself; a turn that ranked answers with its ranking only.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import LLMChain
from app.modules.conversation.templates import money
from app.shared.money import to_shekel

from .conftest import make_client

PROFILE = ("שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים, "
           "קבוצה ג")
COPAY_PARAGRAPH = "לפי ברירת המחדל בקטלוג"


def start(engine, llm=None):
    client = make_client(engine, llm=LLMChain([llm]) if llm is not None else None)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(text):
        r = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        return r.json()["reply"]

    return say, lambda: client.get(f"/api/conversations/{cid}").json()


def ranked(engine, llm=None):
    say, view = start(engine, llm)
    for text in (PROFILE, "יש לי קורולה קרוס", "300"):
        say(text)
    if llm is not None:
        llm.turn(call("rank_options"))                   # the model's reading of the plural wording (8870)
    reply = say("ומה שלושת הכי משתלמים?" if llm is not None else "מה הכי משתלם?")
    return say, view, reply


def net(row):
    return money(to_shekel(Decimal(row["result"]["alternative"]["net_display"])))


@pytest.mark.parametrize("model", [False, True], ids=["templates", "model-ranks"])
def test_the_ranking_answers_with_the_ranking_only(engine, model):
    _, view, reply = ranked(engine, FakeLLM() if model else None)
    assert len(view()["results"]) == 3 and reply.startswith("התוצאות מוצגות בכרטיסים, מהנטו הגבוה לנמוך.")
    assert COPAY_PARAGRAPH not in reply


@pytest.mark.parametrize(("question", "place"), [("ולמה דווקא הראשון?", 0), ("ולמה השני?", 1), ("ולמה האחרון הכי נמוך?", -1)])
def test_why_a_car_gets_the_engines_reason_for_that_car(engine, question, place):
    say, view, _ = ranked(engine)
    rows = view()["results"]
    reply = say(question)
    row = rows[place]
    assert f"{row['label']}: נטו חודשי משוער {net(row)}" in reply
    assert "הסיבה העיקרית" in reply and COPAY_PARAGRAPH not in reply
    assert view()["results"] == rows                      # a question: nothing recalculated, nothing re-ranked


def test_why_a_named_car_after_a_comparison(engine):
    say, view = start(engine)
    for text in (PROFILE, "יש לי קורולה קרוס", "300", "תשווה בין MG4 ל-NIRO"):
        say(text)
    niro = next(r for r in view()["results"] if r["label"].startswith("NIRO"))
    reply = say("ולמה ה-NIRO פחות משתלם?")
    assert f"NIRO (2025): נטו חודשי משוער {net(niro)}" in reply and COPAY_PARAGRAPH not in reply


def test_a_car_on_screen_named_with_two_prefix_letters_gets_its_own_reason(engine):
    say, view, _ = ranked(engine)
    rows = view()["results"]
    dolphin = next(r for r in rows if "DOLPHIN" in r["label"])
    reply = say("ואיך זה שהדולפין כמעט אותו דבר?")
    assert f"{dolphin['label']}: נטו חודשי משוער {net(dolphin)}" in reply


def test_a_question_about_the_estimate_still_gets_the_copay_paragraph(engine):
    say, _, _ = ranked(engine)
    reply = say("למה ההערכה זמנית?")
    assert "השתתפות" in reply


class RejectedModel(FakeLLM):
    """Every reply the model writes carries a figure the engine never produced: the guard rejects it (8870: the
    model's answer was rejected and the template answered)."""

    def complete(self, messages, tools=(), require_tool=False, timeout_s=None):
        response = super().complete(messages, tools, require_tool, timeout_s)
        if tools and not response.tool_calls:
            from app.modules.conversation.llm.port import LLMResponse
            return LLMResponse(text="MG4 עדיף כי הוא חוסך לכם 9,999 ₪ בחודש.")
        return response


def test_when_the_models_answer_is_rejected_the_fallback_still_answers_why(engine):
    say, view, _ = ranked(engine, RejectedModel())
    rows = view()["results"]
    reply = say("ולמה דווקא הראשון?")
    assert "9,999" not in reply                           # the rejected figure never reaches the employee
    assert f"{rows[0]['label']}: נטו חודשי משוער {net(rows[0])}" in reply and COPAY_PARAGRAPH not in reply
