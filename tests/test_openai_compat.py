"""Tests for the OpenAI-compatible provider."""

from __future__ import annotations

import json

import httpx
import pytest

from corvustui.providers import (
    Message,
    MessageRole,
    ProviderAuthError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
    ProviderType,
)
from corvustui.providers.openai_compat import OpenAIProvider

BASE_URL = "http://api.test/v1"


def build_provider(handler, api_key: str | None = "test-key", model: str = "gpt-test"):
    provider = OpenAIProvider(model=model, base_url=BASE_URL, api_key=api_key)
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


async def close_provider(provider: OpenAIProvider) -> None:
    assert provider._client is not None
    await provider._client.aclose()
    provider._client = None


def test_parse_tool_calls_handles_json_strings_and_repairs():
    provider = OpenAIProvider(
        model="gpt-test",
        base_url=BASE_URL,
        api_key="test-key",
    )

    calls = provider._parse_tool_calls([
        {
            "id": "call_1",
            "function": {"name": "shell", "arguments": '{"command": "ls"}'},
        },
        {
            "id": "call_2",
            "function": {
                "name": "write_file",
                "arguments": "{'path': 'a.txt', 'content': 'hi',}",
            },
        },
        {
            "id": "call_3",
            "function": {"name": "echo", "arguments": {"value": "already-dict"}},
        },
    ])

    assert calls is not None
    assert [call.id for call in calls] == ["call_1", "call_2", "call_3"]
    assert calls[0].arguments == {"command": "ls"}
    assert calls[1].arguments == {"path": "a.txt", "content": "hi"}
    assert calls[2].arguments == {"value": "already-dict"}


def test_parse_tool_calls_empty_and_missing_ids():
    provider = OpenAIProvider(
        model="gpt-test",
        base_url=BASE_URL,
        api_key="test-key",
    )

    assert provider._parse_tool_calls([]) is None

    calls = provider._parse_tool_calls([
        {"function": {"name": "noop", "arguments": ""}},
        {"id": "call_1", "function": {"name": "bad", "arguments": "not json"}},
    ])
    assert calls is not None
    assert calls[0].id == "call_0"
    assert calls[0].arguments == {}
    assert calls[1].arguments == {}


def _response(status: int, payload: dict | None = None, text: str = "") -> httpx.Response:
    if payload is not None:
        return httpx.Response(status, json=payload)
    return httpx.Response(status, text=text)


@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (401, ProviderAuthError),
        (404, ProviderModelNotFoundError),
        (429, ProviderRateLimitError),
        (500, ProviderError),
        (400, ProviderError),
    ],
)
@pytest.mark.asyncio
async def test_handle_error_maps_status_codes(status, exception):
    provider = OpenAIProvider(
        model="gpt-test",
        base_url=BASE_URL,
        api_key="test-key",
    )
    response = _response(status, {"error": {"message": f"status {status}"}})

    with pytest.raises(exception) as excinfo:
        await provider._handle_error(response)

    assert excinfo.value.status_code == status


@pytest.mark.asyncio
async def test_validate_model_requires_api_key():
    provider = OpenAIProvider(model="gpt-test", base_url=BASE_URL, api_key=None)
    with pytest.raises(ProviderAuthError):
        await provider.validate_model()


@pytest.mark.asyncio
async def test_list_models_requires_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make a request without a key")

    provider = build_provider(handler, api_key=None)
    try:
        with pytest.raises(ProviderAuthError):
            await provider.list_models()
    finally:
        await close_provider(provider)


@pytest.mark.asyncio
async def test_list_models_returns_live_names():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-a"}, {"id": "gpt-b"}]},
        )

    provider = build_provider(handler)
    try:
        assert await provider.list_models() == ["gpt-a", "gpt-b"]
    finally:
        await close_provider(provider)


@pytest.mark.asyncio
async def test_streaming_aggregates_tool_call_fragments():
    sse = (
        'data: {"choices":[{"delta":{"tool_calls":['
        '{"index":0,"id":"call_1","function":{"name":"shell","arguments":"{\\"comm"}}'
        ']}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":['
        '{"index":0,"function":{"arguments":"and\\": \\"ls\\"}"}}'
        ']}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],'
        '"usage":{"prompt_tokens":5,"completion_tokens":6,"total_tokens":11}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse.encode(),
            headers={"content-type": "text/event-stream"},
        )

    provider = build_provider(handler)
    try:
        stream = await provider.complete(
            [Message(role=MessageRole.USER, content="hi")],
            stream=True,
        )
        chunks = [chunk async for chunk in stream]
    finally:
        await close_provider(provider)

    tool_chunks = [chunk for chunk in chunks if chunk.tool_calls]
    assert len(tool_chunks) == 1
    call = tool_chunks[0].tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "shell"
    assert call.arguments == {"command": "ls"}

    finish_chunks = [chunk for chunk in chunks if chunk.finish_reason]
    assert len(finish_chunks) == 1
    assert finish_chunks[0].usage is not None
    assert finish_chunks[0].usage.total_tokens == 11


@pytest.mark.asyncio
async def test_streaming_flushes_tool_calls_when_done_has_no_finish_reason():
    sse = (
        'data: {"choices":[{"delta":{"tool_calls":['
        '{"index":0,"id":"call_1","function":{"name":"echo","arguments":"{\\"value\\": \\"x\\"}"}}'
        ']}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse.encode(),
            headers={"content-type": "text/event-stream"},
        )

    provider = build_provider(handler)
    try:
        stream = await provider.complete(
            [Message(role=MessageRole.USER, content="hi")],
            stream=True,
        )
        chunks = [chunk async for chunk in stream]
    finally:
        await close_provider(provider)

    tool_chunks = [chunk for chunk in chunks if chunk.tool_calls]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].tool_calls[0].arguments == {"value": "x"}


@pytest.mark.asyncio
async def test_streaming_usage_only_chunk_is_not_dropped():
    sse = (
        'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":'
        '{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse.encode(),
            headers={"content-type": "text/event-stream"},
        )

    provider = build_provider(handler)
    try:
        stream = await provider.complete(
            [Message(role=MessageRole.USER, content="hi")],
            stream=True,
        )
        chunks = [chunk async for chunk in stream]
    finally:
        await close_provider(provider)

    usage_chunks = [chunk for chunk in chunks if chunk.usage is not None]
    assert len(usage_chunks) == 1
    assert usage_chunks[0].usage.total_tokens == 6


@pytest.mark.asyncio
async def test_completion_parses_usage_and_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "gpt-test"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command": "pwd"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
        )

    provider = build_provider(handler)
    try:
        response = await provider.complete(
            [Message(role=MessageRole.USER, content="hi")]
        )
    finally:
        await close_provider(provider)

    assert response.provider == ProviderType.OPENAI
    assert response.tool_calls is not None
    assert response.tool_calls[0].arguments == {"command": "pwd"}
    assert response.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_completion_without_choices_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    provider = build_provider(handler)
    try:
        with pytest.raises(ProviderError):
            await provider.complete([Message(role=MessageRole.USER, content="hi")])
    finally:
        await close_provider(provider)
