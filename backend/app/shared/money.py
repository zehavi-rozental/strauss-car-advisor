"""כסף במערכת הוא תמיד Decimal. אין float בשום מקום בחישוב."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, getcontext
from typing import Union

getcontext().prec = 34

Number = Union[Decimal, int, str]

ZERO = Decimal("0")
AGORA = Decimal("0.01")
SHEKEL = Decimal("1")


def D(value: Number | float) -> Decimal:
    """ממיר לערך Decimal. float עובר דרך str כדי לא לגרור שגיאות ייצוג בינארי."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def to_shekel(value: Decimal) -> Decimal:
    """עיגול לתצוגה בלבד — לשקל שלם, חצי כלפי מעלה."""
    return value.quantize(SHEKEL, rounding=ROUND_HALF_UP)


def to_agora(value: Decimal) -> Decimal:
    return value.quantize(AGORA, rounding=ROUND_HALF_UP)
