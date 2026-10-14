"""מודל שפה מזויף לבדיקות: תסריט לפי תורות, ורישום של מה שנשלח אליו.

כל תור בתסריט: סבבי קריאות לכלים (קריאה עם כלים) ותשובה מנוסחת (קריאה בלי כלים).
התור נסגר בקריאת הניסוח, או כשמגיעה הודעת עובד חדשה (תור בלי ניסוח — תבנית).
כשהסבבים נגמרו — תשובה ריקה בלי כלים (סוף החילוץ).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .port import ChatMessage, LLMError, LLMResponse, ToolCall, ToolSpec


@dataclass
class _Turn:
    rounds: deque[tuple[ToolCall, ...]] = field(default_factory=deque)
    reply: Optional[str] = None
    forced_only: bool = False
    user_turn: Optional[int] = None    # מספר הודעות העובד כשהתור התחיל


@dataclass(frozen=True)
class FakeCall:
    messages: list[ChatMessage]
    tools: list[str]
    require_tool: bool


class FakeLLM:
    name = "fake"

    def __init__(self) -> None:
        self.script: deque[_Turn] = deque()
        self.calls: list[FakeCall] = []

    def turn(self, *tool_calls: ToolCall, reply: Optional[str] = None,
             rounds: Sequence[Sequence[ToolCall]] = (), forced_only: bool = False) -> "FakeLLM":
        """reply=None → הניסוח נכשל (LLMError) והשיחה משתמשת בתבנית.

        forced_only=True מחקה את Gemini במצב AUTO: בלי require_tool הוא עונה בטקסט
        ולא קורא לכלי; רק כשהבקשה מחייבת כלי הוא מחזיר את הקריאות.
        """
        all_rounds = deque(tuple(r) for r in rounds)
        if tool_calls:
            all_rounds.appendleft(tuple(tool_calls))
        self.script.append(_Turn(rounds=all_rounds, reply=reply, forced_only=forced_only))
        return self

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False) -> LLMResponse:
        self.calls.append(FakeCall(list(messages), [t.name for t in tools], require_tool))
        if tools:
            user_turn = sum(1 for m in messages if m.role == "user")
            if self.script and self.script[0].user_turn not in (None, user_turn):
                self.script.popleft()            # התור הקודם הסתיים בלי ניסוח
            if self.script and self.script[0].user_turn is None:
                self.script[0].user_turn = user_turn
            current = self.script[0] if self.script else None
            if current and current.rounds:
                if current.forced_only and not require_tool:
                    return LLMResponse(text="תודה! רשמתי את הפרטים.")
                return LLMResponse(tool_calls=current.rounds.popleft())
            return LLMResponse()
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
                 require_tool: bool = False) -> LLMResponse:
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

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False) -> LLMResponse:
        self.calls += 1
        outcome = self.outcomes.popleft() if self.outcomes else "ok"
        if isinstance(outcome, int):
            raise LLMError(f"{self.name}: HTTP {outcome}", status=outcome)
        if outcome == "timeout":
            raise LLMError(f"{self.name}: timeout", timeout=True)
        return LLMResponse(text=f"{outcome} from {self.name}:{self.model}")
