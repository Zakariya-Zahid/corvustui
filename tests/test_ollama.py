"""Tests for the Ollama provider."""

from __future__ import annotations

import json

import httpx
import pytest

from corvustui.providers import (
    Message,
    MessageRole,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderTimeoutError,
    ProviderType,
    ProviderUnavailableError,
    ToolDefinition,
)
from corvustui.providers.ollama import OllamaProvider


def build_provider(handler, model: str = "test:1") -> OllamaProvider:
    provider = OllamaProvider(model=model, base_url="http://ollama.test")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


async def close_provider(provider: OllamaProvider) -> None:
    assert provider._client is not None
    await provider._client.aclose()
    provider._client = None


@pytest.mark.asyncio
async def test_list_models_returns_live_names():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3.5:0.8b"}, {"name": "llama3.2:3b"}]},
        )

    provider = build_provider(handler)
    try:
        assert await provider.list_models() == ["qwen3.5:0.8b", "llama3.2:3b"]
    finally:
        await close_provider(provider)


@pytest.mark.asyncio
async def test_list_models_unreachable_has_start_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = build_provider(handler)
    try:
        with pytest.raises(ProviderUnavailableError) as excinfo:
            await provider.list_models()
    finally:
        await close_provider(provider)

    assert "ollama serve" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_list_models_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = build_provider(handler)
    try:
        with pytest.raises(ProviderTimeoutError):
            await provider.list_models()
    finally:
        await close_provider(provider)


@pytest.mark.asyncio
async def test_validate_model_rejects_empty_ollama():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    provider = build_provider(handler, model="missing:1")
    try:
        with pytest.raises(ProviderModelNotFoundError) as excinfo:
            await provider.validate_model()
    finally:
        await close_provider(provider)

    message = str(excinfo.value).lower()
    assert "no models are installed" in message
    assert "ollama pull missing:1" in message


@pytest.mark.asyncio
async def test_validate_model_lists_available_models():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "installed:1"}]})

    provider = build_provider(handler, model="missing:1")
    try:
        with pytest.raises(ProviderModelNotFoundError) as excinfo:
            await provider.validate_model()
    finally:
        await close_provider(provider)

    message = str(excinfo.value)
    assert "missing:1" in message
    assert "installed:1" in message
    assert "ollama pull missing:1" in message


@pytest.mark.asyncio
async def test_validate_model_accepts_installed_model():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "installed:1"}]})

    provider = build_provider(handler, model="installed:1")
    try:
        await provider.validate_model()
    finally:
        await close_provider(provider)


@pytest.mark.asyncio
async def test_validate_model_accepts_bare_name_for_latest_tag():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "installed:latest"}]})

    provider = build_provider(handler, model="installed")
    try:
        await provider.validate_model()
    finally:
        await close_provider(provider)


@pytest.mark.asyncio
async def test_chat_404_is_reported_as_model_not_found():
    """Regression: the deleted duplicate _single_request bypassed errors."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(
                404,
                json={"error": "model 'missing:1' not found, try pulling it first"},
            )
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "installed:1"}]})
        raise AssertionError(f"unexpected path {request.url.path}")

    provider = build_provider(handler, model="missing:1")
    try:
        with pytest.raises(ProviderModelNotFoundError) as excinfo:
            await provider.complete([Message(role=MessageRole.USER, content="hi")])
    finally:
        await close_provider(provider)

    message = str(excinfo.value)
    assert "missing:1" in message
    assert "installed:1" in message


@pytest.mark.asyncio
async def test_chat_500_is_a_generic_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = build_provider(handler)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await provider.complete([Message(role=MessageRole.USER, content="hi")])
    finally:
        await close_provider(provider)

    assert not isinstance(excinfo.value, ProviderModelNotFoundError)
    assert excinfo.value.status_code == 500


@pytest.mark.asyncio
async def test_chat_connect_error_is_provider_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = build_provider(handler)
    try:
        with pytest.raises(ProviderUnavailableError) as excinfo:
            await provider.complete([Message(role=MessageRole.USER, content="hi")])
    finally:
        await close_provider(provider)

    assert "ollama serve" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_streamed_tool_calls_get_unique_ids():
    ndjson = (
        '{"message":{"content":"","tool_calls":['
        '{"function":{"name":"echo","arguments":{"value":"a"}}},'
        '{"function":{"name":"echo","arguments":{"value":"b"}}}'
        ']},"done":false}\n'
        '{"message":{"content":""},"done":true,"done_reason":"stop"}\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson.encode())

    provider = build_provider(handler)
    try:
        stream = await provider.complete(
            [Message(role=MessageRole.USER, content="hi")],
            stream=True,
        )
        chunks = [chunk async for chunk in stream]
    finally:
        await close_provider(provider)

    calls = [chunk.tool_calls[0] for chunk in chunks if chunk.tool_calls]
    assert [call.id for call in calls] == ["call_0", "call_1"]


@pytest.mark.asyncio
async def test_chat_sends_full_openai_tool_wrapper():
    """Regression: Ollama needs the outer type/function wrapper, not just the body."""

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"content": "ok"}, "done_reason": "stop"},
        )

    tools = [
        ToolDefinition(
            name="shell",
            description="Run a shell command",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        )
    ]
    provider = build_provider(handler)
    try:
        await provider.complete(
            [Message(role=MessageRole.USER, content="hi")],
            tools=tools,
        )
    finally:
        await close_provider(provider)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Run a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_chat_drops_invalid_tools_and_normalizes_empty_schema():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"content": "ok"}, "done_reason": "stop"},
        )

    tools = [
        ToolDefinition(name="", description="bad", parameters={}),
        ToolDefinition(name="no_schema", description="", parameters={}),
    ]
    provider = build_provider(handler)
    try:
        await provider.complete(
            [Message(role=MessageRole.USER, content="hi")],
            tools=tools,
        )
    finally:
        await close_provider(provider)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "no_schema",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_chat_omits_tools_when_all_definitions_are_invalid():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"content": "ok"}, "done_reason": "stop"},
        )

    provider = build_provider(handler)
    try:
        await provider.complete(
            [Message(role=MessageRole.USER, content="hi")],
            tools=[ToolDefinition(name="", description="bad", parameters={})],
        )
    finally:
        await close_provider(provider)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "tools" not in payload


@pytest.mark.asyncio
async def test_chat_success_parses_tool_calls_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "shell",
                                "arguments": {"command": "pwd"},
                            }
                        }
                    ],
                },
                "done_reason": "stop",
                "prompt_eval_count": 3,
                "eval_count": 4,
            },
        )

    provider = build_provider(handler)
    try:
        response = await provider.complete(
            [Message(role=MessageRole.USER, content="hi")]
        )
    finally:
        await close_provider(provider)

    assert response.provider == ProviderType.OLLAMA
    assert response.tool_calls is not None
    assert response.tool_calls[0].name == "shell"
    assert response.tool_calls[0].arguments == {"command": "pwd"}
    assert response.usage.total_tokens == 7
