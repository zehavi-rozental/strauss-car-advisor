"""מודול התלוש: ולידציה של הקובץ, חילוץ, בדיקות טווח ופרטיות, ונקודת הקצה.

אין תלות ב-Gemini אמיתי — מודל הראייה מוזרק כמזויף. נבדק שהקובץ לא נשמר ושלא מוחזרים נתונים מזהים.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.payslip.extract import (
    MIME_JPEG,
    MIME_PDF,
    MIME_PNG,
    extract,
    sniff_mime,
    validate_file,
)
from app.modules.payslip.models import SUPPORTED_FIELDS, ExtractionResult, PayslipError
from app.modules.payslip.public import PayslipService, _first_gemini_model, build_vision
from app.shared.settings import Settings

from .conftest import build_engine
from fastapi.testclient import TestClient
from app.api.main import create_app
from app.modules.conversation.public import LLMChain


# ---------- קבצים לדוגמה (חתימות אמיתיות, תוכן מינימלי) ----------

def _pdf(pages: int = 1, encrypted: bool = False) -> bytes:
    body = b"%PDF-1.7\n"
    for i in range(pages):
        body += b"%d 0 obj<</Type /Page>>endobj\n" % (i + 1)
    if encrypted:
        body += b"trailer<</Encrypt 9 0 R>>\n"
    body += b"%%EOF"
    return body


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32


# ---------- מודל ראייה מזויף ----------

class FakeVision:
    def __init__(self, response: str = "", raises: Exception | None = None):
        self.response = response
        self.raises = raises
        self.calls: list[tuple[bytes, str]] = []

    def extract(self, data: bytes, mime: str, prompt: str, timeout_s: float) -> str:
        self.calls.append((data, mime))
        if self.raises is not None:
            raise self.raises
        return self.response


# ---------- sniff_mime ----------

def test_sniff_mime_detects_by_signature():
    assert sniff_mime(_pdf()) == MIME_PDF
    assert sniff_mime(JPEG) == MIME_JPEG
    assert sniff_mime(PNG) == MIME_PNG
    assert sniff_mime(WEBP) == "image/webp"
    assert sniff_mime(b"not a known file") is None
    assert sniff_mime(b"") is None


# ---------- validate_file ----------

def test_validate_rejects_empty():
    with pytest.raises(PayslipError):
        validate_file(b"", max_bytes=1000, max_pages=4)


def test_validate_rejects_oversize():
    big = JPEG + b"\x00" * 2000
    with pytest.raises(PayslipError):
        validate_file(big, max_bytes=100, max_pages=4)


def test_validate_rejects_unknown_type():
    with pytest.raises(PayslipError):
        validate_file(b"<html>fake</html>", max_bytes=10000, max_pages=4)


def test_validate_rejects_encrypted_pdf():
    with pytest.raises(PayslipError) as exc:
        validate_file(_pdf(encrypted=True), max_bytes=10000, max_pages=4)
    assert "סיסמה" in exc.value.message_he


def test_validate_rejects_too_many_pages():
    with pytest.raises(PayslipError):
        validate_file(_pdf(pages=10), max_bytes=100000, max_pages=4)


def test_validate_accepts_image_and_pdf():
    assert validate_file(JPEG, max_bytes=10000, max_pages=4) == MIME_JPEG
    assert validate_file(_pdf(pages=2), max_bytes=10000, max_pages=4) == MIME_PDF


# ---------- extract ----------

def test_extract_maps_supported_fields(snapshot):
    vision = FakeVision('{"commissions": 1500, "bonus": "unknown", "pension_percent": 6, '
                        '"study_fund_percent": 2.5, "credit_points": 2.25, "children_under_23": 2}')
    result = extract(JPEG, MIME_JPEG, vision, snapshot, timeout_s=10)
    assert result.ok is True
    by_field = {f.field: f for f in result.fields}
    assert set(by_field) == set(SUPPORTED_FIELDS)
    assert by_field["commissions"].value == Decimal("1500")
    assert by_field["commissions"].confident is True
    # חסר → None, לא 0
    assert by_field["bonus"].value is None
    assert by_field["bonus"].confident is False
    assert by_field["pension_percent"].value == Decimal("6")
    assert by_field["children_under_23"].value == Decimal("2")


def test_extract_out_of_range_flagged_not_confident(snapshot):
    # פנסיה 50% מחוץ לטווח (0–7): הערך מוצג אך מסומן לא ודאי, אף פעם לא נשמר בשקט
    vision = FakeVision('{"pension_percent": 50}')
    result = extract(JPEG, MIME_JPEG, vision, snapshot, timeout_s=10)
    pension = next(f for f in result.fields if f.field == "pension_percent")
    assert pension.value == Decimal("50")
    assert pension.in_range is False
    assert pension.confident is False
    assert pension.note


def test_extract_never_assumes_zero(snapshot):
    vision = FakeVision("{}")
    result = extract(JPEG, MIME_JPEG, vision, snapshot, timeout_s=10)
    assert all(f.value is None for f in result.fields)
    assert result.ok is True


def test_extract_handles_fenced_json(snapshot):
    vision = FakeVision('```json\n{"bonus": 500}\n```')
    result = extract(JPEG, MIME_JPEG, vision, snapshot, timeout_s=10)
    bonus = next(f for f in result.fields if f.field == "bonus")
    assert bonus.value == Decimal("500")


def test_extract_no_vision_falls_back_to_manual(snapshot):
    result = extract(JPEG, MIME_JPEG, None, snapshot, timeout_s=10)
    assert result.ok is False
    assert result.fields == []


def test_extract_provider_failure_falls_back(snapshot):
    vision = FakeVision(raises=RuntimeError("provider down"))
    result = extract(JPEG, MIME_JPEG, vision, snapshot, timeout_s=10)
    assert result.ok is False


def test_extract_result_dict_has_no_pii(snapshot):
    # גם אם המודל מחזיר שם/ת"ז, הם לא עוברים ל-as_dict
    vision = FakeVision('{"name": "ישראל ישראלי", "id_number": "123456789", '
                        '"base_salary": 18500, "commissions": 1000}')
    result = extract(JPEG, MIME_JPEG, vision, snapshot, timeout_s=10)
    d = result.as_dict()
    text = str(d)
    assert "ישראל" not in text
    assert "123456789" not in text
    assert "18500" not in text
    assert all(f["field"] in SUPPORTED_FIELDS for f in d["fields"])


# ---------- build_vision ----------

def test_build_vision_disabled_by_default():
    assert build_vision(Settings(_env_file=None)) is None


def test_build_vision_needs_api_key():
    assert build_vision(Settings(_env_file=None, payslip_enabled=True)) is None


def test_build_vision_ok_with_key_and_model():
    v = build_vision(Settings(_env_file=None, payslip_enabled=True, gemini_api_key="k",
                              payslip_model="gemini-x"))
    assert v is not None


def test_first_gemini_model_from_chain():
    assert _first_gemini_model("groq:a, gemini:gemini-3.8-flash, gemini:other") == "gemini-3.8-flash"
    assert _first_gemini_model("groq:a") == ""


# ---------- PayslipService ----------

def test_service_disabled_when_no_vision(snapshot):
    from app.modules.rules.public import RulesStore
    rules = _rules_store()
    svc = PayslipService(rules, Settings(_env_file=None), vision=None)
    assert svc.enabled is False


def test_service_extracts_with_injected_vision():
    rules = _rules_store()
    vision = FakeVision('{"commissions": 1200}')
    svc = PayslipService(rules, Settings(_env_file=None, payslip_enabled=True), vision=vision)
    assert svc.enabled is True
    result = svc.extract(JPEG)
    assert result.ok is True
    comm = next(f for f in result.fields if f.field == "commissions")
    assert comm.value == Decimal("1200")


def _rules_store():
    from pathlib import Path
    from app.modules.rules.public import RulesStore
    config = Path(__file__).resolve().parents[1] / "config"
    return RulesStore(config, "dev")


# ---------- נקודת הקצה ----------

def _client_with_vision(vision) -> TestClient:
    settings = Settings(_env_file=None, llm_provider="none", payslip_enabled=True)
    engine = build_engine()
    app = create_app(settings, engine=engine, llm=LLMChain())
    svc = PayslipService(app.state.ctx.rules, settings, vision=vision)
    app.state.ctx.payslip = svc
    return TestClient(app)


def test_endpoint_503_when_disabled():
    settings = Settings(_env_file=None, llm_provider="none")   # payslip_enabled=False
    engine = build_engine()
    app = create_app(settings, engine=engine, llm=LLMChain())
    client = TestClient(app)
    r = client.post("/api/payslip/extract", files={"file": ("p.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 503


def test_endpoint_extracts_supported_fields():
    vision = FakeVision('{"commissions": 1500, "pension_percent": 6}')
    client = _client_with_vision(vision)
    r = client.post("/api/payslip/extract", files={"file": ("p.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    by_field = {f["field"]: f for f in body["fields"]}
    assert by_field["commissions"]["value"] == "1500"
    assert set(by_field) == set(SUPPORTED_FIELDS)


def test_endpoint_rejects_bad_file_type():
    vision = FakeVision('{"commissions": 1500}')
    client = _client_with_vision(vision)
    r = client.post("/api/payslip/extract", files={"file": ("p.txt", b"hello world", "text/plain")})
    assert r.status_code == 422
    assert vision.calls == []   # קובץ לא תקין לא מגיע בכלל למודל


def test_endpoint_rejects_oversize():
    vision = FakeVision('{"commissions": 1}')
    settings = Settings(_env_file=None, llm_provider="none", payslip_enabled=True, payslip_max_bytes=50)
    engine = build_engine()
    app = create_app(settings, engine=engine, llm=LLMChain())
    app.state.ctx.payslip = PayslipService(app.state.ctx.rules, settings, vision=vision)
    client = TestClient(app)
    r = client.post("/api/payslip/extract",
                    files={"file": ("p.jpg", JPEG + b"\x00" * 200, "image/jpeg")})
    assert r.status_code == 413


def test_endpoint_response_has_no_pii():
    vision = FakeVision('{"name": "ישראל ישראלי", "id_number": "123456789", "commissions": 1000}')
    client = _client_with_vision(vision)
    r = client.post("/api/payslip/extract", files={"file": ("p.jpg", JPEG, "image/jpeg")})
    assert "123456789" not in r.text
    assert "ישראל" not in r.text
