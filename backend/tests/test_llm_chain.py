"""שרשרת ספקים: סדר, מעבר לבא בתור, המתנה אחרי 429/418, ופלט /api/health."""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.factory import build_llm_chain, parse_chain
from app.modules.conversation.llm.fake import ScriptedProvider
from app.modules.conversation.llm.port import ChatMessage, LLMChain, LLMError
from app.shared.settings import Settings

from .conftest import make_client

MSG = [ChatMessage("user", "x")]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def chain(*providers, clock=None):
    return LLMChain(providers, cooldown_429_s=60, clock=clock or Clock())


# ---------- סדר ומעבר ----------

def test_first_entry_answers_and_the_rest_are_not_called():
    a, b = ScriptedProvider("gemini", "a"), ScriptedProvider("groq", "c")
    assert chain(a, b).complete(MSG).text == "ok from gemini:a"
    assert (a.calls, b.calls) == (1, 0)


@pytest.mark.parametrize("failure", [429, 418, 500, 503, "timeout", 401, 404])
def test_failure_moves_to_the_next_entry_in_order(failure):
    a = ScriptedProvider("gemini", "a", [failure])
    b = ScriptedProvider("gemini", "b", [failure])
    c = ScriptedProvider("groq", "c")
    assert chain(a, b, c).complete(MSG).text == "ok from groq:c"
    assert (a.calls, b.calls, c.calls) == (1, 1, 1)


def test_400_stops_the_chain():
    """בקשה שגויה תידחה בכל רשומה — לא מנסים את הבאות; התור עובר לתבניות."""
    a, b = ScriptedProvider("gemini", "a", [400]), ScriptedProvider("groq", "c")
    ch = chain(a, b)
    with pytest.raises(LLMError) as err:
        ch.complete(MSG)
    assert err.value.status == 400 and b.calls == 0
    assert ch.complete(MSG).text == "ok from gemini:a"     # בלי המתנה: הבקשה הבאה שונה


def test_all_entries_failing_raises():
    with pytest.raises(LLMError):
        chain(ScriptedProvider("gemini", "a", [503]), ScriptedProvider("groq", "c", ["timeout"])).complete(MSG)


# ---------- המתנה ----------

def test_429_skips_the_entry_for_the_cooldown():
    clock = Clock()
    a, b = ScriptedProvider("gemini", "a", [429]), ScriptedProvider("groq", "c")
    ch = chain(a, b, clock=clock)
    ch.complete(MSG)
    clock.now += 30
    assert ch.complete(MSG).text == "ok from groq:c"
    assert a.calls == 1                      # לא בזבזנו עליו קריאה בתוך ההמתנה
    clock.now += 31
    assert ch.complete(MSG).text == "ok from gemini:a"
    assert a.calls == 2


def test_418_skips_the_entry_until_restart():
    clock = Clock()
    a, b = ScriptedProvider("gemini", "a", [418]), ScriptedProvider("groq", "c")
    ch = chain(a, b, clock=clock)
    ch.complete(MSG)
    clock.now += 7 * 24 * 3600
    ch.complete(MSG)
    assert a.calls == 1


@pytest.mark.parametrize("failure", [500, "timeout"])
def test_5xx_and_timeout_do_not_cool_down(failure):
    a, b = ScriptedProvider("gemini", "a", [failure]), ScriptedProvider("groq", "c")
    ch = chain(a, b)
    ch.complete(MSG)
    assert ch.complete(MSG).text == "ok from gemini:a"   # נוסה שוב מיד


def test_everyone_cooling_down_fails_fast_without_calls():
    a, b = ScriptedProvider("gemini", "a", [429]), ScriptedProvider("gemini", "b", [418])
    ch = chain(a, b)
    with pytest.raises(LLMError):
        ch.complete(MSG)
    with pytest.raises(LLMError):
        ch.complete(MSG)
    assert (a.calls, b.calls) == (1, 1)


# ---------- הגדרות ----------

def test_parse_chain():
    assert parse_chain(" gemini:a , gemini:b,groq:c ,, ollama:llama3:8b") == [
        ("gemini", "a"), ("gemini", "b"), ("groq", "c"), ("ollama", "llama3:8b")]


def test_chain_from_settings_in_order_with_per_provider_keys():
    s = Settings(_env_file=None, llm_chain="gemini:a,gemini:b,groq:c,openrouter:d",
                 gemini_api_key="g", groq_api_key="q")
    ch = build_llm_chain(s)
    assert [e["entry"] for e in ch.status()] == ["gemini:a", "gemini:b", "groq:c", "openrouter:d"]
    assert [p.model for p in ch.providers] == ["a", "b", "c"]
    [disabled] = [e for e in ch.status() if e["status"] == "disabled"]
    assert disabled["entry"] == "openrouter:d" and "OPENROUTER_API_KEY" in disabled["detail"]
    assert ch.mode == "gemini+groq"


def test_legacy_settings_still_work():
    s = Settings(_env_file=None, llm_provider="gemini", llm_model="g", llm_api_key="k",
                 llm_fallback_provider="ollama", llm_fallback_model="llama3")
    assert [e["entry"] for e in build_llm_chain(s).status()] == ["gemini:g", "ollama:llama3"]


def test_chain_overrides_legacy_but_reuses_its_key():
    s = Settings(_env_file=None, llm_chain="gemini:new-model", llm_provider="gemini", llm_model="old",
                 llm_api_key="k")
    assert [e["entry"] for e in build_llm_chain(s).status()] == ["gemini:new-model"]


def test_no_provider_is_template_mode():
    assert build_llm_chain(Settings(_env_file=None, llm_provider="none")).mode == "templates"


# ---------- /api/health ----------

def test_health_lists_every_entry_and_its_last_status(engine):
    a = ScriptedProvider("gemini", "a", [429])
    b = ScriptedProvider("gemini", "b", [503])
    c = ScriptedProvider("groq", "c")
    ch = LLMChain([a, b, c], disabled=[("openrouter:d", "no API key (OPENROUTER_API_KEY)")])
    ch.complete(MSG)
    health = make_client(engine, llm=ch).get("/api/health").json()
    entries = {e["entry"]: e for e in health["llm_chain"]}
    assert list(entries) == ["gemini:a", "gemini:b", "groq:c", "openrouter:d"]
    assert entries["gemini:a"]["status"] == "429" and entries["gemini:a"]["cooling_down"].endswith("s")
    assert entries["gemini:b"]["status"] == "5xx" and entries["gemini:b"]["detail"] == "HTTP 503"
    assert entries["gemini:b"]["cooling_down"] is None
    assert entries["groq:c"]["status"] == "ok" and entries["groq:c"]["last_at"]
    assert entries["openrouter:d"]["status"] == "disabled"
    assert health["llm"] == "gemini+groq"


def test_health_before_any_call_and_after_418(engine):
    a, b = ScriptedProvider("gemini", "a", [418]), ScriptedProvider("groq", "c")
    ch = LLMChain([a, b])
    client = make_client(engine, llm=ch)
    assert [e["status"] for e in client.get("/api/health").json()["llm_chain"]] == ["untried", "untried"]
    ch.complete(MSG)
    first = client.get("/api/health").json()["llm_chain"][0]
    assert first["status"] == "418" and first["cooling_down"] == "until restart"
