"""A conversation waiting on a question opened before rule A (22/10): today's copay or use value was asked from the
transport representative. New conversations never open one (the employee is asked, and "לא יודע" is recorded), but
questions already open in the database are still answered, applied and cancelled by a car correction; these tests
keep that path working."""
from __future__ import annotations

import uuid

from app.modules.cases.public import AnswerType, MissingField, Mode, QuestionRequest
from app.modules.conversation.repository import ConversationRepository
from app.modules.conversation.stage import ConversationState, PendingRep, RepReason, Stage, next_stage


def seed_legacy_wait(service, conversation_id: str, field: str = "CURRENT_COPAY") -> str:
    """Leaves the conversation where the old flow left it after "לא יודע" (profile complete, car known): an open
    REQUIRED question and the conversation waiting on it. Returns the question's id."""
    reason = RepReason(field)
    with service.sessions() as session, session.begin():
        row = ConversationRepository(session).get(uuid.UUID(conversation_id))
        state = ConversationState.model_validate(row.state)
        c = state.current
        label = c.label or "רכב שלא זוהה בקטלוג"
        context = {"group": state.profile.group, "car_label": label}
        if reason == RepReason.CURRENT_COPAY:
            c.copay, c.copay_unknown = None, True
            request = QuestionRequest(MissingField.CURRENT_COPAY, Mode.REQUIRED, AnswerType.MONEY,
                                      f"מה ההשתתפות החודשית הנוכחית של העובד ב-{label}?",
                                      {**context, "catalog_code": c.catalog_code})
        else:
            c.use_value, c.use_value_unknown = None, True
            request = QuestionRequest(MissingField.CURRENT_USE_VALUE, Mode.REQUIRED, AnswerType.MONEY,
                                      f"מה שווי השימוש החודשי של הרכב הנוכחי של העובד ({label})?", context)
        question = service.cases.open_question(session, conversation_id, request)
        state.pending_rep = PendingRep(reason=reason, question_ids=[question.id],
                                       resume_stage=Stage.COLLECT_CURRENT_CAR, labels=[label])
        state.stage = next_stage(state)
        row.state, row.stage = state.model_dump(mode="json"), state.stage.value
    return question.id
