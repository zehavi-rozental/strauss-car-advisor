"""No ID number, account, phone, address, e-mail or name from a payslip reaches the employee's screen,
whatever its format, and whether the model puts it in a label or in a value (privacy.py: three independent
checks — an allow list of payslip words, sensitive words and long numbers after removing separators, and a
plausible value for one monthly line)."""
from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from app.modules.payslip.extract import MIME_JPEG, extract
from app.modules.payslip.privacy import has_long_number, has_sensitive_word, plausible, safe_label

ID = "312456783"


class FakeVision:
    def __init__(self, response: str):
        self.response = response

    def extract(self, data, mime, prompt, timeout_s):
        return self.response


def answer(*items) -> str:
    return json.dumps({"is_payslip": True, "readable": True, "items": list(items)}, ensure_ascii=False)


def item(key, value, label, unit="ILS"):
    return {"key": key, "value": value, "unit": unit, "certainty": "high", "label": label}


SENSITIVE_LABELS = [
    "שכר יסוד 312456783",            # the ID inside a payslip label
    "שכר יסוד 312-456-783",
    "שכר יסוד 31 245 6783",
    "שכר יסוד 312.456.783",
    "שכר ת.ז. 312456783",
    'ת"ז עובד',                        # the ID's name, no number
    "ת.ז שכר",
    "תעודת זהות",
    "שכר מספר עובד",
    "מספר עובד 4711",
    "שכר דרכון",
    "חשבון בנק להעברת שכר",
    "שכר בנק 12-345-678901",
    "IL62 0108 0000 0009 9999 999",
    "טלפון 050-1234567",
    "נייד +972-50-1234567",
    "כתובת: רחוב הרצל",
    "שכר a@b.co",
    "שכר מייל",
    "ישראל ישראלי",                   # a name: not a payslip word
    "עובדת בדיקה ישראלי ישראלי בחברה בעמ",  # too long
]

ALLOWED_LABELS = ["שכר יסוד", "תוספת חודשית", "פנסיה — הפרשת עובד", "מספר ילדים עד גיל 23", "נטו לתשלום",
                  "קרן השתלמות עובד", "נקודות זיכוי", "עמלות מכירה", "שווי שימוש ברכב", 'סה"כ תשלומים']


@pytest.mark.parametrize("label", SENSITIVE_LABELS)
def test_a_sensitive_label_is_never_shown(label):
    assert safe_label(label) == ""


@pytest.mark.parametrize("label", ALLOWED_LABELS)
def test_a_payslip_label_is_shown(label):
    assert safe_label(label) == label


@pytest.mark.parametrize("text", ["312456783", "312-456-783", "31 245 6783", "0-312-456", "(03) 123-4567",
                                  "+972 50 123 4567", "IL62 0108 0000"])
def test_long_numbers_in_any_grouping(text):
    assert has_long_number(text)


@pytest.mark.parametrize("text", ["עד גיל 23", "6% פנסיה", "שכר 12 חודשים"])
def test_short_numbers_are_not_long_numbers(text):
    assert not has_long_number(text)


@pytest.mark.parametrize("text", ["2.25 נקודות", "שכר 09/26"])
def test_dots_and_slashes_join_digits_on_purpose(text):
    """Conservative: "312.456.783" must be caught, so "2.25" counts as 225 too. Only the LABEL is dropped then;
    the value is kept (labels name items; they rarely carry numbers)."""
    assert has_long_number(text)


@pytest.mark.parametrize("text", ['ת"ז', "ת.ז.", "ת׳ז", "תעודת זהות", "מספר עובד", "IBAN", "a@b.co", "דוא\"ל"])
def test_sensitive_words_in_any_spelling(text):
    assert has_sensitive_word(text)


def test_payslip_words_are_not_sensitive():
    # "עובד" alone is a payslip word (פנסיית עובד); only "מספר עובד" / "שם עובד" are sensitive
    assert not any(has_sensitive_word(w) for w in ("פנסיית עובד", "הפרשת מעסיק", "מס הכנסה", "שווי רכב"))


@pytest.mark.parametrize("value,kind,ok", [
    (Decimal(18000), "amount", True), (Decimal(999_999), "amount", True), (Decimal(1_000_000), "amount", False),
    (Decimal(312456783), "amount", False), (Decimal(6), "percent", True), (Decimal(101), "percent", False),
    (Decimal("2.25"), "count", True), (Decimal(312456783), "count", False), (Decimal(-1), "amount", False),
])
def test_plausible_values(value, kind, ok):
    assert plausible(value, kind) is ok


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


@pytest.mark.parametrize("label", SENSITIVE_LABELS)
def test_the_api_answer_never_carries_a_sensitive_label(snapshot, label):
    result = extract(b"\xff\xd8\xff" + b"\x00" * 32, MIME_JPEG,
                     FakeVision(answer(item("base_salary", 18000, label), item("net_pay", 12960, label))),
                     snapshot, timeout_s=10)
    shown = json.dumps(result.as_dict(), ensure_ascii=False)
    assert label not in shown
    assert ID not in _digits(shown)
    assert result.fields[0].value == Decimal(18000)      # the value itself is kept: only the label is dropped


@pytest.mark.parametrize("key,unit", [("base_salary", "ILS"), ("other_addition", "ILS"), ("net_pay", "ILS"),
                                      ("children_under_23", "count"), ("credit_points", "count"),
                                      ("pension_employee_percent", "percent")])
def test_an_id_number_reported_as_a_value_is_dropped_not_shown(snapshot, key, unit):
    result = extract(b"\xff\xd8\xff" + b"\x00" * 32, MIME_JPEG,
                     FakeVision(answer(item(key, int(ID), "שכר יסוד", unit), item(key, "312-456-783", "שכר", unit))),
                     snapshot, timeout_s=10)
    assert ID not in _digits(json.dumps(result.as_dict(), ensure_ascii=False))
    # never shown and never filled; on a profile field the employee is asked to check the payslip (22/10)
    assert all(f.value is None and f.status in ("missing", "ambiguous") for f in result.fields)
    assert result.info == []
