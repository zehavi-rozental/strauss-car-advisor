"""מה השאלה הנוכחית מצפה לקבל — נבנה בקוד ממכונת המצבים, ונשלח עם כל תשובה.

הלקוח יכול להציג כפתורים (choices / quick_replies) ולשלוח בחירה מובנית ({"choice"} / {"choices"}).
בחירה נבדקת מול ה-expects הנוכחי ומיושמת ישירות בקוד — לא דרך הקורא ולא דרך מודל השפה.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Optional

from app.modules.rules.public import RulesSnapshot
from app.modules.vehicles.public import VehicleInfo, VehicleKey

from .free_text import looks_like_title
from .llm.port import ToolCall
from .stage import MAX_OPTIONS, ConversationState, Stage, next_stage, question_fields
from .templates import (DRIVE_HE, LINK_LABELS, call_to_action, catalog_copay_hint, copay_choices_offered, money,
                        placeholder_text)
from .tools import available_options

VehicleGetter = Callable[[VehicleKey], Optional[VehicleInfo]]

# שאלה → הארגומנט ב-save_profile
NUMBER_ARGS = {
    "base": "base_salary", "commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
    "study_fund_percent": "study_fund_percent", "credit_points": "credit_points", "children": "children_under_23",
    "use_value": "current_use_value", "copay": "current_copay", "maintenance": "current_maintenance",
}
_QUICK = {"none": "אין", "unknown": "לא ידוע", "no_car": "אין רכב", "rank": "הכי משתלם"}
# maintenance: "אין" → 0 (the employee receives no car-maintenance allowance), "לא ידוע" → defer / unknown
_NONE_ALLOWED = {"commissions", "bonus", "study_fund_percent", "children", "copay", "maintenance"}
_UNKNOWN_ALLOWED = {"use_value", "copay", "maintenance"}
_UNKNOWN_FLAGS = {"use_value": "current_use_value_unknown", "copay": "current_copay_unknown",
                  "maintenance": "current_maintenance_unknown"}
OCCUPATION_YES = {"id": "yes", "label": "כן, זה התפקיד שלי"}
YES_NO = [{"id": "yes", "label": "כן"}, {"id": "no", "label": "לא"}]
CONFIRM_FIX = [{"id": "yes", "label": "לאשר"}, {"id": "no", "label": "לתקן"}]
CLARIFY = [{"id": "mine", "label": "זה הרכב שלי"}, {"id": "alternative", "label": "חלופה לבדיקה"}]
CLARIFY_ROLE = [{"id": "mine", "label": "הרכב שלי היום"}, {"id": "alternative", "label": "חלופה לבדוק"}]
INVALID = "הבחירה אינה מהאפשרויות שהוצעו"
FIELD_NEEDED = "יש לציין לאיזה שדה הבחירה"


class ChoiceInvalid(Exception):
    """בחירה שלא הוצעה בשאלה הנוכחית (או יותר מדי בחירות). ההודעה בעברית."""


class FieldsInvalid(ChoiceInvalid):
    """The profile form: every field outside its range, by field name (the form shows each one next to its
    field). Nothing is saved: the form is all or nothing."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__(" ".join(errors.values()))


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


FORM_FIELDS = ("base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points",
               "children_under_23")      # base_salary: from a reviewed payslip (api ProfileFieldsIn)
_FORM_LABELS = {"base_salary": "שכר בסיס", "commissions": "עמלות", "bonus": "בונוס", "pension_percent": "פנסיה",
                "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי", "children_under_23": "ילדים"}


def validate_fields(snapshot: RulesSnapshot, values: dict[str, Any]) -> dict[str, Any]:
    """הטופס המובנה: לפחות שדה אחד, ורק שדות הטופס; כל ערך בטווח של extensions.yaml (אותם טווחים
    כמו save_profile). מחוץ לטווח — ChoiceInvalid (422) והמצב לא משתנה."""
    values = {k: v for k, v in values.items() if v is not None}
    if not values or any(k not in FORM_FIELDS for k in values):
        raise ChoiceInvalid(INVALID)
    ranges = snapshot.extensions.value_ranges
    errors = {key: f"{_FORM_LABELS[key]}: הערך מחוץ לטווח ({ranges[key][0]}–{ranges[key][1]})"
              for key, value in values.items()
              if key in ranges and not Decimal(ranges[key][0]) <= Decimal(value) <= Decimal(ranges[key][1])}
    if errors:
        raise FieldsInvalid(errors)
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
def COPAY_CHOICES(code: Optional[str]) -> list[dict[str, str]]:
    return [{"id": f"catalog:{code}", "label": "זה הסכום שלי"}, {"id": "other", "label": "הסכום שונה"},
            {"id": "rep", "label": "לברר מול נציג"}]


# no catalog amount for today's car (1/11): nothing is suggested; a known amount, or the representative
COPAY_CHOICES_NO_CATALOG = [{"id": "other", "label": "להזין סכום"}, {"id": "rep", "label": "לברר מול נציג"}]


CLOSED = {"confirmation", "occupation_confirmation", "clarification", "car_choice", "drive", "rep_question",
          "ambiguity", "ask_rep"}


def build_expects(state: ConversationState, snapshot: RulesSnapshot) -> dict[str, Any]:
    expects = _build(state, snapshot)
    fields = expects.get("fields") or []
    only_group = [f["field"] for f in fields] == ["group"]
    for f in fields:
        f["closed"] = f["field"] in CLOSED or only_group or (f["field"] == "copay" and f["type"] == "choice")
    if state.unanswered_question is not None and state.pending_rep_question is None:
        # question: what the inquiry card opens with (the employee's own words; "" when they are still to be said)
        fields.append({"field": "ask_rep", "type": "choice", "choices": ASK_REP, "quick_replies": [],
                       "max": None, "closed": True, "question": state.unanswered_question})
        expects["fields"] = fields
    offer = state.rep_copay_offer
    if offer is not None:
        # the representative's amount for today's copay (1/11): one button; the client asks to confirm, showing the
        # amount, the car and the amount it replaces
        c = state.current
        previous = str(c.copay) if c.copay is not None else None
        provisional = str(c.copay_provisional) if c.copay is None and c.copay_provisional is not None else None
        fields.append({"field": "rep_copay", "type": "choice", "quick_replies": [], "max": None, "closed": True,
                       "choices": [{"id": f"apply:{offer.question_id}", "label": "עדכון הסכום וחישוב מחדש"}],
                       "context": {"car": offer.car_label, "amount": str(offer.amount), "previous": previous,
                                   "provisional": provisional}})
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
        # "כן, זה התפקיד שלי" only for text that reads as a role; otherwise only the table's names (P0-B)
        yes = [OCCUPATION_YES] if looks_like_title(snapshot, pending.text) else []
        return _expects("occupation_confirmation", "choice",
                        [{"id": n, "label": n} for n in pending.suggestions] + yes)
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
        if question == "copay" and (hint := catalog_copay_hint(state, snapshot)) is not None:
            # today's car has a catalog amount (31/10): three choices only. The amount is the employee's once they
            # choose it; "הסכום שונה" opens an amount field in the client; "לברר מול נציג" opens an inquiry draft
            out = _expects("copay", "choice", COPAY_CHOICES(state.current.catalog_code))
            context = {"car": state.current.label, "catalog_amount": str(hint)}
            out["context"] = out["fields"][0]["context"] = context     # grouped questions read the top level
            return out
        if question == "copay" and copay_choices_offered(state, snapshot):
            out = _expects("copay", "choice", COPAY_CHOICES_NO_CATALOG)
            out["context"] = out["fields"][0]["context"] = {"car": state.current.label}
            return out
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
        if field == "copay" and first == "rep":
            # an inquiry about today's copay was sent: the amount is unknown until it is answered — no default,
            # and nothing that depends on it is calculated (31/10)
            return call("save_profile", current_copay_unknown=True)
        if field == "copay" and first == "other":
            return []                                    # the client opens an amount field; the amount is the answer
        if field == "copay" and first.startswith("catalog:"):
            car = next((c for c in snapshot.catalog if c.catalog_code == first.split(":", 1)[1]), None)
            if car is None or car.default_copay is None:
                return []
            return call("save_profile", current_copay=str(car.default_copay))   # the employee confirmed it
        if first == "unknown":
            return call("save_profile", **{_UNKNOWN_FLAGS[field]: True})
        return call("save_profile", **{NUMBER_ARGS[field]: "0"})       # "none"
    return []
