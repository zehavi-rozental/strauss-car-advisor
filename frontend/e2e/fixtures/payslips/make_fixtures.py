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


# what a correct reading of synthetic_payslip.pdf says (base 18,000; the rest is information)
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

CASES = {
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
