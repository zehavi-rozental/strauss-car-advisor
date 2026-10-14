"""קורא ההודעות הדטרמיניסטי. רץ לפני מודל השפה: מה שהוא קורא במלואו לא מגיע למודל בכלל.

fallback_plan מחזיר את הקריאות לכלים ואם הקריאה "מלאה" — כלומר ההודעה נקראה בלי ספק.
כשלא, התור עובר למודל השפה, והקריאות כאן משמשות רק אם המודל נכשל.
כלל ברזל: עדיף לא לקרוא מאשר לקרוא לא נכון ("בערך 18 וחצי" אינו 18).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.modules.rules.public import CatalogCar, RulesSnapshot, catalog_names, matched_name, mentions

from .llm.port import ToolCall
from .parsing import (
    find_year,
    is_no_car,
    is_none,
    is_approximate,
    is_unknown,
    leftover_words,
    match_drive,
    match_group,
    negates,
    numbers_in,
    parse_copay,
    parse_profile_fields,
    strip_year,
)
from .stage import ConversationState, Stage, missing_profile, next_question, next_stage
from .tools import OPT_OUT_WORDS, available_options

_RANK_RE = re.compile(r"הכי\s*משתלם|הכי\s*טוב|תמליץ|המלצ|דרג", re.IGNORECASE)
_NOT_THIS_CAR_RE = re.compile(r"לא\s+(?:זה\s+)?(?:הרכב|הדגם)|(?:זה|זו)\s+לא\s+(?:נכון|הוא|היא)")
# בזמן שאוספים את פרטי הרכב הנוכחי, הודעה שמזכירה רכב אחר היא תיקון — לא תשובה לשאלה
_CAR_DETAIL_QUESTIONS = {"copay", "use_value", "drive", "maintenance", "car_choice"}
_ONLY_NUMBERS_RE = re.compile(r"^\s*\d+(?:\s*(?:,|ו-?|and|&)\s*\d+)*\s*[.!]?\s*$")
SHORT_MODEL_WORDS = 3     # תשובה קצרה בלי שם מוכר → חיפוש במאגר; משפט ארוך → שאלה חוזרת
SINGLE_VALUE_WORDS = 4    # תשובה לשאלה בודדת: עד 4 מילים ומספר אחד בדיוק

# שם השדה בשאלה → שם הארגומנט ב-save_profile
_PROFILE_ARG = {"occupation": "occupation", "group": "eligibility_group", "base": "base_salary",
                "commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
                "study_fund_percent": "study_fund_percent", "credit_points": "credit_points",
                "children": "children_under_23"}
_ZERO_ALLOWED = {"commissions", "bonus", "study_fund_percent", "children"}


@dataclass(frozen=True)
class ParserPlan:
    calls: list[ToolCall] = field(default_factory=list)
    complete: bool = False          # ההודעה נקראה במלואה — אין צורך במודל השפה
    notice: Optional[str] = None    # משפט שהקורא רוצה להציג לפני השאלה הבאה


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-zא-ת]+", "", text.lower())


def _call(name: str, **args) -> list[ToolCall]:
    return [ToolCall(name=name, args=args, id=f"fallback_{name}")]


def single_value(text: str) -> Optional[str]:
    """הערך בתשובה קצרה: עד 4 מילים ומספר אחד בדיוק ("18,500 ש"ח", "6%"). אחרת None."""
    numbers = numbers_in(text)
    if is_approximate(text, re.split(r"\d[\d,.]*", text, maxsplit=1)[-1]):
        return None                         # "בערך 18 וחצי", "כ-18,500" — לא שומרים ערך משוער
    if len(text.split()) <= SINGLE_VALUE_WORDS and len(numbers) == 1:
        return str(numbers[0])
    return None


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


def fallback_plan(state: ConversationState, snapshot: RulesSnapshot, text: str) -> ParserPlan:
    question = next_question(state)
    t = text.strip()
    if not t or question is None:
        return ParserPlan()
    if next_stage(state) == Stage.COLLECT_PROFILE:
        return _profile_plan(state, snapshot, question, t)
    if question == "car":
        return _car_plan(snapshot, t)
    if question in _CAR_DETAIL_QUESTIONS:
        correction = correction_plan(state, snapshot, t)
        if correction is not None:
            return correction
    if question == "car_choice":
        value = single_value(t)
        return ParserPlan(_call("match_current_car", choice_number=int(float(value))), True) if value else ParserPlan()
    if question == "use_value":
        if is_unknown(t):
            return ParserPlan(_call("save_profile", current_use_value_unknown=True), True)
        value = single_value(t)
        return ParserPlan(_call("save_profile", current_use_value=value), True) if value else ParserPlan()
    if question == "drive":
        drive = match_drive(t)
        return ParserPlan(_call("save_profile", current_drive=drive.value), True) if drive else ParserPlan()
    if question == "copay":
        if is_unknown(t):
            return ParserPlan(_call("save_profile", current_copay_unknown=True), True)
        copay = parse_copay(t)
        if copay is None and is_none(t):
            copay = 0
        if copay is None:
            copay = single_value(t)
        return ParserPlan(_call("save_profile", current_copay=str(copay)), True) if copay is not None else ParserPlan()
    if question == "maintenance":
        value = single_value(t)
        return ParserPlan(_call("save_profile", current_maintenance=value), True) if value else ParserPlan()
    if question == "options":
        return _option_plan(state, snapshot, t)
    return ParserPlan()


def _profile_plan(state: ConversationState, snapshot: RulesSnapshot, question: str, text: str) -> ParserPlan:
    asked = _PROFILE_ARG[question]
    # הודעה חופשית עם כמה שדות ("מנהלת מוצר, שכר בסיס 18,500, פנסיה 6%...") — לפי מילות מפתח
    fields = parse_profile_fields(text, snapshot.occupations.keys())
    if fields:
        needed = {_PROFILE_ARG[f] for f in missing_profile(state.profile)}
        return ParserPlan(_call("save_profile", **fields), asked in fields or needed <= set(fields))
    if question == "occupation":
        # עיסוק שלא בטבלה: נשמר כ"לא ידוע" רק אם המודל לא זמין או נכשל — הקריאה לא "מלאה"
        return ParserPlan(_call("save_profile", occupation=text), False)
    if question == "group":
        group = match_group(snapshot.calc.group_order, text)
        return ParserPlan(_call("save_profile", eligibility_group=group), True) if group else ParserPlan()
    if question in _ZERO_ALLOWED and is_none(text):
        return ParserPlan(_call("save_profile", **{asked: 0}), True)
    value = single_value(text)
    if value is None:
        return ParserPlan()
    if question == "children":
        return ParserPlan(_call("save_profile", children_under_23=int(float(value))), True)
    return ParserPlan(_call("save_profile", **{asked: value}), True)


def correction_plan(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[ParserPlan]:
    """תיקון הרכב שזוהה: שם רכב אחר מהקטלוג → התאמה מחדש (ההשתתפות שכבר נמסרה נשמרת);
    "זה לא הרכב" בלי דגם → מוחקים את ההתאמה ושואלים שוב. אחרת None — זו תשובה לשאלה."""
    cars = named_cars(text, snapshot.catalog)
    if cars and cars[0].catalog_code != state.current.catalog_code:
        return _car_plan(snapshot, text)          # כולל בדיקת "לא" באמצע ומילים נוספות
    if not cars and _NOT_THIS_CAR_RE.search(text):
        return ParserPlan(_call("match_current_car", reset=True), True)
    return None


def _car_plan(snapshot: RulesSnapshot, text: str) -> ParserPlan:
    """הרכב הנוכחי ממשפט חופשי: שם מוכר מהקטלוג (+שנה), וגם השתתפות אם צוינה באותה הודעה.
    אף פעם לא שולחים משפט שלם כשם דגם. שם קצר שאינו בקטלוג — לא "מלא": המודל אולי יכיר אותו."""
    if is_no_car(text):
        return ParserPlan(_call("match_current_car", no_car=True), True)
    if negates(text):
        return ParserPlan()     # "זה לא קורולה קרוס" — לא מתאימים דבר, גם לא כגיבוי; המודל או שאלה חוזרת
    year = find_year(text)
    cars = named_cars(text, snapshot.catalog)
    if cars:
        # "מלא" רק אם לא נשאר בהודעה כלום מעבר לשם, לשנה, להשתתפות ולמילות מילוי
        ext = snapshot.extensions
        leftovers = leftover_words(text, matched_name(text, cars[0]) or cars[0].model_name,
                                   ext.car_message_fillers + ext.correction_cues)
        calls = _call("match_current_car", model=cars[0].model_name, registration_year=year)
        complete = not leftovers
    else:
        model = strip_year(text)
        if len(model.split()) > SHORT_MODEL_WORDS:
            return ParserPlan()     # משפט בלי שם מוכר → התבנית שואלת שוב על הדגם בלבד
        calls, complete = _call("match_current_car", model=model, registration_year=year), False
    copay = parse_copay(text)
    if copay is not None:
        calls += _call("save_profile", current_copay=str(copay))
    return ParserPlan(calls, complete)


def _option_plan(state: ConversationState, snapshot: RulesSnapshot, text: str) -> ParserPlan:
    if _RANK_RE.search(text):
        return ParserPlan(_call("rank_options"), True)
    options = available_options(snapshot, state)
    # שמות וכינויים קודם: שם רכב יכול להכיל ספרה (MAZDA 3), אז לא מפרשים אותה כמספר ברשימה
    named = {car.catalog_code for car in named_cars(text, [o.car for o in options if o.car is not None])}
    chosen = [o.id for o in options if o.id in named]
    if any(w in text.lower() for w in OPT_OUT_WORDS):
        chosen.append("OPT_OUT")
    # מספר לבד = בחירה מהרשימה, רק כשההודעה היא מספרים בלבד ("2", "1, 3"); "לבדוק 2 רכבים" אינו בחירה
    if not chosen and _ONLY_NUMBERS_RE.match(text):
        numbers = [int(m) for m in re.findall(r"\d+", text)]
        outside = [n for n in numbers if not 1 <= n <= len(options)]
        if outside:
            # רק מספרים, ולפחות אחד מחוץ לרשימה: נקרא במלואו — עונים מיד, בלי מודל ובלי בחירה
            return ParserPlan(complete=True, notice=(
                f"אין חלופה מספר {', '.join(map(str, outside))}. "
                f"בחרו מספר בין 1 ל-{len(options)}, או כתבו את שם הרכב."))
        chosen = [options[n - 1].id for n in numbers]
    return ParserPlan(_call("calculate", options=chosen), True) if chosen else ParserPlan()
