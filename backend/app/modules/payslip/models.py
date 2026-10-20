"""מודלים של חילוץ התלוש. ערכים כספיים ואחוזים כמחרוזות (Decimal בצד הלקוח/שמירה), בלי נתונים מזהים."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# השדות שהמערכת הקיימת תומכת בהם בטופס הפרופיל (expects.FORM_FIELDS / ProfileFieldsIn).
# אלה בדיוק השדות שנשמרים דרך POST .../messages {fields}. לא יוצרים שדות פרופיל חדשים.
SUPPORTED_FIELDS = (
    "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children_under_23",
)

# סוג הערך לכל שדה: amount = ₪, percent = %, count = מספר שלם.
FIELD_KIND = {
    "commissions": "amount",
    "bonus": "amount",
    "pension_percent": "percent",
    "study_fund_percent": "percent",
    "credit_points": "count",
    "children_under_23": "count",
}

# תוויות עבריות לתצוגה בטופס הסקירה (תואמות ל-FORM_FIELDS בצד הלקוח).
FIELD_LABEL_HE = {
    "commissions": "עמלות",
    "bonus": "בונוס",
    "pension_percent": "פנסיה",
    "study_fund_percent": "קרן השתלמות",
    "credit_points": "נקודות זיכוי",
    "children_under_23": "ילדים עד 23",
}


@dataclass(frozen=True)
class ExtractedField:
    """שדה בודד שחולץ. value=None כשהערך חסר או לא ודאי — חובה סקירה ידנית, אף פעם לא מניחים 0."""

    field: str
    value: Optional[Decimal]
    kind: str                       # amount / percent / count
    confident: bool                 # False → מוצג כ"לא ודאי", דורש אישור ידני
    in_range: bool = True           # עבר את בדיקת הטווח של extensions.yaml
    note: str = ""                  # הערה עברית קצרה לתצוגה (למשל "מחוץ לטווח, נא לבדוק")

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "label": FIELD_LABEL_HE.get(self.field, self.field),
            "value": None if self.value is None else str(self.value),
            "kind": self.kind,
            "confident": self.confident,
            "in_range": self.in_range,
            "note": self.note,
        }


@dataclass(frozen=True)
class ExtractionResult:
    """תוצאת החילוץ. fields תמיד מכיל את כל השדות הנתמכים (חסר = value=None).
    ok=False כשהחילוץ נכשל (Gemini לא זמין/timeout/תשובה לא תקינה) — הלקוח עובר למילוי ידני."""

    ok: bool
    fields: list[ExtractedField] = field(default_factory=list)
    message_he: str = ""            # הודעה עברית (הצלחה או כשל); לעולם לא מכילה ערכים מהתלוש

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "fields": [f.as_dict() for f in self.fields],
            "message_he": self.message_he,
        }


class PayslipError(Exception):
    """קובץ לא תקין: סוג, גודל, מספר עמודים, פגום או מוצפן. message_he בעברית, בלי תוכן פרטי."""

    def __init__(self, message_he: str):
        super().__init__(message_he)
        self.message_he = message_he
