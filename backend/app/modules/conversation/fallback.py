"""קורא ההודעות הדטרמיניסטי.

כלל אחד לכל השאלות: הקורא פועל רק כשקרא את ההודעה במלואה — כשלא נשאר בה דבר בעל משמעות אחרי
מה שזיהה (שמות, מספרים, מילים מוכרות) ומילות המילוי של השאלה (extensions.yaml). אחרת ההודעה עוברת
למודל השפה; ואם אין מודל או שלא קרא לכלי — הבוט שואל שוב ולא משנה דבר.

לקריאות של תוכנית שלא נקראה במלואה יש שימוש אחד בלבד — גיבוי כשאין מודל, ורק כשהערך חד-משמעי:
ערכי פרופיל מתויגים ("פנסיה 6%"), או כשכל ההודעה היא הערך המבוקש (שם תפקיד קצר, שם דגם קצר).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Iterable, Optional

from app.modules.calculation.public import Drive
from app.modules.rules.public import (CarRoleCues, CatalogCar, RulesSnapshot, catalog_names, matched_name,
                                      mentions, words)

from .llm.port import ToolCall
from .parsing import (
    NAME_PREFIX_LETTERS,
    NUMBER_RE,
    approx_spans,
    plain_decimal,
    read_number,
    copay_span,
    copay_unknown_span,
    drive_span,
    find_year,
    has_all_scope,
    is_approximate,
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
from .stage import Q2_FIELDS, CarClarification, ConversationState, current_group
from .templates import money, paced, recorded_text   # recorded_text: re-exported (agent, public)
from .tools import OPT_OUT_WORDS, available_options

_RANK_RE = re.compile(r"(?:מה\s+)?הכי\s*(?:משתלם|כדאי|טוב)(?:\s+לי)?|תמליץ|המלצ|דרג", re.IGNORECASE)
_NOT_THIS_CAR_RE = re.compile(r"לא\s+(?:זה\s+)?(?:הרכב|הדגם)|(?:זה|זו)\s+לא\s+(?:נכון|הוא|היא)"
                              r"|(?<![א-ת])ל?(?:רכב|דגם)\s+אחר(?![א-ת])")
# בזמן שאוספים את פרטי הרכב הנוכחי, הודעה שמזכירה רכב אחר היא תיקון — לא תשובה לשאלה
_CAR_DETAIL_QUESTIONS = {"copay", "use_value", "drive", "maintenance", "car_choice"}
_ONLY_NUMBERS_RE = re.compile(r"^\s*\d+(?:\s*(?:,|ו-?|and|&)\s*\d+)*\s*[.!]?\s*$")
# a blanket answer to a grouped question: we do not split it silently — we ask one field at a time
SCOPE_CLARIFY = "בואו נעבור על זה אחד-אחד, כדי לא לפספס."
SHORT_MODEL_WORDS = 3     # תשובה קצרה בלי שם מוכר → כל ההודעה היא שם הדגם (גיבוי: חיפוש במאגר)
SHORT_TITLE_WORDS = 3     # תשובה קצרה לשאלת התפקיד → כל ההודעה היא שם התפקיד (גיבוי: עיסוק לא ידוע)

# שם השדה בשאלה → שם הארגומנט ב-save_profile
_PROFILE_ARG = {"occupation": "occupation", "group": "eligibility_group", "base": "base_salary",
                "commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
                "study_fund_percent": "study_fund_percent", "credit_points": "credit_points",
                "children": "children_under_23"}
ZERO_ALLOWED = {"commissions", "bonus", "study_fund_percent", "children"}
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
    clarify: Optional[CarClarification] = None   # רכב שלא ברור אם הוא של היום או חלופה — שואלים
    acked: bool = False                      # the notice already names every value saved (no second "רשמתי")
    asks: Optional[str] = None               # the notice IS the question, about this save_profile field
    clarify_scope: bool = False              # a blanket answer ("לא ידוע", "אין לי כלום", "הכל 5") to a
    #   grouped Q2: too broad to split silently — save nothing, switch to one field at a time, ask again


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
    fields = paced(state, fields)        # one-at-a-time: only the field on screen is answered / scoped
    t = text.strip()
    if not t or not fields:
        return ParserPlan()
    correction = _group_correction_plan(state, snapshot, t)
    if correction is not None:
        return correction
    value_correction = _profile_value_correction_plan(state, snapshot, t)
    if value_correction is not None:
        return value_correction
    car_correction = _car_correction_plan(state, snapshot, t)
    if car_correction is not None:
        return car_correction
    combined = _profile_and_no_car_plan(state, snapshot, t)
    if combined is not None:
        return combined
    unknown = _unknown_plan(snapshot, fields, t, group)
    if unknown is not None:
        return unknown
    bound = _bound_value_plan(state, snapshot, t)
    if bound is not None:
        return bound
    return _fallback_plan_rest(state, snapshot, group, fields, t)


# the one profile field the last reply asked about (state.asked_field) → a bare value answers it
_BOUND_ARGS = {"base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points",
               "children_under_23"}
_FIRST_LINE_RE = re.compile(r"^\s*(.+?)\s*(?:(?:\n|;|,(?!\s*\d))\s*(.*))?$", re.S)   # "14,200" / "2,25": one number


def _bound_value_plan(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[ParserPlan]:
    """A bare value as the whole message, or as its first line ("3\nיש קורולה…"), answers the one field the
    last question asked (state.asked_field), even inside a grouped question. The rest of the message is read
    as usual. Never remapped to another field."""
    arg = state.asked_field
    if arg not in _BOUND_ARGS:
        return None
    m = _FIRST_LINE_RE.match(text)
    if m is None:
        return None
    head, rest = m.group(1), (m.group(2) or "").strip()
    read = read_value(head, snapshot.extensions.answer_fillers)
    if read is None:
        return None
    value, approximate = read
    if arg == "children_under_23":
        if approximate or Decimal(value) != int(Decimal(value)):
            return None
        value = int(Decimal(value))
    calls = _call("save_profile", **{arg: value}, approximate_fields=[arg] if approximate else [])
    if not rest:
        return ParserPlan(calls, True)
    others = _fallback_plan_rest(state, snapshot, *current_group(state), rest)
    return ParserPlan([*calls, *others.calls], others.complete, notice=others.notice,
                      fallback_notice=others.fallback_notice, direct=others.direct, clarify=others.clarify)


def _profile_and_no_car_plan(state: ConversationState, snapshot: RulesSnapshot,
                             text: str) -> Optional[ParserPlan]:
    """One natural sentence that states both profile data AND "no company car" ("אני בקבוצה ב ואין לי רכב
    היום"): read the group / salary fields and set no-car together. Only when the car is not yet known and
    no catalog car is named (a named car is handled by the Q3 car reader). Any stage — a no-car fact is
    valid whenever stated."""
    c = state.current
    if c.source is not None or c.query is not None:
        return None                                  # the car situation is already known
    span = no_car_span(text)
    if span is None or named_cars(text, snapshot.catalog):
        return None                                  # no clear "no car", or a car is named
    ext = snapshot.extensions
    calls: list[ToolCall] = []
    read_spans = [span]
    # read any profile fields stated in the same sentence (group in Q1, labelled Q2 values)
    reading = read_profile(text, snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
    found = dict(reading.fields)
    read_spans += list(reading.spans)
    hit = _group_reading(text, snapshot.calc.group_order)
    if hit and "occupation" not in found and state.profile.group is None:
        found["eligibility_group"] = hit[0]
        read_spans.append(hit[1])
    # only act if the remainder (beyond what we read + the no-car span) is just fillers — never guess
    fillers = [*ext.profile_fillers, *ext.car_message_fillers, *ext.answer_fillers, "אני", "ו", "גם", "היום"]
    if unread_words(text, read_spans, reading.phrases, fillers, leading_no=True):
        return None
    # only the COMBINED case is handled here (profile data + no-car in one sentence); a bare "אין רכב" is
    # left to the normal car reader, so existing single-fact flows are unchanged
    if not found:
        return None
    calls.append(ToolCall("save_profile", {**found, "approximate_fields": list(reading.approximate)},
                          "combined"))
    calls.append(ToolCall("match_current_car", {"no_car": True}, "combined"))
    return ParserPlan(calls, True, direct=True)


def _group_correction_plan(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[ParserPlan]:
    """Correcting the eligibility group after it was set ("טעיתי, אני בקבוצה ד"), when it is not already
    the question on screen. Only an explicit "קבוצה X" restatement (not a bare letter, which is ambiguous
    once the group is known). Rejected by save_profile if an occupation determines the group — correct, the
    rules decide it then."""
    p = state.profile
    if p.group is None or p.occupation is not None:
        return None                              # nothing set yet, or the occupation table owns the group
    if "group" in current_group(state)[1]:
        return None                              # the group is already being asked — the normal path handles it
    hit = _group_reading(text, snapshot.calc.group_order)
    if hit is None:
        return None
    new_group, span = hit
    # must be the explicit "קבוצה X" form (a lone letter is too ambiguous once a group is known), and the
    # message must carry nothing else to act on besides fillers / a correction cue
    if not re.search(r"קבוצ(?:ה|ת)", text):
        return None
    if new_group == p.group:
        return None                              # same value — not a change
    ext = snapshot.extensions
    fillers = [*ext.answer_fillers, *ext.correction_cues, "אני", "אבל", "זה", "למעשה", "בעצם"]
    if unread_words(text, [span], (), fillers):
        return None
    return ParserPlan(_call("save_profile", eligibility_group=new_group), True, direct=True,
                      notice=f"עדכנתי: קבוצה {new_group}.")


# save_profile arg name → the Profile attribute it corrects (for a value correction after it was set)
_CORRECTABLE = {"base_salary": "base", "commissions": "commissions", "bonus": "bonus",
                "pension_percent": "pension_percent", "study_fund_percent": "study_fund_percent",
                "credit_points": "credit_points", "children_under_23": "children"}


def _profile_value_correction_plan(state: ConversationState, snapshot: RulesSnapshot,
                                   text: str) -> Optional[ParserPlan]:
    """Correcting a profile value after it was already saved ("טעיתי, השכר שלי 16,000", "בעצם פנסיה 7%"),
    at any stage — including after a result is on screen. Needs an explicit correction cue and a labelled
    value for a field that already has a (different) value; the whole message must carry nothing else to
    act on. save_profile clears calculated_for, so the net is recomputed with the corrected value."""
    ext = snapshot.extensions
    if not _has_correction_cue(text, ext.correction_cues):
        return None
    reading = read_profile(text, snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
    p = state.profile
    changes: dict[str, object] = {}
    for arg, attr in _CORRECTABLE.items():
        if arg not in reading.fields:
            continue
        current = getattr(p, attr)
        if current is None:
            continue                                 # not a correction — the field was never set
        new = reading.fields[arg]
        same = (current == int(new)) if attr == "children" else (Decimal(str(current)) == Decimal(str(new)))
        if same:
            continue                                 # same value — nothing to change
        changes[arg] = new
    if not changes:
        return None
    # nothing else of meaning beyond what we read, the correction cue, and plain fillers
    fillers = [*ext.answer_fillers, *ext.profile_fillers, *ext.correction_cues, "שלי", "הוא", "היא", "אבל",
               "למעשה", "בעצם", "זה", "הם"]
    if unread_words(text, reading.spans, reading.phrases, fillers):
        return None
    def _label(arg: str, v: object) -> str:
        names = {"base_salary": "שכר בסיס", "commissions": "עמלות", "bonus": "בונוס",
                 "pension_percent": "פנסיה", "study_fund_percent": "קרן השתלמות",
                 "credit_points": "נקודות זיכוי", "children_under_23": "ילדים"}
        pct = arg in ("pension_percent", "study_fund_percent")
        return f"{names[arg]} {v}%" if pct else f"{names[arg]} {money(Decimal(str(v)))}" if arg == "base_salary" else f"{names[arg]} {v}"
    notice = "עדכנתי: " + ", ".join(_label(a, v) for a, v in changes.items()) + "."
    # tagged "combined" so the stage gate is relaxed: a correction is a valid fact at any stage, including
    # after a result is on screen (save_profile then clears calculated_for and the net is recomputed).
    # fallback_notice (not notice) so the generic "רשמתי" acknowledgement built from the saved state is not
    # duplicated — the state-based confirmation line is the single source of truth for what changed.
    return ParserPlan([ToolCall("save_profile", changes, "combined")], True, direct=True,
                      fallback_notice=notice)


def _says_mine(text: str, cues: Iterable[str]) -> bool:
    """A correction word, or "יש לי" / "הרכב שלי": the car named is the employee's car today."""
    return _has_correction_cue(text, cues) or "יש לי" in text or "הרכב שלי" in text


def _car_correction_plan(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[ParserPlan]:
    """Correcting the car today once it is known, at any stage (also after a result): "התכוונתי לרכב אחר" /
    "זה לא הרכב שלי" resets only the model (the copay and the fact that there is a car stay); another
    catalog car with a correction word or "יש לי" is matched instead. Tagged "combined": valid at any stage."""
    c = state.current
    if c.source not in ("catalog", "tax", "stated"):
        return None
    ext = snapshot.extensions
    cars = [x for x in named_cars(text, snapshot.catalog) if x.catalog_code != c.catalog_code]
    if not cars:
        m = _NOT_THIS_CAR_RE.search(text)
        fillers = [*ext.answer_fillers, *ext.correction_cues, *ext.car_message_fillers,
                   "התכוונתי", "שלי", "זה", "זו", "בעצם", "למעשה", "סליחה"]
        if m is None or unread_words(text, [m.span()], (), fillers, leading_no=True):
            return None
        return ParserPlan([ToolCall("match_current_car", {"reset": True}, "combined")], True, direct=True)
    if not _says_mine(text, ext.correction_cues):
        return None
    plan = _car_plan(snapshot, text)
    if not (plan.complete and plan.calls):
        return None
    return ParserPlan([ToolCall(x.name, x.args, "combined") for x in plan.calls], True, direct=True)


def _has_correction_cue(text: str, cues: Iterable[str]) -> bool:
    """A correction word among the first few words of the message ("טעיתי, ...", "בעצם ...")."""
    first = words(text)[:3]
    cue_tokens = {w for c in cues for w in words(c)}
    return any(w in cue_tokens for w in first)


# words that name a Q2 field ("לא יודעת כמה נקודות זיכוי")
_FIELD_WORDS = {"עמלות": "commissions", "עמלה": "commissions", "בונוס": "bonus", "בונוסים": "bonus",
                "פנסיה": "pension_percent", "השתלמות": "study_fund_percent", "זיכוי": "credit_points",
                "ילדים": "children"}


def _unknown_plan(snapshot: RulesSnapshot, fields: list[str], text: str,
                  group: Optional[str] = None) -> Optional[ParserPlan]:
    """"לא יודע" when the question on screen asks only Q2 fields: recorded per field as unknown (never a
    value).
      - a named field ("לא יודעת כמה נקודות זיכוי")      → only that field.
      - one Q2 field on screen                           → that field.
      - several on screen + a sweeping scope ("לא יודע כלום", "לא יודע אף אחד") → all of them.
      - several on screen, no scope ("לא ידוע", "לא ידוע לי") → AMBIGUOUS: save nothing, ask one at a time.
    """
    if not fields or any(f not in Q2_FIELDS for f in fields):
        return None
    span = unknown_span(text)
    if span is None:
        return None
    named: list[str] = []
    for w in words(text):
        f = _FIELD_WORDS.get(w) or (_FIELD_WORDS.get(w[1:]) if len(w) > 1 else None)
        if f in fields and f not in named:
            named.append(f)
    ext = snapshot.extensions
    fillers = [*ext.answer_fillers, *ext.profile_fillers, *_FIELD_WORDS, "כמה", "נקודות", "את", "מה", "שלי"]
    # "כלום"/"אף אחד"/"הכל" are allowed extra words when they set the all-of-them scope
    scope_words = ["כלום", "אף", "אחד", "אחת", "הכל", "כולם", "כולן", "שום", "דבר", "פרט", "רכיב",
                   "מהם", "מהן", "מאלה", "מאלו", "אלה", "אלו", "האלה", "האלו", "עדיין", "עוד"]
    if unread_words(text, [span], (), [*fillers, *scope_words]):
        return None
    # the "unknown" group is shown when the only fields left are ones the employee already said they do not
    # know. Repeating "לא יודע" there is understood (acknowledged, pointing to the payslip), never a failure
    # and never a demand to answer again (rule 5).
    if group == "unknown":
        return ParserPlan([], True, direct=True)
    if named:
        return ParserPlan(_call("save_profile", unknown_fields=named), True, direct=True)
    if len(fields) == 1:
        return ParserPlan(_call("save_profile", unknown_fields=[fields[0]]), True, direct=True)
    if has_all_scope(text):                 # "לא יודע כלום / אף אחד מהם" — explicitly all of them
        return ParserPlan(_call("save_profile", unknown_fields=[f for f in fields]), True, direct=True)
    # a bare "לא ידוע" on a grouped question: ambiguous — do not spread. Ask one field at a time.
    return ParserPlan([], True, direct=True, clarify_scope=True, notice=SCOPE_CLARIFY)


def _fallback_plan_rest(state: ConversationState, snapshot: RulesSnapshot, group: Optional[str],
                        fields: list[str], t: str) -> ParserPlan:
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
    """שאלה מקובצת. לא מנחשים: (1) ב-Q3 תפקיד כל רכב בשמו (של היום / חלופה) נקבע לפי מילים, וההודעה
    נקראת יחד עם ההשתתפות וערכים מתויגים שנשארו מ-Q2;
    (2) ערכים מתויגים ("פנסיה 6%", "קבוצה ג") — כשההודעה נקראה במלואה; (3) אחרת ההודעה נקראת
    כתשובה לכל שדה בנפרד, ופועלים רק אם בדיוק שדה אחד קורא אותה במלואה ("300" כשחסרים רכב
    והשתתפות = השתתפות; "אין" כשחסרים עמלות ובונוס — שואלים שוב)."""
    if group == "Q2" or all(f in Q2_FIELDS for f in fields):    # גם כשכל מה שנשאר הוא מ-Q2 (אחרי הרכב והחלופות)
        # "הכל 5" — a sweeping value for a whole group of fields: too broad to spread one number across
        # several fields. Save nothing; ask them one at a time.
        if len([f for f in fields if f in Q2_FIELDS]) > 1 and has_all_scope(t):
            return ParserPlan([], True, direct=True, clarify_scope=True, notice=SCOPE_CLARIFY)
        plan = _bare_none_plan(snapshot, fields, t)
        if plan is not None:
            return plan
    car_guess = None
    if "car" in fields:
        cars = named_cars(t, snapshot.catalog)
        if cars:
            return _q3_car_plan(state, snapshot, fields, cars, t)
        plan = _car_plan(snapshot, t)
        if plan.complete and plan.calls:
            return plan                   # "אין רכב"
        car_guess = plan if plan.calls else None      # דגם קצר שלא בקטלוג — גיבוי, אם אף שדה אחר לא קורא
    profile_fields = [f for f in fields if f in _PROFILE_ARG]
    if profile_fields:
        plan = _profile_group_plan(snapshot, group, profile_fields, t)
        if plan is not None:
            return plan
    # אחרי ש-Q3 הוצגה, מה שנשאר מ-Q2 כבר לא נשאל בהודעה (בלי זנב): ערך בלי תווית ("300") עונה על
    # השאלה שמוצגת, ולא מתחרה בשדות שלא נשאלו. ערך מתויג ("פנסיה 6%") נקרא למעלה כרגיל.
    hidden = (group == "Q3" and "Q3" in state.asked_groups
              and any(f not in Q2_FIELDS for f in fields))
    single = [f for f in fields if f != "car" and not (hidden and f in Q2_FIELDS)]
    candidates = [p for f in single for p in [_field_plan(state, snapshot, f, t)] if p.calls]
    complete = [p for p in candidates if p.complete]
    if len(complete) == 1:
        return complete[0]
    if not complete and len(candidates) == 1:
        return candidates[0]
    if not candidates and car_guess is not None:
        return car_guess
    return ParserPlan()


def _bare_none_plan(snapshot: RulesSnapshot, fields: list[str], text: str) -> Optional[ParserPlan]:
    """"אין" לבד כתשובה ל-Q2: אפס בשדות שבהם אפס הוא ערך סביר (עמלות, בונוס, קרן השתלמות, ילדים).
    פנסיה ונקודות זיכוי לא מקבלות ערך — הן נשאלות שוב במפורש.
    אבל "אין לי כלום" על שאלה מקובצת הוא מענה גורף ועמום — לא מאפסים בשקט כמה שדות; שואלים אחד-אחד."""
    span = none_span(text)
    if not span:
        return None
    multi = len([f for f in fields if f in Q2_FIELDS]) > 1
    if multi and has_all_scope(text):            # "אין לי כלום" — too broad to split across the group
        return ParserPlan([], True, direct=True, clarify_scope=True, notice=SCOPE_CLARIFY)
    if unread_words(text, [span], (), snapshot.extensions.answer_fillers):
        return None
    zero = [f for f in fields if f in ZERO_ALLOWED]
    return none_plan(zero) if zero else None


def none_plan(fields: Iterable[str]) -> ParserPlan:
    """אפס בשדות הפרופיל שנאמר עליהם "אין", עם שורה שאומרת מה נרשם. תשובה לשאלה של הקוד (direct)."""
    zero = {_PROFILE_ARG[f]: 0 for f in fields}
    return ParserPlan(_call("save_profile", **zero), True, notice=f"רשמתי: {recorded_text(zero)}.", direct=True)


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
    m = re.search(rf"(?:ב|ו|ה)?קבוצ(?:ה|ת)(?:\s*(?:ה)?זכאות)?\s*(?:שלי\s*)?(?:היא\s*)?([{letters}])(?![א-ת])",
                  text)
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
    fillers = list(ext.profile_fillers)
    if group == "Q1":
        hit = _group_reading(text, snapshot.calc.group_order)
        if hit and "occupation" not in found:
            found["eligibility_group"] = hit[0]
            spans.append(hit[1])
            fillers += ["היא", "הוא", "בלבד", "הקבוצה", "קבוצת", "זכאות", "שלי"]   # group-phrasing words
        if "base" in fields and "base_salary" not in found:
            _add_free_number(text, "base_salary", found, spans, approximate)
    if not found:
        return None
    if not unread_words(text, spans + approx_spans(text), reading.phrases, fillers):
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
    if question in ZERO_ALLOWED:
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


def _labelled_fallback(labelled: dict[str, object]) -> ParserPlan:
    """רק ערכים מתויגים חד-משמעיים; התשובה אומרת מה נרשם (המספרים — מההודעה)."""
    if not labelled:
        return ParserPlan()
    notice = f"רשמתי: {recorded_text(labelled)}. אם משהו לא נכון, כתבו את התיקון."
    return ParserPlan(_call("save_profile", **labelled), False, fallback_notice=notice)


# ---------- הרכב הנוכחי ----------

def correction_plan(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[ParserPlan]:
    """תיקון הרכב שזוהה: שם רכב אחר מהקטלוג → התאמה מחדש (ההשתתפות שכבר נמסרה נשמרת);
    "זה לא הרכב" בלי דגם → מוחקים את ההתאמה ושואלים שוב. אחרת None — זו תשובה לשאלה."""
    cars = named_cars(text, snapshot.catalog)
    if state.current.source == "none" and cars and not _says_mine(text, snapshot.extensions.correction_cues):
        # "אין לי רכב" was said: a catalog car named now is an alternative to check, never the car today
        available = {o.id for o in available_options(snapshot, state)}
        codes = [x.catalog_code for x in cars]
        if state.selected or not all(code in available for code in codes):
            return ParserPlan()
        return ParserPlan([ToolCall("select_options", {"options": codes}, "combined")], True, direct=True)
    if cars and cars[0].catalog_code != state.current.catalog_code:
        plan = _car_plan(snapshot, text)          # כולל בדיקת "לא" באמצע ומילים נוספות
        # a correction of the car today is valid at any stage ("combined"), like the reset below
        return replace(plan, calls=[ToolCall(c.name, c.args, "combined") for c in plan.calls])
    if not cars and _NOT_THIS_CAR_RE.search(text):
        return ParserPlan([ToolCall("match_current_car", {"reset": True}, "combined")], True)
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
    unknown_copay = copay_unknown_span(text) if copay is None else None
    cars = named_cars(text, snapshot.catalog)
    if cars:
        # the manufacturer's name next to its model ("קיה נירו", "טויוטה קורולה") is not extra meaning
        car_fillers = fillers + _descriptors(cars[0], ext.manufacturer_brands) + ["אני", "עליו", "עליה"]
        name = matched_name(text, cars[0]) or cars[0].model_name
        if unread_words(text, [copay_span(text), unknown_copay, *year_spans(text)], [name], car_fillers,
                        leading_no=True):
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
    elif unknown_copay is not None:
        calls += _call("save_profile", current_copay_unknown=True)
    return ParserPlan(calls, complete, guess=not complete)


# משפטי-משנה: פסיק (לא בתוך "14,200"), נקודה (לא בתוך "2.5"), נקודה-פסיק, שורה חדשה
_CLAUSE_RE = re.compile(r"[;\n]|,(?!\d{3})|\.(?!\d)")


def _phrase_at(ws: list[str], phrase: str) -> list[tuple[int, int]]:
    """היכן הביטוי מופיע ברשימת המילים (מילים שלמות; אות שימוש אחת לפניו). ביטוי שמסתיים באות אחת
    ("לעבור ל") — האות היא תחילת המילה הבאה ("לעבור לאטו 3")."""
    tokens = words(phrase)
    tail = tokens.pop() if len(tokens) > 1 and len(tokens[-1]) == 1 else None
    target = "".join(tokens)
    hits = []
    for i in range(len(ws)):
        for j in range(i + 1, len(ws) + 1):
            joined = "".join(ws[i:j])
            if joined == target or (joined[0] in NAME_PREFIX_LETTERS and joined[1:] == target):
                if tail is None or (j < len(ws) and ws[j].startswith(tail)):
                    hits.append((i, j))
                break
            if len(joined) > len(target) + 1:
                break
    return hits


def car_roles(text: str, cars: list[CatalogCar], cues: CarRoleCues) -> dict[str, Optional[str]]:
    """תפקיד כל רכב שבהודעה: "current" / "alternative" / None (בלי מילה, או מילים משני הסוגים).
    מילה נקשרת לרכב הראשון אחריה באותו משפט-משנה ("יש לי קורולה קרוס ורוצה לבדוק ATTO 3"),
    ואם אין אחריה רכב — לרכב שלפניה ("ATTO 3 מעניין אותי", "קורולה קרוס ואני משתתפת ב-300")."""
    found: dict[str, set[str]] = {c.catalog_code: set() for c in cars}
    for clause in _CLAUSE_RE.split(text):
        ws = words(clause)
        at = [(i, j, car.catalog_code) for car in cars for n in catalog_names(car) for i, j in _phrase_at(ws, n)]
        # שם שמוכל בשם ארוך יותר ("קורולה" בתוך "קורולה קרוס") — לא רכב נפרד
        at = sorted((i, j, c) for i, j, c in at if not any(a <= i and j <= b and (a, b) != (i, j) for a, b, _ in at))
        if not at:
            continue
        for role, phrases in (("current", cues.current), ("alternative", cues.alternative)):
            for phrase in phrases:
                for i, j in _phrase_at(ws, phrase):
                    after = [c for a, _, c in at if a >= j]
                    before = [c for _, b, c in at if b <= i]
                    if after or before:
                        found[after[0] if after else before[-1]].add(role)
    return {code: next(iter(roles)) if len(roles) == 1 else None for code, roles in found.items()}


def _q3_car_plan(state: ConversationState, snapshot: RulesSnapshot, fields: list[str],
                 cars: list[CatalogCar], text: str) -> ParserPlan:
    """תשובה ל-Q3 עם רכב בשמו, יחד עם ההשתתפות וערכים מתויגים שנשארו מ-Q2
    ("אין בונוס, 2 ילדים, יש לי קורולה קרוס ואני משתתפת ב-300"). הכלל הוא של כל ההודעה:
    פועלים רק כשלא נשאר בה דבר שלא נקרא.

    השאלה שואלת גם על הרכב של היום וגם על החלופות, ולכן תפקיד כל רכב נקבע במילים (car_role_cues),
    לא מנחשים: רכב בלי מילה ("ATTO 3") — שואלים אם זה הרכב של היום או חלופה."""
    ext = snapshot.extensions
    if negates(text):
        return ParserPlan()
    roles = car_roles(text, cars, ext.car_role_cues)
    year = find_year(text)
    copay = parse_copay(text)
    unknown_copay = copay_unknown_span(text) if copay is None else None
    # רכב יחיד בלי מילה: השתתפות שנמסרה היא תמיד על הרכב של היום ("קורולה קרוס, אין לי השתתפות"),
    # ושנה אחרת מהקטלוג — החלופות הן רכבים חדשים מהקטלוג ("COROLLA CROSS 2023")
    if len(cars) == 1 and roles[cars[0].catalog_code] is None and (
            copay is not None or (year is not None and year != cars[0].reg_year)):
        roles[cars[0].catalog_code] = "current"
    # the question on screen asks only about the car today (the alternatives come after it), so a car
    # named without a role word is the answer to it; a role word ("רוצה לבדוק") still makes it an alternative
    if "options" not in fields or "car" in fields:
        roles = {code: role or "current" for code, role in roles.items()}
    current = [c for c in cars if roles[c.catalog_code] == "current"]
    alternatives = [c for c in cars if roles[c.catalog_code] == "alternative"]
    unclear = [c for c in cars if roles[c.catalog_code] is None]
    if len(current) > 1 or len(unclear) > 1:
        return ParserPlan()
    available = {o.id for o in available_options(snapshot, state)}
    if any(c.catalog_code not in available for c in alternatives):
        return ParserPlan()                   # חלופה שאינה זמינה (קבוצה גבוהה) — המודל או שאלה
    reading = read_profile(text, snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
    # השנה שייכת לרכב של היום (או לרכב ששואלים עליו); עם חלופות בלבד היא נשארת לא מובנת
    spans = [*reading.spans, *approx_spans(text), copay_span(text), unknown_copay,
             *(year_spans(text) if current or unclear else [])]
    phrases = [*reading.phrases, *(matched_name(text, c) or c.model_name for c in cars)]
    fillers = [*ext.profile_fillers, *ext.car_message_fillers, *ext.option_message_fillers, *ext.correction_cues,
               *ext.car_role_cues.current, *ext.car_role_cues.alternative, "אני", "עליו", "עליה"]
    for car in cars:         # the manufacturer's name next to any named car ("קיה נירו") is not extra meaning
        fillers += _descriptors(car, ext.manufacturer_brands)
    if unread_words(text, spans, phrases, fillers, leading_no=True):
        return ParserPlan()
    calls: list[ToolCall] = []
    if current:
        calls += _call("match_current_car", model=current[0].model_name, registration_year=year)
    profile = dict(reading.fields)
    if copay is not None:
        profile["current_copay"] = str(copay)
    elif unknown_copay is not None and current:
        profile["current_copay_unknown"] = True
    if profile:
        calls += _call("save_profile", **profile, approximate_fields=list(reading.approximate))
    if alternatives:
        calls += _call("calculate", options=[*state.selected, *(c.catalog_code for c in alternatives)])
    if not unclear:
        return ParserPlan(calls, True)
    car = unclear[0]
    label = f"{car.model_name} ({year})" if year else car.label     # השנה כפי שהעובד כתב
    if car.catalog_code in state.role_asked:
        # כבר שאלנו על הרכב הזה ולא קיבלנו תשובה ברורה: לא שואלים שוב בלולאה — מניחים שזה הרכב של היום,
        # ואומרים זאת במפורש (העובד יכול לתקן)
        calls = _call("match_current_car", model=car.model_name, registration_year=year) + calls
        notice = (f"לא היה ברור אם {label} הוא הרכב שלכם היום או חלופה, אז הנחתי שזה הרכב שלכם היום. "
                  f"אם זו חלופה, כתבו \"רוצה לבדוק {car.model_name}\".")
        return ParserPlan(calls, True, notice=notice, direct=True)
    return ParserPlan(calls, True, direct=True,
                      clarify=CarClarification(catalog_code=car.catalog_code, label=label, reg_year=year))


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
    if question in ("use_value", "copay", "maintenance") and only(unknown_span(text)):
        flag = {"use_value": "current_use_value_unknown", "copay": "current_copay_unknown",
                "maintenance": "current_maintenance_unknown"}[question]
        return ParserPlan(_call("save_profile", **{flag: True}), True)
    if question == "maintenance" and only(none_span(text)):
        # "אין" to the maintenance-allowance question: a confirmed 0 (the employee receives no allowance)
        return ParserPlan(_call("save_profile", current_maintenance="0"), True)
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
        # "הכי משתלם — אתה משווה את כל סוגי הרכבים?" — a ranking request phrased as a question, with extra
        # words but no specific car named and no list number chosen: still a request to rank all eligible
        # cars, never a re-ask. A named car / a chosen number is handled below as a specific comparison.
        if not named_cars(text, snapshot.catalog) and not re.search(r"\d", text):
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
