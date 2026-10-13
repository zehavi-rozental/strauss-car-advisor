"""שומר המספרים: כל מספר בהודעת הבוט חייב להופיע בתוצאת המנוע, בפרופיל או בנתונים שהקוד הציג.

מודל השפה מנסח בלבד. אם הוא הוסיף מספר שלא בא מהעובדות — ההודעה נחסמת ומוחלפת בתבנית.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from app.shared.money import to_shekel

from .parsing import numbers_in


def _variants(value: Decimal) -> set[Decimal]:
    v = abs(value)
    return {v, to_shekel(v)}


def collect_numbers(obj: Any) -> set[Decimal]:
    """כל המספרים במבנה (dict / list / str / Decimal / int), כולל מספרים בתוך מחרוזות."""
    out: set[Decimal] = set()
    if obj is None or isinstance(obj, bool):
        return out
    if isinstance(obj, Decimal):
        return _variants(obj)
    if isinstance(obj, int):
        return {Decimal(abs(obj))}
    if isinstance(obj, str):
        for n in numbers_in(obj):
            out |= _variants(n)
        return out
    if isinstance(obj, dict):
        items: Iterable[Any] = list(obj.keys()) + list(obj.values())
    elif isinstance(obj, (list, tuple, set)):
        items = obj
    else:
        return out
    for item in items:
        out |= collect_numbers(item)
    return out


def violations(text: str, allowed: set[Decimal]) -> list[Decimal]:
    """המספרים בטקסט שאינם מותרים. הסימן לא נבדק (‎+661 ו-661 הם אותו ערך)."""
    return [n for n in numbers_in(text) if abs(n) not in allowed]


def is_safe(text: str, allowed: set[Decimal]) -> bool:
    return not violations(text, allowed)
