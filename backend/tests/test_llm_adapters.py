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


def test_forced_tool_mode_in_requests():
    bodies = []

    def gemini(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": ""}]}}]})

    def openai(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    tools = [TOOL_SPECS["save_profile"], TOOL_SPECS["match_current_car"]]
    GeminiAdapter("g", "k", client=_client(gemini)).complete(MESSAGES, tools, require_tool=True)
    GeminiAdapter("g", "k", client=_client(gemini)).complete(MESSAGES, tools)
    OpenAICompatAdapter("groq", "m", "k", client=_client(openai)).complete(MESSAGES, tools, require_tool=True)
    OpenAICompatAdapter("groq", "m", "k", client=_client(openai)).complete(MESSAGES, tools)

    assert bodies[0]["toolConfig"] == {"functionCallingConfig": {
        "mode": "ANY", "allowedFunctionNames": ["save_profile", "match_current_car"]}}
    assert "toolConfig" not in bodies[1]
    assert bodies[2]["tool_choice"] == "required"
    assert "tool_choice" not in bodies[3]


@pytest.mark.parametrize("make", [
    lambda h: GeminiAdapter("g", "k", client=_client(h)),
    lambda h: OpenAICompatAdapter("groq", "m", "k", client=_client(h)),
])
def test_adapters_report_status_and_timeout_for_the_chain(make):
    with pytest.raises(LLMError) as err:
        make(lambda r: httpx.Response(429)).complete(MESSAGES)
    assert err.value.status == 429 and not err.value.timeout

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError) as err:
        make(slow).complete(MESSAGES)
    assert err.value.timeout and err.value.status is None


def test_gemini_two_rounds_send_back_thought_signatures_unchanged():
    """Gemini 3: functionCall מגיע עם thoughtSignature; בסבב הבא ה-parts חוזרים בדיוק כמו שהם,
    ואחריהם תור user אחד עם functionResponse לכל קריאה, באותו סדר."""
    returned_parts = [
        {"functionCall": {"name": "match_current_car", "args": {"model": "COROLLA CROSS"}}, "thoughtSignature": "sig-A"},
        {"functionCall": {"name": "save_profile", "args": {"current_copay": 300}, "id": "fc-2"}},
    ]
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        parts = returned_parts if len(bodies) == 1 else [{"text": ""}]
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": parts}}]})

    adapter = GeminiAdapter("gemini-3.8-flash", "k", client=_client(handler))
    first = adapter.complete([ChatMessage("system", "sys"), ChatMessage("user", "קורולה קרוס, 300")],
                             [TOOL_SPECS["match_current_car"], TOOL_SPECS["save_profile"]], require_tool=True)
    assert [c.name for c in first.tool_calls] == ["match_current_car", "save_profile"]

    # כמו בסוכן: הודעת assistant עם raw, ואחריה תשובת tool לכל קריאה
    history = [ChatMessage("system", "sys"), ChatMessage("user", "קורולה קרוס, 300"),
               ChatMessage("assistant", first.text, tool_calls=first.tool_calls, raw=first.raw)]
    history += [ChatMessage("tool", json.dumps({"ok": True}), tool_call_id=c.id, name=c.name) for c in first.tool_calls]
    adapter.complete(history, [TOOL_SPECS["list_options"]])

    second = bodies[1]
    assert second["contents"][0] == {"role": "user", "parts": [{"text": "קורולה קרוס, 300"}]}
    assert second["contents"][1] == {"role": "model", "parts": returned_parts}      # בלי שינוי, כולל החתימה
    assert second["contents"][2] == {"role": "user", "parts": [
        {"functionResponse": {"name": "match_current_car", "response": {"result": {"ok": True}}}},
        {"functionResponse": {"name": "save_profile", "response": {"result": {"ok": True}}, "id": "fc-2"}},
    ]}
    assert len(second["contents"]) == 3


@pytest.mark.parametrize("model, expected", [
    ("gemini-3.8-flash", {"thinkingLevel": "low"}),
    ("gemini-2.5-flash", {"thinkingBudget": 0}),
])
def test_gemini_thinking_config(model, expected):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "x"}]}}]})

    GeminiAdapter(model, "k", client=_client(handler)).complete(MESSAGES)
    assert bodies[0]["generationConfig"]["thinkingConfig"] == expected


def test_4xx_keeps_the_providers_error_message():
    body = {"error": {"code": 400, "message": "Function call is missing a thought_signature"}}
    adapter = GeminiAdapter("g", "k", client=_client(lambda r: httpx.Response(400, json=body)))
    with pytest.raises(LLMError) as err:
        adapter.complete(MESSAGES)
    assert err.value.status == 400 and err.value.provider_message == "Function call is missing a thought_signature"
    # 5xx: אין הודעת ספק
    adapter = GeminiAdapter("g", "k", client=_client(lambda r: httpx.Response(503, json=body)))
    with pytest.raises(LLMError) as err:
        adapter.complete(MESSAGES)
    assert err.value.provider_message is None


def test_adapter_call_timeout_is_capped_by_the_turn():
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "x"}]}}]})

    adapter = GeminiAdapter("g", "k", timeout_s=8, client=_client(handler))
    adapter.complete(MESSAGES)
    adapter.complete(MESSAGES, timeout_s=3)
    adapter.complete(MESSAGES, timeout_s=30)
    assert seen == [8, 3, 8]
