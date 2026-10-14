"""חילוץ דטרמיניסטי מטקסט: מספרים, עיסוק, קבוצה, הנעה, "לא יודע".

משמש את הכלים (לנרמול מה שמודל השפה העביר) ואת מצב הגיבוי בלי מודל שפה.
"""
from __future__ import annotations

import itertools
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from app.modules.calculation.public import Drive
from app.modules.rules.public import words

NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
YEAR_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")
ID_NUMBER_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")

_UNKNOWN_RE = re.compile(r"לא\s*(?:יודע|יודעת|זוכר|זוכרת|בטוח|בטוחה)|אין\s*לי\s*מושג|don'?t\s+know|not\s+sure",
                         re.IGNORECASE)
# "לא" לבד = אין; "לא ברור לי" — לא תשובה (אין ברירת מחדל לסכום חסר)
_NONE_RE = re.compile(r"^\s*(?:אין|לא|ללא|אפס|0|none|no)\s*[.!]?\s*$|^\s*(?:אין|ללא)(?:\s|$)", re.IGNORECASE)
_NO_CAR_RE = re.compile(r"(?:אין|בלי|ללא)\s*(?:לי\s*)?רכב|no\s+car", re.IGNORECASE)

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


def match_occupation(names: Iterable[str], text: str) -> Optional[str]:
    """השם מטבלת העיסוקים שמתאים לטקסט; ההתאמה הארוכה ביותר מנצחת."""
    t = _norm(text).replace("/", " ")
    best: Optional[tuple[int, str]] = None
    for name in names:
        if _norm(name) == _norm(text):
            return name
        for variant in occupation_variants(name):
            if variant in t and (best is None or len(variant) > best[0]):
                best = (len(variant), name)
    return best[1] if best else None


_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_NEG = r"(?:אין|בלי|ללא|לא)"
_GAP = r"[^\d,;.\n]{0,12}?"          # בין מילת המפתח למספר — לא חוצים פסיק לשדה הבא
# כל ביטוי: (שדה ב-save_profile, תבנית). המספר הוא הקבוצה הראשונה.
_PROFILE_PATTERNS = (
    ("base_salary", rf"(?:שכר(?:\s*(?:ה)?בסיס)?|ברוטו|משכורת){_GAP}{_NUM}"),
    ("pension_percent", rf"פנסי(?:ה|ת){_GAP}{_NUM}"),
    ("study_fund_percent", rf"השתלמות{_GAP}{_NUM}"),
    ("credit_points", rf"{_NUM}\s*נקוד(?:ות|ה)\s*(?:ה)?זיכוי"),
    ("credit_points", rf"נקודות\s*(?:ה)?זיכוי{_GAP}{_NUM}"),
    ("children_under_23", r"(\d+)\s*(?:ילדים|ילדות|ילד)"),
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
_COPAY_RE = re.compile(rf"(?:משתתף|משתתפ(?:ת|ים|ות)|השתתפות(?:\s*עצמית)?)[^\d,;\n]{{0,20}}?{_NUM}")
_NO_COPAY_RE = re.compile(rf"{_NEG}(?:\s+לי)?\s*השתתפות|לא\s+משתתפ|לא\s+משתתף")


def parse_copay(text: str) -> Optional[Decimal]:
    """ההשתתפות שהעובד ציין בהודעה חופשית; "אין השתתפות" = 0; לא צוינה = None."""
    m = _COPAY_RE.search(text)
    if m:
        return Decimal(m.group(1).replace(",", ""))
    if _NO_COPAY_RE.search(text):
        return Decimal(0)
    return None


# ערך משוער: "בערך", "כ-", "בסביבות"... לפני המספר, או "וחצי", "אלף"... אחריו. אסור לשמור אותו כמדויק.
_APPROX_BEFORE_RE = re.compile(r"בערך|כמעט|בסביבות|משהו\s*כמו|לפחות|(?:^|\s)כ-?\s*\d|~")
_APPROX_AFTER_RE = re.compile(r"^\s*(?:ו?חצי|ורבע|ושלושת\s*רבעי|אלף|אלפים|[kK](?![a-zA-Z]))")


def is_approximate(before: str, after: str = "") -> bool:
    return bool(_APPROX_BEFORE_RE.search(before) or _APPROX_AFTER_RE.search(after))


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


def leftover_words(text: str, name: str, fillers: Iterable[str]) -> list[str]:
    """מה נשאר בהודעה על הרכב אחרי: ההשתתפות, השנה, שם הדגם, מילות מילוי ו"לא" פותח.
    כל מילה שנשארה ("קרוסאובר", "הגבוהה", "של טויוטה") אומרת שיש בהודעה משמעות שהקורא לא מבין."""
    span = copay_span(text)
    if span:
        text = text[:span[0]] + " " + text[span[1]:]
    ws = words(YEAR_RE.sub(" ", text))
    if ws and ws[0] == "לא":
        ws = ws[1:]
    target = "".join(words(name))
    for i in range(len(ws)):
        for j in range(i + 1, len(ws) + 1):
            joined = "".join(ws[i:j])
            if joined == target or (joined[0] in NAME_PREFIX_LETTERS and joined[1:] == target):
                ws = ws[:i] + ws[j:]
                break
        else:
            continue
        break
    fill = {w for f in fillers for w in words(f)}
    return [w for w in ws if w not in fill and not (len(w) > 1 and w[0] in PREFIX_LETTERS and w[1:] in fill)]


def parse_profile_fields(text: str, occupations: Iterable[str]) -> dict[str, object]:
    """כמה שדות פרופיל מהודעה חופשית אחת. רק מה שנאמר במפורש; שום ברירת מחדל."""
    found: dict[str, object] = {}
    occupation = match_occupation(occupations, text)
    if occupation:
        found["occupation"] = occupation
    for field, pattern in _PROFILE_PATTERNS:          # סכום מפורש קודם ל"אין"
        m = re.search(pattern, text)
        if m and is_approximate(m.group(0), text[m.end(1):]):
            continue                                   # "בערך 18 וחצי" — לא מנחשים; המודל יקרא
        if m and field not in found:
            value = m.group(1).replace(",", "")
            found[field] = int(value) if field == "children_under_23" else value
    for field, pattern in _ZERO_PATTERNS:
        if field not in found and re.search(pattern, text):
            found[field] = 0
    return found


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
