"""הכלים של הסוכן. כל כלי מקבל ארגומנטים מאומתים, מעדכן את מצב השיחה ומחזיר עובדות.

הכלים לא קובעים את השלב (stage.next_stage עושה את זה) ולא כותבים למסד.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Any, Callable, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from app.modules.calculation.public import (
    FAMILY_GROUP,
    CarState,
    CopayStatus,
    Drive,
    Employee,
    OccupationRule,
    OptOutState,
    State,
)
from app.modules.calculation.public import rank_options as engine_rank_options
from app.modules.rules.public import (
    CatalogCar,
    RulesSnapshot,
    car_state,
    find_catalog_car,
    occupation_rule,
)
from app.modules.vehicles.public import CurrentCarMatch

from .llm.port import ToolSpec
from .parsing import match_drive, match_group, match_occupation, parse_decimal
from .stage import (
    MAX_OPTIONS,
    OPT_OUT_ID,
    CarChoice,
    ConversationState,
    CurrentCar,
    missing_current_car,
    missing_profile,
)

OPT_OUT_WORDS = ("ויתור", "לוותר", "opt out", "opt_out", "בלי רכב", "ללא רכב")


# חיפוש במאגר רשות המסים: (דגם, שנת רישום) → התאמה. כל קריאה פותחת סשן קריאה קצר משלה,
# כך שאין טרנזקציה פתוחה בזמן שהסוכן מחכה למודל השפה.
VehicleLookup = Callable[[str, Optional[int]], CurrentCarMatch]


@dataclass
class ToolContext:
    snapshot: RulesSnapshot
    find_vehicle: VehicleLookup
    state: ConversationState
    # מה התור מבקש מהנציג / מבטל — נפתח ונכתב בשלב הכתיבה של התור, באותה טרנזקציה
    question_requests: list[Any] = field(default_factory=list)
    cancel_question_ids: list[str] = field(default_factory=list)


class ToolError(Exception):
    pass


# ---------- ארגומנטים ----------

def _decimal(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    d = parse_decimal(v)
    if d is None:
        raise ValueError("לא מספר")
    return d


Num = Annotated[Optional[Decimal], BeforeValidator(_decimal)]


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SaveProfileArgs(_Args):
    occupation: Optional[str] = None
    eligibility_group: Optional[str] = None
    base_salary: Num = Field(None, gt=0, le=1_000_000)
    commissions: Num = Field(None, ge=0, le=1_000_000)
    bonus: Num = Field(None, ge=0, le=1_000_000)
    pension_percent: Num = Field(None, ge=0, le=30)
    study_fund_percent: Num = Field(None, ge=0, le=20)
    credit_points: Num = Field(None, ge=0, le=30)
    children_under_23: Optional[int] = Field(None, ge=0, le=20)
    current_copay: Num = Field(None, ge=0, le=50_000)
    current_copay_unknown: bool = False
    current_use_value: Num = Field(None, gt=0, le=50_000)
    current_use_value_unknown: bool = False
    current_drive: Optional[str] = None
    current_maintenance: Num = Field(None, ge=0, le=50_000)


class MatchCarArgs(_Args):
    model: Optional[str] = None
    registration_year: Optional[int] = Field(None, ge=1980, le=2049)
    choice_number: Optional[int] = Field(None, ge=1, le=5)
    no_car: bool = False
    reset: bool = False          # "זה לא הרכב שלי" בלי דגם: מוחקים את ההתאמה ושואלים שוב


class CalculateArgs(_Args):
    options: list[str] = Field(min_length=1)


class NoArgs(_Args):
    pass


# ---------- save_profile ----------

def save_profile(ctx: ToolContext, args: SaveProfileArgs) -> dict[str, Any]:
    p, c, snap = ctx.state.profile, ctx.state.current, ctx.snapshot
    saved: list[str] = []
    ignored: list[dict[str, str]] = []

    if args.occupation:
        name = match_occupation(snap.occupations.keys(), args.occupation)
        if name:
            p.occupation, p.occupation_text, p.occupation_unknown = name, None, False
            p.group = snap.occupations[name].group
            saved.append("occupation")
        else:
            p.occupation, p.occupation_text, p.occupation_unknown = None, args.occupation.strip()[:80], True
            p.group = None
            saved.append("occupation_unknown")

    if args.eligibility_group:
        group = match_group(snap.calc.group_order, args.eligibility_group)
        if p.occupation:
            ignored.append({"field": "eligibility_group", "reason": "הקבוצה נקבעת לפי טבלת העיסוקים"})
        elif group is None:
            ignored.append({"field": "eligibility_group", "reason": "קבוצה לא מוכרת"})
        else:
            p.group, p.occupation_unknown = group, True
            saved.append("eligibility_group")

    for arg, attr in (("base_salary", "base"), ("commissions", "commissions"), ("bonus", "bonus"),
                      ("pension_percent", "pension_percent"), ("study_fund_percent", "study_fund_percent"),
                      ("credit_points", "credit_points"), ("children_under_23", "children")):
        value = getattr(args, arg)
        if value is not None:
            setattr(p, attr, value)
            saved.append(arg)

    if args.current_copay is not None:
        c.copay, c.copay_unknown = args.current_copay, False
        saved.append("current_copay")
    elif args.current_copay_unknown and c.copay is None:
        c.copay_unknown = True
        saved.append("current_copay_unknown")

    if args.current_use_value is not None or args.current_use_value_unknown:
        if c.source in ("catalog", "tax"):
            ignored.append({"field": "current_use_value", "reason": "שווי השימוש נלקח מהמאגר"})
        elif c.query is None and c.source is None:
            ignored.append({"field": "current_use_value", "reason": "עוד לא ידוע מה הרכב"})
        elif args.current_use_value is not None:
            c.source, c.label = "stated", c.label or c.query
            c.use_value, c.use_value_unknown = args.current_use_value, False
            saved.append("current_use_value")
        elif c.use_value is None:
            c.source, c.label, c.use_value_unknown = "stated", c.label or c.query, True
            saved.append("current_use_value_unknown")

    if args.current_drive:
        drive = match_drive(args.current_drive) or _drive_enum(args.current_drive)
        if drive is None:
            ignored.append({"field": "current_drive", "reason": "סוג הנעה לא מוכר"})
        else:
            c.drive = drive
            saved.append("current_drive")

    if args.current_maintenance is not None:
        if c.source == "none":
            c.maintenance = args.current_maintenance
            saved.append("current_maintenance")
        else:
            ignored.append({"field": "current_maintenance", "reason": "רלוונטי רק למי שאין לו רכב היום"})

    _invalidate_results(ctx.state)
    return {"saved": saved, "ignored": ignored,
            "missing": missing_profile(p) + missing_current_car(c)}


def _drive_enum(text: str) -> Optional[Drive]:
    try:
        return Drive(text.strip().upper())
    except ValueError:
        return None


def _invalidate_results(state: ConversationState) -> None:
    """נתון השתנה → התוצאות הקודמות כבר לא תקפות; יחושבו מחדש לאותן חלופות."""
    state.calculated_for = []


# ---------- match_current_car ----------

def match_current_car(ctx: ToolContext, args: MatchCarArgs) -> dict[str, Any]:
    old = ctx.state.current
    keep = dict(copay=old.copay, copay_unknown=old.copay_unknown)

    if args.reset:
        ctx.state.current = CurrentCar(**keep)
        _invalidate_results(ctx.state)
        return {"status": "RESET", "next": "ask the employee for the exact model"}

    if args.no_car:
        ctx.state.current = CurrentCar(source="none", label="ללא רכב")
        _invalidate_results(ctx.state)
        return {"status": "NO_CAR"}

    if args.choice_number is not None and old.choices and not args.model:
        if args.choice_number > len(old.choices):
            raise ToolError("מספר בחירה לא קיים")
        ch = old.choices[args.choice_number - 1]
        ctx.state.current = CurrentCar(query=old.query, reg_year=ch.reg_year, source="tax", label=ch.label,
                                       drive=ch.drive, use_value=ch.use_value, **keep)
        _invalidate_results(ctx.state)
        return {"status": "FOUND", "source": "tax", "label": ch.label, "use_value": str(ch.use_value)}

    if not args.model:
        raise ToolError("חסר דגם")
    model = args.model.strip()[:80]
    year = args.registration_year

    cars = find_catalog_car(ctx.snapshot, model)
    if len(cars) == 1 and (year is None or year == cars[0].reg_year):
        car = cars[0]
        ctx.state.current = CurrentCar(
            query=model, reg_year=car.reg_year, source="catalog", label=car.label,
            catalog_code=car.catalog_code, group=car.group, drive=car.drive, use_value=car.use_value,
            monthly_cost=car.monthly_cost, **keep)
        _invalidate_results(ctx.state)
        return {"status": "FOUND", "source": "catalog", "label": car.label, "group": car.group,
                "use_value": str(car.use_value)}

    match = ctx.find_vehicle(model, year)
    if match.status == "FOUND" and match.vehicle is not None:
        v = match.vehicle
        ctx.state.current = CurrentCar(query=model, reg_year=v.key.reg_year, source="tax", label=v.label,
                                       drive=Drive(v.drive), use_value=v.use_value, **keep)
        result = {"status": "FOUND", "source": "tax", "label": v.label, "use_value": str(v.use_value)}
    elif match.status == "CHOOSE":
        choices = [CarChoice(label=v.label, description=v.model_description, drive=Drive(v.drive),
                             use_value=v.use_value, reg_year=v.key.reg_year)
                   for v in match.choices]
        ctx.state.current = CurrentCar(query=model, reg_year=year, choices=choices, **keep)
        result = {"status": "CHOOSE", "choices": [
            {"number": i + 1, "label": ch.label, "model_code": ch.description,
             "use_value": str(ch.use_value)} for i, ch in enumerate(choices)]}
    else:
        ctx.state.current = CurrentCar(query=model, reg_year=year, label=model, **keep)
        result = {"status": "NOT_FOUND", "next": "ask the employee for the use value on their payslip"}
    _invalidate_results(ctx.state)
    return result


# ---------- list_options / calculate / rank_options ----------

@dataclass(frozen=True)
class Option:
    id: str
    label: str
    group: Optional[str]
    drive: Optional[Drive]
    car: Optional[CatalogCar] = None


def available_options(snapshot: RulesSnapshot, state: ConversationState) -> list[Option]:
    """רכבים מהקבוצה של העובד ומטה, רכבי המסלול המשפחתי ל-4+ ילדים, וויתור כשהעיסוק מאפשר."""
    p = state.profile
    order = snapshot.calc.group_order
    rank = order.index(p.group) if p.group in order else -1
    family = (p.children or 0) >= snapshot.calc.family_min_children
    out: list[Option] = []
    for car in snapshot.catalog:
        if car.catalog_code == state.current.catalog_code:
            continue
        own_or_lower = car.group in order and order.index(car.group) <= rank
        if own_or_lower or (car.group == FAMILY_GROUP and family):
            out.append(Option(id=car.catalog_code, label=car.label, group=car.group, drive=car.drive, car=car))
    occ = snapshot.occupations.get(p.occupation or "")
    if state.current.source != "none" and (occ is None or (occ.opt_out_allowed and not occ.car_mandatory)):
        out.append(Option(id=OPT_OUT_ID, label=OptOutState().label, group=None, drive=None))
    return out


def resolve_option(snapshot: RulesSnapshot, options: list[Option], text: str) -> Optional[Option]:
    t = text.strip()
    by_id = {o.id.upper(): o for o in options}
    if t.upper() in by_id:
        return by_id[t.upper()]
    if any(w in t.lower() for w in OPT_OUT_WORDS):
        return by_id.get(OPT_OUT_ID)
    cars = find_catalog_car(snapshot, t)
    for car in cars:
        if car.catalog_code in by_id:
            return by_id[car.catalog_code]
    return None


def list_options(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    return {"options": [{"id": o.id, "label": o.label, "group": o.group,
                         "drive": o.drive.value if o.drive else None}
                        for o in available_options(ctx.snapshot, ctx.state)]}


def calculate(ctx: ToolContext, args: CalculateArgs) -> dict[str, Any]:
    options = available_options(ctx.snapshot, ctx.state)
    chosen: list[str] = []
    unknown: list[str] = []
    for text in args.options:
        opt = resolve_option(ctx.snapshot, options, text)
        if opt is None:
            unknown.append(text[:40])
        elif opt.id not in chosen:
            chosen.append(opt.id)
    if not chosen:
        raise ToolError("אף חלופה לא זוהתה")
    ctx.state.selected = chosen[:MAX_OPTIONS]
    _invalidate_results(ctx.state)
    return {"selected": ctx.state.selected, "not_recognized": unknown,
            "note": "the system calculates now; do not compute anything yourself"}


def rank_options(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    options = available_options(ctx.snapshot, ctx.state)
    employee, current, occupation = engine_inputs(ctx.snapshot, ctx.state)
    states = {o.id: option_state(o) for o in options}
    ranked = engine_rank_options(employee, current, list(states.values()), ctx.snapshot.calc, occupation)
    by_label = {s.label: oid for oid, s in states.items()}
    top = [by_label[r.alternative.label] for r in ranked if r.alternative is not None][:MAX_OPTIONS]
    if not top:
        raise ToolError("אין חלופות זכאיות לדירוג")
    ctx.state.selected = top
    _invalidate_results(ctx.state)
    return {"selected": top, "note": "the system calculates the top options now"}


# ---------- קלט למנוע ----------

def engine_inputs(snapshot: RulesSnapshot, state: ConversationState
                  ) -> tuple[Employee, State, Optional[OccupationRule]]:
    p, c = state.profile, state.current
    employee = Employee(
        group=p.group, base=p.base, commissions=p.commissions or Decimal(0), bonus=p.bonus or Decimal(0),
        pension_rate=None if p.pension_percent is None else p.pension_percent / 100,
        study_fund_rate=(p.study_fund_percent or Decimal(0)) / 100,
        credit_points=p.credit_points, eligible_children=p.children or 0,
    )
    if c.source == "none":
        current: State = OptOutState(label="ללא רכב", maintenance=c.maintenance)
    elif c.source == "catalog":
        car = next(x for x in snapshot.catalog if x.catalog_code == c.catalog_code)
        current = car_state(car, copay=c.copay, copay_status=CopayStatus.EMPLOYEE_STATED)
    else:
        if c.drive is None:   # המכונה לא מתקדמת בלי הנעה; אין ברירת מחדל
            raise ToolError("חסר סוג ההנעה של הרכב הנוכחי")
        current = CarState(label=c.label or c.query or "הרכב שלך", group=None, drive=c.drive,
                           use_value=c.use_value, copay=c.copay, copay_status=CopayStatus.EMPLOYEE_STATED)
    occupation = occupation_rule(snapshot.occupations.get(p.occupation or ""))
    return employee, current, occupation


def option_state(option: Option, copay_policy: str = "CATALOG_DEFAULT_CONFIRM_ASYNC") -> State:
    """חלופה כמצב למנוע. השתתפות לפי הקטלוג: ממתינה לאישור נציג — אלא אם המדיניות היא CATALOG_DEFAULT,
    שבה הקטלוג הוא הסכום הסופי."""
    if option.car is None:
        return OptOutState()
    if copay_policy == "CATALOG_DEFAULT":
        return car_state(option.car, copay=option.car.default_copay, copay_status=CopayStatus.CONFIRMED)
    return car_state(option.car)


# ---------- הגדרות לכלים (למודל השפה) ----------

def _obj(props: dict[str, Any], required: Optional[list[str]] = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


NUM = {"type": "number"}

TOOL_SPECS: dict[str, ToolSpec] = {
    "save_profile": ToolSpec(
        "save_profile",
        "Record what the employee said about their job, salary and current car costs. "
        "Only pass fields the employee actually stated. Percentages as numbers (6 = 6%). "
        "A stated 'none' for commissions, bonus or study fund is 0.",
        _obj({
            "occupation": {"type": "string", "description": "job title as the employee said it"},
            "eligibility_group": {"type": "string", "description": "car eligibility group letter, only if "
                                  "the occupation is not in the table"},
            "base_salary": NUM, "commissions": NUM, "bonus": NUM,
            "pension_percent": NUM, "study_fund_percent": NUM, "credit_points": NUM,
            "children_under_23": {"type": "integer"},
            "current_copay": {"type": "number", "description": "monthly copay the employee pays today"},
            "current_copay_unknown": {"type": "boolean", "description": "employee does not know their copay"},
            "current_use_value": {"type": "number", "description": "use value from the payslip"},
            "current_use_value_unknown": {"type": "boolean"},
            "current_drive": {"type": "string", "description": "PETROL, HYBRID, PLUG_IN or ELECTRIC"},
            "current_maintenance": {"type": "number", "description": "car maintenance on the payslip, "
                                    "only for employees without a car today"},
        })),
    "match_current_car": ToolSpec(
        "match_current_car",
        "Identify the employee's current company car. Pass the model name in Latin letters as on the "
        "tax-authority list (e.g. 'COROLLA CROSS'), the registration year if stated, a choice_number when "
        "the employee picks from a list, or no_car=true if they have no car today. If the employee says the "
        "identified car is wrong without naming another model, pass reset=true.",
        _obj({"model": {"type": "string"}, "registration_year": {"type": "integer"},
              "choice_number": {"type": "integer"}, "no_car": {"type": "boolean"},
              "reset": {"type": "boolean"}})),
    "list_options": ToolSpec("list_options", "List the alternatives the employee may compare.", _obj({})),
    "calculate": ToolSpec(
        "calculate",
        "Select up to 3 alternatives to compare (option ids or model names). The system calculates.",
        _obj({"options": {"type": "array", "items": {"type": "string"}}}, ["options"])),
    "rank_options": ToolSpec(
        "rank_options", "Select the alternatives that raise the employee's net pay the most.", _obj({})),
}

TOOLS: dict[str, tuple[type[_Args], Callable[[ToolContext, Any], dict[str, Any]]]] = {
    "save_profile": (SaveProfileArgs, save_profile),
    "match_current_car": (MatchCarArgs, match_current_car),
    "list_options": (NoArgs, list_options),
    "calculate": (CalculateArgs, calculate),
    "rank_options": (NoArgs, rank_options),
}


def run_tool(ctx: ToolContext, name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
    """מריץ כלי. שגיאת קלט לא מפילה את השיחה — חוזרת כעובדה."""
    args_model, fn = TOOLS[name]
    try:
        args = args_model.model_validate(raw_args or {})
        return {"ok": True, **fn(ctx, args)}
    except ValidationError as exc:
        fields = sorted({".".join(str(x) for x in e["loc"]) for e in exc.errors()})
        return {"ok": False, "error": "invalid arguments", "fields": fields}
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}

