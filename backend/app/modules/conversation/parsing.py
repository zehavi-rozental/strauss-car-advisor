"""חילוץ דטרמיניסטי מטקסט: מספרים, עיסוק, קבוצה, הנעה, "לא יודע".

משמש את הכלים (לנרמול מה שמודל השפה העביר) ואת מצב הגיבוי בלי מודל שפה.
"""
from __future__ import annotations

import itertools
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from app.modules.calculation.public import Drive

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
