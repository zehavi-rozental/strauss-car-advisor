"""חילוץ שדות נתמכים מתלוש (PDF/תמונה) דרך Gemini vision.

עקרונות פרטיות ואבטחה:
- הקובץ לא נשמר בשרת. הוא נכנס כ-bytes, נשלח ל-Gemini inline, ונזרק מהזיכרון בסוף הבקשה.
- מחולצים רק השדות ב-SUPPORTED_FIELDS (עמלות, בונוס, פנסיה, קרן השתלמות, נקודות זיכוי, ילדים).
  שם, ת"ז, מספר עובד, שכר בסיס — לא מבוקשים ולא מוחזרים. גם אם המודל יחזיר אותם, הם מסוננים כאן.
- כל ערך נבדק מול הטווחים של extensions.yaml. מחוץ לטווח → confident=False + note, אף פעם לא נשמר בשקט.
- ערך חסר או לא ודאי → value=None. אף פעם לא מניחים 0.
- הודעות שגיאה בעברית, בלי תוכן פרטי.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Protocol

from app.modules.rules.public import RulesSnapshot

from .models import (
    FIELD_KIND,
    SUPPORTED_FIELDS,
    ExtractedField,
    ExtractionResult,
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

_EXTRACT_OK = "סקרו את מה שזוהה מהתלוש, תקנו מה שצריך ואשרו. התלוש עצמו לא נשמר."
_EXTRACT_FAIL = "לא הצלחנו לקרוא נתונים מהתלוש. אפשר למלא את השדות ידנית."
_OUT_OF_RANGE = "מחוץ לטווח הסביר — נא לבדוק"
_LOW_CONFIDENCE = "לא ודאי — נא לבדוק"


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
    """בודק סוג, גודל, הצפנה ומספר עמודים. מחזיר את ה-MIME המזוהה או זורק PayslipError בעברית."""
    if not data:
        raise PayslipError(_EMPTY)
    if len(data) > max_bytes:
        raise PayslipError(_TOO_BIG.format(mb=max_bytes // (1024 * 1024)))
    mime = sniff_mime(data)
    if mime is None or mime not in ALLOWED_MIME:
        raise PayslipError(_BAD_TYPE)
    if mime == MIME_PDF:
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


# הנחיית החילוץ. מבקשת JSON של השדות הנתמכים בלבד, ומפרשת מה כל שדה הוא בתלוש ישראלי.
# לא מבקשת שם / ת"ז / שכר בסיס. "unknown" כשהערך לא מופיע בבירור — עדיף חסר על ניחוש.
_SYSTEM_PROMPT = """You read an Israeli salary payslip (תלוש שכר) image or PDF and extract ONLY these fields.
Return a single JSON object, no prose, with exactly these keys:
- "commissions": monthly commissions (עמלות), a number in NIS, or "unknown".
- "bonus": monthly bonus (בונוס / מענק), a number in NIS, or "unknown".
- "pension_percent": the EMPLOYEE's pension contribution rate (הפרשת עובד לפנסיה / תגמולים), a percent number (e.g. 6), or "unknown".
- "study_fund_percent": the EMPLOYEE's study-fund rate (הפרשת עובד לקרן השתלמות), a percent number, or "unknown".
- "credit_points": tax credit points (נקודות זיכוי), a number, or "unknown".
- "children_under_23": number of children up to age 23 (ילדים עד גיל 23), an integer, or "unknown".
Rules:
- Use "unknown" whenever the value is not clearly stated. Never guess. Never output 0 unless the payslip clearly shows 0.
- Percentages are the rate (6), not the amount of money. Do not confuse the employer rate with the employee rate; take the EMPLOYEE rate.
- Do NOT return the employee name, ID number, employee number, base salary, or any other field. Only the keys above.
- Output JSON only."""


def _coerce_number(raw: Any) -> Optional[Decimal]:
    """מחרוזת/מספר מהמודל → Decimal; "unknown"/ריק/לא-מספר → None. פסיקים ו-₪ מוסרים."""
    if raw is None:
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


def _parse_json(text: str) -> dict[str, Any]:
    """JSON מהמודל, גם אם עטוף ב-```json … ``` או בטקסט. מחזיר {} כשאין אובייקט תקין."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        parsed = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _in_range(snapshot: RulesSnapshot, field: str, value: Decimal) -> bool:
    ranges = snapshot.extensions.value_ranges
    if field not in ranges:
        return True
    low, high = ranges[field]
    return Decimal(low) <= value <= Decimal(high)


def _build_fields(snapshot: RulesSnapshot, parsed: dict[str, Any]) -> list[ExtractedField]:
    """בונה בדיוק את כל השדות הנתמכים. חסר/לא-מספר → value=None, confident=False.
    ערך תקין מחוץ לטווח → value נשמר לתצוגה אבל confident=False ו-in_range=False (דורש בדיקה ידנית)."""
    out: list[ExtractedField] = []
    for field in SUPPORTED_FIELDS:
        kind = FIELD_KIND[field]
        value = _coerce_number(parsed.get(field))
        if value is None:
            out.append(ExtractedField(field=field, value=None, kind=kind, confident=False, note=""))
            continue
        if kind == "count":
            value = Decimal(int(value))   # נקודות זיכוי/ילדים — מספר שלם לתצוגה
        in_range = _in_range(snapshot, field, value)
        out.append(ExtractedField(
            field=field, value=value, kind=kind,
            confident=in_range, in_range=in_range,
            note="" if in_range else _OUT_OF_RANGE,
        ))
    return out


def extract(data: bytes, mime: str, vision: Optional[VisionPort], snapshot: RulesSnapshot,
            timeout_s: float) -> ExtractionResult:
    """מריץ חילוץ על קובץ שכבר עבר validate_file. אין מודל ראייה/כשל → ok=False (מילוי ידני).
    אף פעם לא זורק — מחזיר ExtractionResult כדי שה-API תמיד יחזיר תשובה ברורה."""
    if vision is None:
        return ExtractionResult(ok=False, fields=[], message_he=_EXTRACT_FAIL)
    try:
        raw = vision.extract(data, mime, _SYSTEM_PROMPT, timeout_s)
    except Exception as exc:  # כשל ספק / timeout — עוברים למילוי ידני, בלי לחשוף פרטים
        log.warning("payslip extraction failed: %s", type(exc).__name__)
        return ExtractionResult(ok=False, fields=[], message_he=_EXTRACT_FAIL)
    parsed = _parse_json(raw)
    fields = _build_fields(snapshot, parsed)
    if not any(f.value is not None for f in fields):
        # המודל ענה אבל לא זיהה אף שדה נתמך — עדיין מצליח, אבל הטופס ריק לסקירה
        return ExtractionResult(ok=True, fields=fields, message_he=_EXTRACT_FAIL)
    return ExtractionResult(ok=True, fields=fields, message_he=_EXTRACT_OK)
