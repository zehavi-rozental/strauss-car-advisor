"""A matrix of employees through the conversation's financial path: eligibility, the ranking, each option's engine result, the map.

`python -m tests.financial_matrix capture` (from backend/) writes tests/fixtures/financial_baseline.json. It was run once, on the base
commit 9198531, before the product work of 4/11. **Do not run it again to make a test pass:** a difference means a calculation, an
order or an eligibility changed, and formulas, rules, the catalog and source data change only with the owner's approval.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.modules.conversation.option_map import build_option_map, calculated, evaluate_option, leaders
from app.modules.conversation.stage import ConfirmedCopay, ConversationState, CurrentCar, Profile
from app.modules.conversation.tools import NoArgs, ToolContext, available_options, classify_options, engine_inputs, rank_options
from app.modules.rules.public import load_snapshot

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND / "config"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "financial_baseline.json"

BASES = ("9000", "18500", "45000")
CHILDREN = (0, 2, 4)
EXTRAS = (("0", "0"), ("2400", "1000"))

# what Strauss delivered, and the engine (paths from backend/)
LOCKED_FILES = ("config/strauss-rules.json", "config/strauss-data.xlsx", "data/tax-authority-2026.csv",
                "app/modules/calculation/engine.py", "app/modules/calculation/models.py", "app/modules/calculation/public.py",
                "app/modules/calculation/tax.py")
# the decisions in extensions.yaml a calculation or a check depends on (word lists change with the conversation, these do not)
LOCKED_EXTENSIONS = ("family_gap_adds_to_copay", "copay_policy", "plug_in_counts_as_electric", "child_age_inclusive",
                     "default_vehicle_type", "value_ranges", "small_amount_below", "unusual_values")


def file_hash(relative: str) -> str:
    raw = (BACKEND / relative).read_bytes()
    if not relative.endswith(".xlsx"):
        raw = raw.replace(b"\r\n", b"\n")      # the same file on any checkout
    return hashlib.sha256(raw).hexdigest()


def locked_extensions() -> dict[str, Any]:
    data = yaml.safe_load((CONFIG / "extensions.yaml").read_text(encoding="utf-8"))
    return {k: data[k] for k in LOCKED_EXTENSIONS}


def state_for(group: str, base: str, children: int, extra: tuple[str, str], current: CurrentCar, occupation: str | None = None,
              confirmed: dict[str, ConfirmedCopay] | None = None) -> ConversationState:
    p = Profile(group=group, base=Decimal(base), commissions=Decimal(extra[0]), bonus=Decimal(extra[1]),
                pension_percent=Decimal("6"), study_fund_percent=Decimal("2.5"), credit_points=Decimal("2.25"), children=children,
                occupation=occupation)
    return ConversationState(profile=p, current=current, confirmed_copays=confirmed or {})


def run(snapshot: Any, state: ConversationState) -> dict[str, Any]:
    out: dict[str, Any] = {}
    options, excluded = classify_options(snapshot, state)
    out["eligible"] = [o.id for o in options]
    out["excluded"] = sorted(f"{x.label}: {x.reason}" for x in excluded)
    out["available"] = [o.id for o in available_options(snapshot, state)]
    employee, current, occupation = engine_inputs(snapshot, state)
    rows = {}
    for option in options:
        _, r = evaluate_option(snapshot, state, option, employee, current, occupation)
        rows[option.id] = [r.status.value, None if r.delta_display is None else str(r.delta_display),
                           None if r.alternative is None else str(r.alternative.net_display),
                           None if r.today is None else str(r.today.net_display)]
    out["results"] = rows
    ranked = copy.deepcopy(state)
    ctx = ToolContext(snapshot=snapshot, find_vehicle=lambda *_a, **_k: None, state=ranked)
    try:
        out["top"] = rank_options(ctx, NoArgs()).get("selected")
        out["count"] = ranked.ranked_count
    except Exception as e:      # nothing to rank is an outcome too
        out["top"], out["count"] = None, type(e).__name__
    m = build_option_map(snapshot, state)
    out["map"] = {e.option_id: e.status for e in m.entries}
    out["calculated"] = [e.option_id for e in calculated(m)]
    out["leaders"] = [e.option_id for e in leaders(m)]
    return out


def rep_car(snapshot: Any) -> Any:
    return [c for c in snapshot.catalog if c.default_copay][1]


def current_catalog(snapshot: Any) -> CurrentCar:
    today = next(c for c in snapshot.catalog if c.default_copay)
    return CurrentCar(source="catalog", catalog_code=today.catalog_code, label=today.catalog_code, use_value=today.use_value,
                      copay=Decimal("300"))


def matrix(snapshot: Any) -> dict[str, ConversationState]:
    currents = {"no-car": lambda: CurrentCar(source="none", maintenance=Decimal("0")), "catalog": lambda: current_catalog(snapshot)}
    cases: dict[str, ConversationState] = {}
    for group in snapshot.calc.group_order:
        for base in BASES:
            for children in CHILDREN:
                for name, make in currents.items():
                    extra = EXTRAS[1] if base == "18500" and children == 2 else EXTRAS[0]
                    cases[f"{group}/{base}/{children}/{name}"] = state_for(group, base, children, extra, make())
    for name, role in sorted(snapshot.occupations.items())[:6]:
        cases[f"role/{name}"] = state_for(role.group, "18500", 2, EXTRAS[0], current_catalog(snapshot), occupation=name)
    # a representative's amounts, usual and far from the catalog: the calculation and the order they give, with no mark
    car = rep_car(snapshot)
    for amount in ("300", "999", "350000"):
        confirmed = {car.catalog_code: ConfirmedCopay(value=Decimal(amount), changed=True)}
        cases[f"rep/{car.catalog_code}/{amount}"] = state_for(car.group or snapshot.calc.group_order[0], "18500", 2, EXTRAS[0],
                                                             current_catalog(snapshot), confirmed=confirmed)
    return cases


def capture() -> dict[str, Any]:
    snapshot = load_snapshot(CONFIG)
    return {"commit": "9198531", "rules_version": snapshot.calc.rules_version,
            "files": {f: file_hash(f) for f in LOCKED_FILES}, "extensions": locked_extensions(),
            "cases": {name: run(snapshot, state) for name, state in matrix(snapshot).items()}}


if __name__ == "__main__":
    if sys.argv[1:] != ["capture"]:
        sys.exit("python -m tests.financial_matrix capture")
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(capture(), ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
