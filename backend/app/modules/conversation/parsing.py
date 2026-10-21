"""חילוץ דטרמיניסטי מטקסט: מספרים, עיסוק, קבוצה, הנעה, "לא יודע".

משמש את הכלים (לנרמול מה שמודל השפה העביר) ואת מצב הגיבוי בלי מודל שפה.
"""
from __future__ import annotations

import difflib
import itertools
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from app.modules.calculation.public import Drive
from app.modules.rules.public import words

NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
YEAR_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")
ID_NUMBER_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")

_UNKNOWN_RE = re.compile(r"לא\s*(?:ידוע|יודעים|יודע|יודעת|זוכר|זוכרת|בטוח|בטוחה)|אין\s*לי\s*מושג|don'?t\s+know|not\s+sure",
                         re.IGNORECASE)
# "לא" לבד = אין; "לא ברור לי" — לא תשובה (אין ברירת מחדל לסכום חסר)
_NONE_RE = re.compile(r"^\s*(?:אין|לא|ללא|אפס|0|none|no)\s*[.!]?\s*$|^\s*(?:אין|ללא)(?:\s|$)", re.IGNORECASE)
# "אין/בלי/ללא [לי] רכב" — the "לי" tolerates a light typo ("ליר", "ליי") via up to 2 trailing letters
_NO_CAR_RE = re.compile(r"(?:אין|בלי|ללא)\s*(?:ל[יי][א-ת]{0,2}\s+)?רכב|no\s+car", re.IGNORECASE)

_DRIVE_WORDS = {
    Drive.PLUG_IN: ("פלאג", "plug"),
    Drive.ELECTRIC: ("חשמלי", "electric", "ev"),
    Drive.HYBRID: ("היברי", "hybrid"),
    Drive.PETROL: ("בנזין", "petrol", "gasoline", "דלק"),
}


def redact_id_numbers(text: str) -> str:
    """מספר בן 9 ספרות (ת"ז) לא נשמר ולא נשלח הלאה."""
    return ID_NUMBER_RE.sub("[הוסר]", text)


def parse_decimal(value: object) -> Optional[Decimal]:
    """"18,500" / 18500 / "6%" → Decimal. float עובר דרך str."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    m = NUMBER_RE.search(str(value))
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[Decimal]:
    return [Decimal(m.replace(",", "")) for m in NUMBER_RE.findall(text)]


def first_number(text: str) -> Optional[Decimal]:
    found = numbers_in(text)
    return found[0] if found else None


Span = tuple[int, int]


def unknown_span(text: str) -> Optional[Span]:
    m = _UNKNOWN_RE.search(text)
    return m.span() if m else None


def none_span(text: str) -> Optional[Span]:
    m = _NONE_RE.search(text)
    return m.span() if m else None


def no_car_span(text: str) -> Optional[Span]:
    m = _NO_CAR_RE.search(text)
    return m.span() if m else None


def drive_span(text: str) -> Optional[tuple[Drive, Span]]:
    t = text.lower()
    for drive, words_ in _DRIVE_WORDS.items():
        for w in words_:
            m = re.search(rf"(?<![a-zא-ת]){re.escape(w)}", t)
            if m:
                return drive, m.span()
    return None


def is_unknown(text: str) -> bool:
    return bool(_UNKNOWN_RE.search(text))


def is_none(text: str) -> bool:
    return bool(_NONE_RE.search(text))


def is_no_car(text: str) -> bool:
    return bool(_NO_CAR_RE.search(text))


def find_year(text: str) -> Optional[int]:
    m = YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def strip_year(text: str) -> str:
    return re.sub(r"\(?\s*" + YEAR_RE.pattern + r"\s*\)?", " ", text).strip()


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zA-Zא-ת/ ]+", " ", text).split()).lower()


def occupation_variants(name: str) -> set[str]:
    """"מנהל/ת מוצר" → {"מנהל מוצר", "מנהלת מוצר"}."""
    options = []
    for token in _norm(name).split():
        if "/" in token:
            stem, suffix = token.split("/", 1)
            options.append((stem, stem + suffix))
        else:
            options.append((token,))
    return {" ".join(combo) for combo in itertools.product(*options)}


def occupation_match(names: Iterable[str], text: str) -> Optional[tuple[str, str]]:
    """(השם מטבלת העיסוקים, הצורה שנמצאה בטקסט); ההתאמה הארוכה ביותר מנצחת."""
    t = _norm(text).replace("/", " ")
    best: Optional[tuple[str, str]] = None
    for name in names:
        if _norm(name) == _norm(text):
            return name, text
        for variant in occupation_variants(name):
            # מילים שלמות (אות שימוש אחת מותרת): "מנהלת מוצרים" אינה "מנהל/ת מוצר" — מציעים, לא מנחשים
            whole = re.search(rf"(?<![0-9a-zא-ת])[והבלכש]?{re.escape(variant)}(?![0-9a-zא-ת])", t)
            if whole and (best is None or len(variant) > len(best[1])):
                best = (name, variant)
    return best


SIMILAR_OCCUPATION_RATIO = 0.7


def similar_occupations(names: Iterable[str], text: str, limit: int = 3) -> list[str]:
    """עד 3 שמות מהטבלה שדומים לטקסט (דמיון פשוט על המילים המנורמלות), הדומה ביותר קודם."""
    t = _norm(text).replace("/", " ")
    scored = []
    for name in names:
        score = max(difflib.SequenceMatcher(None, t, v).ratio() for v in occupation_variants(name))
        if score >= SIMILAR_OCCUPATION_RATIO:
            scored.append((score, name))
    return [name for _, name in sorted(scored, key=lambda x: -x[0])[:limit]]


def match_occupation(names: Iterable[str], text: str) -> Optional[str]:
    """השם מטבלת העיסוקים שמתאים לטקסט; ההתאמה הארוכה ביותר מנצחת."""
    hit = occupation_match(names, text)
    return hit[0] if hit else None


_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_NEG = r"(?:אין|בלי|ללא|לא)"
# בין מילת המפתח למספר — לא חוצים פסיק לשדה הבא. המילים שבפער נבדקות ("בונוס שנתי 12,000" — לא נקרא במלואו)
_GAP = r"(?P<gap>[^\d,;.\n]{0,12}?)"
# כל ביטוי: (שדה ב-save_profile, תבנית). המספר הוא הקבוצה הראשונה.
_PROFILE_PATTERNS = (
    ("base_salary", rf"(?:שכר(?:\s*(?:ה)?בסיס)?|ברוטו|משכורת){_GAP}{_NUM}"),
    ("pension_percent", rf"פנסי(?:ה|ת){_GAP}{_NUM}"),
    ("study_fund_percent", rf"השתלמות{_GAP}{_NUM}"),
    ("credit_points", rf"{_NUM}\s*נקוד(?:ות|ה)\s*(?:ה)?זיכוי"),
    ("credit_points", rf"נקודות\s*(?:ה)?זיכוי{_GAP}{_NUM}"),
    ("children_under_23", r"(?<![\d.,])(\d+)\s*(?:ילדים|ילדות|ילד)"),
    ("commissions", rf"עמלות{_GAP}{_NUM}"),
    ("bonus", rf"בונוס(?:ים)?{_GAP}{_NUM}"),
)
# "אין עמלות ואין בונוס" / "בלי עמלות ובונוסים" → 0
_ZERO_PATTERNS = (
    ("commissions", rf"{_NEG}(?:\s+לי)?\s*עמלות"),
    ("bonus", rf"{_NEG}(?:\s+[^\s,]+){{0,2}}?\s*ו?(?:{_NEG}\s*)?בונוס"),
    ("study_fund_percent", rf"{_NEG}(?:\s+לי)?\s*(?:קרן\s*)?השתלמות"),
    ("children_under_23", rf"{_NEG}(?:\s+לי)?\s*ילדים"),
)


# השתתפות: מספר אחרי משתתף/ת / השתתפות, בלי לחצות פסיק או מספר אחר ("משתתפת היום ב-300 ש"ח")
_COPAY_RE = re.compile(rf"(?:משתתף|משתתפ(?:ת|ים|ות)|משלם|משלמ(?:ת|ים|ות)|השתתפות(?:\s*עצמית)?)[^\d,;\n]{{0,20}}?{_NUM}")
_NO_COPAY_RE = re.compile(rf"{_NEG}(?:\s+לי)?\s*השתתפות|(?:לא|אינני|איני|אינו|אינה)\s+(?:משתתפ|משתתף|משלמ|משלם)")


def parse_copay(text: str) -> Optional[Decimal]:
    """ההשתתפות שהעובד ציין בהודעה חופשית; "אין השתתפות" = 0; לא צוינה = None."""
    m = _COPAY_RE.search(text)
    if m:
        return Decimal(m.group(1).replace(",", ""))
    if _NO_COPAY_RE.search(text):
        return Decimal(0)
    return None


# ערך משוער: "בערך", "כ-", "בסביבות"... לפני המספר, או "וחצי", "אלף"... אחריו. אסור לשמור אותו כמדויק.
_APPROX_BEFORE_RE = re.compile(r"בערך|כמעט|בסביבות|משהו\s*כמו|לפחות|בממוצע|משתנ(?:ה|ות|ים)|(?:^|\s)כ-?\s*\d|~")
_APPROX_AFTER_RE = re.compile(r"^\s*(?:ו?חצי|ורבע|ושלושת\s*רבעי|אלף|אלפים|[kK](?![a-zA-Z]))")


def is_approximate(before: str, after: str = "") -> bool:
    return bool(_APPROX_BEFORE_RE.search(before) or _APPROX_AFTER_RE.search(after))


_UNIT_RE = re.compile(r"^\s*(?:(?P<half>ו?חצי)|(?P<thousand>אלף|אלפים|[kK](?![a-zA-Z]))|ש\"?ח|₪)?")
_APPROX_MARKER_RE = re.compile(r"בערך|כמעט|בסביבות|בממוצע|משתנ(?:ה|ות|ים)|(?:^|(?<=\s))כ-?(?=\s*\d)|~")


@dataclass(frozen=True)
class ReadNumber:
    value: Decimal
    span: Span            # המספר עם היחידה ("18 אלף", "18 וחצי")
    approximate: bool     # נאמר כהערכה ("כ-1,000", "בערך 18,500", "משתנות")


def read_number(text: str, match: "re.Match[str]", group: int = 0) -> ReadNumber:
    """מספר עם יחידה: "18 אלף" = 18,000 (מפורש), "18 וחצי" = 18.5 (מקוצר; אישור יגיע מטווח הסבירות)."""
    value = Decimal(match.group(group).replace(",", ""))
    end = match.end(group)
    unit = _UNIT_RE.match(text[end:])
    if unit and unit.group("half"):
        value += Decimal("0.5")
    if unit and unit.group("thousand"):
        value *= 1000
    if unit:
        end += unit.end()
    # סימן הערכה שייך למספר רק באותו משפט-משנה: לא חוצים פסיק/נקודה ("כ-18,500, פנסיה 6" — רק השכר משוער)
    clause = re.split(r"[,;\n]|\.(?!\d)", text[max(0, match.start() - 25):match.start(group)])[-1]
    # "כ-800": הסימן צמוד למספר; "1,000 בחודש בערך": הסימן אחרי המספר, באותו משפט-משנה
    after = re.split(r"[,;\n]|\.(?!\d)", text[end:end + 25])[0]
    approximate = bool(_APPROX_MARKER_RE.search(clause + match.group(group)) or _APPROX_MARKER_RE.search(after))
    return ReadNumber(value, (match.start(group), end), approximate)


def approx_spans(text: str) -> list[Span]:
    return [m.span() for m in _APPROX_MARKER_RE.finditer(text)]


def copay_span(text: str) -> Optional[tuple[int, int]]:
    """היכן בטקסט נאמרה ההשתתפות (כדי להוציא אותה כשבודקים מה נשאר בהודעה)."""
    m = _COPAY_RE.search(text) or _NO_COPAY_RE.search(text)
    return m.span() if m else None


PREFIX_LETTERS = "והבל"          # אות שימוש אחת לפני מילה: "ואני", "בחודש", "והרכב"
NAME_PREFIX_LETTERS = "והבלמשכ"  # כמו mentions: "הקורולה", "ואטו 3"


def negates(text: str) -> bool:
    """ "לא" (או "ולא"/"שלא") בכל מקום חוץ מהמילה הראשונה: "זה לא קורולה קרוס" שולל את הרכב.
    "לא" כמילה ראשונה הוא תיקון ("לא, זו קורולה קרוס")."""
    return any(w == "לא" or (len(w) == 3 and w[0] in "וש" and w[1:] == "לא") for w in words(text)[1:])


def _widen(text: str, start: int, end: int) -> Span:
    """טווח שזוהה מורחב למילים שלמות ("ובונוסים" כולו, לא רק "בונוס")."""
    # רק כשהקצה באמצע מילה: טווח שנגמר ברווח לא "בולע" את המילה הבאה ("18,500 נטו")
    while 0 < start < len(text) and text[start].isalnum() and text[start - 1].isalnum():
        start -= 1
    while 0 < end < len(text) and text[end - 1].isalnum() and text[end].isalnum():
        end += 1
    return start, end


def _remove_phrase(ws: list[str], phrase: str) -> list[str]:
    target = "".join(words(phrase))
    if not target:
        return ws
    for i in range(len(ws)):
        for j in range(i + 1, len(ws) + 1):
            joined = "".join(ws[i:j])
            if joined == target or (joined[0] in NAME_PREFIX_LETTERS and joined[1:] == target):
                return ws[:i] + ws[j:]
            if len(joined) > len(target) + 1:
                break
    return ws


def unread_words(text: str, spans: Iterable[Optional[Span]] = (), phrases: Iterable[str] = (),
                 fillers: Iterable[str] = (), list_numbers: int = 0, leading_no: bool = False) -> list[str]:
    """הכלל המשותף לכל השאלות: מה נשאר בהודעה אחרי מה שהקורא זיהה.

    מסירים: טווחים שזוהו (מורחבים למילים שלמות), ביטויים שזוהו (שם רכב, עיסוק; אות שימוש אחת),
    מספרים מהרשימה (1..list_numbers), מילות מילוי של השאלה (אות שימוש אחת), ו"לא" פותח אם מותר.
    כל מילה שנשארה היא משמעות שהקורא לא הבין — ואז הוא לא פועל."""
    chars = list(text)
    for span in spans:
        if span is None:
            continue
        a, b = _widen(text, *span)
        chars[a:b] = " " * (b - a)
    ws = words("".join(chars))
    if leading_no and ws and ws[0] == "לא":
        ws = ws[1:]
    for phrase in phrases:
        ws = _remove_phrase(ws, phrase)
    if list_numbers:
        ws = [w for w in ws if not (w.isdigit() and 1 <= int(w) <= list_numbers)]
    fill = {w for f in fillers for w in words(f)}
    return [w for w in ws if w not in fill and not (len(w) > 1 and w[0] in PREFIX_LETTERS and w[1:] in fill)]


def leftover_words(text: str, name: str, fillers: Iterable[str]) -> list[str]:
    """הודעה על הרכב: מה נשאר אחרי ההשתתפות, השנה, שם הדגם, מילות המילוי ו"לא" פותח."""
    return unread_words(text, spans=[copay_span(text), *year_spans(text)], phrases=[name], fillers=fillers,
                        leading_no=True)


def year_spans(text: str) -> list[Span]:
    return [m.span() for m in YEAR_RE.finditer(text)]


# ---------- פרופיל: קריאה עם טווחים, וערכים מתויגים לגיבוי ----------

@dataclass(frozen=True)
class ProfileReading:
    fields: dict[str, object]                 # כל מה שזוהה (לקריאה מלאה)
    spans: list[Span]
    phrases: list[str]                        # צורת העיסוק שנמצאה בטקסט
    labelled: dict[str, object]               # רק ערכים מתויגים חד-משמעיים (לגיבוי)
    approximate: list[str] = ()               # שדות שנאמרו כהערכה


_SEP = r"\s*[:\-–]?\s*"
# מילת מפתח ומיד אחריה מספר אחד בדיוק
_LABELLED = (
    ("base_salary", rf"(?:שכר\s*(?:ה)?בסיס|שכר|ברוטו|משכורת){_SEP}{_NUM}"),
    ("commissions", rf"עמלות{_SEP}{_NUM}"),
    ("bonus", rf"בונוס(?:ים)?{_SEP}{_NUM}"),
    ("pension_percent", rf"פנסי(?:ה|ת){_SEP}{_NUM}"),
    ("study_fund_percent", rf"(?:קרן\s*)?השתלמות{_SEP}{_NUM}"),
    ("credit_points", rf"{_NUM}\s*נקוד(?:ות|ה)\s*(?:ה)?זיכוי"),
    ("credit_points", rf"נקודות\s*(?:ה)?זיכוי{_SEP}{_NUM}"),
    ("children_under_23", r"(?<![\d.,])(\d+)\s*(?:ילדים|ילדות|ילד)(?![א-ת])"),
)
_LABELLED_ZERO = (
    ("commissions", r"(?:אין|בלי|ללא)\s+(?:לי\s+)?עמלות"),
    ("bonus", r"(?:אין|בלי|ללא)\s+(?:לי\s+)?בונוס"),
    ("study_fund_percent", r"(?:אין|בלי|ללא)\s+(?:לי\s+)?(?:קרן\s+)?השתלמות"),
    ("children_under_23", r"(?:אין|בלי|ללא)\s+(?:לי\s+)?ילדים"),
)
_DIGITS_RE = re.compile(r"\d+(?:[.,]\d+)*")
_SECOND_NUMBER_RE = re.compile(r"^\s*(?:או|עד|-|–|/)\s*\d")
_NEGATIONS = {"לא"}


def _labelled_values(text: str, cues: Iterable[str], qualifiers: Iterable[str] = ()) -> dict[str, object]:
    """ערכים שאין ספק בהם: מילת מפתח + מספר אחד, בלי שלילה, תיקון, הערכה או מספר שני."""
    stop = _NEGATIONS | {w for c in cues for w in words(c)}
    qualify = {w for q in qualifiers for w in words(q)}
    found: dict[str, set] = {}
    matched: list[Span] = []
    for field, pattern in _LABELLED:
        for m in re.finditer(pattern, text):
            matched.append(m.span())
            before = words(text[:m.start()])[-1:]
            after = text[m.end():]
            # "שכר בסיס 18,500 נטו", "בונוס 12,000 בשנה" — מילה שמסייגת את הערך
            if any(w in qualify or (len(w) > 1 and w[0] in PREFIX_LETTERS and w[1:] in qualify)
                   for w in words(after)[:2]):
                found.setdefault(field, set()).add(None)
                continue
            if (before and before[0] in stop) or _SECOND_NUMBER_RE.match(after) \
                    or is_approximate(text[max(0, m.start() - 15):m.end()], after):
                found.setdefault(field, set()).add(None)        # לא חד-משמעי
                continue
            value = m.group(1).replace(",", "")
            found.setdefault(field, set()).add(int(value) if field == "children_under_23" else value)
    for field, pattern in _LABELLED_ZERO:
        for m in re.finditer(pattern, text):
            before = words(text[:m.start()])[-1:]
            found.setdefault(field, set()).add(None if before and before[0] in stop else 0)
    # מספר שאינו שייך לאף ערך מתויג ("3 ילדים, אחד מעל 23") — ייתכן שהוא מסייג ערך: לא רושמים דבר
    if any(not any(s <= n.start() and n.end() <= e for s, e in matched) for n in _DIGITS_RE.finditer(text)):
        return {}
    # שדה עם יותר מערך אחד, או עם מופע לא חד-משמעי — לא נשמר
    return {f: next(iter(v)) for f, v in found.items() if len(v) == 1 and None not in v}


def read_profile(text: str, occupations: Iterable[str], cues: Iterable[str] = (),
                 qualifiers: Iterable[str] = ()) -> ProfileReading:
    fields: dict[str, object] = {}
    spans: list[Span] = []
    phrases: list[str] = []
    hit = occupation_match(occupations, text)
    if hit:
        fields["occupation"], variant = hit
        phrases.append(variant)
    approximate: list[str] = []
    for field, pattern in _PROFILE_PATTERNS:          # סכום מפורש קודם ל"אין"
        m = re.search(pattern, text)
        if m and field not in fields:
            number = read_number(text, m, m.lastindex)        # המספר — הקבוצה האחרונה
            value = number.value
            if field == "children_under_23" and value != int(value):
                continue                                      # "2.5 ילדים" — לא מספר ילדים
            fields[field] = int(value) if field == "children_under_23" else plain_decimal(value)
            keyword = (m.start(), m.start("gap")) if "gap" in m.re.groupindex else m.span()
            spans += [keyword, number.span]
            if number.approximate:
                approximate.append(field)
    for field, pattern in _ZERO_PATTERNS:
        m = re.search(pattern, text)
        if m and field not in fields:
            fields[field] = 0
            spans.append(m.span())
        elif m and fields.get(field) == 0:
            spans.append(m.span())
    return ProfileReading(fields, spans, phrases, _labelled_values(text, cues, qualifiers), approximate)


def parse_profile_fields(text: str, occupations: Iterable[str]) -> dict[str, object]:
    """כמה שדות פרופיל מהודעה חופשית אחת. רק מה שנאמר במפורש; שום ברירת מחדל."""
    return read_profile(text, occupations).fields


def match_group(groups: Iterable[str], text: str) -> Optional[str]:
    """"קבוצה ג" / "ג" → "ג"."""
    cleaned = re.sub(r"קבוצ(?:ה|ת)|זכאות|[^א-ת ]", " ", text)
    found = [g for g in groups if re.search(rf"(?<![א-ת]){re.escape(g)}(?![א-ת])", cleaned)]
    return found[0] if len(found) == 1 else None


def match_drive(text: str) -> Optional[Drive]:
    t = text.lower()
    for drive, words in _DRIVE_WORDS.items():
        if any(re.search(rf"(?<![a-zא-ת]){re.escape(w)}", t) for w in words):
            return drive
    return None


def plain_decimal(value: Decimal) -> str:
    """"18500", "2.5" — בלי אפסים מיותרים ובלי כתיב מדעי."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
