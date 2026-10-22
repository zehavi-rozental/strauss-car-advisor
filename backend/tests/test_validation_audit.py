"""Validation audit fixes: (2) a free-text car name never carries pay data to the representative, (1) a value the
model saves must be said by the employee (grounding), (3) alternatives resolve by exact name or a unique fragment.
Each test goes through the real service with a scripted model, so the tool-call layer and the visible reply are
both checked."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.conversation.tools import (
    GENERIC_CAR_LABEL, ToolContext, available_options, rep_car_label, resolve_option, run_tool, safe_car_label)
from app.modules.conversation.stage import ConversationState
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CAR_WORDS, CONFIG, NOA_WORDS, build_engine  # noqa: F401  (build_engine: the shared DB)

SNAP = load_snapshot(CONFIG)
PROFILE = ("מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")


def service(sessions, fake):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))


def run(svc, fake, steps):
    """steps: (message, [tool calls the model makes]) → the last turn, the token and the state."""
    turn, token = svc.start()
    for message, calls in steps:
        if calls is not None:
            fake.turn(*calls)
        turn = svc.send(turn.conversation_id, token, message)
    return turn, token, svc.view(turn.conversation_id, token).state


# ====================== Fix 2: the car label that reaches the representative ======================

LEAK = "טויוטה שכר 18,500 הבונוס שלי 2,000"


@pytest.mark.parametrize("text, safe", [
    ("פולקסווגן גולף", True), ("מאזדה 6", True), ("פביה 2000", True), ("BMW X5 2021", True),
    (LEAK, False), ("טויוטה 150000", False), ("קורולה 1500", False), ("רכב עם ברוטו 20", False),
    ("x" * 50, False), ("א ב ג ד ה ו", False), ("", False), (None, False),
])
def test_a_free_text_car_name_is_kept_only_when_it_is_a_car_name(text, safe):
    label = safe_car_label(text)
    assert (label == " ".join(str(text).split())) if safe else label == GENERIC_CAR_LABEL


def test_pay_data_typed_as_a_car_never_reaches_the_representative(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token, s = run(svc, fake, [
        (PROFILE, [call("save_profile", occupation="מנהלת מוצר")]),
        (f"יש לי {LEAK}", [call("match_current_car", model=LEAK)]),
        ("לא ידוע", [call("save_profile", current_use_value_unknown=True)]),
    ])
    # P0-B (22/10): text with pay data is not a car name — nothing is stored (not even sanitized), the car is
    # asked again, and no representative question is opened about it
    assert (s.current.query, s.current.label) == (None, None)
    for question in svc.cases.list_open():
        blob = f"{question.question_text if hasattr(question, 'question_text') else question} {question.context}"
        assert not any(x in blob for x in ("18,500", "18500", "2,000", "שכר", "בונוס")), blob


def test_a_real_car_name_still_reaches_the_representative(sessions):
    """The car label a question to the representative carries (a general question's context). The unknown use
    value itself is the employee's to find and is never sent (rule A, 22/10)."""
    fake = FakeLLM()
    svc = service(sessions, fake)
    _, _, s = run(svc, fake, [(PROFILE, [call("save_profile", occupation="מנהלת מוצר")]),
                              ("יש לי פולקסווגן גולף", [call("match_current_car", model="פולקסווגן גולף")]),
                              ("לא ידוע", [call("save_profile", current_use_value_unknown=True)])])
    assert svc.cases.list_open() == [] and s.current.use_value_unknown
    assert rep_car_label(s.current, SNAP) == "פולקסווגן גולף"


# ====================== Fix 1: grounding ======================

UNGROUNDED = [
    ("zeros nobody said", "שלום", dict(commissions=0, bonus=0, children_under_23=0), {}),
    ("pension and credit points from small talk", "שלום", dict(pension_percent=6, credit_points=2.5), {}),
    ("a group from small talk", "שלום", dict(eligibility_group="ד"), {}),
    ("a salary from small talk", "היי מה אתם עושים", dict(base_salary=25000), {}),
    ("children that unlock the family track", "שלום", dict(children_under_23=4), {}),
    ("an occupation from small talk", "שלום", dict(occupation="מנהלת מוצר"), {}),
    ("a drive from small talk", "תודה", dict(current_drive="ELECTRIC"), {}),
    ("an amount x1000 (\"2\" children is not 2,000)", "יש לי 2 ילדים", dict(commissions=2000, children_under_23=2),
     {"children": 2}),
    ("a bare 2 is not 2,000", "בונוס 2", dict(bonus=2000), {}),
    ("a zero without a cue", "לא יודע", dict(commissions=0), {}),
]


@pytest.mark.parametrize("label, message, calls, kept", UNGROUNDED, ids=[u[0] for u in UNGROUNDED])
def test_a_value_the_employee_did_not_say_is_never_saved(sessions, label, message, calls, kept):
    fake = FakeLLM()
    svc = service(sessions, fake)
    _, _, s = run(svc, fake, [(message, [call("save_profile", **calls)])])
    p = s.profile
    saved = {k: getattr(p, k) for k in ("base", "commissions", "bonus", "pension_percent", "study_fund_percent",
                                        "credit_points", "children") if getattr(p, k) is not None}
    assert saved == kept and p.group is None and p.occupation is None and s.current.drive is None, saved
    assert s.stage == Stage.COLLECT_PROFILE


def test_a_copay_of_zero_nobody_said_is_not_saved_with_the_car_known(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    _, _, s = run(svc, fake, [(NOA_WORDS, [call("save_profile", occupation="מנהלת מוצר", base_salary=18500,
                                                commissions=0, bonus=0, pension_percent=6, study_fund_percent=2.5,
                                                credit_points=2.75, children_under_23=2)]),
                              ("יש לי קורולה קרוס 2025", [call("match_current_car", model="COROLLA CROSS")]),
                              ("תודה", [call("save_profile", current_copay=0)])])
    assert s.current.catalog_code == "CAR-09" and s.current.copay is None      # asked, not zero


def test_what_the_employee_said_is_saved_in_digits_in_words_and_as_a_zero(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    _, _, s = run(svc, fake, [(NOA_WORDS, [call("save_profile", occupation="מנהלת מוצר", base_salary=18500,
                                                commissions=0, bonus=0, pension_percent=6, study_fund_percent=2.5,
                                                credit_points=2.75, children_under_23=2)])])
    p = s.profile
    assert (p.group, p.base, p.commissions, p.bonus) == ("ג", 18500, 0, 0)          # "שמונה עשר וחצי אלף", "בלי"
    assert (p.pension_percent, p.study_fund_percent, p.credit_points, p.children) == (6, Decimal("2.5"),
                                                                                       Decimal("2.75"), 2)


@pytest.mark.parametrize("message, model_value, saved", [
    ("שכר בסיס שמונה עשר וחצי אלף", 18500, 18500), ("משכורת עשרים אלף ברוטו", 20000, 20000),
    ("שכר חמש עשרה אלף", 15000, 15000), ("שכר בסיס 20 אלף", 20000, 20000),
    ("שכר בסיס 18.5 ברוטו", "18.5", None), ("מרוויחה 18 וחצי ברוטו", "18.5", None),      # shorthand: asked 18,500?
])
def test_number_words_and_shorthand_are_not_wrongly_refused_end_to_end(sessions, message, model_value, saved):
    fake = FakeLLM()
    svc = service(sessions, fake)
    _, _, s = run(svc, fake, [(message, [call("save_profile", base_salary=model_value)])])
    if saved is None:
        assert s.profile.base is None and s.pending_confirmation.value == 18500    # held, asked "18,500 ₪?"
    else:
        assert s.profile.base == saved


def test_a_zero_the_employee_said_is_saved_but_one_they_did_not_say_in_the_same_call_is_not(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    _, _, s = run(svc, fake, [("אין עמלות ואין בונוס", [call("save_profile", commissions=0, bonus=0,
                                                              study_fund_percent=0)])])
    p = s.profile
    assert (p.commissions, p.bonus, p.study_fund_percent) == (0, 0, None)             # the study fund was not said


def test_the_alternative_the_model_picks_must_be_named_numbered_or_a_ranking_request(sessions):
    steps = [(NOA_WORDS, [call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                               pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2)]),
             (CAR_WORDS, [call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300)])]
    for message, picks, selected in [
        ("מה שלומך?", ["ATTO 3"], []),                       # small talk: nothing chosen
        ("תבדוק לי את אטו 3", ["ATTO 3"], ["CAR-10"]),        # named
        ("תבדוק לי את אטו 3", ["MG4"], ["CAR-10"]),           # the model chose another: what was said is selected
        ("את החשמלי הסיני בבקשה", ["ATTO 3"], ["CAR-10"]),    # described by its drive
        ("מספר 10 ברשימה", ["ATTO 3"], ["CAR-10"]),           # a number from the list shown
    ]:
        fake = FakeLLM()
        svc = service(sessions, fake)
        _, _, s = run(svc, fake, [*steps, (message, [call("select_options", options=picks)])])
        assert s.selected == selected and "CAR-06" not in s.selected, (message, picks, s.selected)


def test_asking_for_the_ranking_grounds_a_ranked_selection(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, _, s = run(svc, fake, [
        (NOA_WORDS, [call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                          pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2)]),
        (CAR_WORDS, [call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300)]),
        ("מה הכי משתלם לי?", [call("rank_options")])])
    assert s.ranked_count > 0 and s.selected


# ====================== Fix 3: exact or unique alternatives ======================

def _options():
    st = ConversationState()
    st.profile.group, st.profile.children = "ג", 0
    return available_options(SNAP, st)


@pytest.mark.parametrize("text, car", [
    ("אטו", "CAR-10"), ("ATTO 3", "CAR-10"), ("ATTO 3 (2025)", "CAR-10"), ("atto", "CAR-10"),       # exact / unique
    ("mazda", "CAR-13"), ("קורולה", "CAR-07"), ("קורולה קרוס", "CAR-09"), ("ev3", "CAR-11"), ("CAR-06", "CAR-06"),
    ("3", None), ("a", None), ("ev", None), ("", None),                                              # too short
    ("cross", None), ("קורולה קר", None),                                                           # several cars
    ("MODEL 3", None), ("טסלה", None),                                                              # not eligible
])
def test_alternatives_resolve_by_exact_name_or_a_unique_fragment(text, car):
    opt = resolve_option(SNAP, _options(), text)
    assert (opt.id if opt else None) == car


def test_an_ambiguous_fragment_is_reported_back_so_the_employee_is_asked(sessions):
    st = ConversationState()
    p = st.profile
    p.group, p.occupation_unknown, p.base, p.commissions, p.bonus = "ג", True, 18500, 0, 0
    p.pension_percent, p.study_fund_percent, p.credit_points, p.children = 6, Decimal("2.5"), Decimal("2.75"), 2
    ctx = ToolContext(snapshot=SNAP, find_vehicle=lambda *_: None, state=st)
    result = run_tool(ctx, "select_options" if False else "calculate", {"options": ["cross"]})
    assert result["ok"] is False and "YARIS CROSS HSD (2025) או COROLLA CROSS (2025)" in result["error"]
    result = run_tool(ctx, "calculate", {"options": ["cross", "אטו"]})
    assert result["ok"] and result["selected"] == ["CAR-10"] and "cross" in result["ambiguous"]
