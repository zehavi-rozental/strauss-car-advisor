"""Who processes the data, decided on the server (5/11; app.shared.ai_policy).

The screens said "Google Gemini" and "זו הדגמה, אעלה רק תלוש לדוגמה" in their own code. Now the processing policy says
it: the provider, where it runs, the environment (test unless the organization approved real data), what of a payslip
leaves, and which notices are asked. A provider the adapter only reaches over a public API is never "organization".
"""
from __future__ import annotations

from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.llm.port import LLMChain
from app.shared.ai_policy import ORGANIZATION, ProcessingPolicy, Provider, policy_from
from app.shared.settings import Settings

from .conftest import make_client


def settings(**kw) -> Settings:
    return Settings(_env_file=None, llm_chain="gemini:gemini-3.5-flash-lite", **kw)


def test_the_default_is_a_test_environment_with_an_external_provider():
    p = policy_from(settings())
    assert (p.environment, p.chat.name, p.chat.short, p.chat.hosting) == ("test", "Google Gemini", "Gemini", "external")
    assert p.ai_consent_required and p.payslip_consent_required
    assert p.payslip_minimized and not p.payslip_full_file and p.payslip_formats == ["pdf"]
    assert "Google Gemini" in p.payslip_notice() and "תלוש לדוגמה" in p.payslip_notice()
    assert "לדוגמה" in p.payslip_acknowledgement()


def test_gemini_is_never_inside_the_organization_whatever_the_setting_says():
    p = policy_from(settings(ai_provider_hosting="organization", payslip_provider_hosting="organization"))
    assert p.chat.external and p.payslip.external and p.payslip_minimized


def test_an_approved_deployment_says_no_word_about_a_demo_and_still_asks_for_an_external_provider():
    p = policy_from(settings(real_data_approved=True))
    assert p.environment == "production"
    assert "לדוגמה" not in p.payslip_notice() and "בדיקות" not in p.payslip_notice()
    assert p.payslip_consent_required and "Google Gemini" in p.payslip_acknowledgement()
    assert p.payslip_formats == ["pdf"]                     # an external reader: the pay lines of a PDF only


def test_an_organization_model_reads_any_payslip_and_needs_no_checkbox():
    org = Provider(id="openai", name="Strauss AI", short="Strauss AI", hosting=ORGANIZATION)
    p = ProcessingPolicy(chat=org, payslip=org, real_data_approved=True, allow_full_file_setting=False,
                         require_ai_consent_setting=True)
    assert not p.ai_consent_required and not p.payslip_consent_required and p.payslip_acknowledgement() == ""
    assert p.payslip_full_file and p.payslip_formats == ["pdf", "image"] and not p.payslip_minimized
    assert "Strauss AI" in p.ai_notice() and "לא יוצאים מהארגון" in p.payslip_notice()


def test_an_openai_compatible_model_can_be_named_and_placed_inside_the_organization():
    p = policy_from(Settings(_env_file=None, llm_chain="openai:strauss-llm", ai_provider_name="Strauss AI",
                             ai_provider_hosting="organization", real_data_approved=True))
    assert (p.chat.name, p.chat.hosting, p.ai_consent_required) == ("Strauss AI", "organization", False)
    assert p.payslip.external                                # the payslip reader is still Gemini: still external


def test_a_new_provider_asks_the_notice_again():
    a = policy_from(settings()).ai_notice_version
    b = policy_from(settings(ai_provider_name="Other")).ai_notice_version
    assert a != b
    assert policy_from(settings()).payslip_notice_version != policy_from(settings(real_data_approved=True)).payslip_notice_version


def test_the_view_carries_the_policy_and_the_screen_writes_nothing_of_its_own(engine):
    client = make_client(engine, llm=LLMChain([FakeLLM()]), require_ai_consent=True,
                         llm_chain="gemini:gemini-3.5-flash-lite")
    cid = client.post("/api/conversations").json()["conversation_id"]
    privacy = client.get(f"/api/conversations/{cid}").json()["privacy"]
    assert privacy["environment"] == "test" and privacy["demo"] is True
    assert privacy["ai"]["provider"] == "Gemini" and "Google Gemini" in privacy["ai"]["notice"]
    assert privacy["payslip"]["required"] and privacy["payslip"]["formats"] == ["pdf"]
    assert "לדוגמה" in privacy["payslip"]["acknowledgement"]


def test_a_notice_accepted_for_another_provider_is_asked_again(engine):
    client = make_client(engine, llm=LLMChain([FakeLLM()]), require_ai_consent=True)
    cid = client.post("/api/conversations").json()["conversation_id"]
    service = client.app.state.ctx.service
    client.post(f"/api/conversations/{cid}/consent",
                json={"kind": "ai_processing", "accepted": True, "version": service.notice_versions["ai_processing"]})
    assert client.get(f"/api/conversations/{cid}").json()["privacy"]["ai"]["accepted"] is True
    service.notice_versions["ai_processing"] = "2026-11.test.external.another-provider"
    assert client.get(f"/api/conversations/{cid}").json()["privacy"]["ai"]["accepted"] is False


def test_an_upload_in_a_test_environment_needs_the_sample_only_acknowledgement(engine):
    client = make_client(engine, payslip_enabled=True)
    cid = client.post("/api/conversations").json()["conversation_id"]
    pdf = {"file": ("p.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")}
    r = client.post("/api/payslip/extract", files=pdf, data={"conversation_id": cid})
    assert r.status_code in (422, 503)                        # without the yes, nothing is read (503: no reader here)
