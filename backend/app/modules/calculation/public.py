"""הממשק הציבורי של מודול החישוב. מודולים אחרים מייבאים רק מכאן."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .engine import ENGINE_VERSION, evaluate, evaluate_state
from .models import (
    FAMILY_GROUP,
    Bracket,
    Breakdown,
    CalcRules,
    CarState,
    CopayStatus,
    Drive,
    Employee,
    Line,
    OccupationRule,
    OptOutState,
    ScenarioResult,
    State,
    Status,
    TwoTier,
)


def rank_options(employee: Employee, current: State, candidates: list[State], rules: CalcRules,
                 occupation: Optional[OccupationRule] = None) -> list[ScenarioResult]:
    """מחשב את כל החלופות ומחזיר את הזכאיות ממוינות מההפרש הגבוה לנמוך."""
    results = [evaluate(employee, current, c, rules, occupation) for c in candidates]
    eligible = [r for r in results if r.eligible and r.delta_display is not None]
    return sorted(eligible, key=lambda r: r.delta_display or Decimal(0), reverse=True)


__all__ = [
    "ENGINE_VERSION", "FAMILY_GROUP", "Bracket", "Breakdown", "CalcRules", "CarState",
    "CopayStatus", "Drive", "Employee", "Line", "OccupationRule", "OptOutState",
    "ScenarioResult", "State", "Status", "TwoTier", "evaluate", "evaluate_state", "rank_options",
]
