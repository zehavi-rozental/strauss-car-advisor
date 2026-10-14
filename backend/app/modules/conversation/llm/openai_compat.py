"""ספק תואם OpenAI (Groq / OpenRouter / Ollama): POST {base_url}/chat/completions."""
from __future__ import annotations

import json
from typing import Any, Optional, Sequence

import httpx

from .port import ChatMessage, LLMError, describe_error, LLMResponse, ToolCall, ToolSpec

PRESETS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "openai": "https://api.openai.com/v1",
}


def _message(m: ChatMessage) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                             for c in m.tool_calls]
    return out


class OpenAICompatAdapter:
    def __init__(self, provider: str, model: str, api_key: str = "", base_url: str = "",
                 timeout_s: float = 20.0, client: Optional[httpx.Client] = None):
        self.name = provider
        self.model = model
        self.base_url = (base_url or PRESETS.get(provider, "")).rstrip("/")
        if not self.base_url:
            raise ValueError(f"אין כתובת לספק {provider}")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(timeout=timeout_s)

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
                 require_tool: bool = False) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "temperature": 0.2,
                                "messages": [_message(m) for m in messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
            if require_tool:
                body["tool_choice"] = "required"
        try:
            r = self.client.post(f"{self.base_url}/chat/completions", json=body, headers=self.headers)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            calls = tuple(
                ToolCall(name=c["function"]["name"], id=c.get("id", ""),
                         args=json.loads(c["function"].get("arguments") or "{}"))
                for c in msg.get("tool_calls") or [])
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(f"{self.name}: {describe_error(exc)}") from exc
        return LLMResponse(text=msg.get("content") or "", tool_calls=calls)
