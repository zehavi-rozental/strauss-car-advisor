"""הממשק למודל שפה. מודל השפה מחלץ נתונים, קורא לכלים ומנסח — הוא לא מחשב."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, Sequence

log = logging.getLogger(__name__)


class LLMError(Exception):
    """הספק לא זמין / החזיר תשובה לא תקינה. השיחה ממשיכה בספק הבא או בתבניות."""


def describe_error(exc: Exception) -> str:
    """תיאור בטוח ללוג: סוג השגיאה וקוד HTTP — בלי תוכן הבקשה או התשובה."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"{type(exc).__name__}" + (f" HTTP {status}" if status else "")


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = ""


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""      # להודעת tool: לאיזו קריאה זו התשובה
    name: str = ""              # להודעת tool: שם הכלי


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (תת-קבוצה שגם Gemini מקבל)


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)


class LLMPort(Protocol):
    """require_tool=True: הספק חייב לקרוא לאחד הכלים (Gemini mode ANY / OpenAI tool_choice required)."""

    name: str

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False) -> LLMResponse:
        ...


class LLMChain:
    """ספק ראשי → ספק גיבוי. אם כולם נכשלו — LLMError, והשיחה עוברת לתבניות.

    בלוג נרשמים רק שם הספק, משך ותוצאה — אף פעם לא תוכן ההודעות.
    """

    def __init__(self, providers: Sequence[LLMPort] = ()):
        self.providers = list(providers)

    @property
    def available(self) -> bool:
        return bool(self.providers)

    @property
    def mode(self) -> str:
        return "+".join(p.name for p in self.providers) or "templates"

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False) -> LLMResponse:
        last: Optional[Exception] = None
        for provider in self.providers:
            started = time.monotonic()
            try:
                response = provider.complete(messages, tools, require_tool)
            except Exception as exc:  # כל כשל בספק — עוברים לבא בתור
                log.warning("llm provider=%s failed after %.0fms: %s", provider.name,
                            (time.monotonic() - started) * 1000, exc)
                last = exc
                continue
            log.info("llm provider=%s ok in %.0fms tool_calls=%d", provider.name,
                     (time.monotonic() - started) * 1000, len(response.tool_calls))
            return response
        raise LLMError("אין ספק מודל שפה זמין") from last
