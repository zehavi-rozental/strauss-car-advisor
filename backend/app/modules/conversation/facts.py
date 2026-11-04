"""What the system knows about the employee, as one list derived from the state: the single view behind the context strip and
"הנתונים שלי". Nothing here is stored and nothing is calculated: each item says its value, where it came from, whether it is
used in the calculation, and how it can be changed.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from app.modules.rules.public import RulesSnapshot

from .stage import PROFILE_ARGS, ConversationState, Stage, current_unknown, next_stage, results_stale
from .templates import _VALUE_LABELS, _show

SOURCE_LABELS = {"chat": "מהשיחה", "form": "הקלדתם", "payslip": "מהתלוש, באישורכם", "representative": "מהנציג",
                 "catalog": "מהקטלוג", "tax": "ממאגר רשות המסים", "table": "מטבלת העיסוקים", "engine": "חישוב המנוע"}
_COPAY_SOURCES = {"rep_confirmed": ("representative", "known"), "catalog_confirmed": ("catalog", "known"),
                  "carried": ("chat", "assumption")}
_STATUS_LABELS = {"missing": "חסר", "unknown": "לא ידוע", "estimated": "הערכה", "provisional": "זמני",
                  "held": "ממתין לאישור", "assumption": "הנחה", "calculated": "מחושב"}
_PAY = ("base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points",
        "children_under_23")
_KINDS = {"base_salary": "money", "commissions": "money", "bonus": "money", "pension_percent": "percent",
          "study_fund_percent": "percent", "credit_points": "count", "children_under_23": "integer",
          "current_copay": "money", "current_use_value": "money", "current_maintenance": "money"}


def _item(key: str, label: str, section: str, value: Optional[Any], display: str, source: Optional[str], status: str,
          edit: Optional[str] = None, used: bool = True, note: str = "", **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "section": section, "value": None if value is None else str(value),
            "display": display, "source": source, "source_label": SOURCE_LABELS.get(source or "", ""),
            "status": status, "status_label": _STATUS_LABELS.get(status, ""), "edit": edit, "used_in_calc": used,
            "note": note, **extra}


def _number(snapshot: RulesSnapshot, state: ConversationState, arg: str, value: Any, section: str, source: Optional[str],
            status: str = "known", edit: bool = True, note: str = "") -> dict[str, Any]:
    bounds = snapshot.extensions.value_ranges.get(arg)
    held = state.pending_confirmation
    if held is not None and (held.field == arg or arg in held.also):
        status = "held"
    display = _show(arg, value) if value is not None else ""
    return _item(arg, _VALUE_LABELS[arg][0], section, value, display, source, status,
                 edit=_KINDS[arg] if edit else None, note=note,
                 min=str(bounds[0]) if bounds else None, max=str(bounds[1]) if bounds else None)


def _strip_keys(state: ConversationState, known: set[str]) -> list[str]:
    """At most four items, the ones this stage is about. The rest stays one tap away, in "הנתונים שלי"."""
    stage = next_stage(state)
    if stage in (Stage.PRESENTED, Stage.CALCULATING, Stage.WAITING_FOR_REP) or state.results:
        wanted = ["base_salary", "pension_percent", "credit_points", "current_copay"]
    elif stage == Stage.CHOOSE_OPTIONS:
        wanted = ["group", "current_car", "current_copay", "base_salary"]
    elif stage == Stage.COLLECT_CURRENT_CAR:
        wanted = ["group", "current_car", "current_copay", "base_salary"]
    else:
        wanted = ["group", "occupation", "base_salary", "pension_percent", "credit_points"]
    return [k for k in wanted if k in known][:4]


def facts_view(state: ConversationState, snapshot: RulesSnapshot) -> dict[str, Any]:
    p, c = state.profile, state.current
    items: list[dict[str, Any]] = []
    # eligibility: the occupations table decides the group once a role in it is known
    if p.occupation or p.occupation_text:
        items.append(_item("occupation", "תפקיד", "eligibility", p.occupation or p.occupation_text,
                           p.occupation or p.occupation_text or "", "chat", "known", edit="occupation", used=bool(p.occupation),
                           choices=list(snapshot.occupations)))
    if p.group:
        owned = p.occupation is not None
        items.append(_item("group", "קבוצת זכאות", "eligibility", p.group, f"קבוצה {p.group}",
                           "table" if owned else "chat", "known", edit=None if owned else "group",
                           note="נקבעת לפי התפקיד. אפשר לעדכן את התפקיד, או לבקש מהנציג לבדוק." if owned else "",
                           choices=list(snapshot.calc.group_order)))
    else:
        items.append(_item("group", "קבוצת זכאות", "eligibility", None, "", None, "missing", edit="group",
                           choices=list(snapshot.calc.group_order)))
    # pay: every field, said or still missing
    for arg in _PAY:
        attr = PROFILE_ARGS[arg]
        value = getattr(p, attr)
        if value is None:
            status = "unknown" if attr in p.unknown_fields else "missing"
            items.append(_number(snapshot, state, arg, None, "pay", None, status))
        else:
            status = "estimated" if arg in p.approximate_fields else "known"
            items.append(_number(snapshot, state, arg, value, "pay", state.field_sources.get(arg, "chat"), status))
    # today's car
    if c.source == "none":
        items.append(_item("current_car", "הרכב של היום", "car", "none", "אין רכב צמוד", "chat", "known", edit="car"))
        if c.maintenance is not None:
            items.append(_number(snapshot, state, "current_maintenance", c.maintenance, "car",
                                 state.field_sources.get("current_maintenance", "chat")))
        else:
            items.append(_number(snapshot, state, "current_maintenance", None, "car", None,
                                 "unknown" if c.maintenance_unknown else "missing"))
    elif c.source or c.label or c.choices or c.has_car:
        items.append(_item("current_car", "הרכב של היום", "car", c.catalog_code or c.label, c.label or "", c.source,
                           "known" if c.source else "missing", edit="car"))
        if c.use_value is not None or c.use_value_unknown or c.source == "stated":
            lookup = c.source in ("catalog", "tax")
            source = c.source if lookup else state.field_sources.get("current_use_value", "chat")
            status = "unknown" if c.use_value_unknown and c.use_value is None else "known"
            items.append(_number(snapshot, state, "current_use_value", c.use_value, "car", source, status,
                                 edit=not lookup))
        if c.copay is not None:
            source, status = _COPAY_SOURCES.get(c.copay_source or "", (state.field_sources.get("current_copay", "chat"), "known"))
            items.append(_number(snapshot, state, "current_copay", c.copay, "car", source, status,
                                 note="נלקחה מהרכב הקודם. אם היא שונה ברכב הזה, עדכנו אותה." if c.copay_source == "carried" else ""))
        elif c.copay_provisional is not None:
            items.append(_number(snapshot, state, "current_copay", c.copay_provisional, "car", "catalog", "provisional",
                                 note="סכום הקטלוג, עד לתשובת הנציג."))
        else:
            items.append(_number(snapshot, state, "current_copay", None, "car", None,
                                 "unknown" if "copay" in current_unknown(c) else "missing"))
    else:
        items.append(_item("current_car", "הרכב של היום", "car", None, "", None, "missing", edit="car"))
    # the comparison and what the engine calculated from all of the above
    labels = {r.option_id: r.label for r in state.results}
    if state.selected:
        names = [labels.get(o, o) for o in state.selected]
        items.append(_item("selected", "בהשוואה", "results", ",".join(state.selected), ", ".join(names), None, "known",
                           edit="selection", used=True))
    ok = [r for r in state.results if r.result.get("status") == "OK" and r.result.get("today")]
    stale = results_stale(state)
    if ok and not stale:
        today = Decimal(str(ok[0].result["today"]["net_display"]))
        items.append(_item("today_net", "הנטו שלכם היום", "results", today, f"{today:,.0f} ₪", "engine", "calculated"))
    known = {i["key"] for i in items if i["value"] is not None}
    missing = [i["label"] for i in items if i["status"] in ("missing", "unknown")]
    assumptions = list(dict.fromkeys(a for r in ok for a in r.result.get("assumptions") or []))
    if "mock" in snapshot.calc.rules_version:
        assumptions.append("כללי המס, הקטלוג והזכאות כאן הם נתוני הדגמה, ולא אומתו מול מקור רשמי.")
    return {"version": state.data_version, "results_version": state.results_version, "ranked": bool(state.ranked_count),
            "items": items,
            "strip": _strip_keys(state, known), "missing": missing, "assumptions": assumptions}
