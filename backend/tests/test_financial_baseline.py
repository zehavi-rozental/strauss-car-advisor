"""The calculations stay what the files from Strauss give.

The fixture was captured on the base commit 9198531, before the product work of 4/11 (see financial_matrix.py). A failure here means a
calculation, an order, an eligibility or a source file changed. Formulas, rules, the catalog and source data change only with the
owner's approval: **do not regenerate the fixture to make this pass**, find what moved. A suspected error in the source is documented,
not fixed. The reference employees are checked on their own, against an independent oracle (test_acceptance, test_reference_oracle).
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from app.modules.conversation.option_map import dump_option, evaluate_option
from app.modules.conversation.stage import ConfirmedCopay
from app.modules.conversation.tools import available_options, engine_inputs, rep_copay_unusual
from app.modules.rules.public import load_snapshot

from .financial_matrix import CONFIG, FIXTURE, LOCKED_FILES, current_catalog, file_hash, locked_extensions, matrix, rep_car, run, state_for

BASELINE = json.loads(FIXTURE.read_text(encoding="utf-8"))
SNAPSHOT = load_snapshot(CONFIG)
CASES = matrix(SNAPSHOT)


def test_the_matrix_really_exercises_the_ranking_and_the_engine():
    assert len(BASELINE["cases"]) == len(CASES) == 81
    assert sum(len(c["results"]) for c in BASELINE["cases"].values()) >= 800
    ranked = [c for c in BASELINE["cases"].values() if c["top"]]
    assert len(ranked) > 60 and all(len(c["top"]) <= 3 for c in ranked)
    assert any(c["excluded"] for c in BASELINE["cases"].values())


@pytest.mark.parametrize("name", sorted(CASES))
def test_eligibility_ranking_engine_and_map_match_the_base(name):
    assert run(SNAPSHOT, copy.deepcopy(CASES[name])) == BASELINE["cases"][name]


@pytest.mark.parametrize("path", LOCKED_FILES)
def test_what_strauss_delivered_and_the_engine_are_untouched(path):
    assert file_hash(path) == BASELINE["files"][path], f"{path} changed: this needs the owner's approval"


def test_the_decisions_a_calculation_depends_on_are_untouched():
    assert locked_extensions() == BASELINE["extensions"]
    assert SNAPSHOT.calc.rules_version == BASELINE["rules_version"]


# ---------- a mark for the screen changes nothing ----------

def _with_rep_amount(amount: str, unusual: bool):
    car = rep_car(SNAPSHOT)
    confirmed = {car.catalog_code: ConfirmedCopay(value=Decimal(amount), changed=True, unusual=unusual)}
    return state_for(car.group or SNAPSHOT.calc.group_order[0], "18500", 2, ("0", "0"), current_catalog(SNAPSHOT), confirmed=confirmed)


@pytest.mark.parametrize("multiple", ["3.1", "10", "1000"])
def test_the_unusual_mark_changes_no_figure_no_order_and_no_status(multiple):
    car = rep_car(SNAPSHOT)
    amount = str(car.default_copay * Decimal(multiple))
    assert rep_copay_unusual(SNAPSHOT, car.catalog_code, Decimal(amount)) is True
    assert run(SNAPSHOT, _with_rep_amount(amount, True)) == run(SNAPSHOT, _with_rep_amount(amount, False))


def test_a_marked_car_with_the_highest_net_still_leads():
    """A marked amount can still leave the highest net: the order is the engine's, and the mark is only said."""
    car = rep_car(SNAPSHOT)
    amount = str(car.default_copay * Decimal("3.1"))
    state = _with_rep_amount(amount, True)
    out = run(SNAPSHOT, state)
    nets = {o: Decimal(r[1]) for o, r in out["results"].items() if r[1] is not None}
    assert out["leaders"] == [o for o, d in nets.items() if d == max(nets.values())]
    assert out["top"] and [nets[o] for o in out["top"]] == sorted(nets.values(), reverse=True)[:3]


def test_the_mark_is_added_to_the_result_and_nothing_else_in_it_moves():
    car = rep_car(SNAPSHOT)
    amount = str(car.default_copay * Decimal("10"))
    results = []
    for unusual in (True, False):
        state = _with_rep_amount(amount, unusual)
        employee, current, occupation = engine_inputs(SNAPSHOT, state)
        option = next(o for o in available_options(SNAPSHOT, state) if o.id == car.catalog_code)
        alternative, result = evaluate_option(SNAPSHOT, state, option, employee, current, occupation)
        results.append(dump_option(state, option.id, alternative, result))
    marked, plain = results
    assert marked.pop("unusual_copay") is True and "unusual_copay" not in plain
    assert marked == plain


def test_the_rep_threshold_is_ours_and_only_asks():
    """rep_copay_review_multiple is not a rule of Strauss: it decides when the representative is asked to confirm, nothing more."""
    assert SNAPSHOT.extensions.rep_copay_review_multiple == 3
    car = rep_car(SNAPSHOT)
    assert rep_copay_unusual(SNAPSHOT, car.catalog_code, car.default_copay) is False
    assert rep_copay_unusual(SNAPSHOT, car.catalog_code, car.default_copay * 3) is False


# ---------- what the employee types reaches the calculation as it was typed ----------

@pytest.mark.parametrize("text, value", [("שכר בסיס 18,500", "18,500"), ("הכנסה 120000 בשנה", "120000"), ("עמלות 2400 ובונוס 1000", "2400"),
                                         ("השתתפות 1250 בחודש", "1250")])
def test_removing_contact_details_leaves_amounts_as_they_are(text, value):
    from app.modules.conversation.parsing import redact_contact_details
    assert value in redact_contact_details(text)
