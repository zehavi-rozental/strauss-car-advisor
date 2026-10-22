"""מודלים של חילוץ התלוש. ערכים כספיים ואחוזים כמחרוזות (Decimal בצד הלקוח/שמירה), בלי נתונים מזהים.

Two kinds of output:
- profile fields (`SUPPORTED_FIELDS`): exactly the values the conversation accepts through
  POST .../messages {fields}; the employee reviews, edits or rejects each one before it is saved;
- information items (`INFO_KEYS`): what the payslip shows that the calculation does not take as is (an
  unclassified addition, the pension as an amount, gross, net, taxes). Shown with a note, never saved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# The fields the conversation accepts in {fields} (api ProfileFieldsIn / expects.FORM_FIELDS).
SUPPORTED_FIELDS = (
    "base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points",
    "children_under_23",
)

# סוג הערך לכל שדה: amount = ₪, percent = %, count = מספר.
FIELD_KIND = {
    "base_salary": "amount",
    "commissions": "amount",
    "bonus": "amount",
    "pension_percent": "percent",
    "study_fund_percent": "percent",
    "credit_points": "count",
    "children_under_23": "count",
}

# תוויות עבריות לתצוגה בטופס הסקירה (תואמות ל-PAYSLIP_FIELDS בצד הלקוח).
FIELD_LABEL_HE = {
    "base_salary": "שכר בסיס",
    "commissions": "עמלות",
    "bonus": "בונוס",
    "pension_percent": "פנסיה",
    "study_fund_percent": "קרן השתלמות",
    "credit_points": "נקודות זיכוי",
    "children_under_23": "ילדים עד 23",
}

# Keys the model may report that are not profile fields: shown for information only, with a note.
INFO_KEYS = {
    "other_addition": ("תוספת לשכר", "amount",
                       "לא ברור אם זו עמלה, בונוס או רכיב קבוע אחר, ולכן היא לא נכנסת לחישוב אוטומטית. "
                       "אם זה חלק מהשכר החודשי, אפשר לשייך אותה לשדה המתאים."),
    "pension_employee_amount": ("ניכוי פנסיה (סכום)", "amount",
                                "זה סכום ולא אחוז. החישוב צריך את אחוז ההפרשה שלכם, ולכן הסכום לא מומר "
                                "לאחוז. הזינו את האחוז בשדה הפנסיה."),
    "study_fund_employee_amount": ("ניכוי קרן השתלמות (סכום)", "amount",
                                   "זה סכום ולא אחוז. הזינו את אחוז ההפרשה בשדה קרן ההשתלמות."),
    "gross_salary": ("שכר ברוטו", "amount", "לעיון בלבד. החישוב בונה את הברוטו מהרכיבים."),
    "net_pay": ("נטו בתלוש", "amount",
                "לעיון בלבד. ההשוואה מחושבת במנוע לפי הכללים, ולא מהנטו שבתלוש."),
    "income_tax": ("מס הכנסה", "amount", "לעיון בלבד. המס מחושב במנוע."),
    "national_insurance": ("ביטוח לאומי", "amount", "לעיון בלבד. מחושב במנוע."),
    "health_insurance": ("ביטוח בריאות", "amount", "לעיון בלבד. מחושב במנוע."),
    "car_use_value": ("שווי שימוש ברכב", "amount",
                      "לעיון. שווי השימוש של הרכב הנוכחי נלקח מהקטלוג או ממאגר רשות המסים לפי הדגם."),
    "car_copay": ("השתתפות עובד ברכב", "amount",
                  "ניכוי מהנטו ולא תוספת לשכר, ולכן לא נכנס לשום רכיב שכר. את ההשתתפות שלכם היום רושמים "
                  "בשיחה על הרכב הנוכחי."),
    "car_maintenance": ("אחזקת רכב", "amount",
                        "תשלום שקשור לרכב ולא רכיב שכר, ולכן לא נכנס לשכר הבסיס, לעמלות או לבונוס. אם אין לכם "
                        "רכב צמוד, את האחזקה רושמים בשיחה על הרכב."),
}

# The only information item the employee may assign to an EMPTY commissions / bonus row (the server decides,
# InfoItem.assignable): car items are never pay, whatever the model called them.
ASSIGNABLE_INFO = ("other_addition",)

# An information value that does not look like this payslip line (a use value of 3.4 read from a reversed
# "003,4"): still shown, never as a fact, and it is information only anyway.
SUSPECT_NOTE = "הערך הזה לא נראה סביר לשורה הזו, וייתכן שנקרא לא נכון. בדקו אותו מול התלוש. הוא לא נכנס לחישוב."
# An addition whose label does not say what it is (none printed, or a bare "השתתפות"): not assumed to be pay
# or a deduction. It can still be assigned, as the employee's own decision.
UNCLEAR_NOTE = ("לא ברור מהתלוש אם זו תוספת לשכר או ניכוי (למשל השתתפות). היא לא נכנסת לחישוב. שייכו אותה "
                "לעמלות או לבונוס רק אם אתם יודעים שזו תוספת לשכר.")

# The outcome of one extraction (ExtractionResult.status).
STATUSES = ("found", "partial", "nothing_found", "not_payslip", "unreadable", "failed")
# Why an extraction failed (status "failed"): each has its own message and recovery.
FAIL_REASONS = ("timeout", "quota", "unavailable", "rejected", "invalid_response", "not_configured")


@dataclass(frozen=True)
class ExtractedField:
    """שדה פרופיל אחד. value=None כשהערך חסר או עמום — חובה סקירה ידנית, אף פעם לא מניחים 0.

    status: found (a clear value), missing (not on the payslip), ambiguous (on the payslip, but not
    usable as is: two different values, the wrong unit, or outside the plausible range)."""

    field: str
    value: Optional[Decimal]
    kind: str                       # amount / percent / count
    confident: bool                 # False → מוצג כ"לא ודאי", דורש אישור ידני
    in_range: bool = True           # עבר את בדיקת הטווח של extensions.yaml
    note: str = ""                  # הערה עברית קצרה לתצוגה (למשל "מחוץ לטווח, נא לבדוק")
    status: str = "missing"
    source_label: str = ""          # the label as printed on the payslip ("שכר יסוד"), never a name or a number
    certainty: str = ""             # high / medium / low, as the model reported it

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "label": FIELD_LABEL_HE.get(self.field, self.field),
            "value": None if self.value is None else str(self.value),
            "kind": self.kind,
            "confident": self.confident,
            "in_range": self.in_range,
            "note": self.note,
            "status": self.status,
            "source_label": self.source_label,
            "certainty": self.certainty,
        }


@dataclass(frozen=True)
class InfoItem:
    """What the payslip shows that is not entered into the profile automatically (with the reason)."""

    key: str
    value: Decimal
    kind: str
    source_label: str = ""
    suspect: bool = False           # outside what this line can be: shown with SUSPECT_NOTE, never as a fact
    unclear: bool = False           # an addition with no label, or a bare "השתתפות": pay or a deduction?

    def as_dict(self) -> dict:
        label, _, note = INFO_KEYS[self.key]
        return {"key": self.key, "label": label, "value": str(self.value), "kind": self.kind,
                "source_label": self.source_label,
                "note": SUSPECT_NOTE if self.suspect else UNCLEAR_NOTE if self.unclear else note,
                "suspect": self.suspect, "unclear": self.unclear,
                "assignable": self.key in ASSIGNABLE_INFO and not self.suspect}


@dataclass(frozen=True)
class ExtractionResult:
    """תוצאת החילוץ. fields תמיד מכיל את כל השדות הנתמכים (חסר = value=None) כשהתלוש נקרא.

    status (STATUSES) tells the client which outcome this is, so a failure never looks like an empty
    extraction: found (every profile field), partial, nothing_found (a payslip, but no profile field on
    it), not_payslip, unreadable (the model could not read the file), failed (no usable answer: `reason`).
    ok is False only for failed."""

    ok: bool
    fields: list[ExtractedField] = field(default_factory=list)
    message_he: str = ""            # הודעה עברית (הצלחה או כשל); לעולם לא מכילה ערכים מהתלוש
    status: str = "failed"
    reason: str = ""                # FAIL_REASONS when status == "failed"
    info: list[InfoItem] = field(default_factory=list)
    retry: bool = False             # trying the same file again may help (timeout, quota, a bad answer)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "reason": self.reason,
            "retry": self.retry,
            "fields": [f.as_dict() for f in self.fields],
            "info": [i.as_dict() for i in self.info],
            "message_he": self.message_he,
        }


class PayslipError(Exception):
    """קובץ לא תקין: סוג, גודל, מספר עמודים, פגום או מוצפן. message_he בעברית, בלי תוכן פרטי."""

    def __init__(self, message_he: str):
        super().__init__(message_he)
        self.message_he = message_he
