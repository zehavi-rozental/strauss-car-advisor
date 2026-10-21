"""חילוץ נתונים מתלוש (PDF/תמונה) דרך Gemini vision.

עקרונות פרטיות ואבטחה:
- הקובץ לא נשמר בשרת. הוא נכנס כ-bytes, נשלח ל-Gemini inline, ונזרק מהזיכרון בסוף הבקשה.
- The model reports what the payslip shows as items (a known key, the printed label, the value, its unit
  and how sure it is). Code decides: only the profile fields in SUPPORTED_FIELDS can be saved, and only
  after the employee reviews them; everything else (an unclassified addition, the pension as an amount,
  gross, net, taxes) is shown for information with the reason it is not used. Nothing is converted:
  an amount never becomes a percent, an addition never becomes a bonus.
- שם, ת"ז, מספר עובד, בנק, כתובת — לא מבוקשים ולא מוחזרים. גם אם המודל יחזיר אותם, הם מסוננים כאן.
- כל ערך נבדק מול הטווחים של extensions.yaml. מחוץ לטווח, יחידה שגויה או שני ערכים שונים → "עמום",
  אף פעם לא נשמר בשקט. ערך חסר → value=None; אף פעם לא מניחים 0.
- הודעות שגיאה בעברית, בלי תוכן פרטי. בלוג: סיבת כשל וספירות בלבד.
"""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Protocol

from app.modules.rules.public import RulesSnapshot

from .models import (
    FIELD_KIND,
    INFO_KEYS,
    SUPPORTED_FIELDS,
    ExtractedField,
    ExtractionResult,
    InfoItem,
    PayslipError,
)

log = logging.getLogger(__name__)

# סוגי קבצים מותרים (תלוש = PDF או תצלום). מזוהים לפי חתימת הבייטים, לא לפי שם הקובץ או ה-Content-Type.
MIME_PDF = "application/pdf"
MIME_JPEG = "image/jpeg"
MIME_PNG = "image/png"
MIME_WEBP = "image/webp"
ALLOWED_MIME = (MIME_PDF, MIME_JPEG, MIME_PNG, MIME_WEBP)

_BAD_TYPE = "אפשר להעלות רק קובץ PDF או תמונה (JPG / PNG / WEBP) של התלוש."
_TOO_BIG = "הקובץ גדול מדי. אפשר להעלות עד {mb} MB."
_EMPTY = "הקובץ ריק."
_TOO_MANY_PAGES = "בקובץ יותר מדי עמודים. אפשר להעלות תלוש של עד {pages} עמודים."
_ENCRYPTED = "הקובץ מוגן בסיסמה. צריך להסיר את ההגנה לפני ההעלאה, או למלא את הנתונים ידנית."
_CORRUPT = "לא הצלחנו לקרוא את הקובץ. ודאו שזה תלוש תקין ונסו שוב, או מלאו ידנית."

_MESSAGES = {
    "found": "סקרו את מה שזוהה מהתלוש, תקנו מה שצריך ואשרו. התלוש עצמו לא נשמר.",
    "partial": ("זיהינו חלק מהנתונים. שדה ריק לא נמצא בתלוש: מלאו אותו ידנית או השאירו ריק. "
                "התלוש עצמו לא נשמר."),
    "nothing_found": ("לא מצאנו בתלוש נתון שאפשר להכניס לחישוב: שכר בסיס, עמלות, בונוס, אחוז הפרשה לפנסיה, "
                      "אחוז קרן השתלמות, נקודות זיכוי או מספר ילדים. אפשר למלא ידנית או לנסות קובץ אחר."),
    "not_payslip": "הקובץ לא נראה כמו תלוש שכר. אפשר לנסות קובץ אחר או למלא את הנתונים ידנית.",
    "unreadable": ("לא הצלחנו לקרוא את הכתוב בקובץ. נסו צילום חד יותר או קובץ PDF מקורי, "
                   "או מלאו את הנתונים ידנית."),
}
_FAIL_MESSAGES = {
    "timeout": "שירות הקריאה לא הגיב בזמן. אפשר לנסות שוב בעוד רגע, או למלא את הנתונים ידנית.",
    "quota": "שירות הקריאה עמוס כרגע. אפשר לנסות שוב בעוד דקה, או למלא את הנתונים ידנית.",
    "unavailable": "שירות הקריאה לא זמין כרגע. אפשר לנסות שוב בעוד רגע, או למלא את הנתונים ידנית.",
    "rejected": "שירות הקריאה לא קיבל את הקובץ. אפשר לנסות קובץ אחר, או למלא את הנתונים ידנית.",
    "invalid_response": "קיבלנו תשובה לא תקינה משירות הקריאה. אפשר לנסות שוב, או למלא את הנתונים ידנית.",
    "not_configured": "קריאת תלושים לא זמינה כרגע. אפשר למלא את הנתונים ידנית.",
}
_RETRY = {"timeout", "quota", "unavailable", "invalid_response"}

_OUT_OF_RANGE = "מחוץ לטווח הסביר — נא לבדוק"
_LOW_CONFIDENCE = "לא ודאי — נא לבדוק"
_TWO_VALUES = "בתלוש מופיעים שני ערכים שונים — נא לבחור"
_WRONG_UNIT = "הופיע ביחידה אחרת מהנדרש — נא לבדוק"
_NOT_WHOLE = "צריך מספר שלם — נא לבדוק"


def sniff_mime(data: bytes) -> Optional[str]:
    """זיהוי סוג הקובץ מחתימת הבייטים (magic number). מחזיר None לסוג לא מוכר."""
    if data[:5] == b"%PDF-":
        return MIME_PDF
    if data[:3] == b"\xff\xd8\xff":
        return MIME_JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return MIME_PNG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return MIME_WEBP
    return None


def _pdf_is_encrypted(data: bytes) -> bool:
    """PDF מוצפן מכיל /Encrypt במילון ה-trailer. בדיקה קלה בלי תלות חיצונית."""
    return b"/Encrypt" in data


def _pdf_page_count(data: bytes) -> int:
    """אומדן מספר העמודים לפי אובייקטי /Type /Page (לא /Pages). מספיק כדי לדחות מסמך ארוך."""
    # סופר "/Type /Page" ו-"/Type/Page" אך לא "/Type /Pages".
    count = 0
    marker = b"/Type"
    i = data.find(marker)
    while i != -1:
        j = i + len(marker)
        while j < len(data) and data[j] in b" \t\r\n":
            j += 1
        if data[j:j + 5] == b"/Page" and data[j + 5:j + 6] != b"s":
            count += 1
        i = data.find(marker, i + len(marker))
    return count or 1


def validate_file(data: bytes, max_bytes: int, max_pages: int) -> str:
    """בודק סוג, גודל, שלמות, הצפנה ומספר עמודים. מחזיר את ה-MIME המזוהה או זורק PayslipError בעברית."""
    if not data:
        raise PayslipError(_EMPTY)
    if len(data) > max_bytes:
        raise PayslipError(_TOO_BIG.format(mb=max_bytes // (1024 * 1024)))
    mime = sniff_mime(data)
    if mime is None or mime not in ALLOWED_MIME:
        raise PayslipError(_BAD_TYPE)
    if mime == MIME_PDF:
        if b"%%EOF" not in data[-2048:]:
            raise PayslipError(_CORRUPT)      # cut off (a partial download): no end-of-file marker
        if _pdf_is_encrypted(data):
            raise PayslipError(_ENCRYPTED)
        if _pdf_page_count(data) > max_pages:
            raise PayslipError(_TOO_MANY_PAGES.format(pages=max_pages))
    return mime


# ---------- הממשק למודל הראייה ----------

class VisionPort(Protocol):
    """מודל שמקבל קובץ (bytes + mime) ובקשת מערכת, ומחזיר טקסט (JSON). מחלץ בלבד — לא מחשב."""

    def extract(self, data: bytes, mime: str, prompt: str, timeout_s: float) -> str:
        ...


class VisionError(Exception):
    """A vision adapter's failure, with the reason the employee is told about (FAIL_REASONS)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


# The keys the model may report. Profile fields first (what the review form can save), then information.
ITEM_KEYS = ("base_salary", "commissions", "bonus", "pension_employee_percent", "study_fund_employee_percent",
             "credit_points", "children_under_23", *INFO_KEYS)
_KEY_TO_FIELD = {"base_salary": "base_salary", "commissions": "commissions", "bonus": "bonus",
                 "pension_employee_percent": "pension_percent", "study_fund_employee_percent": "study_fund_percent",
                 "credit_points": "credit_points", "children_under_23": "children_under_23",
                 # the profile names, as an older answer (or a model ignoring the schema) may use them
                 "pension_percent": "pension_percent", "study_fund_percent": "study_fund_percent"}
# a percent field reported as an amount is information, never converted
_AMOUNT_OF = {"pension_percent": "pension_employee_amount", "study_fund_percent": "study_fund_employee_amount"}
_UNIT_OF_KIND = {"amount": "ILS", "percent": "percent", "count": "count"}

# Structured output (Gemini responseSchema, an OpenAPI subset): the answer's shape is enforced by the API,
# and checked again here.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "is_payslip": {"type": "BOOLEAN"},
        "readable": {"type": "BOOLEAN"},
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "key": {"type": "STRING", "enum": list(ITEM_KEYS)},
                    "label": {"type": "STRING"},
                    "value": {"type": "NUMBER"},
                    "unit": {"type": "STRING", "enum": ["ILS", "percent", "count"]},
                    "certainty": {"type": "STRING", "enum": ["high", "medium", "low"]},
                },
                "required": ["key", "value", "unit", "certainty"],
            },
        },
    },
    "required": ["is_payslip", "readable", "items"],
}

# הנחיית החילוץ: מה שמופיע בבירור, עם היחידה כפי שהודפסה. לא מחשבים, לא ממירים, לא מסווגים בלי ראיה.
_SYSTEM_PROMPT = """You read an Israeli salary payslip (תלוש שכר), as an image or a PDF, in Hebrew. Report what it
clearly shows, as JSON with: "is_payslip" (is this a payslip at all), "readable" (could you read its text), and
"items": one entry per value you find, with
- "key": one of
  base_salary (שכר יסוד, שכר בסיס, משכורת בסיס, שכר חודשי),
  commissions (עמלות, עמלת מכירות), bonus (בונוס, מענק, פרמיה),
  pension_employee_percent (the EMPLOYEE's pension rate, only when printed as a percent: תגמולי עובד %, פנסיה עובד %),
  study_fund_employee_percent (the EMPLOYEE's study-fund rate, only when printed as a percent),
  credit_points (נקודות זיכוי), children_under_23 (number of children: ילדים, מספר ילדים),
  other_addition (any other addition to pay: תוספת, תוספת חודשית, שעות נוספות, אחזקת רכב, החזר הוצאות ...),
  pension_employee_amount (the employee's pension deduction in NIS: פנסיה, ניכוי פנסיה, תגמולי עובד ₪, קופת גמל),
  study_fund_employee_amount (the employee's study-fund deduction in NIS),
  gross_salary (ברוטו, סה"כ תשלומים), net_pay (נטו, נטו לתשלום), income_tax (מס הכנסה),
  national_insurance (ביטוח לאומי), health_insurance (ביטוח בריאות, מס בריאות), car_use_value (שווי רכב, שווי שימוש);
- "label": the label exactly as printed next to the value (Hebrew, a few words, no numbers);
- "value": the number as printed (no currency sign, no thousands separator);
- "unit": "ILS" for money, "percent" for a rate printed with %, "count" for points or children;
- "certainty": "high" when label and value are clear, "medium" when the layout makes it likely, "low" otherwise.
Rules:
- Report only what is printed. Never calculate, never convert an amount into a percent, never guess a missing value.
- An addition whose type is not clearly commissions or bonus is other_addition, with its own label.
- A pension or study-fund deduction printed in NIS is the *_amount key, never the *_percent key.
- Take the EMPLOYEE's contribution, not the employer's.
- Do NOT report the employee's or employer's name, ID number, employee number, address, bank account or any other
  personal detail. Only the keys above.
- If the file is not a payslip, set is_payslip false and items []. If you cannot read it, set readable false.
Output JSON only."""


def _coerce_number(raw: Any) -> Optional[Decimal]:
    """מחרוזת/מספר מהמודל → Decimal; "unknown"/ריק/לא-מספר/בוליאני → None. פסיקים ו-₪ מוסרים."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None
    if isinstance(raw, str):
        text = raw.strip().replace(",", "").replace("₪", "").replace("%", "").strip()
        if not text or text.lower() in ("unknown", "n/a", "na", "none", "null"):
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def _parse_json(text: str) -> Optional[dict[str, Any]]:
    """JSON מהמודל, גם אם עטוף ב-```json … ``` או בטקסט. None כשאין אובייקט תקין (a failure, not an
    empty payslip)."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


_DIGITS = re.compile(r"\d")


def _safe_label(raw: Any) -> str:
    """The printed label, kept only when it is a short label: no digits (an ID, an account, an amount) and at
    most five words. Anything else is dropped rather than shown."""
    if not isinstance(raw, str):
        return ""
    label = " ".join(raw.split())[:40]
    if not label or _DIGITS.search(label) or len(label.split()) > 5:
        return ""
    return label


def _items(parsed: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """The answer as items. The schema's {"items": [...]}, or an older flat {"commissions": 1500, ...}.
    None when the answer has neither shape (an invalid response)."""
    if "items" in parsed:
        items = parsed["items"]
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else None
    return [{"key": k, "value": v, "unit": _UNIT_OF_KIND[FIELD_KIND[_KEY_TO_FIELD[k]]], "certainty": "medium"}
            for k, v in parsed.items() if k in _KEY_TO_FIELD]


def _in_range(snapshot: RulesSnapshot, field: str, value: Decimal) -> bool:
    ranges = snapshot.extensions.value_ranges
    if field not in ranges:
        return True
    low, high = ranges[field]
    return Decimal(low) <= value <= Decimal(high)


def _build(snapshot: RulesSnapshot, items: list[dict[str, Any]]) -> tuple[list[ExtractedField], list[InfoItem], int]:
    """Sorts the items into the profile fields (found / missing / ambiguous) and information items.
    Returns (fields, info, dropped)."""
    seen: dict[str, list[tuple[Decimal, str, str, str]]] = {f: [] for f in SUPPORTED_FIELDS}
    info: list[InfoItem] = []
    dropped = 0
    for item in items:
        key, value = item.get("key"), _coerce_number(item.get("value"))
        unit = item.get("unit") if item.get("unit") in ("ILS", "percent", "count") else ""
        certainty = item.get("certainty") if item.get("certainty") in ("high", "medium", "low") else "low"
        label = _safe_label(item.get("label"))
        if value is None or value < 0 or not isinstance(key, str):
            dropped += 1
            continue
        field = _KEY_TO_FIELD.get(key)
        if field is not None:
            if field in _AMOUNT_OF and unit == "ILS":           # a pension amount: information, not a percent
                info.append(InfoItem(_AMOUNT_OF[field], value, "amount", label))
                continue
            seen[field].append((value, unit, certainty, label))
        elif key in INFO_KEYS:
            info.append(InfoItem(key, value, INFO_KEYS[key][1], label))
        else:
            dropped += 1
    fields = [_field(snapshot, f, seen[f]) for f in SUPPORTED_FIELDS]
    return fields, info, dropped


def _field(snapshot: RulesSnapshot, field: str, found: list[tuple[Decimal, str, str, str]]) -> ExtractedField:
    kind = FIELD_KIND[field]
    if not found:
        return ExtractedField(field=field, value=None, kind=kind, confident=False, status="missing")
    labels = [lab for _, _, _, lab in found if lab]
    label = labels[0] if labels else ""
    if len({v for v, _, _, _ in found}) > 1:
        return ExtractedField(field=field, value=None, kind=kind, confident=False, status="ambiguous",
                              note=_TWO_VALUES, source_label=label, certainty="low")
    value, unit, certainty, _ = found[0]
    if unit and unit != _UNIT_OF_KIND[kind]:
        return ExtractedField(field=field, value=value, kind=kind, confident=False, status="ambiguous",
                              note=_WRONG_UNIT, source_label=label, certainty=certainty)
    if field == "children_under_23" and value != value.to_integral_value():
        return ExtractedField(field=field, value=value, kind=kind, confident=False, status="ambiguous",
                              note=_NOT_WHOLE, source_label=label, certainty=certainty)
    if field == "children_under_23":
        value = value.quantize(Decimal(1))
    if not _in_range(snapshot, field, value):
        return ExtractedField(field=field, value=value, kind=kind, confident=False, in_range=False,
                              status="ambiguous", note=_OUT_OF_RANGE, source_label=label, certainty=certainty)
    confident = certainty != "low"
    return ExtractedField(field=field, value=value, kind=kind, confident=confident, status="found",
                          note="" if confident else _LOW_CONFIDENCE, source_label=label, certainty=certainty)


def failed(reason: str) -> ExtractionResult:
    return ExtractionResult(ok=False, status="failed", reason=reason, retry=reason in _RETRY,
                            message_he=_FAIL_MESSAGES[reason])


def extract(data: bytes, mime: str, vision: Optional[VisionPort], snapshot: RulesSnapshot,
            timeout_s: float) -> ExtractionResult:
    """מריץ חילוץ על קובץ שכבר עבר validate_file. אף פעם לא זורק — מחזיר ExtractionResult עם status,
    כדי שה-API תמיד יחזיר תשובה ברורה ולעולם לא יציג כשל כהצלחה."""
    if vision is None:
        return failed("not_configured")
    try:
        raw = vision.extract(data, mime, _SYSTEM_PROMPT, timeout_s)
    except VisionError as exc:
        log.warning("payslip extraction failed: %s", exc)        # the reason (HTTP status / timeout), no content
        return failed(exc.reason if exc.reason in _FAIL_MESSAGES else "unavailable")
    except Exception as exc:  # כשל ספק לא צפוי — עוברים למילוי ידני, בלי לחשוף פרטים
        log.warning("payslip extraction failed: %s (%s)", type(exc).__name__, exc)
        return failed("unavailable")
    parsed = _parse_json(raw)
    items = _items(parsed) if parsed is not None else None
    if items is None:
        log.warning("payslip extraction failed: the model's answer has no usable shape (%d chars)", len(raw))
        return failed("invalid_response")
    if parsed.get("is_payslip") is False:
        log.info("payslip extraction: not a payslip")
        return ExtractionResult(ok=True, status="not_payslip", message_he=_MESSAGES["not_payslip"])
    if parsed.get("readable") is False and not items:
        log.info("payslip extraction: unreadable")
        return ExtractionResult(ok=True, status="unreadable", message_he=_MESSAGES["unreadable"])
    fields, info, dropped = _build(snapshot, items)
    found = sum(f.status == "found" for f in fields)
    unclear = sum(f.status == "ambiguous" for f in fields)
    # counts only: never a value, a label or the file
    log.info("payslip extraction: %d of %d fields found, %d ambiguous, %d info items, %d dropped",
             found, len(fields), unclear, len(info), dropped)
    if found == len(fields):
        status = "found"
    elif found or unclear:
        status = "partial"
    else:
        status = "nothing_found"
    return ExtractionResult(ok=True, status=status, fields=fields, info=info, message_he=_MESSAGES[status])
