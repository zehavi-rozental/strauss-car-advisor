"""הכלים של הסוכן. כל כלי מקבל ארגומנטים מאומתים, מעדכן את מצב השיחה ומחזיר עובדות.

הכלים לא קובעים את השלב (stage.next_stage עושה את זה) ולא כותבים למסד.
"""
from __future__ import annotations

import re
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
    catalog_names,
    mentions,
    related_cars,
)
from app.modules.vehicles.public import CurrentCarMatch, VehicleInfo, VehicleKey

from .llm.port import ToolSpec
from .parsing import match_drive, match_group, match_occupation, parse_decimal, similar_occupations
from .stage import (
    PROFILE_ARGS,
    MAX_OPTIONS,
    Q2_FIELDS,
    OPT_OUT_ID,
    CarChoice,
    ConversationState,
    CurrentCar,
    PendingConfirmation,
    PendingOccupation,
    Stage,
    missing_current_car,
    missing_profile,
    next_stage,
)

OPT_OUT_WORDS = ("ויתור", "לוותר", "opt out", "opt_out", "בלי רכב", "ללא רכב")

# profile fields (state attribute names) the employee may ask to answer later
_DEFERRABLE_FIELDS = ("base", "commissions", "bonus", "pension_percent", "study_fund_percent",
                      "credit_points", "children")


# חיפוש במאגר רשות המסים: (דגם, שנת רישום) → התאמה. כל קריאה פותחת סשן קריאה קצר משלה,
# כך שאין טרנזקציה פתוחה בזמן שהסוכן מחכה למודל השפה.
VehicleLookup = Callable[[str, Optional[int]], CurrentCarMatch]
# רכב מסוים לפי המפתח שלו (בחירה מובנית "tax:..." מתוצאות החיפוש)
VehicleGetter = Callable[[VehicleKey], Optional[VehicleInfo]]


@dataclass
class ToolContext:
    snapshot: RulesSnapshot
    find_vehicle: VehicleLookup
    state: ConversationState
    # מה התור מבקש מהנציג / מבטל — נפתח ונכתב בשלב הכתיבה של התור, באותה טרנזקציה
    question_requests: list[Any] = field(default_factory=list)
    cancel_question_ids: list[str] = field(default_factory=list)
    # ערכים שנדחו בבדיקת הסבירות: (שדה, ערך, "low" / "high")
    range_problems: list[tuple[str, Decimal, str]] = field(default_factory=list)
    get_vehicle: Optional[VehicleGetter] = None
    # העובד אישר תפקיד שאינו בטבלה ("כן, זה התפקיד שלי") — רק הקוד מדליק את זה, לא המודל
    allow_unknown_occupation: bool = False
    # the profile form / a reviewed payslip: each amount sits in its own shekel field, so a small amount is
    # what it says (never "3 ₪ or 3,000 ₪?"); unusual values are still confirmed
    exact_amounts: bool = False


class ToolError(Exception):
    pass


class MissingInputs(ToolError):
    """The engine was asked to calculate with a profile field still unknown. The engine's Employee would read
    an absent commissions / bonus / study fund / children as 0, so the conversation never sends one (rule 4)."""


# ---------- free-text car name → a label that may be shown and sent to the representative ----------

GENERIC_CAR_LABEL = "רכב שלא זוהה בקטלוג"
# words that mean the text is about pay, not about a car: never sent to the representative
_PAY_WORDS = ("שכר", "משכורת", "בונוס", "עמלה", "עמלות", "פנסיה", "ברוטו", "נטו", "השתלמות", "תלוש", "מרוויח")


def safe_car_label(text: Any) -> str:
    """A car name typed by the employee and not found in any file is free text: it may carry anything ("טויוטה
    שכר 18,500"). Only a short name with no figures but a model number (1-2 digits) or a plausible year is
    kept; anything else becomes a generic label. Privacy: the representative never sees pay data."""
    t = " ".join(str(text or "").split())
    if not t or len(t) > 40 or len(t.split()) > 5 or any(w in t for w in _PAY_WORDS):
        return GENERIC_CAR_LABEL
    for token in re.findall(r"\d[\d,.]*", t):
        if not token.isdigit() or not (len(token) <= 2 or (len(token) == 4 and 1980 <= int(token) <= 2035)):
            return GENERIC_CAR_LABEL
    return t


def rep_car_label(c: Any) -> Optional[str]:
    """The car as the representative may see it: catalog and tax-file labels are ours; the rest is sanitized."""
    if c.source in ("catalog", "tax"):
        return c.label
    return safe_car_label(c.label or c.query) if (c.label or c.query) else None


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
    # הטווחים הסבירים נבדקים ב-save_profile מול extensions.yaml (value_ranges) — לכל מקור
    base_salary: Num = None
    commissions: Num = None
    bonus: Num = None
    pension_percent: Num = None
    study_fund_percent: Num = None
    credit_points: Num = None
    children_under_23: Optional[int] = None
    current_copay: Num = None
    current_copay_unknown: bool = False
    current_use_value: Num = None
    current_use_value_unknown: bool = False
    current_drive: Optional[str] = None
    current_maintenance: Num = None
    current_maintenance_unknown: bool = False
    approximate_fields: list[str] = Field(default_factory=list)    # שדות שהעובד אמר כהערכה
    unknown_fields: list[str] = Field(default_factory=list)       # Q2 fields the employee does not know
    # fields the employee asked to answer LATER ("אח\"כ אבדוק את השכר"): postponed, not a value, not dropped
    defer_fields: list[str] = Field(default_factory=list)


class MatchCarArgs(_Args):
    model: Optional[str] = None
    registration_year: Optional[int] = Field(None, ge=1980, le=2049)
    choice_number: Optional[int] = Field(None, ge=1, le=5)
    no_car: bool = False
    reset: bool = False          # "זה לא הרכב שלי" בלי דגם: מוחקים את ההתאמה ושואלים שוב
    vehicle_key: Optional[str] = None   # "tax:<שנת מס>:<שנת רישום>:<סוג>:<יצרן>:<דגם>" — רק מבחירה מובנית


class CalculateArgs(_Args):
    options: list[str] = Field(min_length=1)


class NoArgs(_Args):
    pass


# ---------- save_profile ----------

RANGED_FIELDS = ("base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent",
                 "credit_points", "children_under_23", "current_use_value", "current_copay",
                 "current_maintenance")
SHORTENED_FIELDS = ("base_salary", "commissions", "bonus", "current_use_value", "current_copay",
                    "current_maintenance")


def _unusual(ctx: ToolContext, field: str, value: Decimal) -> bool:
    """A value inside the plausible range but beyond the thresholds in extensions.yaml, not yet confirmed."""
    rule = ctx.snapshot.extensions.unusual_values.get(field)
    if not rule or field in ctx.state.confirmed_unusual:
        return False
    return ("at_most" in rule and value <= rule["at_most"]) or ("at_least" in rule and value >= rule["at_least"])


def _check_ranges(ctx: ToolContext, args: SaveProfileArgs) -> tuple[SaveProfileArgs, list[dict[str, str]]]:
    """בדיקת סבירות לכל ערך, מכל מקור. מחוץ לטווח — לא נשמר. סכום מקוצר ("18" לשכר, כש-18,000 בטווח)
    לא מורחב בשקט: נשמר כ"ממתין לאישור" והבוט שואל."""
    ranges = ctx.snapshot.extensions.value_ranges
    drop: dict[str, None] = {}
    rejected: list[dict[str, str]] = []

    def hold(arg: str, pending: PendingConfirmation, reason: str) -> None:
        """One value is confirmed at a time: the first held value keeps the question, a second one in the same
        message is not saved and is asked again as a missing field afterwards (never replacing the first)."""
        if ctx.state.pending_confirmation is None:
            ctx.state.pending_confirmation = pending
            rejected.append({"field": arg, "reason": reason})
        else:
            rejected.append({"field": arg, "reason": "not saved: another value is being confirmed first; "
                                                     "it will be asked again"})

    for arg in RANGED_FIELDS:
        value = getattr(args, arg)
        if value is None or arg not in ranges:
            continue
        low, high = ranges[arg]
        small = ctx.snapshot.extensions.small_amount_below.get(arg)
        if (small is not None and not ctx.exact_amounts and 0 < Decimal(value) < small
                and low <= Decimal(value) * 1000 <= high and arg not in ctx.state.confirmed_unusual):
            drop[arg] = None             # "3" for commissions: 3 ₪ or 3,000 ₪? asked, never guessed
            hold(arg, PendingConfirmation(field=arg, value=Decimal(value),
                                          approximate=arg in args.approximate_fields, scale=True),
                 "too small to be sure; the employee is asked 3 or 3,000")
            continue
        if low <= Decimal(value) <= high:
            if _unusual(ctx, arg, Decimal(value)):
                drop[arg] = None
                hold(arg, PendingConfirmation(field=arg, value=Decimal(value),
                                              approximate=arg in args.approximate_fields, unusual=True),
                     "unusual value; the employee is asked to confirm")
            continue
        drop[arg] = None
        thousands = Decimal(value) * 1000
        if arg in SHORTENED_FIELDS and Decimal(value) < low and low <= thousands <= high:
            hold(arg, PendingConfirmation(field=arg, value=thousands, approximate=arg in args.approximate_fields),
                 "shortened; the employee is asked to confirm")
        else:
            ctx.range_problems.append((arg, Decimal(value), "low" if Decimal(value) < low else "high"))
            rejected.append({"field": arg, "reason": f"outside the plausible range {low}–{high}"})
    return (args.model_copy(update=drop) if drop else args), rejected


def save_profile(ctx: ToolContext, args: SaveProfileArgs) -> dict[str, Any]:
    p, c, snap = ctx.state.profile, ctx.state.current, ctx.snapshot
    saved: list[str] = []
    args, ignored = _check_ranges(ctx, args)

    if args.occupation:
        name = match_occupation(snap.occupations.keys(), args.occupation)
        if name:
            p.occupation, p.occupation_text, p.occupation_unknown = name, None, False
            p.group = snap.occupations[name].group
            saved.append("occupation")
        elif ctx.allow_unknown_occupation:
            # קבוצה שכבר נמסרה (Q1) נשמרת; אחרת היא נשאלת אחרי התפקיד
            p.occupation, p.occupation_text, p.occupation_unknown = None, args.occupation.strip()[:80], True
            ctx.state.pending_occupation = None
            saved.append("occupation_unknown")
        else:
            # לא בטבלה: לא נשמר. שואלים — עם עד 3 הצעות דומות מהטבלה
            text = args.occupation.strip()[:80]
            ctx.state.pending_occupation = PendingOccupation(
                text=text, suggestions=similar_occupations(snap.occupations.keys(), text))
            ignored.append({"field": "occupation", "reason": "not in the occupations table; the employee is asked"})

    if args.eligibility_group:
        group = match_group(snap.calc.group_order, args.eligibility_group)
        if p.occupation:
            ignored.append({"field": "eligibility_group", "reason": "הקבוצה נקבעת לפי טבלת העיסוקים"})
        elif group is None:
            ignored.append({"field": "eligibility_group", "reason": "קבוצה לא מוכרת"})
        else:
            p.group, p.occupation_unknown = group, True
            saved.append("eligibility_group")

    for arg, attr in PROFILE_ARGS.items():
        value = getattr(args, arg)
        if value is not None:
            setattr(p, attr, value)
            saved.append(arg)
            if attr in p.unknown_fields:
                p.unknown_fields.remove(attr)          # the value was found after all
            if attr in ctx.state.deferred_fields:
                ctx.state.deferred_fields.remove(attr)  # a value was given — no longer deferred

    # "לא יודע": recorded per field, never a value (only fields that are still empty)
    for name in args.unknown_fields:
        if name in Q2_FIELDS and getattr(p, name) is None and name not in p.unknown_fields:
            p.unknown_fields.append(name)
            saved.append(f"unknown:{name}")

    # "אח\"כ אבדוק": postpone a field. Recorded in the state; the stage machine skips it while other work
    # exists (stage.hidden_deferred). Only still-missing profile fields can be deferred; never a value.
    for name in args.defer_fields:
        if name in _DEFERRABLE_FIELDS and getattr(p, name, None) is None and name not in ctx.state.deferred_fields:
            ctx.state.deferred_fields.append(name)
            saved.append(f"defer:{name}")

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
            c.source, c.label = "stated", c.label or safe_car_label(c.query)
            c.use_value, c.use_value_unknown = args.current_use_value, False
            saved.append("current_use_value")
        elif c.use_value is None:
            c.source, c.label, c.use_value_unknown = "stated", c.label or safe_car_label(c.query), True
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
            c.maintenance, c.maintenance_unknown = args.current_maintenance, False
            if "maintenance" in ctx.state.deferred_fields:
                ctx.state.deferred_fields.remove("maintenance")   # answered — no longer deferred
            saved.append("current_maintenance")
        else:
            ignored.append({"field": "current_maintenance", "reason": "רלוונטי רק למי שאין לו רכב היום"})
    elif args.current_maintenance_unknown and c.source == "none" and c.maintenance is None:
        # "אבדוק בהמשך": postpone the allowance question (deferred_fields). hidden_deferred skips it while
        # alternatives can be chosen and brings it back before the net is shown — never assumed 0.
        c.maintenance_unknown = True
        if "maintenance" not in ctx.state.deferred_fields:
            ctx.state.deferred_fields.append("maintenance")
        saved.append("current_maintenance_unknown")

    # הערכה: השדות שנשמרו עכשיו מסומנים (או מנוקים, אם נאמרו עכשיו במדויק)
    for arg in RANGED_FIELDS:
        if arg in saved:
            if arg in args.approximate_fields and arg not in p.approximate_fields:
                p.approximate_fields.append(arg)
            elif arg not in args.approximate_fields and arg in p.approximate_fields:
                p.approximate_fields.remove(arg)
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

def _longest_named(cars: list[CatalogCar], text: str) -> list[CatalogCar]:
    """Several partial matches ("טויוטה קורולה קרוס" mentions COROLLA and COROLLA CROSS): the car whose
    mentioned name is longest is the one meant."""
    if len(cars) <= 1:
        return cars
    size = {c.catalog_code: max((len(n) for n in catalog_names(c) if mentions(text, n)), default=0) for c in cars}
    best = max(size.values())
    return [c for c in cars if size[c.catalog_code] == best] if best else cars


def match_current_car(ctx: ToolContext, args: MatchCarArgs) -> dict[str, Any]:
    old = ctx.state.current
    keep = dict(copay=old.copay, copay_unknown=old.copay_unknown, rejected=list(old.rejected), has_car=old.has_car)

    if args.reset:
        # "זה לא הרכב שלי" / "התכוונתי לרכב אחר": only the model is wrong. There is a car (has_car), its copay
        # stays, and the model just rejected is not offered again.
        rejected = [*old.rejected, *([old.catalog_code] if old.catalog_code else [])]
        ctx.state.current = CurrentCar(**{**keep, "rejected": rejected, "has_car": True})
        _invalidate_results(ctx.state)
        return {"status": "RESET", "next": "ask the employee for the exact model (they have a company car)"}

    if args.no_car:
        ctx.state.current = CurrentCar(source="none", label="ללא רכב")
        _invalidate_results(ctx.state)
        return {"status": "NO_CAR"}

    if args.vehicle_key:
        parts = args.vehicle_key.strip().split(":")
        info = None
        if len(parts) == 6 and parts[0] == "tax" and parts[1].isdigit() and parts[2].isdigit() and ctx.get_vehicle:
            info = ctx.get_vehicle(VehicleKey(int(parts[1]), int(parts[2]), parts[3], parts[4], parts[5]))
        if info is None:
            raise ToolError("הרכב לא נמצא במאגר")
        ctx.state.current = CurrentCar(query=info.commercial_name, reg_year=info.key.reg_year, source="tax",
                                       label=info.label, drive=Drive(info.drive), use_value=info.use_value, **keep)
        _invalidate_results(ctx.state)
        return {"status": "FOUND", "source": "tax", "label": info.label, "use_value": str(info.use_value)}

    if args.choice_number is not None and old.choices and not args.model:
        if args.choice_number > len(old.choices):
            raise ToolError("מספר בחירה לא קיים")
        ch = old.choices[args.choice_number - 1]
        if ch.catalog_code:
            car = next(x for x in ctx.snapshot.catalog if x.catalog_code == ch.catalog_code)
            ctx.state.current = CurrentCar(
                query=old.query, reg_year=car.reg_year, source="catalog", label=car.label,
                catalog_code=car.catalog_code, group=car.group, drive=car.drive, use_value=car.use_value,
                monthly_cost=car.monthly_cost, **keep)
            _invalidate_results(ctx.state)
            return {"status": "FOUND", "source": "catalog", "label": car.label, "group": car.group,
                    "use_value": str(car.use_value)}
        ctx.state.current = CurrentCar(query=old.query, reg_year=ch.reg_year, source="tax", label=ch.label,
                                       drive=ch.drive, use_value=ch.use_value, **keep)
        _invalidate_results(ctx.state)
        return {"status": "FOUND", "source": "tax", "label": ch.label, "use_value": str(ch.use_value)}

    if not args.model:
        raise ToolError("חסר דגם")
    model = args.model.strip()[:80]
    year = args.registration_year

    related = related_cars(ctx.snapshot, model)
    if len(related) > 1 and old.rejected:
        # a model the employee already said is not theirs is not offered again ("התכוונתי לרכב אחר" → "קורולה")
        related = [c for c in related if c.catalog_code not in old.rejected] or related
        if len(related) == 1:
            model = related[0].model_name
    by_year = [c for c in related if c.reg_year == year] if year is not None else []
    if len(by_year) == 1:
        related = []                     # the year tells them apart ("COROLLA 2025" → COROLLA CROSS)
    if len(related) > 1:
        # "קורולה": COROLLA or COROLLA CROSS? one question with the actual catalog matches as buttons
        choices = [CarChoice(label=c.label, description=c.model_description, drive=c.drive, use_value=c.use_value,
                             reg_year=c.reg_year, catalog_code=c.catalog_code) for c in related[:5]]
        ctx.state.current = CurrentCar(query=model, reg_year=year, choices=choices, **keep)
        _invalidate_results(ctx.state)
        return {"status": "CHOOSE", "choices": [{"number": i + 1, "label": ch.label}
                                                 for i, ch in enumerate(choices)]}
    cars = by_year if len(by_year) == 1 else _longest_named(find_catalog_car(ctx.snapshot, model), model)
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
        ctx.state.current = CurrentCar(query=model, reg_year=year, label=safe_car_label(model), **keep)
        result = {"status": "NOT_FOUND", "next": "ask the employee for the use value on their payslip"}
        if match.years and year is not None and not match.years[0] <= year <= match.years[1]:
            # outside the tax file: say what it covers; the use value comes from the payslip, never invented
            result["coverage"] = f"{match.years[0]}–{match.years[1]}"
            ctx.state.current.coverage = result["coverage"]
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


MIN_PARTIAL_CHARS = 3        # a fragment shorter than this ("3", "a", "ev") is never a car


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-zא-ת]+", "", re.sub(r"\(\d{4}\)", "", text).lower())


def _match_option(snapshot: RulesSnapshot, options: list[Option], text: str) -> tuple[Optional[Option], list[Option]]:
    """(the alternative, the alternatives it could be when ambiguous). Exact only: an id, a catalog name or an
    alias, or the opt-out words. A fragment ("atto", "mazda") counts only when it is at least 3 characters and
    names exactly one eligible car; several ("3": ATTO 3, MAZDA 3) are never picked silently."""
    t = text.strip()
    by_id = {o.id.upper(): o for o in options}
    if t.upper() in by_id:
        return by_id[t.upper()], []
    if any(w in t.lower() for w in OPT_OUT_WORDS):
        return by_id.get(OPT_OUT_ID), []
    key = _key(t)
    if not key:
        return None, []
    exact = [c for c in snapshot.catalog if key == _key(c.catalog_code) or key in {_key(n) for n in catalog_names(c)}]
    if exact:                                    # a car named exactly; not eligible → not an alternative
        return next((by_id[c.catalog_code] for c in exact if c.catalog_code in by_id), None), []
    if len(key) < MIN_PARTIAL_CHARS:
        return None, []
    partial = [by_id[c.catalog_code] for c in find_catalog_car(snapshot, t) if c.catalog_code in by_id]
    return (partial[0], []) if len(partial) == 1 else (None, partial)


def resolve_option(snapshot: RulesSnapshot, options: list[Option], text: str) -> Optional[Option]:
    return _match_option(snapshot, options, text)[0]


def list_options(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    return {"options": [{"id": o.id, "label": o.label, "group": o.group,
                         "drive": o.drive.value if o.drive else None}
                        for o in available_options(ctx.snapshot, ctx.state)]}


def calculate(ctx: ToolContext, args: CalculateArgs) -> dict[str, Any]:
    options = available_options(ctx.snapshot, ctx.state)
    chosen: list[str] = []
    unknown: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for text in args.options:
        opt, several = _match_option(ctx.snapshot, options, text)
        if opt is None:
            unknown.append(text[:40])
            if several:
                ambiguous[text[:40]] = [o.label for o in several]
        elif opt.id not in chosen:
            chosen.append(opt.id)
    if not chosen:
        if ambiguous:
            raise ToolError("שם לא חד-משמעי: " + "; ".join(f"{k} = {' או '.join(v)}" for k, v in ambiguous.items())
                            + ". לשאול את העובד לאיזה התכוון")
        raise ToolError("אף חלופה לא זוהתה")
    ctx.state.selected = chosen[:MAX_OPTIONS]
    ctx.state.rank_requested = False
    ctx.state.ranked_count = 0
    _invalidate_results(ctx.state)
    return {"selected": ctx.state.selected, "not_recognized": unknown, "ambiguous": ambiguous,
            "note": "the system calculates now; do not compute anything yourself"}


def rank_options(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    # the ranking needs every value it ranks on: a field the employee does not know yet defers it too
    if (next_stage(ctx.state) in (Stage.COLLECT_PROFILE, Stage.COLLECT_CURRENT_CAR)
            or missing_profile(ctx.state.profile)):
        # הדירוג צריך את הפרופיל ואת הרכב של היום: הבקשה נרשמת, והבחירה נעשית ברגע שהם ידועים
        ctx.state.rank_requested = True
        return {"deferred": True, "note": "ranked as soon as the profile and the current car are known"}
    ctx.state.rank_requested = False
    options = available_options(ctx.snapshot, ctx.state)
    employee, current, occupation = engine_inputs(ctx.snapshot, ctx.state)
    states = {o.id: option_state(o) for o in options}
    ranked = engine_rank_options(employee, current, list(states.values()), ctx.snapshot.calc, occupation)
    by_label = {s.label: oid for oid, s in states.items()}
    top = [by_label[r.alternative.label] for r in ranked if r.alternative is not None][:MAX_OPTIONS]
    if not top:
        raise ToolError("אין חלופות זכאיות לדירוג")
    ctx.state.selected = top
    ctx.state.ranked_count = len([r for r in ranked if r.alternative is not None])
    _invalidate_results(ctx.state)
    return {"selected": top, "note": "the system calculates the top options now"}


# ---------- קלט למנוע ----------

def engine_inputs(snapshot: RulesSnapshot, state: ConversationState
                  ) -> tuple[Employee, State, Optional[OccupationRule]]:
    p, c = state.profile, state.current
    missing = [f for f in ("commissions", "bonus", "study_fund_percent", "children") if getattr(p, f) is None]
    if missing:
        raise MissingInputs("חסרים פרטי שכר לחישוב: " + ", ".join(missing))   # never a silent 0
    employee = Employee(
        group=p.group, base=p.base, commissions=p.commissions, bonus=p.bonus,
        pension_rate=None if p.pension_percent is None else p.pension_percent / 100,
        study_fund_rate=p.study_fund_percent / 100,
        credit_points=p.credit_points, eligible_children=p.children,
    )
    if c.source == "none":
        current: State = OptOutState(label="ללא רכב", maintenance=c.maintenance)
    elif c.source == "catalog":
        car = next(x for x in snapshot.catalog if x.catalog_code == c.catalog_code)
        current = car_state(car, copay=c.copay, copay_status=CopayStatus.EMPLOYEE_STATED)
    else:
        if c.drive is None:   # המכונה לא מתקדמת בלי הנעה; אין ברירת מחדל
            raise ToolError("חסר סוג ההנעה של הרכב הנוכחי")
        current = CarState(label=c.label or safe_car_label(c.query) if (c.label or c.query) else "הרכב שלכם", group=None, drive=c.drive,
                           use_value=c.use_value, copay=c.copay, copay_status=CopayStatus.EMPLOYEE_STATED)
    occupation = occupation_rule(snapshot.occupations.get(p.occupation or ""))
    if occupation is None and p.occupation_unknown and p.group:
        # עיסוק שאינו בטבלה: לפי תשובות הנציג. ויתור מחושב רק כששתי התשובות ידועות.
        if p.rep_opt_out_allowed is False:
            occupation = OccupationRule(name=p.occupation_text or "", group=p.group, car_mandatory=False,
                                        opt_out_allowed=False, maintenance=Decimal(0))
        elif p.rep_opt_out_allowed and p.rep_opt_out_maintenance is not None:
            occupation = OccupationRule(name=p.occupation_text or "", group=p.group, car_mandatory=False,
                                        opt_out_allowed=True, maintenance=p.rep_opt_out_maintenance)
    return employee, current, occupation


def option_state(option: Option, copay_policy: str = "CATALOG_DEFAULT_CONFIRM_ASYNC",
                 confirmed: Optional[Decimal] = None) -> State:
    """חלופה כמצב למנוע. השתתפות שהנציג אישר — סופית. אחרת לפי הקטלוג: ממתינה לאישור נציג —
    אלא אם המדיניות היא CATALOG_DEFAULT, שבה הקטלוג הוא הסכום הסופי."""
    if option.car is None:
        return OptOutState()
    if confirmed is not None:
        return car_state(option.car, copay=confirmed, copay_status=CopayStatus.CONFIRMED)
    if copay_policy == "CATALOG_DEFAULT":
        return car_state(option.car, copay=option.car.default_copay, copay_status=CopayStatus.CONFIRMED)
    return car_state(option.car)


# ---------- הגדרות לכלים (למודל השפה) ----------

def _obj(props: dict[str, Any], required: Optional[list[str]] = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


class RepQuestionArgs(_Args):
    question: str


def propose_rep_question(ctx: ToolContext, args: RepQuestionArgs) -> dict[str, Any]:
    """Only the offer: the employee chooses "לשאול את הנציג" (then a privacy-checked preview) or "להמשיך בלי"."""
    question = " ".join(args.question.split())[:300]
    if not question:
        raise ToolError("חסרה שאלה")
    ctx.state.unanswered_question = question
    return {"offered": True, "note": "end your reply by offering to ask the representative; buttons are shown"}


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
            "current_maintenance": {"type": "number", "description": "the monthly car-maintenance allowance "
                                    "(אחזקת רכב) the employee receives TODAY, only for employees with no "
                                    "company car. Pass 0 only if the employee says they do NOT receive one. "
                                    "Never guess it."},
            "current_maintenance_unknown": {"type": "boolean", "description": "the employee does not know / "
                                            "will check later whether they get a maintenance allowance. Never "
                                            "assume 0."},
            "unknown_fields": {"type": "array", "items": {"type": "string", "enum": list(Q2_FIELDS)},
                               "description": "fields the employee says they do not know (לא יודע / לא זוכרת). "
                               "Never guess or fill a value for them."},
            "defer_fields": {"type": "array",
                             "items": {"type": "string",
                                       "enum": ["base", "commissions", "bonus", "pension_percent",
                                                "study_fund_percent", "credit_points", "children"]},
                             "description": "fields the employee asked to answer LATER, not now (e.g. "
                             "'את השכר אבדוק אחר כך', 'אח\"כ', 'בינתיים נתקדם', 'נשלים בהמשך', 'אין לי את זה "
                             "עכשיו'). Use 'base' for the base salary. Postpone them — the system will stop "
                             "asking for them and bring them back only when needed. Never a value, never a guess."},
            "approximate_fields": {"type": "array", "items": {"type": "string"},
                                   "description": "names of the fields above that the employee stated as an "
                                   "estimate (כ-, בערך, בסביבות, בממוצע, משתנות)"},
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
    "select_options": ToolSpec(
        "select_options",
        "Select up to 3 alternatives to compare (option ids or model names from STATE). The system calculates "
        "as soon as the profile and the current car are known; you never calculate.",
        _obj({"options": {"type": "array", "items": {"type": "string"}}}, ["options"])),
    "propose_rep_question": ToolSpec(
        "propose_rep_question",
        "The employee asked something the FAQ and STATE do not answer. Offer to ask the transport "
        "representative: the employee then sees the buttons \"לשאול את הנציג\" / \"להמשיך בלי\". Nothing is sent "
        "now (a privacy-checked preview comes first). Pass the employee's question in short Hebrew.",
        _obj({"question": {"type": "string"}}, ["question"])),
}

TOOLS: dict[str, tuple[type[_Args], Callable[[ToolContext, Any], dict[str, Any]]]] = {
    "save_profile": (SaveProfileArgs, save_profile),
    "match_current_car": (MatchCarArgs, match_current_car),
    "list_options": (NoArgs, list_options),
    "calculate": (CalculateArgs, calculate),
    "rank_options": (NoArgs, rank_options),
    "select_options": (CalculateArgs, calculate),
    "propose_rep_question": (RepQuestionArgs, propose_rep_question),
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

