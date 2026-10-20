"""מודל שפה מזויף לבדיקות: תסריט לפי תורות, ורישום של מה שנשלח אליו.

כל תור בתסריט: סבבי קריאות לכלים (קריאה עם כלים) ותשובה מנוסחת (קריאה בלי כלים).
התור נסגר בקריאת הניסוח, או כשמגיעה הודעת עובד חדשה (תור בלי ניסוח — תבנית).
כשהסבבים נגמרו — תשובה ריקה בלי כלים (סוף החילוץ).
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .port import ChatMessage, LLMError, LLMResponse, ToolCall, ToolSpec

VERIFY_MARK = "You verify a reply"            # guards.VERIFY_PROMPT (the claim verifier)
RETRY_MARK = "Your reply broke these rules"   # agent._retry (the feedback after a guard violation)


@dataclass
class _Turn:
    rounds: deque[tuple[ToolCall, ...]] = field(default_factory=deque)
    reply: Optional[str] = None
    forced_only: bool = False
    retry: Optional[str] = None          # the reply after a guard violation (None → the retry fails)
    user_turn: Optional[tuple] = None    # the employee message this turn belongs to
    replied: bool = False


@dataclass(frozen=True)
class FakeCall:
    messages: list[ChatMessage]
    tools: list[str]
    require_tool: bool


def _key(messages: Sequence[ChatMessage]) -> Optional[tuple]:
    last = next((m for m in reversed(messages) if m.role == "user"), None)
    return (id(last), last.content) if last is not None else None


class FakeLLM:
    """Scripted turns. In a turn: the tool rounds, then (on the next tools call, as Gemini writes its reply
    after seeing the tool results) the reply text. reply=None → no text → the template.
    The claim verifier gets `verdicts` (default: every sentence supported); a retry gets the turn's `retry`."""

    name = "fake"

    def __init__(self) -> None:
        self.script: deque[_Turn] = deque()
        self.calls: list[FakeCall] = []
        self.verdicts: deque[str] = deque()
        self.last: Optional[_Turn] = None

    def turn(self, *tool_calls: ToolCall, reply: Optional[str] = None, retry: Optional[str] = None,
             rounds: Sequence[Sequence[ToolCall]] = (), forced_only: bool = False) -> "FakeLLM":
        all_rounds = deque(tuple(r) for r in rounds)
        if tool_calls:
            all_rounds.appendleft(tuple(tool_calls))
        self.script.append(_Turn(rounds=all_rounds, reply=reply, forced_only=forced_only, retry=retry))
        return self

    def verdict(self, unsupported: Sequence[int]) -> "FakeLLM":
        self.verdicts.append(json.dumps({"unsupported": list(unsupported)}))
        return self

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False, timeout_s: Optional[float] = None) -> LLMResponse:
        self.calls.append(FakeCall(list(messages), [t.name for t in tools], require_tool))
        if tools:
            key = _key(messages)
            if self.script and self.script[0].user_turn not in (None, key):
                self.script.popleft()            # the previous turn ended
            if self.script and self.script[0].user_turn is None:
                self.script[0].user_turn = key
            current = self.script[0] if self.script else None
            if current and current.rounds:
                if current.forced_only and not require_tool:
                    return LLMResponse(text="תודה! רשמתי את הפרטים.")
                return LLMResponse(tool_calls=current.rounds.popleft())
            if current and current.reply is not None and not current.replied:
                current.replied, self.last = True, current
                return LLMResponse(text=current.reply)
            return LLMResponse()
        system = messages[0].content if messages and messages[0].role == "system" else ""
        if system.startswith(VERIFY_MARK):
            return LLMResponse(text=self.verdicts.popleft() if self.verdicts else '{"unsupported": []}')
        if messages and messages[-1].role == "user" and messages[-1].content.startswith(RETRY_MARK):
            if self.last is None or self.last.retry is None:
                raise LLMError("FakeLLM: no retry scripted")
            text, self.last.retry = self.last.retry, None
            return LLMResponse(text=text)
        if not self.script:
            raise LLMError("FakeLLM: התסריט נגמר")
        reply = self.script.popleft().reply
        if reply is None:
            raise LLMError("FakeLLM: ניסוח נכשל")
        return LLMResponse(text=reply)

class FailingLLM:
    """ספק שתמיד נכשל — לבדיקת מעבר לגיבוי."""

    name = "failing"

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False, timeout_s: Optional[float] = None) -> LLMResponse:
        raise LLMError("down")


def call(name: str, **args) -> ToolCall:
    return ToolCall(name=name, args=args, id=f"call_{name}")


class ScriptedProvider:
    """ספק לבדיקות השרשרת: כל קריאה לוקחת את התוצאה הבאה — קוד HTTP (int), "timeout",
    או טקסט. כשהתסריט נגמר הוא עונה "ok"."""

    def __init__(self, name: str, model: str = "", outcomes: Sequence[object] = ()):
        self.name, self.model = name, model
        self.outcomes: deque[object] = deque(outcomes)
        self.calls = 0
        self.last_timeout_s: Optional[float] = None

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False, timeout_s: Optional[float] = None) -> LLMResponse:
        self.calls += 1
        self.last_timeout_s = timeout_s
        outcome = self.outcomes.popleft() if self.outcomes else "ok"
        if isinstance(outcome, int):
            raise LLMError(f"{self.name}: HTTP {outcome}", status=outcome)
        if outcome == "timeout":
            raise LLMError(f"{self.name}: timeout", timeout=True)
        return LLMResponse(text=f"{outcome} from {self.name}:{self.model}")
