""""הכי משתלם" / "תמליץ" / "דרג" is a request to rank ALL eligible cars — never a re-ask to pick three.
It is understood whether it is a bare request or a question with extra words, at the options stage or
earlier (then it is remembered and ranked once the details are known).
"""
from __future__ import annotations

from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

FULL = ("מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
        "2.75 נקודות זיכוי, 2 ילדים")


def _svc(sessions):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain())


def _state(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


def test_rank_as_a_question_ranks_all_eligible_cars(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    for t in [FULL, "אין לי רכב", "אין"]:
        turn = svc.send(turn.conversation_id, token, t)
    assert turn.stage == Stage.CHOOSE_OPTIONS
    turn = svc.send(turn.conversation_id, token, "הכי משתלם אתה משווה את כל סוגי הרכבים שאני יכול לקבל?")
    st = _state(svc, turn, token)
    # it ranked every eligible car and presented the best ones — it did not ask the employee to choose
    assert st.ranked_count > 3 and turn.results
    assert "עם הנטו המשוער הגבוה ביותר" in turn.reply and "בחרו" not in turn.reply.split("\n")[0]


def test_bare_rank_request_at_options(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    for t in [FULL, "אין לי רכב", "אין", "הכי משתלם"]:
        turn = svc.send(turn.conversation_id, token, t)
    assert _state(svc, turn, token).ranked_count > 3 and turn.results


def test_rank_request_before_the_profile_is_remembered(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "אני בקבוצה ג, מרוויחה 15,000 ואין לי רכב היום")
    turn = svc.send(turn.conversation_id, token, "הכי משתלם אתה משווה את כל סוגי הרכבים שאני יכול לקבל?")
    st = _state(svc, turn, token)
    assert st.rank_requested is True
    # the follow-up does not demand the employee pick cars to compare
    assert "הרכבים להשוואה" not in turn.reply


def test_rank_request_then_completing_the_profile_ranks(sessions):
    svc = _svc(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "הכי משתלם")                 # asked up front
    turn = svc.send(turn.conversation_id, token, FULL)
    turn = svc.send(turn.conversation_id, token, "אין לי רכב")
    turn = svc.send(turn.conversation_id, token, "אין")                        # no allowance -> all known
    st = _state(svc, turn, token)
    assert st.ranked_count > 3 and turn.results      # ranked automatically, never re-asked to choose
