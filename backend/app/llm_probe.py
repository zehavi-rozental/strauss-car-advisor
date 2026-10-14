"""מדידת ספקי מודל השפה: בקשה זעירה אחת לכל רשומה ב-LLM_CHAIN.

    python -m app.llm_probe [--timeout S]

מדפיס לכל רשומה: provider:model, ok / קוד HTTP / timeout / שגיאה, וזמן במילישניות.
לא מדפיס מפתחות. בקשה אחת לרשומה — כמעט לא צורך מכסה. מתעלם מהמתנות (cool-down) של השרשרת.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from app.modules.conversation.public import ChatMessage, LLMError, build_llm_chain
from app.shared.settings import ENV_FILE, get_settings

PROMPT = [ChatMessage("user", "Reply with OK.")]


def _outcome(exc: Exception) -> str:
    status = getattr(exc, "status", None)
    if status:
        message = getattr(exc, "provider_message", None)
        return f"HTTP {status}" + (f" ({message[:120]})" if message else "")
    if getattr(exc, "timeout", False):
        return "timeout"
    return f"error {type(exc.__cause__ or exc).__name__}"


def probe_lines(timeout_s: Optional[float] = None) -> list[str]:
    settings = get_settings()
    if timeout_s is not None:
        settings = settings.model_copy(update={"llm_timeout_s": timeout_s})
    chain = build_llm_chain(settings)
    lines = [f"env file: {ENV_FILE} ({'found' if ENV_FILE.exists() else 'missing'}), "
             f"timeout per call: {settings.llm_timeout_s:g}s"]
    if not chain.entries and not chain.disabled:
        lines.append("no LLM chain configured (template mode)")
    width = max((len(e.label) for e in chain.entries + chain.disabled), default=0)
    for entry in chain.entries:
        assert entry.provider is not None
        started = time.monotonic()
        try:
            entry.provider.complete(PROMPT)
            outcome = "ok"
        except LLMError as exc:
            outcome = _outcome(exc)
        ms = (time.monotonic() - started) * 1000
        lines.append(f"{entry.label:<{width}}  {outcome:<14}  {ms:6.0f} ms")
    for entry in chain.disabled:
        lines.append(f"{entry.label:<{width}}  disabled ({entry.detail})")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.llm_probe",
                                     description="One tiny request per LLM_CHAIN entry: status and latency.")
    parser.add_argument("--timeout", type=float, default=None,
                        help="per-call timeout in seconds (default: LLM_TIMEOUT_S)")
    args = parser.parse_args(argv)
    lines = probe_lines(args.timeout)
    print("\n".join(lines))
    return 0 if any("  ok  " in line for line in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
