"""בניית שרשרת הספקים מההגדרות.

LLM_CHAIN="gemini:<model>,gemini:<model>,groq:<model>" — לפי הסדר, עם מפתח לכל ספק
(GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY). בלי LLM_CHAIN: LLM_PROVIDER/LLM_MODEL
ואחריו LLM_FALLBACK_* כמו קודם. בלי אף ספק — מצב תבניות.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.shared.settings import Settings

from .gemini import GeminiAdapter
from .openai_compat import PRESETS, OpenAICompatAdapter
from .port import LLMChain, LLMPort

log = logging.getLogger(__name__)

NO_PROVIDER = {"", "none", "fake", "templates"}
KEY_FIELDS = {"gemini": "gemini_api_key", "groq": "groq_api_key", "openrouter": "openrouter_api_key"}
NO_KEY_NEEDED = {"ollama"}


def parse_chain(text: str) -> list[tuple[str, str]]:
    """"gemini:a, groq:b" → [("gemini", "a"), ("groq", "b")]. רק הנקודתיים הראשונים מפרידים (ollama:llama3:8b)."""
    entries = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        provider, _, model = item.partition(":")
        entries.append((provider.strip().lower(), model.strip()))
    return entries


def _legacy_entries(settings: Settings) -> list[tuple[str, str]]:
    pairs = [(settings.llm_provider, settings.llm_model), (settings.llm_fallback_provider, settings.llm_fallback_model)]
    return [(p.strip().lower(), m.strip()) for p, m in pairs if p.strip().lower() not in NO_PROVIDER]


def _api_key(settings: Settings, provider: str) -> str:
    """מפתח ייעודי לספק; אחרת המפתח הישן אם הספק הוא LLM_PROVIDER / LLM_FALLBACK_PROVIDER."""
    specific = getattr(settings, KEY_FIELDS[provider]) if provider in KEY_FIELDS else ""
    if specific:
        return specific
    if provider == settings.llm_provider.strip().lower():
        return settings.llm_api_key
    if provider == settings.llm_fallback_provider.strip().lower():
        return settings.llm_fallback_api_key
    return ""


def _base_url(settings: Settings, provider: str) -> str:
    if provider == settings.llm_provider.strip().lower():
        return settings.llm_base_url
    if provider == settings.llm_fallback_provider.strip().lower():
        return settings.llm_fallback_base_url
    return ""


def _provider(settings: Settings, name: str, model: str) -> tuple[Optional[LLMPort], str]:
    """(מתאם, "") או (None, הסיבה שהרשומה מושבתת)."""
    if not model:
        return None, "no model configured"
    key, base_url, timeout = _api_key(settings, name), _base_url(settings, name), settings.llm_timeout_s
    if name == "gemini":
        if not key:
            return None, "no API key (GEMINI_API_KEY)"
        return GeminiAdapter(model, key, timeout_s=timeout), ""
    if name in PRESETS or base_url:
        if not key and name not in NO_KEY_NEEDED:
            return None, f"no API key ({KEY_FIELDS.get(name, 'LLM_API_KEY').upper()})"
        return OpenAICompatAdapter(name, model, key, base_url, timeout_s=timeout), ""
    return None, "unknown provider"


def build_llm_chain(settings: Settings) -> LLMChain:
    entries = parse_chain(settings.llm_chain) if settings.llm_chain.strip() else _legacy_entries(settings)
    providers: list[LLMPort] = []
    disabled: list[tuple[str, str]] = []
    for name, model in entries:
        if name in NO_PROVIDER:
            continue
        port, reason = _provider(settings, name, model)
        if port is None:
            log.warning("llm %s:%s disabled: %s", name, model, reason)
            disabled.append((f"{name}:{model}" if model else name, reason))
        else:
            providers.append(port)
    return LLMChain(providers, disabled=disabled, cooldown_429_s=settings.llm_cooldown_429_s)
