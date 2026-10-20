"""מה השאלה הנוכחית מצפה לקבל — נבנה בקוד ממכונת המצבים, ונשלח עם כל תשובה.

הלקוח יכול להציג כפתורים (choices / quick_replies) ולשלוח בחירה מובנית ({"choice"} / {"choices"}).
בחירה נבדקת מול ה-expects הנוכחי ומיושמת ישירות בקוד — לא דרך הקורא ולא דרך מודל השפה.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Optional

from app.modules.rules.public import RulesSnapshot
from app.modules.vehicles.public import VehicleInfo, VehicleKey

from .llm.port import ToolCall
from .stage import MAX_OPTIONS, ConversationState, Stage, next_stage, question_fields
from .templates import DRIVE_HE, LINK_LABELS, call_to_action, money, placeholder_text
from .tools import available_options

VehicleGetter = Callable[[VehicleKey], Optional[VehicleInfo]]

# שאלה → הארגומנט ב-save_profile
NUMBER_ARGS = {
    "base": "base_salary", "commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
    "study_fund_percent": "study_fund_percent", "credit_points": "credit_points", "children": "children_under_23",
    "use_value": "current_use_value", "copay": "current_copay", "maintenance": "current_maintenance",
}
_QUICK = {"none": "אין", "unknown": "לא ידוע", "no_car": "אין רכב", "rank": "הכי משתלם"}
_NONE_ALLOWED = {"commissions", "bonus", "study_fund_percent", "children", "copay"}
_UNKNOWN_ALLOWED = {"use_value", "copay"}
_UNKNOWN_FLAGS = {"use_value": "current_use_value_unknown", "copay": "current_copay_unknown"}
OCCUPATION_YES = {"id": "yes", "label": "כן, זה התפקיד שלי"}
YES_NO = [{"id": "yes", "label": "כן"}, {"id": "no", "label": "לא"}]
CONFIRM_FIX = [{"id": "yes", "label": "לאשר"}, {"id": "no", "label": "לתקן"}]
CLARIFY = [{"id": "mine", "label": "זה הרכב שלי"}, {"id": "alternative", "label": "חלופה לבדיקה"}]
CLARIFY_ROLE = [{"id": "mine", "label": "הרכב שלי היום"}, {"id": "alternative", "label": "חלופה לבדוק"}]
INVALID = "הבחירה אינה מהאפשרויות שהוצעו"
FIELD_NEEDED = "יש לציין לאיזה שדה הבחירה"


class ChoiceInvalid(Exception):
    """בחירה שלא הוצעה בשאלה הנוכחית (או יותר מדי בחירות). ההודעה בעברית."""


def _quick(*ids: str) -> list[dict[str, str]]:
    return [{"id": i, "label": _QUICK[i]} for i in ids]


def _expects(field: Optional[str], kind: str, choices: Optional[list[dict[str, Any]]] = None,
             quick: Optional[list[dict[str, str]]] = None, most: Optional[int] = None) -> dict[str, Any]:
    one = {"field": field, "type": kind, "choices": choices or [], "quick_replies": quick or [], "max": most}
    return {**one, "fields": [one] if field else []}


def _pick_field(expects: dict[str, Any], ids: list[str], field: Optional[str]) -> dict[str, Any]:
    """השדה שהבחירה עונה עליו. בשאלה מקובצת: לפי "field", או השדה היחיד שמציע את המזהה."""
    items = [{k: v for k, v in f.items() if k != "fields"} for f in expects.get("fields") or []]
    if field:
        for item in items:
            if item["field"] == field:
                return item
        raise ChoiceInvalid(INVALID)
    if len(items) <= 1:
        return items[0] if items else expects
    offering = [f for f in items
                if any(i in {c["id"] for c in f["choices"] + f["quick_replies"]} for i in ids)
                or (f["type"] == "car_search" and any(i not in {q["id"] for q in f["quick_replies"]} for i in ids))]
    if len(offering) == 1:
        return offering[0]
    if len(offering) > 1:
        raise ChoiceInvalid(FIELD_NEEDED)
    raise ChoiceInvalid(INVALID)


FORM_FIELDS = ("commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children_under_23")
_FORM_LABELS = {"commissions": "עמלות", "bonus": "בונוס", "pension_percent": "פנסיה",
                "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי", "children_under_23": "ילדים"}


def validate_fields(snapshot: RulesSnapshot, values: dict[str, Any]) -> dict[str, Any]:
    """הטופס המובנה: לפחות שדה אחד, ורק שדות הטופס; כל ערך בטווח של extensions.yaml (אותם טווחים
    כמו save_profile). מחוץ לטווח — ChoiceInvalid (422) והמצב לא משתנה."""
    values = {k: v for k, v in values.items() if v is not None}
    if not values or any(k not in FORM_FIELDS for k in values):
        raise ChoiceInvalid(INVALID)
    ranges = snapshot.extensions.value_ranges
    for key, value in values.items():
        if key in ranges:
            low, high = ranges[key]
            if not Decimal(low) <= Decimal(value) <= Decimal(high):
                raise ChoiceInvalid(f"{_FORM_LABELS[key]}: הערך מחוץ לטווח ({low}–{high})")
    return values


def vehicle_id(key: VehicleKey) -> str:
    return f"tax:{key.tax_year}:{key.reg_year}:{key.vehicle_type}:{key.manufacturer_code}:{key.model_code}"


def parse_vehicle_id(value: str) -> Optional[VehicleKey]:
    parts = value.split(":")
    if len(parts) != 6 or parts[0] != "tax" or not (parts[1].isdigit() and parts[2].isdigit()):
        return None
    return VehicleKey(int(parts[1]), int(parts[2]), parts[3], parts[4], parts[5])


def tax_label(v: VehicleInfo) -> str:
    """"FABIA (2022) NJ32 AMBITION" — התיאור מבדיל בין גרסאות של אותו כינוי."""
    return v.label if v.model_description in ("", v.commercial_name) else f"{v.label} {v.model_description}"


ASK_REP = [{"id": "ask_rep", "label": "לשאול את הנציג"}, {"id": "skip", "label": "להמשיך בלי"}]
REP_SEND = [{"id": "send", "label": "לשלוח"}, {"id": "cancel", "label": "לבטל"}]
AMBIGUITY = [{"id": "no_car", "label": "אין לי רכב צמוד היום"}, {"id": "zero_copay", "label": "יש רכב, ההשתתפות 0"}]
# questions with a closed set of answers: the client shows buttons for these only. Every other
# question is answered by typing (free text first); the server still accepts a structured choice.
CLOSED = {"confirmation", "occupation_confirmation", "clarification", "car_choice", "drive", "rep_question",
          "ambiguity", "ask_rep"}


def build_expects(state: ConversationState, snapshot: RulesSnapshot) -> dict[str, Any]:
    expects = _build(state, snapshot)
    fields = expects.get("fields") or []
    only_group = [f["field"] for f in fields] == ["group"]
    for f in fields:
        f["closed"] = f["field"] in CLOSED or only_group
    if state.unanswered_question is not None and state.pending_rep_question is None:
        fields.append({"field": "ask_rep", "type": "choice", "choices": ASK_REP, "quick_replies": [],
                       "max": None, "closed": True})
        expects["fields"] = fields
    expects["closed"] = bool(fields) and fields[0].get("closed", False)
    cta, link = call_to_action(state, snapshot) if next_stage(state) != Stage.WAITING_FOR_REP else ("", None)
    expects["link"] = {"kind": link, "label": LINK_LABELS[link]} if link else None
    if link == "payslip_upload" and snapshot.extensions.payslip_upload_url:
        # a pilot's external page; empty here means the client uses the built-in in-chat uploader
        expects["link"]["url"] = snapshot.extensions.payslip_upload_url
    expects["placeholder"] = placeholder_text(state, snapshot)
    return expects


def _build(state: ConversationState, snapshot: RulesSnapshot) -> dict[str, Any]:
    stage = next_stage(state)
    if state.pending_rep_question is not None:
        return _expects("rep_question", "choice", REP_SEND)
    if state.pending_ambiguity is not None:
        return _expects("ambiguity", "choice", AMBIGUITY)
    if state.pending_confirmation is not None:
        pending = state.pending_confirmation
        unusual = pending.unusual
        if pending.scale:
            return _expects("confirmation", "choice", [
                {"id": "small", "label": money(pending.value)},
                {"id": "thousands", "label": money(pending.value * 1000)}])
        return _expects("confirmation", "choice", CONFIRM_FIX if unusual else YES_NO)
    if state.pending_occupation is not None:
        pending = state.pending_occupation
        return _expects("occupation_confirmation", "choice",
                        [{"id": n, "label": n} for n in pending.suggestions] + [OCCUPATION_YES])
    if state.pending_clarification is not None:
        return _expects("clarification", "choice", CLARIFY if stage == Stage.WAITING_FOR_REP else CLARIFY_ROLE)
    if stage == Stage.WAITING_FOR_REP:
        return _expects(None, "none")
    fields = question_fields(state)
    if "occupation" in fields:            # Q1: קבוצה או תפקיד — שניהם עונים
        i = fields.index("occupation")
        fields = fields[:i + 1] + ["group"] + fields[i + 1:]
    items = [{k: v for k, v in _field_expects(f, state, snapshot).items() if k != "fields"} for f in fields]
    if not items:
        return _expects(None, "none")
    return {**items[0], "fields": items}

def _field_expects(question: str, state: ConversationState, snapshot: RulesSnapshot) -> dict[str, Any]:
    if question in ("occupation", "opt_out_occupation"):
        return _expects(question, "choice", [{"id": n, "label": n} for n in snapshot.occupations])
    if question == "group":
        return _expects("group", "choice", [{"id": g, "label": f"קבוצה {g}"} for g in snapshot.calc.group_order])
    if question == "car":
        return _expects("car", "car_search", quick=_quick("no_car"))
    if question == "car_choice":
        return _expects("car_choice", "choice", [
            {"id": str(i), "label": c.label if c.catalog_code else      # a catalog car: its name only
             f"{c.label} {c.description} — שווי שימוש {money(c.use_value)}".replace("  ", " ")}
            for i, c in enumerate(state.current.choices, 1)])
    if question == "drive":
        return _expects("drive", "choice", [{"id": d, "label": he} for d, he in DRIVE_HE.items()])
    if question == "options":
        return _expects("options", "multi_choice", [
            {"id": o.id, "label": o.label, "group": o.group, "drive": o.drive.value if o.drive else None}
            for o in available_options(snapshot, state)], _quick("rank"), MAX_OPTIONS)
    if question in NUMBER_ARGS:
        quick = [q for q, allowed in (("none", _NONE_ALLOWED), ("unknown", _UNKNOWN_ALLOWED)) if question in allowed]
        return _expects(question, "number", quick=_quick(*quick))
    return _expects(None, "none")


def validate_choice(expects: dict[str, Any], ids: list[str], snapshot: RulesSnapshot,
                    get_vehicle: VehicleGetter, field: Optional[str] = None) -> tuple[str, str]:
    """בודק שהבחירה הוצעה. מחזיר (התווית בעברית — נשמרת כהודעת העובד, השדה שהיא עונה עליו)."""
    ids = [i.strip() for i in ids]
    if not ids:
        raise ChoiceInvalid(INVALID)
    expects = _pick_field(expects, ids, field)
    return _validate(expects, ids, snapshot, get_vehicle), expects["field"]


def _validate(expects: dict[str, Any], ids: list[str], snapshot: RulesSnapshot, get_vehicle: VehicleGetter) -> str:
    kind = expects["type"]
    if not ids or kind == "none" or (kind != "multi_choice" and len(ids) != 1):
        raise ChoiceInvalid(INVALID)
    if kind == "multi_choice" and len(set(ids)) > (expects["max"] or 1):
        raise ChoiceInvalid(f"אפשר לבחור עד {expects['max']} אפשרויות")
    offered = {c["id"]: c["label"] for c in expects["choices"] + expects["quick_replies"]}
    if kind == "car_search" and ids[0] not in offered:
        code = ids[0]
        car = next((c for c in snapshot.catalog if c.catalog_code == code), None)
        if car is not None:
            return car.label
        key = parse_vehicle_id(code)
        info = get_vehicle(key) if key is not None else None
        if info is None:
            raise ChoiceInvalid(INVALID)
        return tax_label(info)
    if any(i not in offered for i in ids):
        raise ChoiceInvalid(INVALID)
    if kind == "multi_choice" and len(ids) > 1 and any(i in {q["id"] for q in expects["quick_replies"]} for i in ids):
        raise ChoiceInvalid(INVALID)                    # "הכי משתלם" — לבד
    return ", ".join(offered[i] for i in dict.fromkeys(ids))


def choice_calls(field: str, ids: list[str], snapshot: RulesSnapshot) -> list[ToolCall]:
    """הבחירה (שכבר נבדקה) כקריאות לכלים — אותם כלים ואותן בדיקות כמו כל מקור אחר."""
    ids = list(dict.fromkeys(i.strip() for i in ids))
    first = ids[0]

    def call(name: str, **args: Any) -> list[ToolCall]:
        return [ToolCall(name, args, f"choice_{name}")]

    if field in ("occupation", "opt_out_occupation"):
        return call("save_profile", occupation=first)
    if field == "group":
        return call("save_profile", eligibility_group=first)
    if field == "drive":
        return call("save_profile", current_drive=first)
    if field == "car_choice":
        return call("match_current_car", choice_number=int(first))
    if field == "car":
        if first == "no_car":
            return call("match_current_car", no_car=True)
        car = next((c for c in snapshot.catalog if c.catalog_code == first), None)
        if car is not None:
            return call("match_current_car", model=car.catalog_code)
        return call("match_current_car", vehicle_key=first)
    if field == "options":
        return call("rank_options") if first == "rank" else call("calculate", options=ids)
    if field in NUMBER_ARGS:
        if first == "unknown":
            return call("save_profile", **{_UNKNOWN_FLAGS[field]: True})
        return call("save_profile", **{NUMBER_ARGS[field]: "0"})       # "none"
    return []
