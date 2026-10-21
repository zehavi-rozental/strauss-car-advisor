"""What of the model's answer may reach the employee's screen. Three independent checks, so no single rule
(such as the length of a number) is all that stands between an ID number and the page:

1. a printed label is shown only when it names a payslip item (an allow list of payslip words), so a name
   ("ישראל ישראלי") never passes, digits or not;
2. and only when it has no sensitive word (ID, employee number, passport, bank, account, branch, phone,
   address, e-mail) and no number of three digits or more once separators are removed ("312-456-783",
   "31 245 6783", "+972-50-…", "IL62 0108 …");
3. a value is used only when it is plausible for one monthly payslip line (an amount under a million, a rate
   up to 100%, a count up to 50): a nine-digit ID reported as an "amount" is dropped, not shown.

Nothing here is logged: the caller only counts what was dropped.
"""
from __future__ import annotations

import re
from decimal import Decimal

# separators people print inside numbers: "312-456-783", "31 245 6783", "050.123.4567", "(03) 123"
_SPLIT_NUMBER = re.compile(r"(?<=\d)[\s\-‐-―./_()+'\"׳״]+(?=\d)")
_LONG_NUMBER = re.compile(r"\d{3,}")
_QUOTES = re.compile(r"[\"'׳״`.]")

# compared on the label without quotes, dots and geresh: ת"ז / ת.ז / ת׳ז → תז
_SENSITIVE = (
    "תז", "תעודתזהות", "תעודת זהות", "מספר זהות", "מספרזהות", "זהות", "דרכון", "passport",
    "מספר עובד", "מס עובד", "מספר העובד", "שם עובד", "שם העובד", "שם המעסיק", "חפ", "עוסק מורשה", "תיק ניכויים",
    "חשבון", "בנק", "סניף", "iban", "swift", "טלפון", "נייד", "פלאפון", "phone", "כתובת", "רחוב", "מיקוד",
    "דואל", "מייל", "email", "e-mail",
)
# a label names a payslip item: at least one of these (whole words or word starts)
_PAYSLIP_WORDS = (
    "שכר", "משכורת", "יסוד", "בסיס", "עמלה", "עמלות", "מכירה", "מכירות", "בונוס", "מענק", "פרמיה", "תוספת",
    "תוספות", "שעות", "נוספות", "אחזקה", "אחזקת", "החזר", "הוצאות", "פנסיה", "פנסיית", "פנסיוני", "תגמולים",
    "תגמולי", "גמל", "השתלמות", "קרן", "נקודות", "זיכוי", "ילדים", "ילד", "ברוטו", "נטו", "תשלום", "לתשלום",
    "תשלומים", "מס", "הכנסה", "ביטוח", "לאומי", "בריאות", "שווי", "רכב", "שימוש", "ניכוי", "ניכויים", "הפרשה",
    "הפרשת", "הפרשות", "סהכ", "עובד", "עובדת",
)
MAX_WORDS = 5
MAX_LABEL_CHARS = 40
# plausible for one monthly payslip line; anything above is not a value of this payslip (an ID, an account)
_MAX_VALUE = {"amount": Decimal(1_000_000), "percent": Decimal(100), "count": Decimal(50)}


def _flat(text: str) -> str:
    return _QUOTES.sub("", text).lower()


def has_long_number(text: str) -> bool:
    """A number of three digits or more, also when it is printed in groups ("312-456-783", "31 245 6783")."""
    return bool(_LONG_NUMBER.search(_SPLIT_NUMBER.sub("", text)))


def has_sensitive_word(text: str) -> bool:
    if "@" in text:                                   # an e-mail address is one word with @ inside
        return True
    flat = _flat(text)
    words = set(flat.split())
    return any((w in words) if " " not in w and len(w) <= 3 else (w in flat) for w in _SENSITIVE)


def names_a_payslip_item(text: str) -> bool:
    words = [w.strip("—-–:()") for w in _flat(text).split()]
    return any(w == p or (len(p) >= 3 and w.startswith(p)) or (len(w) > 2 and w[1:].startswith(p))
               for w in words for p in _PAYSLIP_WORDS)


def safe_label(raw: object) -> str:
    """The printed label when it may be shown, else "" (dropped rather than shown)."""
    if not isinstance(raw, str):
        return ""
    label = " ".join(raw.split())[:MAX_LABEL_CHARS]
    if (not label or len(label.split()) > MAX_WORDS or has_long_number(label) or has_sensitive_word(label)
            or not names_a_payslip_item(label)):
        return ""
    return label


def plausible(value: Decimal, kind: str) -> bool:
    return Decimal(0) <= value < _MAX_VALUE[kind] if kind == "amount" else Decimal(0) <= value <= _MAX_VALUE[kind]
