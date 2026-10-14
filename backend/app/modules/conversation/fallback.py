"""מצב גיבוי בלי מודל שפה: שאלה אחת בכל פעם, והתשובה מתורגמת לקריאות לאותם כלים."""
from __future__ import annotations

import re

from typing import Iterable

from app.modules.rules.public import CatalogCar, RulesSnapshot, catalog_names, mentions

from .llm.port import ToolCall
from .parsing import (
    find_year,
    first_number,
    is_no_car,
    is_none,
    is_unknown,
    match_drive,
    parse_copay,
    parse_profile_fields,
    strip_year,
)
from .stage import ConversationState, Stage, next_question, next_stage
from .tools import OPT_OUT_WORDS, available_options

_RANK_RE = re.compile(r"הכי\s*משתלם|הכי\s*טוב|תמליץ|המלצ|דרג", re.IGNORECASE)
SHORT_MODEL_WORDS = 3     # תשובה קצרה בלי שם מוכר → חיפוש במאגר; משפט ארוך → שאלה חוזרת


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-zא-ת]+", "", text.lower())


def named_cars(text: str, cars: Iterable[CatalogCar]) -> list[CatalogCar]:
    """רכבי הקטלוג שהטקסט מזכיר (שם, שם בלי BYD, כינויים מ-extensions.yaml), כמילים שלמות.
    השם הארוך קודם; שם שמוכל בשם ארוך יותר שנמצא ("קורולה" בתוך "קורולה קרוס") נזרק."""
    hits: dict[str, tuple[str, CatalogCar]] = {}
    for car in cars:
        found = [_key(n) for n in catalog_names(car) if mentions(text, n)]
        if found:
            hits[car.catalog_code] = (max(found, key=len), car)
    keep = [(n, car) for n, car in hits.values() if not any(n != m and n in m for m, _ in hits.values())]
    return [car for _, car in sorted(keep, key=lambda x: -len(x[0]))]


def _call(name: str, **args) -> list[ToolCall]:
    return [ToolCall(name=name, args=args, id=f"fallback_{name}")]


def _money_or_zero(field: str, text: str) -> list[ToolCall]:
    if is_none(text):
        return _call("save_profile", **{field: 0})
    n = first_number(text)
    return _call("save_profile", **{field: str(n)}) if n is not None else []


def fallback_calls(state: ConversationState, snapshot: RulesSnapshot, text: str) -> list[ToolCall]:
    field = next_question(state)
    t = text.strip()
    if not t or field is None:
        return []
    n = first_number(t)

    # הודעה חופשית עם כמה שדות ("מנהלת מוצר, שכר בסיס 18,500, פנסיה 6%...") — כולם בקריאה אחת
    if next_stage(state) == Stage.COLLECT_PROFILE:
        fields = parse_profile_fields(t, snapshot.occupations.keys())
        if fields:
            return _call("save_profile", **fields)

    if field == "occupation":
        return _call("save_profile", occupation=t)
    if field == "group":
        return _call("save_profile", eligibility_group=t)
    if field in ("commissions", "bonus", "study_fund_percent"):
        return _money_or_zero(field, t)
    if field == "children":
        if is_none(t):
            return _call("save_profile", children_under_23=0)
        return _call("save_profile", children_under_23=int(n)) if n is not None else []
    if field in ("base", "pension_percent", "credit_points"):
        arg = {"base": "base_salary"}.get(field, field)
        return _call("save_profile", **{arg: str(n)}) if n is not None else []
    if field == "car":
        return _car_calls(snapshot, t)
    if field == "car_choice":
        return _call("match_current_car", choice_number=int(n)) if n is not None else []
    if field == "use_value":
        if is_unknown(t):
            return _call("save_profile", current_use_value_unknown=True)
        return _call("save_profile", current_use_value=str(n)) if n is not None else []
    if field == "drive":
        drive = match_drive(t)
        return _call("save_profile", current_drive=drive.value) if drive else []
    if field == "copay":
        if is_unknown(t):
            return _call("save_profile", current_copay_unknown=True)
        copay = parse_copay(t)
        if copay is not None:
            return _call("save_profile", current_copay=str(copay))
        return _money_or_zero("current_copay", t)
    if field == "maintenance":
        return _call("save_profile", current_maintenance=str(n)) if n is not None else []
    if field == "options":
        return _option_calls(state, snapshot, t)
    return []


def _car_calls(snapshot: RulesSnapshot, text: str) -> list[ToolCall]:
    """הרכב הנוכחי ממשפט חופשי: שם מוכר מהקטלוג (+שנה), וגם השתתפות אם צוינה באותה הודעה.
    אף פעם לא שולחים משפט שלם כשם דגם."""
    if is_no_car(text):
        return _call("match_current_car", no_car=True)
    year = find_year(text)
    cars = named_cars(text, snapshot.catalog)
    if cars:
        calls = _call("match_current_car", model=cars[0].model_name, registration_year=year)
    else:
        model = strip_year(text)
        if len(model.split()) > SHORT_MODEL_WORDS:
            return []           # משפט בלי שם מוכר → התבנית שואלת שוב על הדגם בלבד
        calls = _call("match_current_car", model=model, registration_year=year)
    copay = parse_copay(text)
    if copay is not None:
        calls += _call("save_profile", current_copay=str(copay))
    return calls


def _option_calls(state: ConversationState, snapshot: RulesSnapshot, text: str) -> list[ToolCall]:
    if _RANK_RE.search(text):
        return _call("rank_options")
    options = available_options(snapshot, state)
    # שמות וכינויים קודם: שם רכב יכול להכיל ספרה (MAZDA 3), אז לא מפרשים אותה כמספר ברשימה
    named = {car.catalog_code for car in named_cars(text, [o.car for o in options if o.car is not None])}
    chosen = [o.id for o in options if o.id in named]
    if any(w in text.lower() for w in OPT_OUT_WORDS):
        chosen.append("OPT_OUT")
    if not chosen:
        for m in re.findall(r"\d+", text):
            i = int(m)
            if 1 <= i <= len(options):
                chosen.append(options[i - 1].id)
    return _call("calculate", options=chosen) if chosen else []
