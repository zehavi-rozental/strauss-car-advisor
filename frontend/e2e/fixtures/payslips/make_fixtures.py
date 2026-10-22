"""Builds the payslip fixtures for the browser tests and their recorded model answers.

    python frontend/e2e/fixtures/payslips/make_fixtures.py

All payslips are SYNTHETIC (no real person, employer or ID). `synthetic_payslip.pdf` is the demo payslip
("תלוש שכר לדוגמה - נתונים סינתטיים בלבד"); the others are minimal PDFs that only need to pass the file checks.
The answers in vision/<sha256>.json are what the vision model is recorded to say about each file; the server
reads them when PAYSLIP_FAKE_VISION_DIR points at vision/ (tests only, never in a deployment).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
VISION = HERE / "vision"


def pdf(case: str) -> bytes:
    return f"%PDF-1.4\n% synthetic test payslip: {case}\n1 0 obj<</Type /Page>>endobj\n%%EOF\n".encode()


def item(key, value, unit="ILS", certainty="high", label=""):
    return {"key": key, "value": value, "unit": unit, "certainty": certainty, "label": label}


def answer(*items, is_payslip=True, readable=True):
    return {"is_payslip": is_payslip, "readable": readable, "items": list(items)}


# what gemini-3.5-flash-lite answered for synthetic_payslip.pdf in the live check (21/10): base 18,000; the rest
# is information (the same eight items, the same labels, all "high")
SYNTHETIC = answer(
    item("base_salary", 18000, label="שכר יסוד"), item("other_addition", 1000, label="תוספת חודשית"),
    item("gross_salary", 19000, label="שכר ברוטו"), item("income_tax", 2700, label="מס הכנסה"),
    item("national_insurance", 1100, label="ביטוח לאומי"), item("health_insurance", 900, label="ביטוח בריאות"),
    item("pension_employee_amount", 1140, label="פנסיית עובד"), item("net_pay", 12960, label="נטו לתשלום"),
)
# a payslip with every profile field (Noa Levi's reference values)
FULL = answer(
    item("base_salary", 18500, label="שכר יסוד"), item("commissions", 0, label="עמלות"), item("bonus", 0, label="בונוס"),
    item("pension_employee_percent", 6, "percent", label="תגמולי עובד"),
    item("study_fund_employee_percent", 2.5, "percent", label="קרן השתלמות עובד"),
    item("credit_points", 2.75, "count", label="נקודות זיכוי"), item("children_under_23", 2, "count", label="ילדים"),
    item("net_pay", 11000, label="נטו לתשלום"),
)
# two base salaries, an impossible pension rate and an unsure bonus
AMBIGUOUS = answer(
    item("base_salary", 18000, label="שכר יסוד"), item("base_salary", 19500, label="משכורת"),
    item("pension_employee_percent", 60, "percent", label="פנסיה"), item("bonus", 700, certainty="low", label="מענק"),
    item("credit_points", 2.25, "count", label="נקודות זיכוי"),
)

# The answer gemini-3.5-flash-lite actually gave for synthetic_full_payslip.pdf (live check, 21/10), item for
# item: whole amounts come back as 16000.0, and the server normalizes them.
LIVE_FULL = answer(
    item("credit_points", 2.25, "count", label="נקודות זיכוי"),
    item("children_under_23", 3, "count", label="מספר ילדים עד גיל 23"),
    item("base_salary", 16000.0, label="שכר יסוד"), item("commissions", 2400.0, label="עמלות מכירה"),
    item("bonus", 1500.0, label="בונוס"), item("gross_salary", 19900.0, label="שכר ברוטו"),
    item("pension_employee_percent", 6, "percent", label="פנסיה — הפרשת עובד"),
    item("study_fund_employee_percent", 2.5, "percent", label="קרן השתלמות — הפרשת עובד"),
    item("income_tax", 2950.0, label="מס הכנסה"), item("national_insurance", 1150.0, label="ביטוח לאומי"),
    item("health_insurance", 920.0, label="ביטוח בריאות"),
    item("pension_employee_amount", 1194.0, label="פנסיה עובד"),
    item("study_fund_employee_amount", 497.5, label="קרן השתלמות עובד"),
    item("net_pay", 13188.5, label="נטו לתשלום"),
)

# a synthetic ID (312456783) everywhere the model could put it: labels, values, an extra key
LEAKY = answer(
    item("base_salary", 16000, label="שכר יסוד ת.ז 312-456-783"), item("commissions", 2400, label="עמלות מכירה"),
    item("credit_points", 2.25, "count", label="נקודות זיכוי"), item("bonus", 312456783, label="בונוס"),
    item("children_under_23", 312456783, "count", label="ילדים"),
    item("other_addition", 1000, label="תוספת 31 245 6783"), item("net_pay", 12960, label="נטו לתשלום"),
    {"key": "id_number", "value": 312456783, "unit": "count", "certainty": "high", "label": "ת\"ז"},
)

# A car copay on a payslip with no commissions or bonus rows (QA 22/10): reported the way gemini-3.5-flash-lite
# reported it on 7 of 10 live payslips (as other_addition), next to a real addition and a use value read from
# reversed digits ("003,4" -> 3.4).
CAR_COPAY = answer(
    item("base_salary", 15000, label="שכר בסיס"), item("other_addition", 300, label="השתתפות עובד ברכב"),
    item("other_addition", 1000, label="תוספת חודשית"), item("car_use_value", 3.4, label="שווי שימוש ברכב"),
    item("credit_points", 2.25, "count", label="נקודות זיכוי"), item("net_pay", 11000, label="נטו לתשלום"),
)

# Labels that do not say what a line is (QA 22/10, second pass): a bonus "השתתפות", commissions with no label,
# an addition "השתתפות", next to a car copay that names the car and a clear addition.
UNCLEAR_LABELS = answer(
    item("base_salary", 15000, label="שכר בסיס"), item("bonus", 300, label="השתתפות"),
    {"key": "commissions", "value": 500, "unit": "ILS", "certainty": "high"},
    item("other_addition", 200, label="השתתפות"), item("other_addition", 1000, label="תוספת חודשית"),
    item("other_addition", 250, label="השתתפות עובד ברכב"),
)

CASES = {
    "car_copay_payslip.pdf": (pdf("a car copay, no commissions or bonus rows"), CAR_COPAY),
    "unclear_labels_payslip.pdf": (pdf("labels that do not say what a line is"), UNCLEAR_LABELS),
    "leaky_payslip.pdf": (pdf("an ID in labels and values"), LEAKY),
    # rendered from synthetic_full_payslip.html (Chromium, A4): a real document, read live on 21/10
    "synthetic_full_payslip.pdf": (None, LIVE_FULL),
    "synthetic_payslip.pdf": (None, SYNTHETIC),
    "full_payslip.pdf": (pdf("every field"), FULL),
    "ambiguous_payslip.pdf": (pdf("ambiguous"), AMBIGUOUS),
    "quota_payslip.pdf": (pdf("provider quota"), {"__error__": "quota"}),
    "rejected_payslip.pdf": (pdf("provider rejects"), {"__error__": "rejected"}),
    "not_a_payslip.pdf": (pdf("not a payslip"), answer(is_payslip=False)),
    "unreadable_payslip.pdf": (pdf("unreadable"), answer(readable=False)),
    "invalid_answer_payslip.pdf": (pdf("invalid answer"), "I am sorry, I cannot help with that."),
    "../synthetic_payslip.png": (None, answer(item("commissions", 2400, label="עמלות"),
                                             item("pension_employee_percent", 6, "percent", label="פנסיה"))),
}


def main() -> None:
    VISION.mkdir(exist_ok=True)
    for old in VISION.glob("*.json"):
        old.unlink()
    for name, (content, recorded) in CASES.items():
        path = HERE / name
        if content is not None:
            path.write_bytes(content)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        text = recorded if isinstance(recorded, str) else json.dumps(recorded, ensure_ascii=False)
        (VISION / f"{digest}.json").write_text(text, encoding="utf-8")
        print(f"{name}: {digest[:12]}")


if __name__ == "__main__":
    main()
