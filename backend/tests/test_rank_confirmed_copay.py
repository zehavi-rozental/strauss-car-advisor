""""הכי משתלם" ranks with the copay the representative confirmed, like the comparison and the map (QA 22/10).

Found in the calculation audit: tools.rank_options built each option with option_state(o) — the catalog copay,
the default policy — while the comparison (agent._calculate) and the option map (option_map.evaluate_option)
use the representative's confirmed copay. After a confirmation, the top three could differ from what the
numbers on screen say. The engine is unchanged; only the ranking's inputs are."""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation.option_map import build_option_map
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.conversation.stage import ConfirmedCopay
from app.modules.conversation.tools import NoArgs, ToolContext, available_options, rank_options
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CONFIG

SNAP = load_snapshot(CONFIG)
NOA = "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"
CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"


def noa_state(sessions):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    turn, token = svc.start()
    for text in (NOA, CAR):
        turn = svc.send(turn.conversation_id, token, text)
    return svc.view(turn.conversation_id, token).state


def ranked(state):
    ctx = ToolContext(snapshot=SNAP, find_vehicle=lambda *_: None, state=state)
    assert rank_options(ctx, NoArgs())["selected"]
    return list(state.selected)


def test_a_confirmed_copay_changes_the_ranking(sessions):
    state = noa_state(sessions)
    before = ranked(state.model_copy(deep=True))
    cars = {o.id: o for o in available_options(SNAP, state) if o.car is not None}
    best = next(oid for oid in before if oid in cars)                  # the highest-ranked car (not opting out)
    option = cars[best]
    # the representative confirmed a copay 5,000 higher than the catalog's for the best option: it is no longer
    # among the three highest nets
    state.confirmed_copays[best] = ConfirmedCopay(value=option.car.default_copay + Decimal(5000), changed=True)
    after = ranked(state.model_copy(deep=True))
    assert best not in after


def test_the_ranking_agrees_with_the_option_map(sessions):
    state = noa_state(sessions)
    some = [o for o in available_options(SNAP, state) if o.car is not None][3]
    state.confirmed_copays[some.id] = ConfirmedCopay(value=Decimal(0), changed=True)     # the rep waived it
    top = ranked(state.model_copy(deep=True))
    m = build_option_map(SNAP, state.model_copy(deep=True))
    calculated = [e for e in m.entries if e.result is not None and e.status != "incomplete"]
    by_net = [e.option_id for e in sorted(calculated, key=lambda e: Decimal(e.result["delta_display"]), reverse=True)]
    assert top == by_net[:len(top)]
