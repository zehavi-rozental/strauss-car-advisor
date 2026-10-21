"""Payslip privacy through the whole path (upload → review → {fields} → profile → engine), 21/10:

1. legitimate pay values (16,000 ₪, 2,400 ₪, 2.25 credit points) are never rejected by the privacy checks;
2. a sensitive identifier on the payslip reaches no error message, no log line and no conversation history;
3. what the privacy checks dropped never reaches the calculation (it cannot be sent: {fields} accepts only the
   seven profile fields, and the profile the engine reads is unchanged);
4. a partial payslip still lets the employee confirm the valid values that were found.
"""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.modules.conversation.public import LLMChain
from app.modules.payslip.extract import MIME_PDF, VisionError, extract
from app.modules.payslip.public import PayslipService
from app.shared.settings import Settings

from . import reference_oracle as oracle
from .conftest import build_engine

PDF = b"%PDF-1.4\n1 0 obj<</Type /Page>>endobj\n%%EOF"
ID = "312456783"
ID_FORMS = ["312456783", "312-456-783", "31 245 6783", "312.456.783"]
NOA = "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"


class FakeVision:
    def __init__(self, response: str = "", raises: Exception | None = None):
        self.response, self.raises = response, raises

    def extract(self, data, mime, prompt, timeout_s):
        if self.raises is not None:
            raise self.raises
        return self.response


def item(key, value, label="", unit="ILS"):
    return {"key": key, "value": value, "unit": unit, "certainty": "high", "label": label}


def answer(*items) -> str:
    return json.dumps({"is_payslip": True, "readable": True, "items": list(items)}, ensure_ascii=False)


# a partial payslip with the ID in every place the model could put it: labels, values, an extra key
LEAKY = answer(
    item("base_salary", 16000, f"שכר יסוד ת.ז {ID_FORMS[1]}"),
    item("commissions", 2400, "עמלות מכירה"),
    item("credit_points", 2.25, "נקודות זיכוי", "count"),
    item("bonus", int(ID), "בונוס"),                                   # the ID as an "amount"
    item("children_under_23", int(ID), "ילדים", "count"),             # the ID as a "count"
    item("other_addition", 1000, f"תוספת {ID_FORMS[2]}"),
    item("net_pay", 12960, "נטו לתשלום"),
    {"key": "id_number", "value": int(ID), "unit": "count", "certainty": "high", "label": "ת\"ז"},
)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def client_with(vision) -> TestClient:
    settings = Settings(_env_file=None, llm_provider="none", payslip_enabled=True)
    app = create_app(settings, engine=build_engine(), llm=LLMChain())
    app.state.ctx.payslip = PayslipService(app.state.ctx.rules, settings, vision=vision)
    return TestClient(app)


# ---------- 1. legitimate values pass ----------

@pytest.mark.parametrize("key,value,unit,label", [
    ("base_salary", 16000, "ILS", "שכר יסוד"), ("base_salary", "16,000.00", "ILS", "שכר יסוד 16,000"),
    ("commissions", 2400, "ILS", "עמלות"), ("commissions", 2400.0, "ILS", "עמלות מכירה 2,400"),
    ("credit_points", 2.25, "count", "נקודות זיכוי"), ("credit_points", "2.25", "count", "נקודות זיכוי 2.25"),
    ("bonus", 999_999, "ILS", "בונוס שנתי"),
])
def test_legitimate_pay_values_are_never_rejected(snapshot, key, value, unit, label):
    result = extract(PDF, MIME_PDF, FakeVision(answer(item(key, value, label, unit))), snapshot, timeout_s=10)
    field = {"base_salary": "base_salary", "commissions": "commissions", "credit_points": "credit_points",
             "bonus": "bonus"}[key]
    f = next(x for x in result.fields if x.field == field)
    expected = Decimal(str(value).replace(",", ""))
    assert f.value == expected and f.status in ("found", "ambiguous")      # never dropped
    if key != "bonus":                                                     # 999,999 is outside value_ranges
        assert f.status == "found"


# ---------- 2. no sensitive data in errors, logs or history ----------

def test_nothing_sensitive_in_the_answer_or_the_logs(snapshot, caplog):
    with caplog.at_level(logging.DEBUG):
        result = extract(PDF, MIME_PDF, FakeVision(LEAKY), snapshot, timeout_s=10)
    shown = json.dumps(result.as_dict(), ensure_ascii=False)
    assert ID not in _digits(shown) and ID not in _digits(caplog.text)
    assert "ת.ז" not in shown and "ת\"ז" not in shown


@pytest.mark.parametrize("raised", [VisionError("rejected", f"bad file {ID}"), ValueError(f"cannot parse {ID}"),
                                    RuntimeError(f"timeout reading 'שכר יסוד {ID_FORMS[1]}'")])
def test_failures_carry_nothing_sensitive_into_messages_or_logs(snapshot, caplog, raised):
    with caplog.at_level(logging.DEBUG):
        result = extract(PDF, MIME_PDF, FakeVision(raises=raised), snapshot, timeout_s=10)
    assert result.status == "failed"
    assert ID not in _digits(result.message_he) and not re.search(r"\d", result.message_he)
    if not isinstance(raised, VisionError):                              # our own errors carry the HTTP detail only
        assert ID not in _digits(caplog.text)


def test_the_whole_path_keeps_the_id_out_of_the_api_the_history_and_the_logs(caplog):
    client = client_with(FakeVision(LEAKY))
    with caplog.at_level(logging.DEBUG):
        cid = client.post("/api/conversations").json()["conversation_id"]
        client.post(f"/api/conversations/{cid}/messages", json={"text": "מנהלת מוצר, שכר בסיס 15,000"})
        extracted = client.post("/api/payslip/extract", files={"file": ("p.pdf", PDF, "application/pdf")})
        kept = {f["field"]: f["value"] for f in extracted.json()["fields"] if f["status"] == "found"}
        r = client.post(f"/api/conversations/{cid}/messages", json={"fields": kept})
        view = client.get(f"/api/conversations/{cid}").json()
    assert r.status_code == 200, r.text
    for text in (extracted.text, r.text, json.dumps(view, ensure_ascii=False), caplog.text):
        assert ID not in _digits(text)


# ---------- 3. what was dropped never reaches the calculation ----------

def test_dropped_values_cannot_be_sent_and_never_reach_the_engine():
    client = client_with(FakeVision(LEAKY))
    cid = client.post("/api/conversations").json()["conversation_id"]
    send = lambda body: client.post(f"/api/conversations/{cid}/messages", json=body)  # noqa: E731
    for text in (NOA, "יש לי קורולה קרוס", "300", "אטו 3"):
        send({"text": text})
    before = client.get(f"/api/conversations/{cid}").json()

    extracted = client.post("/api/payslip/extract", files={"file": ("p.pdf", PDF, "application/pdf")}).json()
    fields = {f["field"]: f for f in extracted["fields"]}
    assert fields["bonus"]["value"] is None and fields["children_under_23"]["value"] is None   # the ID: dropped
    # a client cannot send what is not a profile field, nor an implausible value
    assert send({"fields": {"id_number": ID}}).status_code == 422
    assert send({"fields": {"bonus": int(ID)}}).status_code == 422
    assert send({"fields": {"children_under_23": int(ID)}}).status_code == 422
    after = client.get(f"/api/conversations/{cid}").json()
    assert after["profile"] == before["profile"]                          # nothing changed
    assert after["results"][0]["result"]["delta_display"] == "661"         # Noa's reference result, unchanged


# ---------- 4. a partial payslip: the valid values can be confirmed ----------

def test_a_partial_payslip_lets_the_employee_confirm_the_valid_values():
    client = client_with(FakeVision(LEAKY))
    cid = client.post("/api/conversations").json()["conversation_id"]
    send = lambda body: client.post(f"/api/conversations/{cid}/messages", json=body)  # noqa: E731
    send({"text": "מנהלת מוצר, שכר בסיס 15,000"})
    extracted = client.post("/api/payslip/extract", files={"file": ("p.pdf", PDF, "application/pdf")}).json()
    assert extracted["status"] == "partial"
    found = {f["field"]: f["value"] for f in extracted["fields"] if f["status"] == "found"}
    assert found == {"base_salary": "16000", "commissions": "2400", "credit_points": "2.25"}
    r = send({"fields": found})
    assert r.status_code == 200 and "רשמתי" in r.json()["reply"]
    p = client.get(f"/api/conversations/{cid}").json()["profile"]
    assert (p["base"], p["commissions"], p["credit_points"]) == ("16000", "2400", "2.25")
    assert p["bonus"] is None and p["children"] is None                   # never filled from the dropped ID

    # and the rest of the journey is calculated from the confirmed values (checked with the oracle)
    for text in ("אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2 ילדים", "יש לי קורולה קרוס", "300", "אטו 3"):
        send({"text": text})
    view = client.get(f"/api/conversations/{cid}").json()
    rules, data = oracle.load_rules(), oracle.load_data()
    emp = oracle.Employee(id="x", name="", occupation="מנהל/ת מוצר", group="ג", base=Decimal(16000),
                          commissions=Decimal(2400), bonus=Decimal(0), pension_pct=Decimal(6),
                          study_pct=Decimal("2.5"), points=Decimal("2.25"), children=2,
                          current_car="COROLLA CROSS", current_copay=Decimal(300))
    today = oracle.evaluate(emp, oracle.Situation("t", car=data.cars["COROLLA CROSS"], copay=Decimal(300)), rules)
    alt = oracle.evaluate(emp, oracle.Situation("a", car=data.cars["ATTO 3"], copay=data.cars["ATTO 3"].default_copay),
                          rules)
    assert Decimal(view["results"][0]["result"]["delta_display"]) == oracle.shekel(alt.net) - oracle.shekel(today.net)
