"""Property-based checks of the syncing between form, chat, profile and results (QA 21/10).

Random sequences of form submissions, labelled chat messages and car corrections, in template mode (no LLM).
Invariants:
- a form is all or nothing, and after it the profile holds exactly the last approved values;
- a labelled message saves every valid value it names, refuses the invalid one, and touches nothing else;
- a result that is not marked stale is the engine's result for the current profile and car.
"""
from __future__ import annotations

import os
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.modules.calculation.public import evaluate
from app.modules.conversation.public import ConversationService, FieldsInvalid, LLMChain, results_stale
from app.modules.conversation.tools import available_options, engine_inputs, option_state
from app.modules.rules.public import RulesStore, load_snapshot
from app.shared.db import session_factory

from .conftest import CONFIG, build_engine

SNAP = load_snapshot(CONFIG)
RANGES = SNAP.extensions.value_ranges
PROFILE = ("מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")
ATTR = {"base_salary": "base", "commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
        "study_fund_percent": "study_fund_percent", "credit_points": "credit_points", "children_under_23": "children"}
# QA_EXAMPLES=300 for a deeper local run (CI: 40)
FAST = settings(max_examples=int(os.environ.get("QA_EXAMPLES", "40")), deadline=None, suppress_health_check=[HealthCheck.too_slow])


def service():
    return ConversationService(session_factory(build_engine()), RulesStore(CONFIG), LLMChain())


def profile_values(svc, cid, token) -> dict:
    p = svc.view(cid, token).state.profile
    return {arg: getattr(p, attr) for arg, attr in ATTR.items()}


# ordinary values only (rare values are held for "לאשר" by design; tested elsewhere)
def ordinary(field: str):
    if field == "children_under_23":
        return st.integers(0, 8)
    if field == "base_salary":
        return st.integers(6001, 60000).map(Decimal)
    if field in ("commissions", "bonus"):
        return st.one_of(st.just(Decimal(0)), st.integers(100, 20000).map(Decimal))
    low, high = RANGES[field]
    step = Decimal("0.25")
    count = int((Decimal(str(high)) - max(Decimal(str(low)), step)) / step)
    return st.integers(1, count).map(lambda n: n * step)


def out_of_range(field: str):
    high = Decimal(str(RANGES[field][1]))
    return st.integers(1, 50).map(lambda n: high + n)


@st.composite
def submission(draw):
    fields = draw(st.lists(st.sampled_from(list(ATTR)), min_size=1, max_size=4, unique=True))
    values = {f: draw(ordinary(f)) for f in fields}
    if draw(st.booleans()) and any(f != "children_under_23" for f in fields):
        bad = draw(st.sampled_from([f for f in fields if f != "children_under_23"]))
        values[bad] = draw(out_of_range(bad))
    return values


@FAST
@given(st.lists(submission(), min_size=1, max_size=5))
def test_a_form_is_all_or_nothing_and_the_last_approved_value_wins(submissions):
    svc = service()
    turn, token = svc.start()
    cid = turn.conversation_id
    svc.send(cid, token, "אני בקבוצה ג")
    expected = profile_values(svc, cid, token)
    for values in submissions:
        invalid = any(not Decimal(str(RANGES[k][0])) <= Decimal(str(v)) <= Decimal(str(RANGES[k][1]))
                      for k, v in values.items())
        try:
            svc.send_fields(cid, token, dict(values))
            assert not invalid
            expected.update(values)
        except FieldsInvalid as exc:
            assert invalid
            assert set(exc.errors) == {k for k, v in values.items()
                                       if not Decimal(str(RANGES[k][0])) <= Decimal(str(v)) <= Decimal(str(RANGES[k][1]))}
        got = profile_values(svc, cid, token)
        assert {k: (None if v is None else Decimal(str(v))) for k, v in got.items()} == \
               {k: (None if v is None else Decimal(str(v))) for k, v in expected.items()}


LABELS = {"commissions": "עמלות", "bonus": "בונוס", "pension_percent": "פנסיה", "study_fund_percent": "קרן השתלמות",
          "credit_points": "נקודות זיכוי"}


def said(field: str, value: Decimal, sep: str) -> str:
    number = f"{value.normalize():f}"
    return f"{number} נקודות זיכוי" if field == "credit_points" else f"{LABELS[field]}{sep}{number}"


@FAST
@given(st.lists(st.sampled_from(list(LABELS)), min_size=2, max_size=5, unique=True),
       st.sampled_from([" ", ": ", "-", " - "]), st.sampled_from([", ", " ", "\n", "; "]),
       st.data())
def test_a_labelled_message_saves_every_valid_value_and_refuses_only_the_bad_one(fields, sep, join, data):
    values = {f: data.draw(ordinary(f).filter(lambda v: v != 0 or f in ("commissions", "bonus"))) for f in fields}
    bad = data.draw(st.one_of(st.none(), st.sampled_from([f for f in fields if f != "credit_points"])))
    if bad is not None:
        values[bad] = data.draw(out_of_range(bad))
    svc = service()
    turn, token = svc.start()
    cid = turn.conversation_id
    svc.send(cid, token, "אני בקבוצה ג, שכר בסיס 18,000")
    text = join.join(said(f, values[f], sep) for f in fields)
    turn = svc.send(cid, token, text)
    got = profile_values(svc, cid, token)
    for f in fields:
        if f == bad:
            assert got[f] is None, (text, f)
        else:
            assert got[f] is not None and Decimal(str(got[f])) == values[f], (text, f, got[f])
    for f in set(LABELS) - set(fields):
        assert got[f] is None, (text, f)                                  # nothing invented
    assert got["children_under_23"] is None
    if bad is not None:
        assert LABELS[bad] in turn.reply, turn.reply                      # the refused value is named


CAR_STEPS = st.sampled_from([
    ("text", "זה לא הרכב שלי"), ("text", "יש לי קורולה 2026"), ("text", "יש לי קורולה קרוס"),
    ("fields", {"base_salary": 21000}), ("fields", {"bonus": 1500}), ("fields", {"children_under_23": 4}),
    ("text", "אטו 3"), ("text", "MG4"), ("text", "300"),
])


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(CAR_STEPS, min_size=1, max_size=6))
def test_a_result_not_marked_stale_is_the_engines_result_for_the_current_data(steps):
    svc = service()
    turn, token = svc.start()
    cid = turn.conversation_id
    for text in (PROFILE, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש", "אטו 3"):
        svc.send(cid, token, text)
    for kind, value in steps:
        if kind == "text":
            svc.send(cid, token, value)
        else:
            svc.send_fields(cid, token, dict(value))
        s = svc.view(cid, token).state
        if not s.results or results_stale(s):
            continue
        employee, current, occupation = engine_inputs(SNAP, s)
        options = {o.id: o for o in available_options(SNAP, s)}
        for item in s.results:
            confirmed = s.confirmed_copays.get(item.option_id)
            fresh = evaluate(employee, current, option_state(options[item.option_id], SNAP.extensions.copay_policy,
                                                             confirmed.value if confirmed else None),
                             SNAP.calc, occupation)
            assert str(fresh.delta_display) == str(item.result["delta_display"]), (steps, item.label)
