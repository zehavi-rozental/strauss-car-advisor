"""Payslip extraction, second version (21/10): a regular payslip, not exactly six fields.

What the model reports is a list of items; code sorts them into the seven profile fields (found / missing /
ambiguous) and information items (an unclassified addition, the pension as an amount, gross, net, taxes),
and tells six outcomes apart (found, partial, nothing_found, not_payslip, unreadable, failed + reason).
No real Gemini call: the model is a fake that returns a fixed answer (the adapter is tested with an HTTP mock).
"""
from __future__ import annotations

import base64
import json
import logging
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.modules.conversation.public import LLMChain
from app.modules.payslip.extract import (
    MIME_JPEG,
    MIME_PDF,
    RESPONSE_SCHEMA,
    VisionError,
    extract,
    validate_file,
)
from app.modules.payslip.gemini_vision import GeminiVisionAdapter, GeminiVisionError
from app.modules.payslip.models import SUPPORTED_FIELDS, PayslipError
from app.modules.payslip.public import PayslipService
from app.shared.settings import Settings

from .conftest import BACKEND, build_engine

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF = b"%PDF-1.4\n1 0 obj<</Type /Page>>endobj\n%%EOF"


class FakeVision:
    def __init__(self, response: str = "", raises: Exception | None = None):
        self.response, self.raises, self.calls = response, raises, 0

    def extract(self, data, mime, prompt, timeout_s):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.response


def answer(*items, is_payslip=True, readable=True) -> str:
    return json.dumps({"is_payslip": is_payslip, "readable": readable, "items": list(items)}, ensure_ascii=False)


def item(key, value, unit="ILS", certainty="high", label=""):
    return {"key": key, "value": value, "unit": unit, "certainty": certainty, "label": label}


def run(snapshot, response: str):
    return extract(JPEG, MIME_JPEG, FakeVision(response), snapshot, timeout_s=10)


def by_field(result):
    return {f.field: f for f in result.fields}


# What a correct reading of the synthetic test_payslip.pdf looks like (base 18,000, "תוספת חודשית" 1,000,
# gross 19,000, taxes, "פנסיית עובד" 1,140 as an amount, net 12,960).
SYNTHETIC = answer(
    item("base_salary", 18000, label="שכר יסוד"),
    item("other_addition", 1000, label="תוספת חודשית"),
    item("gross_salary", 19000, label="שכר ברוטו"),
    item("income_tax", 2700, label="מס הכנסה"),
    item("national_insurance", 1100, label="ביטוח לאומי"),
    item("health_insurance", 900, label="ביטוח בריאות"),
    item("pension_employee_amount", 1140, label="פנסיית עובד"),
    item("net_pay", 12960, label="נטו לתשלום"),
)


def test_the_synthetic_payslip_gives_base_salary_and_keeps_the_rest_as_information(snapshot):
    result = run(snapshot, SYNTHETIC)
    assert (result.ok, result.status) == (True, "partial")
    fields = by_field(result)
    base = fields["base_salary"]
    assert (base.status, base.value, base.source_label, base.confident) == ("found", Decimal(18000), "שכר יסוד", True)
    # nothing invented or converted: no commissions / bonus from the addition, no percent from the amount
    for name in ("commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children_under_23"):
        assert (fields[name].status, fields[name].value) == ("missing", None), name
    info = {i.key: i for i in result.info}
    assert info["other_addition"].value == Decimal(1000) and info["other_addition"].source_label == "תוספת חודשית"
    assert info["pension_employee_amount"].value == Decimal(1140)
    assert info["net_pay"].value == Decimal(12960)
    notes = {i["key"]: i["note"] for i in result.as_dict()["info"]}
    assert "לא מומר" in notes["pension_employee_amount"]
    assert "לא מהנטו שבתלוש" in notes["net_pay"]
    assert "לא נכנסת לחישוב אוטומטית" in notes["other_addition"]


def test_every_field_found_is_found(snapshot):
    result = run(snapshot, answer(
        item("base_salary", 16000), item("commissions", 3000), item("bonus", 0),
        item("pension_employee_percent", 6, "percent"), item("study_fund_employee_percent", 2.5, "percent"),
        item("credit_points", 2.25, "count"), item("children_under_23", 3, "count")))
    assert result.status == "found"
    values = {f.field: f.value for f in result.fields}
    assert values == {"base_salary": Decimal(16000), "commissions": Decimal(3000), "bonus": Decimal(0),
                      "pension_percent": Decimal(6), "study_fund_percent": Decimal("2.5"),
                      "credit_points": Decimal("2.25"), "children_under_23": Decimal(3)}


def test_credit_points_keep_their_fraction(snapshot):
    """The first version cut credit points to a whole number (2.75 -> 2)."""
    result = run(snapshot, answer(item("credit_points", 2.75, "count")))
    assert by_field(result)["credit_points"].value == Decimal("2.75")


def test_a_pension_reported_as_money_under_the_percent_key_is_information(snapshot):
    result = run(snapshot, answer(item("pension_employee_percent", 1140, "ILS", label="פנסיה")))
    assert by_field(result)["pension_percent"].value is None
    assert [(i.key, i.value) for i in result.info] == [("pension_employee_amount", Decimal(1140))]
    assert result.status == "nothing_found"


def test_an_amount_field_printed_as_a_percent_is_ambiguous(snapshot):
    bonus = by_field(run(snapshot, answer(item("bonus", 10, "percent"))))["bonus"]
    assert (bonus.status, bonus.confident) == ("ambiguous", False) and "יחידה" in bonus.note


def test_two_different_values_are_ambiguous_and_two_equal_ones_are_found(snapshot):
    two = by_field(run(snapshot, answer(item("base_salary", 18000), item("base_salary", 19000))))["base_salary"]
    assert (two.status, two.value) == ("ambiguous", None)
    same = by_field(run(snapshot, answer(item("base_salary", 18000), item("base_salary", 18000))))["base_salary"]
    assert (same.status, same.value) == ("found", Decimal(18000))


def test_out_of_range_and_fractional_children_are_ambiguous(snapshot):
    fields = by_field(run(snapshot, answer(item("pension_employee_percent", 60, "percent"),
                                           item("children_under_23", 2.5, "count"))))
    assert (fields["pension_percent"].status, fields["pension_percent"].in_range) == ("ambiguous", False)
    assert fields["children_under_23"].status == "ambiguous"


def test_a_low_certainty_value_is_found_but_marked(snapshot):
    bonus = by_field(run(snapshot, answer(item("bonus", 500, certainty="low"))))["bonus"]
    assert (bonus.status, bonus.confident) == ("found", False) and bonus.note


def test_bad_items_and_personal_labels_are_dropped(snapshot):
    result = run(snapshot, answer(
        {"key": "employee_name", "value": 1, "unit": "count", "certainty": "high"},
        item("commissions", -50), item("commissions", "abc"), item("bonus", True),
        item("base_salary", 18000, label="שכר של עובד 123456789"),
        item("credit_points", 2, "count", label="נקודות זיכוי לעובד בדיקה בחברה פיקטיבית מאוד"),
    ))
    fields = by_field(result)
    assert fields["base_salary"].value == Decimal(18000) and fields["base_salary"].source_label == ""
    assert fields["credit_points"].source_label == ""            # more than five words: not shown
    assert fields["commissions"].status == "missing" and fields["bonus"].status == "missing"
    assert "123456789" not in json.dumps(result.as_dict(), ensure_ascii=False)


@pytest.mark.parametrize("response,status", [
    (answer(is_payslip=False), "not_payslip"),
    (answer(readable=False), "unreadable"),
    (answer(), "nothing_found"),
    ("{}", "nothing_found"),
])
def test_outcomes_without_values(snapshot, response, status):
    result = run(snapshot, response)
    assert (result.ok, result.status) == (True, status)
    assert all(f.value is None for f in result.fields)
    assert result.message_he and not any(ch.isdigit() for ch in result.message_he)


@pytest.mark.parametrize("response", ["I could not read this document.", '["base_salary", 18000]',
                                      '{"items": "base 18000"}', ""])
def test_an_answer_without_a_usable_shape_is_a_failure(snapshot, response):
    result = run(snapshot, response)
    assert (result.ok, result.status, result.reason, result.retry, result.fields) == (
        False, "failed", "invalid_response", True, [])


@pytest.mark.parametrize("raised,reason,retry", [
    (VisionError("timeout"), "timeout", True),
    (VisionError("quota"), "quota", True),
    (VisionError("unavailable"), "unavailable", True),
    (VisionError("rejected"), "rejected", False),
    (VisionError("not_configured"), "not_configured", False),
    (RuntimeError("anything"), "unavailable", True),
])
def test_each_failure_has_its_reason_message_and_retry(snapshot, raised, reason, retry):
    result = extract(JPEG, MIME_JPEG, FakeVision(raises=raised), snapshot, timeout_s=10)
    assert (result.ok, result.status, result.reason, result.retry) == (False, "failed", reason, retry)
    assert "ידנית" in result.message_he                         # manual entry is always offered


def test_no_vision_is_a_configuration_failure(snapshot):
    result = extract(JPEG, MIME_JPEG, None, snapshot, timeout_s=10)
    assert (result.status, result.reason, result.retry) == ("failed", "not_configured", False)


def test_the_log_has_counts_and_reasons_but_no_values_or_labels(snapshot, caplog):
    with caplog.at_level(logging.INFO, logger="app.modules.payslip.extract"):
        run(snapshot, SYNTHETIC)
        extract(JPEG, MIME_JPEG, FakeVision(raises=GeminiVisionError("quota", "HTTP 429 RESOURCE_EXHAUSTED")),
                snapshot, timeout_s=10)
    text = caplog.text
    assert "1 of 7 fields found" in text and "HTTP 429" in text
    for secret in ("18000", "12960", "1140", "שכר יסוד", "תוספת חודשית"):
        assert secret not in text


def test_a_cut_off_pdf_is_rejected_before_the_model():
    with pytest.raises(PayslipError, match="לא הצלחנו לקרוא את הקובץ"):
        validate_file(b"%PDF-1.4\n1 0 obj<</Type /Page>>", 10_000, 4)
    assert validate_file(PDF, 10_000, 4) == MIME_PDF


# ---------- the Gemini adapter (HTTP mocked) ----------

def adapter(handler) -> GeminiVisionAdapter:
    return GeminiVisionAdapter("gemini-test", "test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def respond(status, body):
    return lambda request: httpx.Response(status, json=body) if not isinstance(body, bytes) else \
        httpx.Response(status, content=body)


@pytest.mark.parametrize("handler,reason", [
    (respond(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}}), "quota"),
    (respond(503, {"error": {"status": "UNAVAILABLE", "message": "overloaded"}}), "unavailable"),
    (respond(400, {"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid."}}), "not_configured"),
    (respond(403, {"error": {"status": "PERMISSION_DENIED", "message": "denied"}}), "not_configured"),
    (respond(400, {"error": {"status": "INVALID_ARGUMENT", "message": "The document has no pages."}}), "rejected"),
    (respond(200, {"promptFeedback": {"blockReason": "OTHER"}}), "rejected"),
    (respond(200, {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}), "invalid_response"),
    (respond(200, b"<html>not json</html>"), "invalid_response"),
])
def test_adapter_maps_each_provider_answer_to_a_reason(handler, reason):
    with pytest.raises(GeminiVisionError) as exc:
        adapter(handler).extract(JPEG, MIME_JPEG, "prompt", 10)
    assert exc.value.reason == reason
    assert "test-key" not in str(exc.value)


def test_adapter_timeout():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GeminiVisionError) as exc:
        adapter(slow).extract(JPEG, MIME_JPEG, "prompt", 10)
    assert exc.value.reason == "timeout"


def test_adapter_asks_for_the_schema_and_sends_the_file_inline_with_the_key_in_a_header():
    seen = {}

    def ok(request):
        seen.update(body=json.loads(request.content), key=request.headers["x-goog-api-key"], url=str(request.url))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": SYNTHETIC}]}}]})

    assert adapter(ok).extract(PDF, MIME_PDF, "prompt", 10) == SYNTHETIC
    config = seen["body"]["generationConfig"]
    assert config["responseSchema"] == RESPONSE_SCHEMA and config["temperature"] == 0.0
    inline = seen["body"]["contents"][0]["parts"][1]["inlineData"]
    assert inline["mimeType"] == MIME_PDF and base64.b64decode(inline["data"]) == PDF
    assert seen["key"] == "test-key" and "test-key" not in seen["url"]


def test_the_schema_lists_every_key_the_code_understands():
    keys = set(RESPONSE_SCHEMA["properties"]["items"]["items"]["properties"]["key"]["enum"])
    assert {"base_salary", "other_addition", "pension_employee_amount", "net_pay"} <= keys
    assert not {"name", "id_number", "employee_name", "bank_account"} & keys


# ---------- the endpoint and the hand-off to the conversation ----------

def client_with(vision) -> TestClient:
    settings = Settings(_env_file=None, llm_provider="none", payslip_enabled=True)
    app = create_app(settings, engine=build_engine(), llm=LLMChain())
    app.state.ctx.payslip = PayslipService(app.state.ctx.rules, settings, vision=vision)
    return TestClient(app)


def test_endpoint_contract():
    body = client_with(FakeVision(SYNTHETIC)).post(
        "/api/payslip/extract", files={"file": ("p.pdf", PDF, "application/pdf")}).json()
    assert set(body) == {"ok", "status", "reason", "retry", "fields", "info", "message_he"}
    assert (body["ok"], body["status"], body["reason"]) == (True, "partial", "")
    assert [f["field"] for f in body["fields"]] == list(SUPPORTED_FIELDS)
    base = next(f for f in body["fields"] if f["field"] == "base_salary")
    assert set(base) == {"field", "label", "value", "kind", "confident", "in_range", "note", "status",
                         "source_label", "certainty"}
    assert (base["value"], base["status"], base["source_label"]) == ("18000", "found", "שכר יסוד")
    assert {i["key"] for i in body["info"]} >= {"other_addition", "pension_employee_amount", "net_pay"}
    assert all(set(i) == {"key", "label", "value", "kind", "source_label", "note"} for i in body["info"])


def test_endpoint_failure_contract():
    body = client_with(FakeVision(raises=VisionError("quota"))).post(
        "/api/payslip/extract", files={"file": ("p.pdf", PDF, "application/pdf")}).json()
    assert (body["ok"], body["status"], body["reason"], body["retry"], body["fields"], body["info"]) == (
        False, "failed", "quota", True, [], [])


PROFILE = "אני מנהלת מוצר, שכר בסיס 15,000, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"


def test_confirmed_payslip_values_reach_the_profile_and_the_calculation():
    """Upload -> review -> confirm sends {fields}; the base salary from the payslip replaces the typed one and
    the result is recalculated from it (not from the payslip's net)."""
    client = client_with(FakeVision(SYNTHETIC))
    cid = client.post("/api/conversations").json()["conversation_id"]
    send = lambda body: client.post(f"/api/conversations/{cid}/messages", json=body)  # noqa: E731
    for text in (PROFILE, "יש לי קורולה קרוס", "300", "אטו 3"):
        r = send({"text": text})
        assert r.status_code == 200, r.text
    before = client.get(f"/api/conversations/{cid}").json()
    assert before["profile"]["base"] == "15000" and before["results"]

    extracted = client.post("/api/payslip/extract", files={"file": ("p.pdf", PDF, "application/pdf")}).json()
    base = next(f for f in extracted["fields"] if f["field"] == "base_salary")["value"]
    r = send({"fields": {"base_salary": base}})
    assert r.status_code == 200, r.text
    assert "רשמתי: שכר בסיס" in r.json()["reply"]
    after = client.get(f"/api/conversations/{cid}").json()
    assert after["profile"]["base"] == "18000"
    old = before["results"][0]["result"]["alternative"]["net_display"]
    new = after["results"][0]["result"]["alternative"]["net_display"]
    assert new != old                                              # recalculated from the new base
    assert new != "12960"                                          # never the payslip's net


def test_a_base_salary_outside_the_range_is_refused_and_nothing_is_saved():
    client = client_with(FakeVision(SYNTHETIC))
    cid = client.post("/api/conversations").json()["conversation_id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": PROFILE})
    r = client.post(f"/api/conversations/{cid}/messages", json={"fields": {"base_salary": "999999"}})
    assert r.status_code == 422 and "שכר בסיס" in r.json()["detail"]
    assert client.get(f"/api/conversations/{cid}").json()["profile"]["base"] == "15000"


# ---------- the recorded vision model for browser tests ----------

def test_fixture_vision_answers_by_file_hash_and_simulates_failures(tmp_path, snapshot):
    import hashlib
    from app.modules.payslip.fixture_vision import FixtureVision
    from app.modules.payslip.public import build_vision

    ok, bad = PDF, PDF.replace(b"1.4", b"1.5")
    (tmp_path / f"{hashlib.sha256(ok).hexdigest()}.json").write_text(SYNTHETIC, encoding="utf-8")
    (tmp_path / f"{hashlib.sha256(bad).hexdigest()}.json").write_text('{"__error__": "quota"}', encoding="utf-8")
    vision = build_vision(Settings(_env_file=None, payslip_enabled=True, payslip_fake_vision_dir=str(tmp_path)))
    assert isinstance(vision, FixtureVision)
    assert extract(ok, MIME_PDF, vision, snapshot, 10).status == "partial"
    assert extract(bad, MIME_PDF, vision, snapshot, 10).reason == "quota"
    unknown = PDF.replace(b"1.4", b"1.6")
    assert extract(unknown, MIME_PDF, vision, snapshot, 10).reason == "rejected"


def test_fixture_vision_is_off_unless_asked_for():
    from app.modules.payslip.public import build_vision
    assert build_vision(Settings(_env_file=None, payslip_enabled=True, gemini_api_key="k",
                                 llm_chain="gemini:m")).__class__.__name__ == "GeminiVisionAdapter"
    assert build_vision(Settings(_env_file=None, payslip_enabled=False, payslip_fake_vision_dir="x")) is None


# ---------- the answers Gemini actually gave in the live check (21/10, gemini-3.5-flash-lite) ----------

FIXTURES = BACKEND.parent / "frontend" / "e2e" / "fixtures" / "payslips"


def _recorded(name: str) -> str:
    import hashlib
    digest = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
    return (FIXTURES / "vision" / f"{digest}.json").read_text(encoding="utf-8")


def test_the_live_answer_for_the_full_synthetic_payslip(snapshot):
    result = run(snapshot, _recorded("synthetic_full_payslip.pdf"))
    assert result.status == "found"
    d = {f["field"]: f for f in result.as_dict()["fields"]}
    assert {k: v["value"] for k, v in d.items()} == {
        "base_salary": "16000", "commissions": "2400", "bonus": "1500", "pension_percent": "6",
        "study_fund_percent": "2.5", "credit_points": "2.25", "children_under_23": "3"}   # 16000.0 → 16000
    assert d["children_under_23"]["source_label"] == "מספר ילדים עד גיל 23"            # "23" is not an ID
    info = {i["key"]: i["value"] for i in result.as_dict()["info"]}
    assert info["pension_employee_amount"] == "1194" and info["net_pay"] == "13188.5"


def test_the_live_answer_for_the_original_synthetic_payslip(snapshot):
    result = run(snapshot, _recorded("synthetic_payslip.pdf"))
    assert result.status == "partial"
    assert [(f.field, str(f.value)) for f in result.fields if f.status == "found"] == [("base_salary", "18000")]
    assert {i.key for i in result.info} == {"other_addition", "gross_salary", "income_tax", "national_insurance",
                                            "health_insurance", "pension_employee_amount", "net_pay"}


@pytest.mark.parametrize("raw,shown", [(16000.0, "16000"), ("2,400.00", "2400"), (12500.50, "12500.5")])
def test_numbers_are_shown_without_padding(snapshot, raw, shown):
    base = by_field(run(snapshot, answer(item("base_salary", raw))))["base_salary"]
    assert str(base.value) == shown


def test_labels_keep_small_numbers_but_never_long_ones(snapshot):
    fields = by_field(run(snapshot, answer(item("children_under_23", 2, "count", label="ילדים עד גיל 23"),
                                           item("base_salary", 18000, label="שכר לעובד 312456783"))))
    assert fields["children_under_23"].source_label == "ילדים עד גיל 23"
    assert fields["base_salary"].source_label == ""
