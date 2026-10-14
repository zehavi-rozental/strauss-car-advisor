"""python -m app.llm_probe: שורה לכל רשומה — סטטוס וזמן, בלי מפתחות."""
from __future__ import annotations

import app.llm_probe as probe
from app.modules.conversation.llm.fake import ScriptedProvider
from app.modules.conversation.llm.port import LLMChain
from app.shared.settings import Settings


def test_probe_prints_status_and_latency_per_entry_without_keys(monkeypatch, capsys):
    settings = Settings(_env_file=None, llm_chain="gemini:a,gemini:b,gemini:c,groq:d,openrouter:e",
                        gemini_api_key="SECRET-GEMINI", groq_api_key="SECRET-GROQ")
    providers = [ScriptedProvider("gemini", "a"), ScriptedProvider("gemini", "b", [503]),
                 ScriptedProvider("gemini", "c", ["timeout"]), ScriptedProvider("groq", "d", [418])]
    chain = LLMChain(providers, disabled=[("openrouter:e", "no API key (OPENROUTER_API_KEY)")])
    seen = {}

    def fake_build(s):
        seen["timeout"] = s.llm_timeout_s
        return chain

    monkeypatch.setattr(probe, "get_settings", lambda: settings)
    monkeypatch.setattr(probe, "build_llm_chain", fake_build)

    assert probe.main(["--timeout", "15"]) == 0
    out = capsys.readouterr().out
    lines = {line.split()[0]: line for line in out.splitlines()[1:]}
    assert "ok" in lines["gemini:a"] and "ms" in lines["gemini:a"]
    assert "HTTP 503" in lines["gemini:b"] and "timeout" in lines["gemini:c"] and "HTTP 418" in lines["groq:d"]
    assert "disabled (no API key (OPENROUTER_API_KEY))" in lines["openrouter:e"]
    assert [p.calls for p in providers] == [1, 1, 1, 1]          # בקשה אחת לרשומה
    assert seen["timeout"] == 15
    assert "SECRET" not in out


def test_probe_exit_code_is_1_when_nothing_answers(monkeypatch, capsys):
    monkeypatch.setattr(probe, "get_settings", lambda: Settings(_env_file=None, llm_provider="none"))
    monkeypatch.setattr(probe, "build_llm_chain", lambda s: LLMChain([ScriptedProvider("gemini", "a", [503])]))
    assert probe.main([]) == 1
    assert "HTTP 503" in capsys.readouterr().out
