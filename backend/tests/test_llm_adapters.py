"""מתאמי מודלי השפה מול שרת מדומה (httpx.MockTransport) — בלי רשת."""
from __future__ import annotations

import json

import httpx
import pytest

from app.modules.conversation.llm.factory import build_llm_chain
from app.modules.conversation.llm.fake import FailingLLM, FakeLLM
from app.modules.conversation.llm.gemini import GeminiAdapter
from app.modules.conversation.llm.openai_compat import OpenAICompatAdapter
from app.modules.conversation.llm.port import ChatMessage, LLMChain, LLMError, ToolCall
from app.modules.conversation.tools import TOOL_SPECS
from app.shared.settings import Settings

MESSAGES = [
    ChatMessage("system", "sys"),
    ChatMessage("user", "קורולה קרוס"),
    ChatMessage("assistant", "", tool_calls=(ToolCall("match_current_car", {"model": "COROLLA CROSS"}, "c1"),)),
    ChatMessage("tool", json.dumps({"ok": True}), tool_call_id="c1", name="match_current_car"),
]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openai_compat_request_and_tool_calls():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "x1", "type": "function",
             "function": {"name": "save_profile", "arguments": '{"current_copay": 300}'}}]}}]})

    adapter = OpenAICompatAdapter("groq", "llama", "key", client=_client(handler))
    out = adapter.complete(MESSAGES, [TOOL_SPECS["save_profile"]])
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer key"
    body = seen["body"]
    assert body["tools"][0]["function"]["name"] == "save_profile"
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"model": "COROLLA CROSS"}'
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}
    assert out.tool_calls == (ToolCall("save_profile", {"current_copay": 300}, "x1"),)


def test_gemini_request_and_function_calls():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "שלום"}, {"functionCall": {"name": "list_options", "args": {}}}]}}]})

    adapter = GeminiAdapter("gemini-2.5-flash", "k", client=_client(handler))
    out = adapter.complete(MESSAGES, [TOOL_SPECS["list_options"]])
    assert seen["url"].endswith("/models/gemini-2.5-flash:generateContent") and seen["key"] == "k"
    body = seen["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["contents"][1]["parts"][0]["functionCall"]["name"] == "match_current_car"
    assert body["contents"][2]["parts"][0]["functionResponse"]["response"] == {"result": {"ok": True}}
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "list_options"
    assert out.text == "שלום" and out.tool_calls[0].name == "list_options"


@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(200, json={"weird": 1})])
def test_adapter_errors_become_llm_error(response):
    adapter = OpenAICompatAdapter("ollama", "m", client=_client(lambda r: response))
    with pytest.raises(LLMError):
        adapter.complete(MESSAGES)


def test_chain_falls_through_to_next_provider():
    fake = FakeLLM().turn(reply="היי")
    chain = LLMChain([FailingLLM(), fake])
    assert chain.complete([ChatMessage("user", "x")]).text == "היי"
    with pytest.raises(LLMError):
        LLMChain([FailingLLM()]).complete([ChatMessage("user", "x")])


def test_factory_builds_chain_from_settings():
    s = Settings(_env_file=None, llm_provider="gemini", llm_model="g", llm_api_key="k",
                 llm_fallback_provider="ollama", llm_fallback_model="llama3")
    assert build_llm_chain(s).mode == "gemini+ollama"
    # בלי מפתח / בלי ספק → מצב תבניות
    s = Settings(_env_file=None, llm_provider="gemini", llm_model="g", llm_api_key="")
    assert build_llm_chain(s).mode == "templates"
