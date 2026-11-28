"""Tests for agent orchestration and provider lifecycle."""

from __future__ import annotations

import pytest

from corvustui.core import agent as agent_module
from corvustui.core.agent import AgentConfig, CorvusAgent
from corvustui.providers import (
    MessageRole,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
    ProviderType,
)
from corvustui.react.loop import IterationStatus
from tests.fakes import FakeProvider, text_response, tool_response


def install_provider(monkeypatch, provider):
    def fake_create(provider_type, model=None, **kwargs):
        return provider

    monkeypatch.setattr(agent_module.ProviderFactory, "create", fake_create)
    return provider


@pytest.mark.asyncio
async def test_run_passes_history_and_adds_new_user_once(monkeypatch):
    provider = install_provider(monkeypatch, FakeProvider([text_response("reply")]))
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="fake"))

    agent.context.add_user("old question")
    agent.context.add_assistant("old answer")

    async with agent:
        result = await agent.run("new question")

    assert result.final_answer == "reply"

    sent = provider.calls[0]
    assert sent[0].role == MessageRole.SYSTEM
    assert sent[-1].role == MessageRole.USER
    assert sent[-1].content == "new question"
    assert [m.content for m in sent if m.role == MessageRole.USER] == [
        "old question",
        "new question",
    ]

    stored = agent.context.messages
    assert [m.role for m in stored] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert stored[2].content == "new question"
    assert stored[3].content == "reply"


@pytest.mark.asyncio
async def test_run_includes_rolling_summary_but_not_duplicate_system(monkeypatch):
    provider = install_provider(monkeypatch, FakeProvider([text_response("reply")]))
    agent = CorvusAgent(
        AgentConfig(
            provider_type=ProviderType.OLLAMA,
            model="fake",
            system_prompt="You are Corvus.",
        )
    )
    agent.context._summary = "The user likes concise answers."
    agent.context.add_user("recent question")

    async with agent:
        await agent.run("new question")

    sent = provider.calls[0]
    system_messages = [m for m in sent if m.role == MessageRole.SYSTEM]
    assert len(system_messages) == 2
    assert "You are Corvus." in (system_messages[0].content or "")
    assert "Previous conversation summary" in (system_messages[1].content or "")
    assert "concise answers" in (system_messages[1].content or "")


@pytest.mark.asyncio
async def test_run_injects_relevant_rag_memory(monkeypatch):
    provider = install_provider(monkeypatch, FakeProvider([text_response("reply")]))
    agent = CorvusAgent(
        AgentConfig(
            provider_type=ProviderType.OLLAMA,
            model="fake",
            enable_rag=True,
        )
    )
    assert agent.memory is not None
    await agent.memory.add("ocean deep")

    async with agent:
        await agent.run("ocean deep")

    sent = provider.calls[0]
    system_prompt = sent[0].content or ""
    assert "Relevant memories" in system_prompt
    assert "ocean deep" in system_prompt


@pytest.mark.asyncio
async def test_run_uses_configured_rag_search_parameters(monkeypatch):
    install_provider(monkeypatch, FakeProvider([text_response("reply")]))
    agent = CorvusAgent(
        AgentConfig(
            provider_type=ProviderType.OLLAMA,
            model="fake",
            enable_rag=True,
            rag_top_k=2,
            rag_similarity_threshold=0.4,
        )
    )

    calls: list[tuple[str, int, float]] = []

    class RecordingMemory:
        async def search(self, query, top_k=5, threshold=0.7, filter_metadata=None):
            calls.append((query, top_k, threshold))
            return []

        async def add(self, content, metadata=None):
            return "id"

        async def get_stats(self):
            return {"total_entries": 0}

    agent.memory = RecordingMemory()

    async with agent:
        await agent.run("question")

    assert calls == [("question", 2, 0.4)]


@pytest.mark.asyncio
async def test_run_streaming_forwards_provider_chunks(monkeypatch):
    provider = install_provider(
        monkeypatch, FakeProvider([text_response("streamed answer")])
    )
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="fake"))
    chunks: list[str] = []

    async with agent:
        result = await agent.run_streaming("question", chunks.append)

    assert result.final_answer == "streamed answer"
    assert "".join(chunks) == "streamed answer"
    assert len(chunks) > 1
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_switch_model_closes_old_provider_and_updates_dependencies(monkeypatch):
    old = FakeProvider([], model="old")
    new = FakeProvider([], model="new")

    def fake_create(provider_type, model=None, **kwargs):
        return new if provider_type == ProviderType.OPENAI else old

    monkeypatch.setattr(agent_module.ProviderFactory, "create", fake_create)
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="old"))

    async with agent:
        assert agent.provider is old
        await agent.switch_model(ProviderType.OPENAI, "gpt-4o")

        assert agent.provider is new
        assert agent.react_loop.provider is new
        assert agent.context.provider is new
        assert agent.config.provider_type == ProviderType.OPENAI
        assert agent.config.model == "gpt-4o"
        assert new.entered == 1
        assert new.validated == 1

    assert old.exited == 1
    assert new.exited == 1


@pytest.mark.asyncio
async def test_failed_switch_keeps_old_provider_alive(monkeypatch):
    old = FakeProvider([], model="old")
    new = FakeProvider([], model="bad")
    new.validation_error = ProviderModelNotFoundError(
        "model 'bad' not found",
        ProviderType.OPENAI,
    )

    def fake_create(provider_type, model=None, **kwargs):
        return new if provider_type == ProviderType.OPENAI else old

    monkeypatch.setattr(agent_module.ProviderFactory, "create", fake_create)
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="old"))

    async with agent:
        with pytest.raises(ProviderModelNotFoundError):
            await agent.switch_model(ProviderType.OPENAI, "bad")

        assert agent.provider is old
        assert agent.config.provider_type == ProviderType.OLLAMA
        assert old.exited == 0
        assert new.exited == 1

    assert old.exited == 1


def test_register_function_tool_refreshes_system_prompt(monkeypatch):
    install_provider(monkeypatch, FakeProvider([]))
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="fake"))

    async def custom_tool(query: str) -> str:
        return query

    assert "custom_lookup" not in agent.react_loop.system_prompt
    agent.register_function_tool("custom_lookup", "Look things up", custom_tool)
    assert "custom_lookup" in agent.react_loop.system_prompt


def test_register_tool_refreshes_system_prompt(monkeypatch):
    from corvustui.tools.base import Tool, ToolResult

    install_provider(monkeypatch, FakeProvider([]))
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="fake"))

    class CustomTool(Tool):
        name = "custom_tool"
        description = "A custom tool"
        parameters = {"type": "object", "properties": {}, "required": []}

        async def execute(self, **kwargs) -> ToolResult:
            return ToolResult.ok("ok")

    agent.register_tool(CustomTool())
    assert "custom_tool" in agent.react_loop.system_prompt


@pytest.mark.asyncio
async def test_run_reinjects_todos_and_completed_work(monkeypatch):
    provider = install_provider(monkeypatch, FakeProvider([text_response("ok")]))
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="fake"))
    agent.todos.replace_from_arguments(
        {"todos": [{"content": "Build expense tracker", "status": "in_progress"}]}
    )
    agent.work_log.append(
        "- write_file({\"path\": \"test_web/index.html\"}) [ok] -> created"
    )

    async with agent:
        await agent.run("continue")

    system_prompt = provider.calls[0][0].content or ""
    assert "Current task list" in system_prompt
    assert "Build expense tracker" in system_prompt
    assert "Work already completed" in system_prompt
    assert "test_web/index.html" in system_prompt


@pytest.mark.asyncio
async def test_rate_limit_keeps_partial_work_for_next_prompt(monkeypatch):
    from corvustui.tools.base import Tool, ToolResult

    class EchoTool(Tool):
        name = "echo"
        description = "Echo a value"
        parameters = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

        async def execute(self, value: str = "", **kwargs) -> ToolResult:
            return ToolResult.ok(f"created {value}")

    class RateLimitedProvider(FakeProvider):
        def __init__(self, responses, fail_after):
            super().__init__(responses)
            self.fail_after = fail_after

        async def complete(self, messages, **kwargs):
            if len(self.calls) >= self.fail_after:
                raise ProviderRateLimitError("rate limited", ProviderType.OLLAMA)
            return await super().complete(messages, **kwargs)

    provider = RateLimitedProvider(
        [tool_response(("echo", {"value": "test_web/index.html"}))],
        fail_after=1,
    )
    install_provider(monkeypatch, provider)
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="fake"))
    agent.register_tool(EchoTool())
    agent.todos.replace_from_arguments(
        {"todos": [{"content": "Build expense tracker", "status": "in_progress"}]}
    )

    async with agent:
        result = await agent.run("create the expense tracker")
        assert result.status == IterationStatus.ERROR

        assert any(
            "echo" in entry and "created test_web/index.html" in entry
            for entry in agent.work_log
        )
        history = [message.content or "" for message in agent.context.messages]
        assert "create the expense tracker" in history
        assert any("Turn interrupted" in text for text in history)

        provider.fail_after = 99
        await agent.run("create the expense tracker")

    retry_prompt = provider.calls[1][0].content or ""
    assert "Build expense tracker" in retry_prompt
    assert "created test_web/index.html" in retry_prompt


def test_reset_session_clears_context_tasks_and_work_log(monkeypatch):
    install_provider(monkeypatch, FakeProvider([]))
    agent = CorvusAgent(AgentConfig(provider_type=ProviderType.OLLAMA, model="fake"))
    agent.context.add_user("old")
    agent.todos.replace_from_arguments(
        {"todos": [{"content": "Old task", "status": "pending"}]}
    )
    agent.work_log.append("- old work")

    agent.reset_session()

    assert agent.context.messages == []
    assert agent.todos.items == []
    assert agent.work_log == []
