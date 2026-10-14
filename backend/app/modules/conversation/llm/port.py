"""הממשק למודל שפה. מודל השפה מחלץ נתונים, קורא לכלים ומנסח — הוא לא מחשב."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional, Protocol, Sequence

import httpx

log = logging.getLogger(__name__)


class LLMError(Exception):
    """הספק לא זמין / החזיר תשובה לא תקינה. השיחה ממשיכה בספק הבא או בתבניות.

    status: קוד HTTP כשיש; timeout: הבקשה לא הסתיימה בזמן. לפיהם השרשרת מחליטה על המתנה.
    """

    def __init__(self, message: str = "", *, status: Optional[int] = None, timeout: bool = False):
        super().__init__(message)
        self.status = status
        self.timeout = timeout

    @classmethod
    def from_http(cls, provider: str, exc: Exception) -> "LLMError":
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return cls(f"{provider}: {describe_error(exc)}", status=status,
                   timeout=isinstance(exc, httpx.TimeoutException))


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


# 429 = מכסה/קצב: מנוחה קצרה. 418 = הספק חסם אותנו: לא חוזרים אליו עד הפעלה מחדש.
FOREVER = float("inf")


@dataclass
class ChainEntry:
    """רשומה בשרשרת: ספק+מודל, ומה קרה בפעם האחרונה. בלי תוכן הודעות."""

    label: str
    provider: Optional[LLMPort]
    status: str = "untried"            # untried / ok / 429 / 418 / 5xx / timeout / error / disabled
    detail: Optional[str] = None       # למשל "HTTP 503" או "no API key"
    last_at: Optional[datetime] = None
    cooldown_until: float = 0.0        # לפי השעון של השרשרת (monotonic)


def entry_label(provider: LLMPort) -> str:
    model = getattr(provider, "model", "")
    return f"{provider.name}:{model}" if model else provider.name


class LLMChain:
    """שרשרת ספקים לפי סדר. כשל זמני (429, 418, 5xx, timeout) או כל כשל אחר — הבא בתור.

    ספק שהחזיר 429 מדולג למשך cooldown_429_s, ו-418 עד הפעלה מחדש, כדי שכל תור לא
    יבזבז עליו זמן. אם כולם נכשלו או בהמתנה — LLMError, והשיחה עוברת לתבניות.
    בלוג נרשמים רק הרשומה, משך ותוצאה — אף פעם לא תוכן ההודעות.
    """

    def __init__(self, providers: Sequence[LLMPort] = (), *, disabled: Sequence[tuple[str, str]] = (),
                 cooldown_429_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.entries = [ChainEntry(entry_label(p), p) for p in providers]
        self.disabled = [ChainEntry(label, None, status="disabled", detail=reason) for label, reason in disabled]
        self.cooldown_429_s = cooldown_429_s
        self.clock = clock
        self._lock = threading.Lock()

    @property
    def providers(self) -> list[LLMPort]:
        return [e.provider for e in self.entries if e.provider is not None]

    @property
    def available(self) -> bool:
        return bool(self.entries)

    @property
    def mode(self) -> str:
        """קצר (נשמר בשיחה): שמות הספקים בלי כפילויות."""
        names = list(dict.fromkeys(e.provider.name for e in self.entries if e.provider is not None))
        return ("+".join(names) or "templates")[:32]

    def status(self) -> list[dict[str, Any]]:
        now = self.clock()
        out = []
        with self._lock:
            for e in self.entries + self.disabled:
                remaining = e.cooldown_until - now
                out.append({
                    "entry": e.label, "status": e.status, "detail": e.detail,
                    "last_at": e.last_at.isoformat() if e.last_at else None,
                    "cooling_down": ("until restart" if remaining == FOREVER
                                     else f"{remaining:.0f}s" if remaining > 0 else None),
                })
        return out

    def _record(self, entry: ChainEntry, status: str, detail: Optional[str] = None,
                cooldown: float = 0.0) -> None:
        with self._lock:
            entry.status, entry.detail, entry.last_at = status, detail, datetime.now(timezone.utc)
            entry.cooldown_until = self.clock() + cooldown if cooldown else 0.0

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False) -> LLMResponse:
        last: Optional[Exception] = None
        for entry in self.entries:
            if self.clock() < entry.cooldown_until:
                continue
            assert entry.provider is not None
            started = time.monotonic()
            try:
                response = entry.provider.complete(messages, tools, require_tool)
            except Exception as exc:  # כל כשל — עוברים לבא בתור
                ms = (time.monotonic() - started) * 1000
                status, cooldown = _classify(exc, self.cooldown_429_s)
                self._record(entry, status, _detail(exc), cooldown)
                log.warning("llm %s failed after %.0fms: %s%s", entry.label, ms, _detail(exc),
                            " (cooling down)" if cooldown else "")
                last = exc
                continue
            self._record(entry, "ok")
            log.info("llm %s ok in %.0fms tool_calls=%d", entry.label,
                     (time.monotonic() - started) * 1000, len(response.tool_calls))
            return response
        raise LLMError("אין ספק מודל שפה זמין") from last


def _classify(exc: Exception, cooldown_429_s: float) -> tuple[str, float]:
    status = getattr(exc, "status", None)
    if status == 429:
        return "429", cooldown_429_s
    if status == 418:
        return "418", FOREVER
    if status is not None and 500 <= status < 600:
        return "5xx", 0.0
    if getattr(exc, "timeout", False):
        return "timeout", 0.0
    return "error", 0.0


def _detail(exc: Exception) -> str:
    status = getattr(exc, "status", None)
    if status:
        return f"HTTP {status}"
    if getattr(exc, "timeout", False):
        return "timeout"
    return type(exc).__name__
