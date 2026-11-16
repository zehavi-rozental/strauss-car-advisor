from __future__ import annotations

from app.modules.conversation.llm.fake import FakeLLM, FailingLLM, call
from app.modules.conversation.public import LLMChain
from app.modules.conversation.stage import ConversationState
from app.modules.conversation.templates import eligible_text

from .conftest import make_client


def start(engine, llm=None):
    client = make_client(engine, llm=LLMChain([llm]) if llm is not None else None)
    conversation_id = client.post("/api/conversations").json()["conversation_id"]

    def say(text):
        response = client.post(f"/api/conversations/{conversation_id}/messages", json={"text": text})
        assert response.status_code == 200, response.text
        return response.json()["reply"]

    return client, conversation_id, say


def expected_electric_answer(snapshot, group):
    state = ConversationState()
    state.profile.group = group
    return eligible_text(state, snapshot, "ELECTRIC")


def test_gemini_routes_a_natural_drive_follow_up_to_catalog_grounded_eligibility(engine, snapshot):
    fake = FakeLLM()
    fake.turn(reply="רשמתי: קבוצה ב.\nמה שכר הבסיס החודשי שלכם?")
    fake.turn(call("answer_eligible_cars", drive="ELECTRIC"), reply="הנה הרכבים החשמליים שפתוחים לכם.")
    _, _, say = start(engine, fake)

    say("אני בקבוצה ב")
    reply = say("ומה הם החשמליים?")

    assert expected_electric_answer(snapshot, "ב") in reply
    assert "נציג" not in reply
    assert "לא מצאתי מידע" not in reply


def test_model_outage_answers_a_clear_drive_follow_up_from_the_open_list(engine, snapshot):
    _, _, say = start(engine, FailingLLM())

    say("אני בקבוצה ב")
    say("בין כמה רכבים אני יכולה לבחור?")
    reply = say("ומה הם החשמליים?")

    assert expected_electric_answer(snapshot, "ב") in reply
    assert "נציג" not in reply
