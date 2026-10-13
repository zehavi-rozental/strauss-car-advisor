"""בניית שרשרת הספקים מההגדרות: ראשי → גיבוי → (בלי ספק) תבניות."""
from __future__ import annotations

import logging
from typing import Optional

from app.shared.settings import Settings

from .gemini import GeminiAdapter
from .openai_compat import PRESETS, OpenAICompatAdapter
from .port import LLMChain, LLMPort

log = logging.getLogger(__name__)

NO_PROVIDER = {"", "none", "fake", "templates"}


def _provider(name: str, model: str, api_key: str, base_url: str, timeout_s: float) -> Optional[LLMPort]:
    name = name.strip().lower()
    if name in NO_PROVIDER:
        return None
    if not model:
        log.warning("llm provider=%s skipped: no model configured", name)
        return None
    if name == "gemini":
        if not api_key:
            log.warning("llm provider=gemini skipped: no API key")
            return None
        return GeminiAdapter(model, api_key, timeout_s=timeout_s)
    if name in PRESETS or base_url:
        if not api_key and name != "ollama":
            log.warning("llm provider=%s skipped: no API key", name)
            return None
        return OpenAICompatAdapter(name, model, api_key, base_url, timeout_s=timeout_s)
    log.warning("llm provider=%s unknown, skipped", name)
    return None


def build_llm_chain(settings: Settings) -> LLMChain:
    providers = [
        _provider(settings.llm_provider, settings.llm_model, settings.llm_api_key,
                  settings.llm_base_url, settings.llm_timeout_s),
        _provider(settings.llm_fallback_provider, settings.llm_fallback_model,
                  settings.llm_fallback_api_key, settings.llm_fallback_base_url, settings.llm_timeout_s),
    ]
    return LLMChain([p for p in providers if p is not None])
