"""The "what changed" card: after a change of a value, the figures before and after, from the engine's own results.

Built from the results on record before they are replaced, and from the new ones: no number is calculated here except the
comparison of what the engine already returned.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from .stage import ConversationState, results_stale
from .templates import _VALUE_LABELS, _show, money


def snapshot_results(state: ConversationState) -> dict[str, Any]:
    """The results on record, before a change replaces them."""
    ok = _calculated(state)
    today = ok[0].result["today"]["net_display"] if ok else None
    return {"today_net": str(today) if today is not None else None,
            "options": {r.option_id: {"label": r.label, "net": str(r.result["alternative"]["net_display"]),
                                      "delta": str(r.result["delta_display"])} for r in ok},
            "order": _order(ok), "stale": results_stale(state), "ranked": state.ranked_count}


def _calculated(state: ConversationState) -> list[Any]:
    return [r for r in state.results if r.result.get("status") == "OK" and r.result.get("delta_display") is not None]


def _order(ok: list[Any]) -> list[str]:
    return [r.option_id for r in sorted(ok, key=lambda r: -Decimal(str(r.result["delta_display"])))]


def build_receipt(before: dict[str, Any], state: ConversationState, changes: list[dict[str, str]]) -> Optional[dict[str, Any]]:
    """None when there was nothing to compare with, or nothing changed. Only what changed is listed."""
    if not before["options"] and not state.removed_options:
        return None
    ok = _calculated(state)
    today_after = str(ok[0].result["today"]["net_display"]) if ok else None
    options = []
    for r in sorted(ok, key=lambda r: -Decimal(str(r.result["delta_display"]))):
        options.append({"option_id": r.option_id, "label": r.label,
                        "unusual": bool(r.result.get("unusual_copay") or r.result.get("unusual_result")),
                        "before": ({k: before["options"][r.option_id][k] for k in ("net", "delta")}
                                   if r.option_id in before["options"] else None),
                        "after": {"net": str(r.result["alternative"]["net_display"]),
                                  "delta": str(r.result["delta_display"])}})
    order = _order(ok)
    kept = [o for o in before["order"] if o in order]
    added = [{"option_id": o["option_id"], "label": o["label"]} for o in options if o["before"] is None]
    left = [{"option_id": i, "label": f["label"]} for i, f in before["options"].items() if i not in order
            and i not in {x.option_id for x in state.removed_options}]
    return {"kind": "receipt", "version": state.results_version, "changes": changes,
            "today_net": {"before": before["today_net"], "after": today_after}, "options": options,
            "removed": [x.model_dump() for x in state.removed_options], "added": added, "left": left,
            "order_changed": kept != [o for o in order if o in kept] or bool(added or left), "ranked": state.ranked_count}


def value_changes(values_before: dict[str, Any], values_after: dict[str, Any], arg_of: dict[str, str],
                  group: Optional[tuple[Optional[str], Optional[str]]] = None,
                  car: Optional[tuple[Optional[str], Optional[str]]] = None) -> list[dict[str, str]]:
    """Each value that changed: label, before and after, in display form."""
    rows: list[dict[str, str]] = []
    if group and group[0] != group[1] and group[1]:
        rows.append({"field": "group", "label": "קבוצת זכאות", "before": f"קבוצה {group[0]}" if group[0] else "",
                     "after": f"קבוצה {group[1]}"})
    for key, after in values_after.items():
        before = values_before.get(key)
        if after is None or before == after:
            continue
        arg = arg_of[key]
        rows.append({"field": arg, "label": _VALUE_LABELS[arg][0], "after": _show(arg, after),
                     "before": _show(arg, before) if before is not None else ""})
    if car and car[0] != car[1] and car[1]:
        rows.append({"field": "current_car", "label": "הרכב של היום", "before": car[0] or "", "after": car[1]})
    return rows


def _delta_words(delta: Any) -> str:
    d = Decimal(str(delta))
    return f"תוספת של {money(d)} בחודש" if d > 0 else f"{money(-d)} פחות בחודש" if d < 0 else "בלי שינוי בנטו"


def receipt_lines(receipt: dict[str, Any]) -> list[str]:
    """The chat text next to the card: what the change means, in at most three short lines."""
    lines = []
    today = receipt["today_net"]
    if today["before"] and today["after"] and today["before"] != today["after"]:
        lines.append(f"הנטו שלכם ברכב של היום: {money(Decimal(today['after']))} במקום {money(Decimal(today['before']))}.")
    if receipt["ranked"]:
        lines.append(f"בדקתי שוב את כל {receipt['ranked']} הרכבים שאתם זכאים להם.")
    if receipt["options"]:
        best = receipt["options"][0]
        head = "הנטו הגבוה ביותר" if len(receipt["options"]) > 1 else "הרכב שבדקתם"
        mark = " (סכום חריג)" if best.get("unusual") else ""
        lines.append(f"{head}: {best['label']}{mark}, {_delta_words(best['after']['delta'])}.")
    if len(receipt["options"]) > 1:
        lines.append("הסדר בין הרכבים השתנה." if receipt["order_changed"] else "הסדר בין הרכבים לא השתנה.")
    return lines
