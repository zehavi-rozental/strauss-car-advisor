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

    def __init__(self, message: str = "", *, status: Optional[int] = None, timeout: bool = False,
                 provider_message: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.timeout = timeout
        self.provider_message = provider_message   # error.message של הספק ב-4xx (בלי תוכן משתמש)

    @classmethod
    def from_http(cls, provider: str, exc: Exception) -> "LLMError":
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return cls(f"{provider}: {describe_error(exc)}", status=status,
                   timeout=isinstance(exc, httpx.TimeoutException),
                   provider_message=_provider_message(response) if status and 400 <= status < 500 else None)


def _provider_message(response: Optional[httpx.Response]) -> Optional[str]:
    """Google ו-OpenAI מחזירים {"error": {"message": ...}} — תיאור הבעיה בבקשה, לא תוכן המשתמש."""
    try:
        message = response.json()["error"]["message"]  # type: ignore[union-attr]
    except Exception:
        return None
    return str(message)[:300] if message else None


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
    raw: Any = None             # להודעת assistant: התוכן של הספק כפי שהוחזר (נשלח חזרה בלי שינוי)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (תת-קבוצה שגם Gemini מקבל)


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    # התוכן הגולמי של הספק (Gemini: ה-parts, כולל thoughtSignature). חייב לחזור כמו שהוא בסבב הבא.
    raw: Any = None


class LLMPort(Protocol):
    """require_tool=True: הספק חייב לקרוא לאחד הכלים (Gemini mode ANY / OpenAI tool_choice required).
    timeout_s: תקרה לקריאה הזו (מה שנשאר מזמן התור); הספק לוקח את הקטן מבינה לבין שלו."""

    name: str

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False, timeout_s: Optional[float] = None) -> LLMResponse:
        ...


# 429 = מכסה/קצב: מנוחה קצרה. 418 = הספק חסם אותנו: לא חוזרים אליו עד הפעלה מחדש.
FOREVER = float("inf")


@dataclass
class ChainEntry:
    """רשומה בשרשרת: ספק+מודל, ומה קרה בפעם האחרונה. בלי תוכן הודעות."""

    label: str
    provider: Optional[LLMPort]
    status: str = "untried"            # untried / ok / 400 / 429 / 418 / 5xx / timeout / error / disabled
    detail: Optional[str] = None       # למשל "HTTP 503" או "no API key"
    last_at: Optional[datetime] = None
    cooldown_until: float = 0.0        # לפי השעון של השרשרת (monotonic)


def _timeout(own: float, cap: Optional[float]) -> float:
    """הזמן לקריאה: של הספק, או מה שנשאר מהתור אם זה קצר יותר."""
    return own if cap is None else max(0.1, min(own, cap))


def entry_label(provider: LLMPort) -> str:
    model = getattr(provider, "model", "")
    return f"{provider.name}:{model}" if model else provider.name


class LLMChain:
    """שרשרת ספקים לפי סדר. כשל זמני (429, 418, 5xx, timeout) או כל כשל אחר — הבא בתור.
    400 (בקשה שגויה, דטרמיניסטי) עוצר את השרשרת לאותה קריאה.

    ספק שהחזיר 429 מדולג למשך cooldown_429_s, 5xx או timeout למשך cooldown_fail_s,
    ו-418 עד הפעלה מחדש, כדי שכל תור לא
    יבזבז עליו זמן. אם כולם נכשלו או בהמתנה — LLMError, והשיחה עוברת לתבניות.
    בלוג נרשמים רק הרשומה, משך ותוצאה — אף פעם לא תוכן ההודעות.
    """

    def __init__(self, providers: Sequence[LLMPort] = (), *, disabled: Sequence[tuple[str, str]] = (),
                 cooldown_429_s: float = 60.0, cooldown_fail_s: float = 60.0,
                 turn_deadline_s: Optional[float] = None, intro_budget_s: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.entries = [ChainEntry(entry_label(p), p) for p in providers]
        self.disabled = [ChainEntry(label, None, status="disabled", detail=reason) for label, reason in disabled]
        self.cooldown_429_s = cooldown_429_s
        self.cooldown_fail_s = cooldown_fail_s
        self.turn_deadline_s = turn_deadline_s
        self.intro_budget_s = intro_budget_s
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

    def turn_deadline(self) -> Optional[float]:
        """נקודת הזמן (לפי שעון השרשרת) שאחריה התור מפסיק לנסות ספקים ועובר לתבניות."""
        return self.clock() + self.turn_deadline_s if self.turn_deadline_s else None

    def intro_deadline(self, turn_deadline: Optional[float]) -> Optional[float]:
        """לפתיח של התוצאות: התקציב הקצר שלו, ולא יותר ממה שנשאר לתור."""
        if not self.intro_budget_s:
            return turn_deadline
        own = self.clock() + self.intro_budget_s
        return own if turn_deadline is None else min(own, turn_deadline)

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
                 require_tool: bool = False, deadline: Optional[float] = None) -> LLMResponse:
        last: Optional[Exception] = None
        for entry in self.entries:
            if self.clock() < entry.cooldown_until:
                continue
            remaining = None if deadline is None else deadline - self.clock()
            if remaining is not None and remaining <= 0:
                log.warning("llm turn deadline reached; not trying %s", entry.label)
                raise LLMError("זמן התור נגמר") from last
            assert entry.provider is not None
            own = getattr(entry.provider, "timeout_s", None)
            capped = remaining is not None and own is not None and remaining < own
            started = time.monotonic()
            try:
                response = entry.provider.complete(messages, tools, require_tool, remaining)
            except Exception as exc:  # כל כשל — עוברים לבא בתור (מלבד 400)
                ms = (time.monotonic() - started) * 1000
                status, cooldown = _classify(exc, self.cooldown_429_s, self.cooldown_fail_s)
                if status == "timeout" and capped:
                    cooldown = 0.0   # נקטע בגלל זמן התור, לא בגלל הספק — לא מענישים אותו
                self._record(entry, status, _detail(exc), cooldown)
                log.warning("llm %s failed after %.0fms: %s%s", entry.label, ms, _detail(exc),
                            " (cooling down)" if cooldown else "")
                if status == "400":
                    # הבקשה עצמה שגויה — כל רשומה תדחה אותה. לא מבזבזים זמן; התור עובר לתבניות.
                    raise LLMError(f"{entry.label}: bad request", status=400) from exc
                last = exc
                continue
            self._record(entry, "ok")
            log.info("llm %s ok in %.0fms tool_calls=%d", entry.label,
                     (time.monotonic() - started) * 1000, len(response.tool_calls))
            return response
        raise LLMError("אין ספק מודל שפה זמין") from last


def _classify(exc: Exception, cooldown_429_s: float, cooldown_fail_s: float) -> tuple[str, float]:
    status = getattr(exc, "status", None)
    if status == 429:
        return "429", cooldown_429_s
    if status == 418:
        return "418", FOREVER
    if status == 400:
        return "400", 0.0
    if status is not None and 500 <= status < 600:
        return "5xx", cooldown_fail_s
    if getattr(exc, "timeout", False):
        return "timeout", cooldown_fail_s
    return "error", 0.0


def _detail(exc: Exception) -> str:
    status = getattr(exc, "status", None)
    if status:
        message = getattr(exc, "provider_message", None)
        return f"HTTP {status}" + (f" ({message})" if message else "")
    if getattr(exc, "timeout", False):
        return "timeout"
    return type(exc).__name__
