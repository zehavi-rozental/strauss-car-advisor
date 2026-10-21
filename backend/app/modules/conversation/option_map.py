"""STRAUSS DRIVE 360°: every option the employee may choose, each calculated by the engine.

Code, not the LLM, decides everything here: which options are eligible (tools.classify_options), each net
(calculation.evaluate, the same call as a regular comparison), each option's status, and which option leads.
The map never chooses for the employee and never changes the selection; it ranks only the options the engine
calculated, by the estimated monthly net alone.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from app.modules.calculation.public import Employee, OccupationRule, ScenarioResult, State, evaluate
from app.modules.calculation.public import Status as CalcStatus
from app.modules.rules.public import RulesSnapshot

from .stage import ConversationState, MapEntry, OptionMap
from .templates import (OPT_OUT_DENIED, approximate_assumptions, confirmed_assumption, net_change,
                        rep_amount_assumption, to_plural, why_sentence)
from .tools import Option, classify_options, engine_inputs, option_state


def dump_result(result: Any) -> dict[str, Any]:
    """The stored result: the engine's fields plus `why`, one plain sentence about the main cause
    (built from the drivers, so the client never builds text from numbers)."""
    out = result.model_dump(mode="json")
    out["why"] = why_sentence(out.get("drivers") or [])
    if out.get("reason"):
        out["reason"] = to_plural(out["reason"])     # the engine keeps the reference wording
    return out


def with_assumptions(result: Any, state: ConversationState, option_id: str, label: str) -> Any:
    """הנחות שהמנוע לא יודע עליהן: ערכים משוערים, והשתתפות שהנציג אישר או קבע."""
    extra = approximate_assumptions(state)
    confirmed = state.confirmed_copays.get(option_id)
    if confirmed is not None:
        extra.append(rep_amount_assumption(label, confirmed.value, confirmed.effective_date) if confirmed.changed
                     else confirmed_assumption(label, confirmed.effective_date))
    return result.model_copy(update={"assumptions": result.assumptions + extra}) if extra else result


def evaluate_option(snapshot: RulesSnapshot, state: ConversationState, option: Option, employee: Employee,
                    current: State, occupation: Optional[OccupationRule]) -> tuple[State, ScenarioResult]:
    """One option through the engine, exactly as a comparison calculates it: the representative's copay when
    confirmed, otherwise the catalog policy; the representative's opt-out answer when it was no."""
    confirmed = state.confirmed_copays.get(option.id)
    alternative = option_state(option, snapshot.extensions.copay_policy, confirmed.value if confirmed else None)
    result = with_assumptions(evaluate(employee, current, alternative, snapshot.calc, occupation), state,
                              option.id, option.label)
    if option.car is None and state.profile.rep_opt_out_allowed is False:
        result = result.model_copy(update={"reason": OPT_OUT_DENIED})   # לפי תשובת הנציג
    return alternative, result


def _status(result: ScenarioResult) -> str:
    if result.status == CalcStatus.OK and result.delta_display is not None:
        return "catalog_copay" if result.copay_pending else "full"
    return "not_eligible" if result.status == CalcStatus.NOT_ELIGIBLE else "incomplete"


def build_option_map(snapshot: RulesSnapshot, state: ConversationState) -> OptionMap:
    """Every eligible option, calculated. Raises tools.MissingInputs while a value the engine needs is unknown
    (the map is then not built: nothing is calculated on a guessed value)."""
    employee, current, occupation = engine_inputs(snapshot, state)
    options, excluded = classify_options(snapshot, state)
    entries = []
    for option in options:
        _, result = evaluate_option(snapshot, state, option, employee, current, occupation)
        entries.append(MapEntry(option_id=option.id, label=option.label, group=option.group,
                                drive=option.drive.value if option.drive else None, status=_status(result),
                                result=dump_result(result)))
    return OptionMap(entries=entries, excluded=excluded, fresh=True)


def calculated(option_map: OptionMap) -> list[MapEntry]:
    """The options the engine calculated (with the final or the catalog copay): the only ones ranked."""
    return [e for e in option_map.entries if e.status in ("full", "catalog_copay")]


def leaders(option_map: OptionMap) -> list[MapEntry]:
    """The calculated options with the highest estimated monthly net. Several: a tie, never an arbitrary
    winner. None while the map is out of date or nothing was calculated."""
    ranked = calculated(option_map)
    if not option_map.fresh or not ranked:
        return []
    best = max(Decimal(str(e.result["delta_display"])) for e in ranked)
    return [e for e in ranked if Decimal(str(e.result["delta_display"])) == best]


def map_json(state: ConversationState) -> Optional[dict[str, Any]]:
    """The map for the API: entries in net order (calculated first, then waiting, then not eligible), the
    excluded catalog cars, the counts, the leading option(s) and whether it is out of date."""
    m = state.option_map
    if m is None:
        return None
    ranked = sorted(calculated(m), key=lambda e: Decimal(str(e.result["delta_display"])), reverse=True)
    waiting = [e for e in m.entries if e.status == "incomplete"]
    not_eligible = [e for e in m.entries if e.status == "not_eligible"]
    return {
        "entries": [e.model_dump(mode="json") for e in [*ranked, *waiting, *not_eligible]],
        "excluded": [x.model_dump(mode="json") for x in m.excluded],
        "counts": {"eligible": len(m.entries) - len(not_eligible), "calculated": len(ranked),
                   "waiting": len(waiting), "catalog_copay": len([e for e in ranked if e.status == "catalog_copay"])},
        "leaders": [e.option_id for e in leaders(m)],
        "stale": not m.fresh,
        "group": state.profile.group,
    }


NET_ONLY = "הדירוג מבוסס על הנטו החודשי המשוער בלבד. התאמת הרכב לצרכים שלכם עשויה להיות שונה."


def map_reply(state: ConversationState) -> str:
    """The chat line when the map is shown: what was mapped, what leads by estimated net (or a tie, or
    nothing to rank), what waits, and that the ranking is by net alone. Numbers only from the engine."""
    m = state.option_map
    if m is None:
        return ""
    data = map_json(state)
    counts = data["counts"]
    lines = [f"מיפיתי את כל {counts['eligible']} החלופות שאתם זכאים להן, וחישבתי {counts['calculated']} מהן."]
    top = leaders(m)
    if len(top) == 1:
        lines.append(f"הנטו החודשי הגבוה ביותר: {top[0].label}. {net_change(top[0].result['delta_display'])}.")
    elif top:
        names = " ו-".join(e.label for e in top)
        lines.append(f"שוויון בנטו המשוער בין {names}. {net_change(top[0].result['delta_display'])}.")
    else:
        lines.append("אין עדיין חלופה שחושבה במלואה, ולכן אין דירוג.")
    if counts["waiting"]:
        lines.append(f"{counts['waiting']} חלופות ממתינות למידע ולא נכללו בדירוג.")
    if counts["catalog_copay"]:
        lines.append("ברכבים החדשים ההשתתפות לפי הקטלוג, עד לאישור נציג התחבורה.")
    if top:
        lines.append(NET_ONLY)
    return "\n".join(lines)
