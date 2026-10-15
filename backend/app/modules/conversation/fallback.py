"""קורא ההודעות הדטרמיניסטי.

כלל אחד לכל השאלות: הקורא פועל רק כשקרא את ההודעה במלואה — כשלא נשאר בה דבר בעל משמעות אחרי
מה שזיהה (שמות, מספרים, מילים מוכרות) ומילות המילוי של השאלה (extensions.yaml). אחרת ההודעה עוברת
למודל השפה; ואם אין מודל או שלא קרא לכלי — הבוט שואל שוב ולא משנה דבר.

לקריאות של תוכנית שלא נקראה במלואה יש שימוש אחד בלבד — גיבוי כשאין מודל, ורק כשהערך חד-משמעי:
ערכי פרופיל מתויגים ("פנסיה 6%"), או כשכל ההודעה היא הערך המבוקש (שם תפקיד קצר, שם דגם קצר).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from app.modules.calculation.public import Drive
from app.modules.rules.public import CatalogCar, RulesSnapshot, catalog_names, matched_name, mentions, words

from .llm.port import ToolCall
from .parsing import (
    NUMBER_RE,
    approx_spans,
    plain_decimal,
    read_number,
    copay_span,
    drive_span,
    find_year,
    is_approximate,
    leftover_words,
    match_group,
    negates,
    no_car_span,
    none_span,
    parse_copay,
    read_profile,
    strip_year,
    unknown_span,
    unread_words,
    year_spans,
)
from .stage import ConversationState, current_group
from .tools import OPT_OUT_WORDS, available_options

_RANK_RE = re.compile(r"הכי\s*משתלם|הכי\s*טוב|תמליץ|המלצ|דרג", re.IGNORECASE)
_NOT_THIS_CAR_RE = re.compile(r"לא\s+(?:זה\s+)?(?:הרכב|הדגם)|(?:זה|זו)\s+לא\s+(?:נכון|הוא|היא)")
# בזמן שאוספים את פרטי הרכב הנוכחי, הודעה שמזכירה רכב אחר היא תיקון — לא תשובה לשאלה
_CAR_DETAIL_QUESTIONS = {"copay", "use_value", "drive", "maintenance", "car_choice"}
_ONLY_NUMBERS_RE = re.compile(r"^\s*\d+(?:\s*(?:,|ו-?|and|&)\s*\d+)*\s*[.!]?\s*$")
SHORT_MODEL_WORDS = 3     # תשובה קצרה בלי שם מוכר → כל ההודעה היא שם הדגם (גיבוי: חיפוש במאגר)
SHORT_TITLE_WORDS = 3     # תשובה קצרה לשאלת התפקיד → כל ההודעה היא שם התפקיד (גיבוי: עיסוק לא ידוע)

# שם השדה בשאלה → שם הארגומנט ב-save_profile
_PROFILE_ARG = {"occupation": "occupation", "group": "eligibility_group", "base": "base_salary",
                "commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
                "study_fund_percent": "study_fund_percent", "credit_points": "credit_points",
                "children": "children_under_23"}
_ZERO_ALLOWED = {"commissions", "bonus", "study_fund_percent", "children"}
# מילים שמתארות רכב שכבר זוהה בשמו ("אטו 3 החשמלי") — מותרות לצד השם בשאלת החלופות
_DRIVE_DESCRIPTORS = {
    Drive.ELECTRIC: ("חשמלי", "חשמלית", "electric", "ev"),
    Drive.HYBRID: ("היברידי", "היברידית", "hybrid"),
    Drive.PETROL: ("בנזין",),
    Drive.PLUG_IN: ("פלאג", "אין", "פלאגאין", "plug", "in"),
}


@dataclass(frozen=True)
class ParserPlan:
    calls: list[ToolCall] = field(default_factory=list)
    complete: bool = False                   # ההודעה נקראה במלואה — הקורא פועל, בלי מודל
    notice: Optional[str] = None             # משפט לפני השאלה הבאה, כשהקורא פועל
    fallback_notice: Optional[str] = None    # משפט לפני השאלה הבאה, כשהקריאות רצו כגיבוי
    direct: bool = False                     # תשובה לשאלה של הקוד (אישור, הבהרה, כפתור) — לא עוברת למודל
    guess: bool = False                      # "כל ההודעה היא הערך" (תפקיד/דגם קצר) — רק כשהמודל לא זמין


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-zא-ת]+", "", text.lower())


def _call(name: str, **args) -> list[ToolCall]:
    return [ToolCall(name=name, args=args, id=f"fallback_{name}")]


def single_value(text: str, fillers: Iterable[str] = ()) -> Optional[str]:
    """הערך בתשובה לשאלה בודדת: מספר אחד בדיוק, לא משוער, ושום דבר אחר מלבד מילות מילוי."""
    found = list(NUMBER_RE.finditer(text))
    if len(found) != 1:
        return None
    m = found[0]
    if is_approximate(text[:m.end()], text[m.end():]):
        return None                         # "בערך 18 וחצי", "כ-18,500" — לא כאן
    if unread_words(text, [m.span()], (), fillers):
        return None                         # "18,500 לפני מס" — יש עוד משמעות
    return m.group(0).replace(",", "")


def read_value(text: str, fillers: Iterable[str] = ()) -> Optional[tuple[str, bool]]:
    """תשובה מספרית לשאלה בודדת, גם כהערכה: (ערך, משוער?). מספר אחד בדיוק (עם יחידה כמו "אלף"),
    ושום דבר נוסף מלבד סימני הערכה ומילות מילוי. "18" לשכר נשמר כ-18 — טווח הסבירות שואל אם התכוונו לאלפים."""
    found = list(NUMBER_RE.finditer(text))
    if len(found) != 1:
        return None
    number = read_number(text, found[0])
    if unread_words(text, [number.span, *approx_spans(text)], (), fillers):
        return None
    return plain_decimal(number.value), number.approximate


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
    group, fields = current_group(state)
    t = text.strip()
    if not t or not fields:
        return ParserPlan()
    if len(fields) == 1:
        return _field_plan(state, snapshot, fields[0], t)
    return _grouped_plan(state, snapshot, group, fields, t)


def _field_plan(state: ConversationState, snapshot: RulesSnapshot, field_: str, t: str) -> ParserPlan:
    """שאלה על שדה אחד (או קריאה של ההודעה כתשובה לשדה אחד מתוך שאלה מקובצת)."""
    if field_ in _PROFILE_ARG or field_ == "opt_out_occupation":
        return _profile_plan(snapshot, "occupation" if field_ == "opt_out_occupation" else field_, t)
    if field_ == "car":
        return _car_plan(snapshot, t)
    if field_ in _CAR_DETAIL_QUESTIONS:
        correction = correction_plan(state, snapshot, t)
        if correction is not None:
            return correction
        return _car_detail_plan(snapshot, field_, t)
    if field_ == "options":
        return _option_plan(state, snapshot, t)
    return ParserPlan()


def _grouped_plan(state: ConversationState, snapshot: RulesSnapshot, group: Optional[str],
                  fields: list[str], t: str) -> ParserPlan:
    """שאלה מקובצת. לא מנחשים: (1) ב-Q3 רכב בשמו הוא הרכב של היום (השאלה שואלת עליו קודם), וההודעה
    נקראת יחד עם ההשתתפות וערכים מתויגים שנשארו מ-Q2;
    (2) ערכים מתויגים ("פנסיה 6%", "קבוצה ג") — כשההודעה נקראה במלואה; (3) אחרת ההודעה נקראת
    כתשובה לכל שדה בנפרד, ופועלים רק אם בדיוק שדה אחד קורא אותה במלואה ("300" כשחסרים רכב
    והשתתפות = השתתפות; "אין" כשחסרים עמלות ובונוס — שואלים שוב)."""
    car_guess = None
    if "car" in fields:
        plan = _car_plan(snapshot, t)
        if plan.complete and plan.calls:
            return plan
        cars = named_cars(t, snapshot.catalog)
        if cars:
            # רכב בשמו לפני שהרכב של היום ידוע — לא בוחרים אותו כחלופה
            return _q3_car_plan(snapshot, cars, t)
        car_guess = plan if plan.calls else None      # דגם קצר שלא בקטלוג — גיבוי, אם אף שדה אחר לא קורא
    profile_fields = [f for f in fields if f in _PROFILE_ARG]
    if profile_fields:
        plan = _profile_group_plan(snapshot, group, profile_fields, t)
        if plan is not None:
            return plan
    candidates = [p for f in fields if f != "car" for p in [_field_plan(state, snapshot, f, t)] if p.calls]
    complete = [p for p in candidates if p.complete]
    if len(complete) == 1:
        return complete[0]
    if not complete and len(candidates) == 1:
        return candidates[0]
    if not candidates and car_guess is not None:
        return car_guess
    return ParserPlan()


_NUMERIC_ARGS = {"base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points"}


def _add_free_number(text: str, arg: str, found: dict, spans: list, approximate: list) -> None:
    """המספר היחיד בהודעה שאינו שייך לערך מתויג — הוא הערך שהשאלה מבקשת."""
    free = [m for m in NUMBER_RE.finditer(text) if not any(a <= m.start() < b for a, b in spans)]
    if len(free) == 1:
        number = read_number(text, free[0])
        found[arg] = plain_decimal(number.value)
        spans.append(number.span)
        if number.approximate:
            approximate.append(arg)


def _group_reading(text: str, order: Iterable[str]) -> Optional[tuple[str, tuple[int, int]]]:
    """"קבוצה ג", "בקבוצת זכאות ב", או האות לבד כתשובה."""
    letters = "".join(order)
    m = re.search(rf"(?:ב|ו)?קבוצ(?:ה|ת)(?:\s*(?:ה)?זכאות)?\s*([{letters}])(?![א-ת])", text)
    if m:
        return m.group(1), m.span()
    # אות קבוצה כמילה בפני עצמה ("ג, שכר בסיס 18,500") — רק כשיש בדיוק אחת כזו
    lone = list(re.finditer(rf"(?<![א-ת])([{letters}])['׳]?(?![א-ת])", text))
    return (lone[0].group(1), lone[0].span()) if len(lone) == 1 else None


def _profile_group_plan(snapshot: RulesSnapshot, group: Optional[str], fields: list[str],
                        text: str) -> Optional[ParserPlan]:
    """ערכים מתויגים בשאלה מקובצת. ב-Q1 גם קבוצה, וסכום יחיד בלי תווית הוא שכר הבסיס
    (זה המספר היחיד שהשאלה מבקשת): "קבוצה ג, 14,200 ₪". None — אין כאן ערכים מתויגים."""
    ext = snapshot.extensions
    reading = read_profile(text, snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
    found = dict(reading.fields)
    spans = list(reading.spans)
    approximate = list(reading.approximate)
    if group == "Q1":
        hit = _group_reading(text, snapshot.calc.group_order)
        if hit and "occupation" not in found:
            found["eligibility_group"] = hit[0]
            spans.append(hit[1])
        if "base" in fields and "base_salary" not in found:
            _add_free_number(text, "base_salary", found, spans, approximate)
    if not found:
        return None
    if not unread_words(text, spans + approx_spans(text), reading.phrases, ext.profile_fillers):
        return ParserPlan(_call("save_profile", **found, approximate_fields=approximate), True)
    if reading.fields:
        return _labelled_fallback(reading.labelled)
    return ParserPlan()


# ---------- פרופיל ----------

def _profile_plan(snapshot: RulesSnapshot, question: str, text: str) -> ParserPlan:
    ext = snapshot.extensions
    reading = read_profile(text, snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
    asked = _PROFILE_ARG[question]
    if reading.fields:
        found, spans, approximate = dict(reading.fields), list(reading.spans), list(reading.approximate)
        if asked in _NUMERIC_ARGS and asked not in found:
            # "18,500, פנסיה 6%" כשנשאל רק השכר: המספר היחיד בלי תווית הוא הערך שנשאל
            _add_free_number(text, asked, found, spans, approximate)
        if not unread_words(text, spans + approx_spans(text), reading.phrases, ext.profile_fillers):
            return ParserPlan(_call("save_profile", **found, approximate_fields=approximate), True)
        return _labelled_fallback(reading.labelled)      # לא נקרא במלואו: רק ערכים מתויגים, כגיבוי
    if question == "occupation":
        hit = _group_reading(text, snapshot.calc.group_order)
        if hit and not unread_words(text, [hit[1]], (), ext.answer_fillers):
            return ParserPlan(_call("save_profile", eligibility_group=hit[0]), True)
        # תפקיד שאינו בטבלה: כל התשובה הקצרה היא שם התפקיד — גיבוי בלבד (המודל קודם)
        # "לא יודעת", "אין", "לא מנהלת" — אינם שם תפקיד
        # ברכה או שאלה ("שלום", "מה זה משנה?") — גם לא
        ws = words(text)
        not_a_title = (unknown_span(text) or none_span(text) or "לא" in ws or "?" in text
                       or any(w in ext.non_title_words for w in ws))
        if len(words(text)) <= SHORT_TITLE_WORDS and not any(ch.isdigit() for ch in text) and not not_a_title:
            return ParserPlan(_call("save_profile", occupation=text), False, guess=True)
        return ParserPlan()
    if question == "group":
        group = match_group(snapshot.calc.group_order, text)
        if group and not unread_words(text, (), [group], ext.answer_fillers):
            return ParserPlan(_call("save_profile", eligibility_group=group), True)
        return ParserPlan()
    if question in _ZERO_ALLOWED:
        span = none_span(text)
        if span and not unread_words(text, [span], (), ext.answer_fillers):
            return ParserPlan(_call("save_profile", **{asked: 0}), True)
    read = read_value(text, ext.answer_fillers)
    if read is None:
        return ParserPlan()
    value, approximate = read
    if question == "children":
        if Decimal(value) != int(Decimal(value)) or approximate:
            return ParserPlan()                                # "2.5", "בערך 2" — שואלים שוב
        return ParserPlan(_call("save_profile", children_under_23=int(Decimal(value))), True)
    return ParserPlan(_call("save_profile", **{asked: value},
                            approximate_fields=[asked] if approximate else []), True)


_LABEL_ORDER = ("base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent",
                "credit_points", "children_under_23")


def _labelled_fallback(labelled: dict[str, object]) -> ParserPlan:
    """רק ערכים מתויגים חד-משמעיים; התשובה אומרת מה נרשם (המספרים — מההודעה)."""
    if not labelled:
        return ParserPlan()
    from .templates import money

    def text(f: str, v: object) -> str:
        zero = v == 0
        return {
            "base_salary": lambda: f"שכר בסיס {money(v)}",
            "commissions": lambda: "אין עמלות" if zero else f"עמלות {money(v)}",
            "bonus": lambda: "אין בונוס" if zero else f"בונוס {money(v)}",
            "pension_percent": lambda: f"פנסיה {Decimal(str(v)).normalize():f}%",
            "study_fund_percent": lambda: "אין קרן השתלמות" if zero else f"קרן השתלמות {Decimal(str(v)).normalize():f}%",
            "credit_points": lambda: f"{Decimal(str(v)).normalize():f} נקודות זיכוי",
            "children_under_23": lambda: "אין ילדים" if zero else f"{v} ילדים",
        }[f]()

    items = [text(f, labelled[f]) for f in _LABEL_ORDER if f in labelled]
    notice = f"רשמתי: {', '.join(items)}. אם משהו לא נכון, כתבו את התיקון."
    return ParserPlan(_call("save_profile", **labelled), False, fallback_notice=notice)


# ---------- הרכב הנוכחי ----------

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
    """הרכב הנוכחי ממשפט חופשי: שם מוכר מהקטלוג (+שנה, +השתתפות). אף פעם לא משפט שלם כשם דגם,
    ושם שנמצא בתוך משפט עם מילים נוספות — לא פועלים עליו (גם לא כגיבוי)."""
    ext = snapshot.extensions
    fillers = ext.car_message_fillers + ext.correction_cues
    span = no_car_span(text)
    if span:
        if not unread_words(text, [span], (), fillers, leading_no=True):
            return ParserPlan(_call("match_current_car", no_car=True), True)
        return ParserPlan()
    if negates(text):
        return ParserPlan()     # "זה לא קורולה קרוס" — לא מתאימים דבר; המודל או שאלה חוזרת
    year = find_year(text)
    copay = parse_copay(text)
    cars = named_cars(text, snapshot.catalog)
    if cars:
        if leftover_words(text, matched_name(text, cars[0]) or cars[0].model_name, fillers):
            return ParserPlan()                     # יש עוד משמעות ("הקרוסאובר", "הגבוהה") — המודל
        calls = _call("match_current_car", model=cars[0].model_name, registration_year=year)
        complete = True
    else:
        model = strip_year(text)
        if len(model.split()) > SHORT_MODEL_WORDS:
            return ParserPlan()     # משפט בלי שם מוכר → התבנית שואלת שוב על הדגם בלבד
        # כל התשובה הקצרה היא שם הדגם — גיבוי בלבד (המודל אולי מכיר את השם)
        calls, complete = _call("match_current_car", model=model, registration_year=year), False
    if copay is not None:
        calls += _call("save_profile", current_copay=str(copay))
    return ParserPlan(calls, complete, guess=not complete)


def _q3_car_plan(snapshot: RulesSnapshot, cars: list[CatalogCar], text: str) -> ParserPlan:
    """תשובה מלאה ל-Q3: הרכב של היום (+שנה, +השתתפות) יחד עם ערכים מתויגים שנשארו מ-Q2
    ("אין בונוס, 2 ילדים, יש לי קורולה קרוס ואני משתתפת ב-300"). הכלל הוא של כל ההודעה:
    פועלים רק כשלא נשאר בה דבר שלא נקרא."""
    ext = snapshot.extensions
    if len(cars) != 1 or negates(text):
        return ParserPlan()
    car = cars[0]
    reading = read_profile(text, snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
    copay = parse_copay(text)
    spans = [*reading.spans, *approx_spans(text), copay_span(text), *year_spans(text)]
    phrases = [*reading.phrases, matched_name(text, car) or car.model_name]
    fillers = [*ext.profile_fillers, *ext.car_message_fillers, *ext.correction_cues]
    if unread_words(text, spans, phrases, fillers, leading_no=True):
        return ParserPlan()
    calls = _call("match_current_car", model=car.model_name, registration_year=find_year(text))
    profile = dict(reading.fields)
    if copay is not None:
        profile["current_copay"] = str(copay)
    if profile:
        calls += _call("save_profile", **profile, approximate_fields=list(reading.approximate))
    return ParserPlan(calls, True)


def _car_detail_plan(snapshot: RulesSnapshot, question: str, text: str) -> ParserPlan:
    ext = snapshot.extensions
    fillers = ext.answer_fillers + ext.car_message_fillers

    def only(span) -> bool:
        return span is not None and not unread_words(text, [span], (), fillers)

    if question == "car_choice":
        value = single_value(text, fillers)
        return ParserPlan(_call("match_current_car", choice_number=int(Decimal(value))), True) if value else ParserPlan()
    if question == "drive":
        hit = drive_span(text)
        if hit and only(hit[1]):
            return ParserPlan(_call("save_profile", current_drive=hit[0].value), True)
        return ParserPlan()
    if question in ("use_value", "copay") and only(unknown_span(text)):
        flag = "current_use_value_unknown" if question == "use_value" else "current_copay_unknown"
        return ParserPlan(_call("save_profile", **{flag: True}), True)
    if question == "copay":
        span = copay_span(text)
        if span is not None:
            return ParserPlan(_call("save_profile", current_copay=str(parse_copay(text))), True) if only(span) \
                else ParserPlan()
        if only(none_span(text)):
            return ParserPlan(_call("save_profile", current_copay="0"), True)
    arg = {"use_value": "current_use_value", "copay": "current_copay", "maintenance": "current_maintenance"}[question]
    read = read_value(text, fillers)
    if read is None:
        return ParserPlan()
    value, approximate = read
    return ParserPlan(_call("save_profile", **{arg: value}, approximate_fields=[arg] if approximate else []), True)


# ---------- חלופות ----------

def _descriptors(car: CatalogCar, brands: dict[str, list[str]]) -> list[str]:
    """מילים שמתארות את הרכב שזוהה: ההנעה שלו והמותג של היצרן שלו."""
    words_ = list(_DRIVE_DESCRIPTORS.get(car.drive, ()))
    for prefix, names in brands.items():
        if car.manufacturer.startswith(prefix):
            words_ += names
    return words_


def _option_plan(state: ConversationState, snapshot: RulesSnapshot, text: str) -> ParserPlan:
    ext = snapshot.extensions
    options = available_options(snapshot, state)
    available = {o.id for o in options}
    fillers = list(ext.option_message_fillers)

    rank = _RANK_RE.search(text)
    if rank:
        if not unread_words(text, [rank.span()], (), fillers):
            return ParserPlan(_call("rank_options"), True)
        return ParserPlan()

    # מספר לבד = בחירה מהרשימה, רק כשההודעה היא מספרים בלבד ("2", "1, 3"); "לבדוק 2 רכבים" אינו בחירה
    if _ONLY_NUMBERS_RE.match(text):
        numbers = [int(m) for m in re.findall(r"\d+", text)]
        outside = [n for n in numbers if not 1 <= n <= len(options)]
        if outside:
            return ParserPlan(complete=True, notice=(
                f"אין חלופה מספר {', '.join(map(str, outside))}. "
                f"בחרו מספר בין 1 ל-{len(options)}, או כתבו את שם הרכב."))
        return ParserPlan(_call("calculate", options=[options[n - 1].id for n in numbers]), True)

    # שמות מול כל הקטלוג (הארוך קודם), ורק אז החלופות הזמינות: "קורולה קרוס" (הרכב הנוכחי)
    # חוסם את "קורולה" שבתוכו, ולא נבחר שום דבר בטעות
    cars = named_cars(text, snapshot.catalog)
    opt_out = [m.span() for w in OPT_OUT_WORDS for m in re.finditer(re.escape(w), text.lower())]
    if not cars and not opt_out:
        return ParserPlan()
    if any(c.catalog_code not in available for c in cars):
        return ParserPlan()                     # רכב שאינו חלופה (הנוכחי, קבוצה גבוהה) — המודל או שאלה
    phrases = [matched_name(text, c) or c.model_name for c in cars]
    for car in cars:
        fillers += _descriptors(car, ext.manufacturer_brands)
    if unread_words(text, opt_out, phrases, fillers):
        return ParserPlan()                     # יש עוד משמעות — לא פועלים על קריאה חלקית
    codes = {c.catalog_code for c in cars} | ({"OPT_OUT"} if opt_out else set())
    chosen = [o.id for o in options if o.id in codes]          # בסדר של הרשימה
    if not chosen:
        return ParserPlan()
    return ParserPlan(_call("calculate", options=chosen), True)
