"""Tests for the ReAct loop."""

from __future__ import annotations

import asyncio

import pytest

from corvustui.providers import MessageRole, ProviderType, ProviderUnavailableError
from corvustui.react.loop import IterationStatus, ReActLoop
from corvustui.tools.base import FunctionTool
from corvustui.tools.registry import ToolRegistry
from tests.fakes import FakeProvider, text_response, tool_response


def make_registry(*names: str) -> tuple[ToolRegistry, list[tuple[str, str]]]:
    registry = ToolRegistry()
    calls: list[tuple[str, str]] = []

    for name in names:
        def make_tool(tool_name: str):
            async def run(value: str = "") -> str:
                calls.append((tool_name, value))
                return f"{tool_name}:{value}"

            return run

        registry.register(FunctionTool(name, f"{name} tool", make_tool(name)))

    return registry, calls


def make_loop(provider, registry, **kwargs) -> ReActLoop:
    kwargs.setdefault("max_iterations", 3)
    return ReActLoop(provider, registry, **kwargs)


def test_parse_response_documented_format():
    loop = make_loop(FakeProvider([]), ToolRegistry())
    thought, action, action_input = loop._parse_response(
        'Thought: search the web\nAction: duckduckgo_search\n'
        'Action Input: {"query": "corvus"}'
    )
    assert thought == "search the web"
    assert action == "duckduckgo_search"
    assert action_input == {"query": "corvus"}


def test_parse_response_inline_malformed_action():
    loop = make_loop(FakeProvider([]), ToolRegistry())
    thought, action, action_input = loop._parse_response(
        'Thought: use the shell\nAction: shell({"command": "pwd"})'
    )
    assert thought == "use the shell"
    assert action == "shell"
    assert action_input == {"command": "pwd"}


def test_parse_response_tool_call_form():
    loop = make_loop(FakeProvider([]), ToolRegistry())
    thought, action, action_input = loop._parse_response(
        'Thought: write it\nTOOL_CALL: write_file({"path": "a.txt", "content": "hi"})'
    )
    assert thought == "write it"
    assert action == "write_file"
    assert action_input == {"path": "a.txt", "content": "hi"}


def test_parse_response_without_action_is_final_answer():
    loop = make_loop(FakeProvider([]), ToolRegistry())
    thought, action, action_input = loop._parse_response("Thought: 2 + 2 = 4")
    assert thought == "2 + 2 = 4"
    assert action is None
    assert action_input is None


def test_parse_json_repairs_single_quotes_and_trailing_commas():
    loop = make_loop(FakeProvider([]), ToolRegistry())
    assert loop._parse_json("{'query': 'test',}") == {"query": "test"}


@pytest.mark.asyncio
async def test_streaming_callback_receives_fragments():
    provider = FakeProvider([text_response("hello world")])
    loop = make_loop(provider, ToolRegistry())
    chunks: list[str] = []

    result = await loop.run("hi", on_chunk=chunks.append)

    assert result.status == IterationStatus.COMPLETE
    assert result.final_answer == "hello world"
    assert "".join(chunks) == "hello world"
    assert len(chunks) > 1


@pytest.mark.asyncio
async def test_streaming_still_executes_tools_and_returns_final_answer():
    provider = FakeProvider([
        tool_response(("echo", {"value": "streamed"})),
        text_response("final answer"),
    ])
    registry, calls = make_registry("echo")
    loop = make_loop(provider, registry)
    chunks: list[str] = []

    result = await loop.run("use the tool", on_chunk=chunks.append)

    assert result.status == IterationStatus.COMPLETE
    assert result.final_answer == "final answer"
    assert calls == [("echo", "streamed")]
    assert "final answer" in "".join(chunks)


@pytest.mark.asyncio
async def test_text_react_executes_tool_and_uses_user_observation():
    provider = FakeProvider([
        text_response(
            'Thought: need the echo tool\nAction: echo\n'
            'Action Input: {"value": "hello"}'
        ),
        text_response("done"),
    ])
    registry, calls = make_registry("echo")
    loop = make_loop(provider, registry)

    result = await loop.run("say hello")

    assert result.status == IterationStatus.COMPLETE
    assert result.final_answer == "done"
    assert calls == [("echo", "hello")]
    assert result.steps[0].action == "echo"
    assert result.steps[0].observation == "echo:hello"

    # The observation must be a user message.  A TOOL message without the
    # matching assistant tool_call is rejected by OpenAI-compatible APIs.
    second_call = provider.calls[1]
    assert second_call[-1].role == MessageRole.USER
    assert "Observation (echo)" in (second_call[-1].content or "")
    assert all(message.role != MessageRole.TOOL for message in second_call)


@pytest.mark.asyncio
async def test_native_multi_tool_response_executes_every_call():
    provider = FakeProvider([
        tool_response(
            ("echo", {"value": "a"}),
            ("upper", {"value": "b"}),
        ),
        text_response("finished"),
    ])
    registry, calls = make_registry("echo", "upper")
    loop = make_loop(provider, registry)

    result = await loop.run("do both")

    assert result.status == IterationStatus.COMPLETE
    assert calls == [("echo", "a"), ("upper", "b")]
    assert [call.name for call in result.tool_calls] == ["echo", "upper"]

    tool_messages = [
        message for message in provider.calls[1]
        if message.role == MessageRole.TOOL
    ]
    assert len(tool_messages) == 2
    assert [message.tool_call_id for message in tool_messages] == ["call_0", "call_1"]


@pytest.mark.asyncio
async def test_state_is_reset_between_runs():
    provider = FakeProvider([
        tool_response(("echo", {"value": "first"})),
        text_response("one"),
        text_response("two"),
    ])
    registry, _ = make_registry("echo")
    loop = make_loop(provider, registry)

    first = await loop.run("first run")
    assert first.status == IterationStatus.COMPLETE
    assert len(first.steps) == 2
    assert first.total_tokens == 4
    assert len(first.tool_calls) == 1

    second = await loop.run("second run")
    assert second.status == IterationStatus.COMPLETE
    assert len(second.steps) == 1
    assert second.total_tokens == 2
    assert second.tool_calls == []
    assert len(loop.steps) == 1


@pytest.mark.asyncio
async def test_action_without_input_executes_no_arg_tool():
    provider = FakeProvider([
        text_response("Thought: ping\nAction: ping"),
        text_response("pong received"),
    ])
    registry = ToolRegistry()
    calls: list[str] = []

    async def ping() -> str:
        calls.append("ping")
        return "pong"

    registry.register(FunctionTool("ping", "ping tool", ping))
    loop = make_loop(provider, registry)

    result = await loop.run("ping")

    assert result.status == IterationStatus.COMPLETE
    assert calls == ["ping"]
    assert result.steps[0].observation == "pong"


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_without_execution():
    provider = FakeProvider([
        text_response('Thought: try it\nAction: missing_tool\nAction Input: {}'),
        text_response("answer"),
    ])
    registry, _ = make_registry("echo")
    loop = make_loop(provider, registry)

    result = await loop.run("go")

    assert result.status == IterationStatus.COMPLETE
    observation = provider.calls[1][-1].content or ""
    assert "Unknown tool 'missing_tool'" in observation
    assert "echo" in observation


@pytest.mark.asyncio
async def test_tool_exception_becomes_error_observation():
    provider = FakeProvider([
        tool_response(("explode", {})),
        text_response("recovered"),
    ])
    registry = ToolRegistry()

    async def explode() -> str:
        raise RuntimeError("boom")

    registry.register(FunctionTool("explode", "explodes", explode))
    loop = make_loop(provider, registry)

    result = await loop.run("go")

    assert result.status == IterationStatus.COMPLETE
    observation = provider.calls[1][-1].content or ""
    assert "Tool error: boom" in observation


@pytest.mark.asyncio
async def test_tool_timeout_becomes_error_observation():
    provider = FakeProvider([
        tool_response(("slow", {})),
        text_response("recovered"),
    ])
    registry = ToolRegistry()

    async def slow() -> str:
        await asyncio.sleep(0.2)
        return "late"

    registry.register(FunctionTool("slow", "slow tool", slow))
    loop = make_loop(provider, registry, tool_timeout=0.01)

    result = await loop.run("go")

    assert result.status == IterationStatus.COMPLETE
    observation = provider.calls[1][-1].content or ""
    assert "timed out" in observation


@pytest.mark.asyncio
async def test_large_observation_is_bounded():
    provider = FakeProvider([
        tool_response(("big", {})),
        text_response("done"),
    ])
    registry = ToolRegistry()

    async def big() -> str:
        return "x" * 50_000

    registry.register(FunctionTool("big", "big output", big))
    loop = make_loop(provider, registry)

    await loop.run("go")

    observation = provider.calls[1][-1].content or ""
    assert "observation truncated" in observation
    assert len(observation) < 13_000


@pytest.mark.asyncio
async def test_max_iterations_stops_loop():
    provider = FakeProvider([
        tool_response(("echo", {"value": "1"})),
        tool_response(("echo", {"value": "2"})),
    ])
    registry, calls = make_registry("echo")
    loop = make_loop(provider, registry, max_iterations=2)

    result = await loop.run("loop forever")

    assert result.status == IterationStatus.MAX_ITERATIONS
    assert result.total_iterations == 2
    assert len(result.steps) == 2
    assert calls == [("echo", "1"), ("echo", "2")]


@pytest.mark.asyncio
async def test_streaming_response_in_non_streaming_run_is_an_error():
    class StreamingProvider:
        provider_type = ProviderType.OLLAMA

        async def complete(self, **kwargs):
            async def stream():
                yield None

            return stream()

    registry, _ = make_registry("echo")
    loop = make_loop(StreamingProvider(), registry, max_iterations=3)

    result = await loop.run("go")

    assert result.status == IterationStatus.ERROR
    assert "streaming response" in result.final_answer


@pytest.mark.asyncio
async def test_provider_error_stops_immediately():
    provider = FakeProvider(
        [],
        fail_with=ProviderUnavailableError("provider down", ProviderType.OLLAMA),
    )
    registry, _ = make_registry("echo")
    loop = make_loop(provider, registry)

    result = await loop.run("go")

    assert result.status == IterationStatus.ERROR
    assert "provider down" in result.final_answer
    assert result.errors


@pytest.mark.asyncio
async def test_on_step_callback_receives_every_step():
    provider = FakeProvider([
        tool_response(("echo", {"value": "a"})),
        text_response("done"),
    ])
    registry, _ = make_registry("echo")
    loop = make_loop(provider, registry)
    seen = []
    loop.on_step = seen.append

    await loop.run("go")

    assert len(seen) == 2
    assert seen[0].action == "echo"
    assert seen[1].observation == "Task complete"
