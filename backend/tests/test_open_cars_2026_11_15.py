""""בין כמה רכבים אני יכולה לבחור?" is answered with the exact number (QA 15/11).

On 8870, after "אני בקבוצה ב", the question got the representative offer ("על זה אין לי מידע מדויק"), and so did "ומה
מהם חשמליים?" and "ועכשיו כמה?" after a group correction. Code already holds the answer: the eligibility rules
(tools.classify_options) give the cars open to the employee, the same list the alternatives question offers. Now a
question about the open cars is answered by code from the state after the turn's saves (a group corrected in the same
message counts), in both modes; a follow-up keeps the list; nothing goes to the representative.
"""
from __future__ import annotations

from app.modules.conversation.public import LLMChain
from app.modules.conversation.stage import ConversationState
from app.modules.conversation.tools import available_options

from .conftest import make_client


def start(engine, llm=None):
    c = make_client(engine, llm=llm) if llm is not None else make_client(engine)
    cid = c.post("/api/conversations").json()["conversation_id"]

    def say(text):
        r = c.post(f"/api/conversations/{cid}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        return r.json()["reply"]
    return c, cid, say


def open_cars(snapshot, group, drive=None):
    state = ConversationState()
    state.profile.group = group
    return [o for o in available_options(snapshot, state) if o.car is not None
            and (drive is None or (o.drive is not None and o.drive.value == drive))]


def test_how_many_cars_the_group_opens_is_said_exactly_never_offered_to_the_rep(engine, snapshot):
    c, cid, say = start(engine)
    say("אני בקבוצה ב")
    reply = say("בין כמה רכבים אני יכולה לבחור?")
    cars = open_cars(snapshot, "ב")
    assert f"בקבוצה ב ומטה פתוחים לכם {len(cars)} רכבים" in reply
    assert all(o.label in reply for o in cars)
    assert "נציג" not in reply and "אין לי מידע" not in reply
    assert "קבוצת הזכאות" not in reply.split("\n\n")[-2]          # the group is remembered: never asked again


def test_a_follow_up_filters_the_same_list_and_a_group_correction_counts_again(engine, snapshot):
    c, cid, say = start(engine)
    say("אני בקבוצה ב")
    say("בין כמה רכבים אני יכולה לבחור?")
    electric = say("ומה מהם חשמליים?")
    picked = open_cars(snapshot, "ב", "ELECTRIC")
    assert f"{len(picked)} חשמליים" in electric and all(o.label in electric for o in picked)
    again = say("רגע, בעצם אני בקבוצה ג. ועכשיו כמה?")
    assert f"בקבוצה ג ומטה פתוחים לכם {len(open_cars(snapshot, 'ג'))} רכבים" in again       # after the correction
    assert again.count("קבוצה ג.") <= 1 and not ("רשמתי: קבוצה ג" in again and "עדכנתי: קבוצה ג" in again)
    assert c.get(f"/api/conversations/{cid}").json()["profile"]["group"] == "ג"


def test_with_the_model_the_count_is_code_s_whatever_the_model_writes(engine, snapshot):
    from app.modules.conversation.llm.fake import FakeLLM
    fake = FakeLLM()
    fake.turn(reply="יש לכם 7 רכבים לבחור מהם.\nמה שכר הבסיס החודשי שלכם?")      # a wrong count of its own
    plain_c, cid, plain_say = start(engine)
    plain_say("אני בקבוצה ב")
    c = make_client(engine, llm=LLMChain([fake]))
    c.cookies = plain_c.cookies
    reply = c.post(f"/api/conversations/{cid}/messages", json={"text": "בין כמה רכבים אני יכולה לבחור?"}).json()["reply"]
    assert f"פתוחים לכם {len(open_cars(snapshot, 'ב'))} רכבים" in reply
    assert "7 רכבים" not in reply, reply                                  # never a second, wrong count
    system = next(m.content for m in fake.calls[0].messages if m.role == "system")
    assert "OPEN CARS:" in system


def test_other_questions_keep_their_meaning(engine):
    c, cid, say = start(engine)
    say("אני בקבוצה ב")
    assert "לוותר" in say("אני יכולה לוותר על הרכב?")                   # opting out: the FAQ, not the list
    catalog = say("אילו רכבים היברידיים יש בקטלוג?")                      # the whole catalog, as before
    assert "פתוחים לכם" not in catalog
    assert "COROLLA" in say("כמה ההשתתפות של ה-COROLLA?")                 # one car named: the catalog's own fact


def test_a_model_acknowledgement_of_nothing_saved_this_turn_is_dropped(engine):
    # live 15/11: "רשמתי: קבוצה ב." on every question after the group was saved, and a sentence about the list itself
    from app.modules.conversation.llm.fake import FakeLLM
    fake = FakeLLM()
    fake.turn(reply="רשמתי: קבוצה ב.\nמה שכר הבסיס החודשי שלכם?")
    plain_c, cid, plain_say = start(engine)
    plain_say("אני בקבוצה ב")
    c = make_client(engine, llm=LLMChain([fake]))
    c.cookies = plain_c.cookies
    reply = c.post(f"/api/conversations/{cid}/messages", json={"text": "בין כמה רכבים אני יכולה לבחור?"}).json()["reply"]
    assert "רשמתי" not in reply and "פתוחים לכם" in reply and "מה שכר הבסיס החודשי שלכם?" not in reply


# ---------- 16/11: context across turns, the update first, the family note only when it matters ----------

PAY = {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
       "credit_points": 2.75}


def test_a_follow_up_after_a_question_between_still_refers_to_the_open_cars(engine, snapshot):
    c, cid, say = start(engine)
    say("אני בקבוצה ב")
    say("בין כמה רכבים אני יכולה לבחור?")
    say("מה זה שווי שימוש?")                                            # a question between
    hybrid = say("ואילו היברידיים?")
    picked = open_cars(snapshot, "ב", "HYBRID")
    assert all(o.label in hybrid for o in picked) and "נציג" not in hybrid
    other = say("כמה אחוז פנסיה צריך לכתוב?")                          # "כמה" about something else
    assert "פתוחים לכם" not in other


def test_the_open_cars_context_expires(engine):
    c, cid, say = start(engine)
    say("אני בקבוצה ב")
    say("בין כמה רכבים אני יכולה לבחור?")
    for m in ("מה זה שווי שימוש?", "מה זה פנסיה?", "מה זה בונוס?", "מה זה עמלות?"):
        say(m)
    assert "פתוחים לכם" not in say("ועכשיו כמה?")                       # too long ago: not guessed


def test_a_bare_follow_up_with_net_figures_on_screen_is_asked_then_answered(engine, snapshot):
    c, cid, say = start(engine)
    say("אני בקבוצה ב")
    c.post(f"/api/conversations/{cid}/messages", json={"fields": {**PAY, "children_under_23": 2}})
    say("קורולה 2026, משלמת 300")
    c.post(f"/api/conversations/{cid}/messages", json={"choices": ["CAR-06"], "field": "options"})
    say("בין כמה רכבים אני יכולה לבחור?")
    say("מה זה שווי שימוש?")
    asked = say("ועכשיו כמה?")
    assert "התכוונתם לכמה רכבים פתוחים לכם, או לנטו שלכם?" in asked and "פתוחים לכם 7" not in asked
    answered = say("הרכבים")
    assert "פתוחים לכם 7 רכבים (בלי הרכב שלכם היום)" in answered       # today's COROLLA is not an alternative


def test_a_change_is_confirmed_once_and_first_then_the_updated_answer(engine):
    c, cid, say = start(engine)
    say("אני בקבוצה ב")
    say("בין כמה רכבים אני יכולה לבחור?")
    reply = say("רגע, בעצם אני בקבוצה ג. ועכשיו כמה?")
    first = reply.split("\n")[0]
    assert first.startswith(("עדכנתי", "רשמתי")) and "קבוצה ג" in first
    assert reply.count("קבוצה ג.") + reply.count("קבוצה ג במקום") == 1
    assert reply.index("קבוצה ג") < reply.index("פתוחים לכם")


def test_the_family_note_only_when_it_may_change_the_answer_and_once(engine):
    c, cid, say = start(engine)
    say("אני בקבוצה ב")
    first = say("בין כמה רכבים אני יכולה לבחור?")
    assert "המסלול המשפחתי" in first                                    # children unknown: it may change the count
    again = say("רגע, בעצם אני בקבוצה ג. ועכשיו כמה?")
    assert "המסלול המשפחתי" not in again                                # said once
    c2, cid2, say2 = start(engine)
    say2("אני בקבוצה ב")
    c2.post(f"/api/conversations/{cid2}/messages", json={"fields": {**PAY, "children_under_23": 2}})
    assert "המסלול המשפחתי" not in say2("בין כמה רכבים אני יכולה לבחור?")   # 2 children: it changes nothing
