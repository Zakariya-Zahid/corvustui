"""Tests for Anthropic and Gemini provider helpers."""

from __future__ import annotations

import httpx
import pytest

from corvustui.providers import Message, MessageRole, ToolCall, ToolDefinition
from corvustui.providers.anthropic import AnthropicProvider
from corvustui.providers.gemini import GeminiProvider


def anthropic_provider(handler) -> AnthropicProvider:
    provider = AnthropicProvider(
        model="claude-test",
        base_url="http://anthropic.test",
        api_key="test-key",
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


def gemini_provider(handler) -> GeminiProvider:
    provider = GeminiProvider(
        model="gemini-test",
        base_url="http://gemini.test/v1beta",
        api_key="test-key",
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


async def close(provider) -> None:
    assert provider._client is not None
    await provider._client.aclose()
    provider._client = None


@pytest.mark.asyncio
async def test_anthropic_list_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "claude-a"}, {"id": "claude-b"}]},
        )

    provider = anthropic_provider(handler)
    try:
        assert await provider.list_models() == ["claude-a", "claude-b"]
    finally:
        await close(provider)


def test_anthropic_merges_alternating_turns_and_preserves_tool_blocks():
    provider = AnthropicProvider(
        model="claude-test",
        base_url="http://anthropic.test",
        api_key="test-key",
    )
    messages = [
        Message(role=MessageRole.SYSTEM, content="system prompt"),
        Message(role=MessageRole.USER, content="first"),
        Message(role=MessageRole.USER, content="second"),
        Message(
            role=MessageRole.ASSISTANT,
            content="calling",
            tool_calls=[ToolCall(id="call_1", name="shell", arguments={"command": "ls"})],
        ),
        Message(
            role=MessageRole.TOOL,
            content="output",
            name="shell",
            tool_call_id="call_1",
        ),
    ]

    converted = provider._convert_messages(messages)

    assert converted[0] == {"role": "user", "content": "first\n\nsecond"}
    assert converted[1]["role"] == "assistant"
    assert converted[1]["content"][0] == {"type": "text", "text": "calling"}
    assert converted[1]["content"][1] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "shell",
        "input": {"command": "ls"},
    }
    assert converted[2]["role"] == "user"
    assert converted[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "call_1",
        "content": "output",
    }
    assert provider._extract_system(messages) == "system prompt"


@pytest.mark.asyncio
async def test_gemini_list_models_filters_generate_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1beta/models"
        assert request.url.params["key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-1.5-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/embedding-001",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )

    provider = gemini_provider(handler)
    try:
        assert await provider.list_models() == ["gemini-1.5-flash"]
    finally:
        await close(provider)


def test_gemini_tool_payload_is_a_function_declaration_list():
    provider = GeminiProvider(
        model="gemini-test",
        base_url="http://gemini.test/v1beta",
        api_key="test-key",
    )
    tools = [
        ToolDefinition(
            name="shell",
            description="run a command",
            parameters={"type": "object", "properties": {}},
        )
    ]

    converted = provider._convert_tools(tools)

    assert converted == [
        {
            "function_declarations": [
                {
                    "name": "shell",
                    "description": "run a command",
                    "parameters": {"type": "object", "properties": {}},
                }
            ]
        }
    ]


@pytest.mark.asyncio
async def test_anthropic_stream_reports_total_tokens():
    sse = (
        'data: {"type":"message_start","message":{"usage":'
        '{"input_tokens":5,"output_tokens":1}}}\n\n'
        'data: {"type":"content_block_start","content_block":{"type":"text"}}\n\n'
        'data: {"type":"content_block_delta","delta":'
        '{"type":"text_delta","text":"hi"}}\n\n'
        'data: {"type":"content_block_stop"}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":3}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse.encode(),
            headers={"content-type": "text/event-stream"},
        )

    provider = anthropic_provider(handler)
    try:
        stream = await provider.complete(
            [Message(role=MessageRole.USER, content="hi")],
            stream=True,
        )
        chunks = [chunk async for chunk in stream]
    finally:
        await close(provider)

    assert "".join(chunk.content or "" for chunk in chunks) == "hi"
    finish = [chunk for chunk in chunks if chunk.finish_reason]
    assert len(finish) == 1
    assert finish[0].usage is not None
    assert finish[0].usage.prompt_tokens == 5
    assert finish[0].usage.completion_tokens == 3
    assert finish[0].usage.total_tokens == 8


def test_gemini_merges_same_role_turns():
    provider = GeminiProvider(
        model="gemini-test",
        base_url="http://gemini.test/v1beta",
        api_key="test-key",
    )
    messages = [
        Message(role=MessageRole.USER, content="one"),
        Message(role=MessageRole.USER, content="two"),
        Message(role=MessageRole.ASSISTANT, content="reply"),
    ]

    converted = provider._convert_messages(messages)

    assert len(converted) == 2
    assert converted[0] == {
        "role": "user",
        "parts": [{"text": "one"}, {"text": "two"}],
    }
    assert converted[1] == {"role": "model", "parts": [{"text": "reply"}]}
