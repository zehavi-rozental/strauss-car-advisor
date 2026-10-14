"""Gemini דרך REST: POST .../models/{model}:generateContent עם functionDeclarations."""
from __future__ import annotations

import json
from typing import Any, Optional, Sequence

import httpx

from .port import ChatMessage, LLMError, LLMResponse, ToolCall, ToolSpec

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


RAW_TAG = "gemini"


def _contents(messages: Sequence[ChatMessage]) -> tuple[Optional[dict], list[dict]]:
    system = None
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system = {"parts": [{"text": m.content}]}
        elif m.role == "user":
            contents.append({"role": "user", "parts": [{"text": m.content}]})
        elif m.role == "assistant":
            if isinstance(m.raw, tuple) and m.raw and m.raw[0] == RAW_TAG:
                # Gemini 3: functionCall מגיע עם thoughtSignature שחייב לחזור בלי שינוי
                parts = list(m.raw[1])
            else:
                parts = [{"text": m.content}] if m.content else []
                parts += [{"functionCall": {"name": c.name, "args": c.args}} for c in m.tool_calls]
            contents.append({"role": "model", "parts": parts})
        else:  # tool — כל התשובות לאותו סבב באותו תור user, בסדר הקריאות
            response: dict[str, Any] = {"name": m.name, "response": {"result": json.loads(m.content or "{}")}}
            if m.tool_call_id and not m.tool_call_id.startswith("gemini_"):
                response["id"] = m.tool_call_id
            part = {"functionResponse": response}
            if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
    return system, contents


def _thinking(model: str, level: str, budget: int) -> Optional[dict[str, Any]]:
    """Gemini 2.x: thinkingBudget; Gemini 3 ומעלה: thinkingLevel ("low" הוא הנמוך שמתקבל ב-3.x)."""
    if model.startswith("gemini-2"):
        return {"thinkingBudget": budget}
    return {"thinkingLevel": level} if level else None


class GeminiAdapter:
    name = "gemini"

    def __init__(self, model: str, api_key: str, base_url: str = "", timeout_s: float = 8.0,
                 client: Optional[httpx.Client] = None, thinking_level: str = "low", thinking_budget: int = 0):
        self.model = model
        self.url = f"{(base_url or BASE_URL).rstrip('/')}/models/{model}:generateContent"
        self.headers = {"x-goog-api-key": api_key}
        self.client = client or httpx.Client(timeout=timeout_s)
        self.thinking = _thinking(model, thinking_level, thinking_budget)

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False) -> LLMResponse:
        system, contents = _contents(messages)
        generation: dict[str, Any] = {"temperature": 0.2}
        if self.thinking:
            generation["thinkingConfig"] = self.thinking
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        if system:
            body["systemInstruction"] = system
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools]}]
            if require_tool:   # החילוץ לא תלוי בכך שהמודל יבחר לקרוא לכלי
                body["toolConfig"] = {"functionCallingConfig": {
                    "mode": "ANY", "allowedFunctionNames": [t.name for t in tools]}}
        try:
            r = self.client.post(self.url, json=body, headers=self.headers)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"].get("parts", [])
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError.from_http("gemini", exc) from exc
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        calls = tuple(ToolCall(name=p["functionCall"]["name"], args=p["functionCall"].get("args") or {},
                               id=p["functionCall"].get("id") or f"gemini_{i}")
                      for i, p in enumerate(parts) if "functionCall" in p)
        return LLMResponse(text=text, tool_calls=calls, raw=(RAW_TAG, tuple(parts)))
