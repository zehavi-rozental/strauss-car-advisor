"""Live-Gemini QA, 22/10: what the 10 live scenarios found, pinned with a scripted model.

Scenario 4: "אין לי רכב צמוד. אני מקבלת אחזקת רכב של 1,500 ₪ בחודש" — Gemini sent save_profile(current_maintenance)
and match_current_car(no_car) in one round. The profile ran first, while the car was not yet "none", so the
allowance was ignored ("relevant only for no car") and asked again. The car now runs first in a round.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.grounding import zero_grounded
from app.modules.conversation.guards import check, strip_tags
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.conversation.stage import current_group
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CONFIG, build_engine  # noqa: F401  (build_engine: the shared DB)

PROFILE = ("מפתחת תוכנה, שכר בסיס 21,000, אין עמלות ואין בונוס, פנסיה 6.5%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, אין ילדים")
NO_CAR = "אין לי רכב צמוד. אני מקבלת אחזקת רכב של 1,500 ₪ בחודש"


def run(sessions, steps):
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    for message, calls in steps:
        if calls is not None:
            fake.turn(*calls)
        turn = svc.send(turn.conversation_id, token, message)
    return turn, svc.view(turn.conversation_id, token).state


def test_no_car_and_its_allowance_in_one_round_are_both_saved(sessions):
    turn, s = run(sessions, [
        (PROFILE, [call("save_profile", occupation="מפתחת תוכנה")]),
        # the order Gemini sent them in: the profile call first, the car call second
        (NO_CAR, [call("save_profile", current_maintenance=1500), call("match_current_car", no_car=True)]),
    ])
    assert s.current.source == "none"
    assert s.current.maintenance == Decimal("1500")
    assert "maintenance" not in current_group(s)[1]            # never asked again
    assert "אחזקת" not in turn.reply.splitlines()[-1]           # the question on screen is not the allowance


# Live run, deadline 8: "…וללא ילדים [[faq:pension_percent,faq:study_fund_percent,faq:credit_points,faq:children]]."
# reached the employee: a tag listing several sources was neither removed nor checked.

SNAP = load_snapshot(CONFIG)
KW = dict(snapshot=SNAP, allowed={Decimal("6.5"), Decimal("2.5"), Decimal("2.75")}, tools_called=(), fields=[],
          offer_rep=False, waiting_answer=False, conflicts=[])
LISTED = ("רשמתי: פנסיה 6.5%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי "
          "[[faq:pension_percent,faq:study_fund_percent,faq:credit_points]].\nמה דגם הרכב הצמוד שלכם?")


def test_a_tag_listing_several_sources_never_reaches_the_screen():
    assert "[[" not in strip_tags(LISTED) and "]]" not in strip_tags(LISTED)
    result = check(LISTED, **KW)
    assert "[[" not in result.text
    assert not [v for v in result.violations if v.kind == "source"]       # every listed id is a real FAQ entry


def test_every_id_in_a_listed_tag_is_checked():
    bad = LISTED.replace("faq:credit_points", "faq:no_such_entry")
    assert any(v.kind == "source" for v in check(bad, **KW).violations)


def test_an_unknown_tag_is_removed_and_counts_as_a_missing_source():
    text = "זה תלוי בהסדר החברה [[policy:x]].\nמה דגם הרכב הצמוד שלכם?"
    result = check(text, **KW)
    assert "[[" not in result.text
    assert any(v.kind == "source" for v in result.violations)


# Scenarios 11 and 13: "עמלות ובונוס אין לי בכלל" / "אין לי עמלות או בונוס" — one of the two zeros was refused
# (not grounded), and the bot asked about it again.

CUES = SNAP.extensions.zero_cues


@pytest.mark.parametrize("field, text, said", [
    ("bonus", "אין לי עמלות או בונוס", True),
    ("commissions", "אין לי עמלות או בונוס", True),
    ("commissions", "עמלות ובונוס אין לי בכלל", True),
    ("bonus", "עמלות ובונוס אין לי בכלל", True),
    ("children", "אין נקודות זיכוי וילדים", False),          # the settled ruling stays
    ("commissions", "יש לי עמלות ובונוס אין", False),         # commissions are there; only the bonus is not
    ("bonus", "בונוס ואין לי עמלות", False),
    ("bonus", "אין לי עמלות, הבונוס 2,000", False),
])
def test_a_zero_said_for_a_list_of_fields(field, text, said):
    assert zero_grounded(field, text, CUES) is said


# Scenario 10 (second run): Gemini answered the tow-hook question itself, a guard rejected the answer, and the
# template that replaced it only asked for the cars to compare: the employee's question was dropped.

TOW_HOOK = "האם מותר להתקין וו גרירה על הרכב הצמוד של החברה?"
CAR = 'יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש"ח בחודש'
NOA = "מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"


def test_a_rejected_answer_to_an_arrangement_question_still_offers_the_representative(sessions):
    unsupported = "בדרך כלל מותר להתקין וו גרירה ברכבי החברה."      # a policy claim with no source, no question
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר"))
    turn = svc.send(turn.conversation_id, token, NOA)
    turn = svc.send(turn.conversation_id, token, CAR)
    fake.turn(reply=unsupported, retry=unsupported)
    turn = svc.send(turn.conversation_id, token, TOW_HOOK)
    view = svc.view(turn.conversation_id, token)
    assert turn.source == "template"                                   # the guard rejected both answers
    assert view.state.unanswered_question is not None
    assert "רוצים שאשאל" in turn.reply                                # the question is not dropped
    assert any(f.get("field") == "ask_rep" for f in view.expects.get("fields", [])) or \
        view.expects.get("field") == "ask_rep"
    assert unsupported not in turn.reply


def test_an_off_topic_question_never_gets_the_offer_after_a_rejected_answer(sessions):
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר"))
    turn = svc.send(turn.conversation_id, token, NOA)
    fake.turn(reply="מכבי תנצח בשבת.", retry="מכבי תנצח בשבת.")
    turn = svc.send(turn.conversation_id, token, "מה דעתך, מי ינצח במשחק של מכבי בשבת?")
    view = svc.view(turn.conversation_id, token)
    assert view.state.unanswered_question is None
    assert "נציג" not in turn.reply


# Scenario 13: Gemini saved every value of this message but the children; the parser distrusted the message
# ("6% פנסיה ו-2.5%" reads wrong) and so the gap fill did not save "2 ילדים" either. The count was lost.

def test_children_the_model_left_out_are_saved_by_the_gap_fill(sessions):
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500))
    turn = svc.send(turn.conversation_id, token, 'אני מנהלת מוצר ומשכורת הבסיס שלי 18,500 ש"ח')
    fake.turn(call("save_profile", commissions=0, bonus=0, pension_percent=6, study_fund_percent=2.5, credit_points=2.75))
    turn = svc.send(turn.conversation_id, token,
                    "אין לי עמלות או בונוס. 6% פנסיה ו-2.5% השתלמות, 2.75 נקודות זיכוי, ויש לי 2 ילדים")
    p = svc.view(turn.conversation_id, token).state.profile
    assert p.children == 2
    assert p.pension_percent == Decimal("6")             # the parser's wrong reading (2.5) never overrides the LLM


def test_children_in_words_at_the_end_of_a_list_are_saved_by_the_gap_fill(sessions):
    # scenario 15, second run: "…2.75 נקודות זיכוי ושני ילדים" — the LLM left the children out, and "ושני" (with "ו")
    # did not match the words pattern
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500))
    turn = svc.send(turn.conversation_id, token, "היי, אני מנהלת מוצר ומשכורת הבסיס שלי שמונה עשר וחצי אלף")
    fake.turn(call("save_profile", commissions=0, bonus=0, pension_percent=6, study_fund_percent=2.5, credit_points=2.75))
    turn = svc.send(turn.conversation_id, token,
                    "בלי עמלות ובלי בונוס, פנסיה 6 אחוז, השתלמות 2.5 אחוז, 2.75 נקודות זיכוי ושני ילדים")
    assert svc.view(turn.conversation_id, token).state.profile.children == 2


# Scenario 13, final run: Gemini answered "רשמתי: עמלות 0 ₪, בונוס 0 ₪, …" without calling save_profile. The guard
# rejected the false acknowledgement, nothing was saved, and the employee was asked again for what they had said.

def test_an_acknowledgement_without_a_tool_call_is_asked_again_with_a_tool_required(sessions):
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500))
    turn = svc.send(turn.conversation_id, token, 'אני מנהלת מוצר ומשכורת הבסיס שלי 18,500 ש"ח')
    # unforced: "תודה! רשמתי את הפרטים." and no tool; only a required tool call saves
    fake.turn(call("save_profile", commissions=0, bonus=0, pension_percent=6, study_fund_percent=2.5,
                   credit_points=2.75, children_under_23=2), forced_only=True)
    turn = svc.send(turn.conversation_id, token,
                    "אין לי עמלות או בונוס. 6% פנסיה ו-2.5% השתלמות, 2.75 נקודות זיכוי, ויש לי 2 ילדים")
    p = svc.view(turn.conversation_id, token).state.profile
    assert (p.commissions, p.bonus, p.pension_percent, p.study_fund_percent, p.credit_points, p.children) == \
        (0, 0, Decimal("6"), Decimal("2.5"), Decimal("2.75"), 2)
    assert any(c.require_tool for c in fake.calls)


def test_a_reply_without_an_acknowledgement_is_not_forced(sessions):
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(reply="מה קבוצת הזכאות שלכם?")
    svc.send(turn.conversation_id, token, "שלום")
    assert not any(c.require_tool for c in fake.calls)


def test_an_age_is_never_a_count_of_children(sessions):
    fake = FakeLLM()
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    turn, token = svc.start()
    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500))
    turn = svc.send(turn.conversation_id, token, 'אני מנהלת מוצר ומשכורת הבסיס שלי 18,500 ש"ח')
    fake.turn(call("save_profile", commissions=0, bonus=0))
    turn = svc.send(turn.conversation_id, token, "אין עמלות ואין בונוס. כמה זה ילדים מתחת לגיל 23?")
    assert svc.view(turn.conversation_id, token).state.profile.children is None


def test_a_car_and_its_use_value_in_one_round_are_both_saved(sessions):
    # the same order problem for a car outside the files: its use value needs the car to be known first
    turn, s = run(sessions, [
        (PROFILE, [call("save_profile", occupation="מפתחת תוכנה")]),
        ("יש לי טויוטה מיסטרי, שווי השימוש בתלוש 3,200 ₪",
         [call("save_profile", current_use_value=3200), call("match_current_car", model="טויוטה מיסטרי")]),
    ])
    assert s.current.query is not None
    assert s.current.use_value == Decimal("3200")
