"""Gemini דרך REST: POST .../models/{model}:generateContent עם functionDeclarations."""
from __future__ import annotations

import json
from typing import Any, Optional, Sequence

import httpx

from .port import ChatMessage, LLMError, describe_error, LLMResponse, ToolCall, ToolSpec

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _contents(messages: Sequence[ChatMessage]) -> tuple[Optional[dict], list[dict]]:
    system = None
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system = {"parts": [{"text": m.content}]}
        elif m.role == "user":
            contents.append({"role": "user", "parts": [{"text": m.content}]})
        elif m.role == "assistant":
            parts: list[dict[str, Any]] = [{"text": m.content}] if m.content else []
            parts += [{"functionCall": {"name": c.name, "args": c.args}} for c in m.tool_calls]
            contents.append({"role": "model", "parts": parts})
        else:  # tool
            contents.append({"role": "user", "parts": [{"functionResponse": {
                "name": m.name, "response": {"result": json.loads(m.content or "{}")}}}]})
    return system, contents


class GeminiAdapter:
    name = "gemini"

    def __init__(self, model: str, api_key: str, base_url: str = "", timeout_s: float = 20.0,
                 client: Optional[httpx.Client] = None):
        self.model = model
        self.url = f"{(base_url or BASE_URL).rstrip('/')}/models/{model}:generateContent"
        self.headers = {"x-goog-api-key": api_key}
        self.client = client or httpx.Client(timeout=timeout_s)

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False) -> LLMResponse:
        system, contents = _contents(messages)
        body: dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": 0.2}}
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
            raise LLMError(f"gemini: {describe_error(exc)}") from exc
        text = "".join(p.get("text", "") for p in parts)
        calls = tuple(ToolCall(name=p["functionCall"]["name"], args=p["functionCall"].get("args") or {},
                               id=f"gemini_{i}")
                      for i, p in enumerate(parts) if "functionCall" in p)
        return LLMResponse(text=text, tool_calls=calls)
