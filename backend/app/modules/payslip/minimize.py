"""What of a payslip may leave the server: the text of the pay lines, not the file.

A PDF with a text layer is read here, locally. What goes to the model is built from an allowed list, never from what is left after
removing: a line leaves only if it has a known pay word and a plausible figure, and of that line only those words and figures leave.
A name, an address, a bank or an ID has no way through unless it is spelled like a pay word. A photo or a scan has no text to read: it
would have to go as a whole, so that is allowed only where a deployment says so (`PAYSLIP_ALLOW_FULL_FILE`, synthetic files only).
"""
from __future__ import annotations

import io
import logging
import re

from .privacy import has_sensitive_word, names_a_payslip_item

log = logging.getLogger(__name__)

MAX_READ_PAGES = 6          # the upload's page limit is checked before; this never reads a whole crafted file

_MIN_TEXT = 12              # less than this is a scan with stray characters, not a text layer
_MAX_TEXT = 12_000
_PUNCTUATION = re.compile(r"[:;,()\[\]{}|/\\\"'׳״–—-]")
_GLUED = re.compile(r"(?<=[א-ת])(?=\d)|(?<=\d)(?=[א-ת])")
_NAME_LABEL = re.compile(r"(?:^|\s)(?:שם|לכבוד|מר|גב')(?:\s|:|$)")
_FIGURE = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?%?|\d{1,6}(?:\.\d{1,2})?%?")
_MAX_INTEGER_DIGITS = 6     # a monthly pay figure; anything longer is an ID, an account or a phone
_EDGES = " \t:;()[]{}₪\"'׳״,.-–—|/\\"
# on a payslip these name a person's file more often than a pay item, so they do not make a line a pay line
_NOT_ENOUGH = {"עובד", "עובדת"}


def pdf_text(data: bytes) -> str:
    """The PDF's own text, or "" (no text layer, unreadable, or the library is not installed)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf is not installed: a payslip's text cannot be read locally")
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in list(reader.pages)[:MAX_READ_PAGES])
    except Exception as exc:  # noqa: BLE001  a damaged file is no reason to fail the upload here
        log.info("payslip text could not be read (%s)", type(exc).__name__)
        return ""


def keep_pay_lines(text: str) -> str:
    """Only known pay words and plausible figures, from lines that have both; lines with an identifying word are not read at all."""
    kept = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or not any(ch.isdigit() for ch in line) or _NAME_LABEL.search(line):
            continue
        if has_sensitive_word(line) or has_sensitive_word(_PUNCTUATION.sub(" ", line)):        # "בנק:" is the word בנק
            continue
        line = _GLUED.sub(" ", line)        # a PDF's cells often come out with no space between a label and its figure
        out, figures, words = [], 0, 0
        for token in line.split():
            clean = token.strip(_EDGES)
            if _is_figure(token.strip(" :;()[]{}₪|")):
                out.append(token.strip(" :;()[]{}₪|"))
                figures += 1
            elif clean not in _NOT_ENOUGH and clean[::-1] not in _NOT_ENOUGH and (names_a_payslip_item(clean) or names_a_payslip_item(clean[::-1])):
                out.append(clean)        # a PDF may hold Hebrew letters in visual order
                words += 1
        if figures and words:
            kept.append(" ".join(out))
    return "\n".join(kept)[:_MAX_TEXT]


def _is_figure(token: str) -> bool:
    if not _FIGURE.fullmatch(token):
        return False
    return len(token.split(".")[0].replace(",", "").rstrip("%")) <= _MAX_INTEGER_DIGITS


def minimized_payslip_text(data: bytes, mime: str) -> str:
    """The text to send instead of the file, or "" when this file has none."""
    if mime != "application/pdf":
        return ""
    raw = pdf_text(data)
    text = keep_pay_lines(raw)
    return text if len(text) >= _MIN_TEXT else ""
