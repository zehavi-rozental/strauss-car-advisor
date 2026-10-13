"""הממשק הציבורי של מודול הרכבים."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Optional

from .repository import VehicleInfo, VehicleKey, VehicleRepository

MAX_CHOICES = 5


@dataclass(frozen=True)
class CurrentCarMatch:
    """תוצאת זיהוי הרכב הנוכחי (כלל זיהוי הרכב, סעיף 7).

    FOUND      — התאמה אחת, או כמה התאמות עם אותו שווי שימוש: ממשיכים בלי לשאול.
    CHOOSE     — כמה התאמות עם שווי שונה: מציגים לעובד עד 5 אפשרויות.
    NOT_FOUND  — אין התאמה: שואלים את העובד מה שווי השימוש בתלוש.
    """

    status: Literal["FOUND", "CHOOSE", "NOT_FOUND"]
    vehicle: Optional[VehicleInfo] = None
    use_value: Optional[Decimal] = None
    choices: list[VehicleInfo] = field(default_factory=list)


def match_current_car(repo: VehicleRepository, text: str, reg_year: Optional[int] = None) -> CurrentCarMatch:
    found = repo.search(text, reg_year=reg_year)
    if not found:
        return CurrentCarMatch(status="NOT_FOUND")
    values = {v.use_value for v in found}
    if len(values) == 1:
        return CurrentCarMatch(status="FOUND", vehicle=found[0], use_value=found[0].use_value)
    # מסדרים לפי שווי השימוש כדי שהעובד יראה טווח מייצג
    choices = sorted({v.use_value: v for v in found}.values(), key=lambda v: v.use_value)[:MAX_CHOICES]
    return CurrentCarMatch(status="CHOOSE", choices=choices)


__all__ = ["CurrentCarMatch", "VehicleInfo", "VehicleKey", "VehicleRepository", "match_current_car"]
