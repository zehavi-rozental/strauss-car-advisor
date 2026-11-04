"""What of a payslip may leave the server: the text of the pay lines, not the file.

A PDF with a text layer is read here, locally, and only its lines about pay go to the model: lines with an identifying word are
dropped, long numbers and contact details are masked. A photo or a scan has no text to read: it would have to go as a whole,
so that is allowed only where a deployment says so (`PAYSLIP_ALLOW_FULL_FILE`, a demo with synthetic files). This reduces what
is sent; it cannot promise that nothing identifying is left in a free-form document, so the screen says what is sent.
"""
from __future__ import annotations

import io
import logging
import re

from .privacy import has_sensitive_word

log = logging.getLogger(__name__)

_MIN_TEXT = 40              # less than this is a scan with stray characters, not a text layer
_MAX_TEXT = 12_000
_LONG_DIGITS = re.compile(r"(?<![\d.,])\d[\d\s-]{6,}\d(?![\d.,])")       # an ID, an account, a phone: never a pay figure
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_NAME_LABEL = re.compile(r"(?:^|\s)(?:שם|לכבוד|מר|גב')(?:\s|:|$)")


def pdf_text(data: bytes) -> str:
    """The PDF's own text, or "" (no text layer, unreadable, or the library is not installed)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf is not installed: a payslip's text cannot be read locally")
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001  a damaged file is no reason to fail the upload here
        log.info("payslip text could not be read (%s)", type(exc).__name__)
        return ""


def keep_pay_lines(text: str) -> str:
    """Only what can be a pay line: it has a digit, and nothing that names a person or an account."""
    kept = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or not any(ch.isdigit() for ch in line):
            continue
        if has_sensitive_word(line) or _NAME_LABEL.search(line):
            continue
        line = _EMAIL.sub(" ", line)
        kept.append(_LONG_DIGITS.sub("[הוסר]", line))
    return "\n".join(kept)[:_MAX_TEXT]


def minimized_payslip_text(data: bytes, mime: str) -> str:
    """The text to send instead of the file, or "" when this file has none."""
    if mime != "application/pdf":
        return ""
    raw = pdf_text(data)
    text = keep_pay_lines(raw)
    return text if len(text) >= _MIN_TEXT else ""
