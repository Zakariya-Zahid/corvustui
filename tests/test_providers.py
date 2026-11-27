"""Tests for provider implementations."""

from __future__ import annotations

import pytest

from corvustui.config.settings import get_settings
from corvustui.context.manager import ContextManager
from corvustui.providers import ProviderFactory, ProviderType
from corvustui.react.loop import ReActLoop
from corvustui.tools.builtin import register_builtin_tools
from corvustui.tools.registry import ToolRegistry


def test_provider_factory_registration():
    """Test that all providers are registered."""
    for provider_type in ProviderType:
        # Should not raise
        provider_class = ProviderFactory._providers.get(provider_type)
        assert provider_class is not None, f"Provider {provider_type} not registered"


def test_provider_factory_uses_provider_specific_default_models(monkeypatch):
    """A local Ollama tag must not be used as a cloud provider default."""
    settings = get_settings()
    expected = {
        ProviderType.OLLAMA: settings.default_model,
        ProviderType.OPENAI: settings.openai_default_model,
        ProviderType.ANTHROPIC: settings.anthropic_default_model,
        ProviderType.GROQ: settings.groq_default_model,
        ProviderType.GEMINI: settings.gemini_default_model,
        ProviderType.OPENROUTER: settings.openrouter_default_model,
        ProviderType.GROK: settings.grok_default_model,
    }

    class DummyProvider:
        def __init__(self, model, **kwargs):
            self.model = model
            self.kwargs = kwargs

    for provider_type in ProviderType:
        monkeypatch.setitem(ProviderFactory._providers, provider_type, DummyProvider)

    for provider_type, model in expected.items():
        provider = ProviderFactory.create(provider_type)
        assert provider.model == model
        assert "qwen" not in provider.model.lower() or provider_type == ProviderType.OLLAMA


def test_tool_registry():
    """Test tool registry operations."""
    registry = ToolRegistry()

    async def dummy_tool(x: int) -> str:
        return f"Result: {x}"

    registry.register_function("test_tool", "A test tool", dummy_tool)

    assert "test_tool" in registry
    assert len(registry) == 1
    tool = registry.get("test_tool")
    assert tool is not None
    assert tool.name == "test_tool"

    definitions = registry.get_definitions()
    assert len(definitions) == 1
    assert definitions[0].name == "test_tool"


def test_builtin_tools_registration():
    """Test built-in tools registration."""
    registry = ToolRegistry()
    register_builtin_tools(registry)

    expected_tools = {"read_file", "write_file", "list_files", "shell", "web_search"}
    registered = {t.name for t in registry.get_all()}
    assert expected_tools.issubset(registered)


def test_context_manager():
    """Test context manager basic operations."""
    # Use a mock provider for testing
    class MockProvider:
        def count_message_tokens(self, msg):
            return len(msg.content or "") // 4

        def count_messages_tokens(self, messages):
            return sum(self.count_message_tokens(m) for m in messages)

    provider = MockProvider()
    ctx = ContextManager(provider, max_tokens=1000, reserved_tokens=100)

    ctx.add_system("You are a helpful assistant.")
    ctx.add_user("Hello!")
    ctx.add_assistant("Hi there!")

    messages = ctx.get_all_messages()
    assert len(messages) == 3
    assert messages[0].role.value == "system"
    assert messages[1].role.value == "user"
    assert messages[2].role.value == "assistant"

    stats = ctx.get_stats()
    assert stats.total_tokens > 0
    assert stats.max_tokens == 1000


@pytest.mark.asyncio
async def test_react_loop_parsing():
    """Test ReAct response parsing."""
    class MockProvider:
        async def complete(self, **kwargs):
            class MockResponse:
                content = "Thought: I need to search\nAction: web_search\nAction Input: {\"query\": \"test\"}"
                tool_calls = []
                usage = type('Usage', (), {'total_tokens': 10})()
                finish_reason = "tool_calls"
            return MockResponse()

    tools = ToolRegistry()
    register_builtin_tools(tools)

    provider = MockProvider()
    loop = ReActLoop(provider, tools, max_iterations=2)

    # Test parsing
    content = 'Thought: Test\nAction: test_tool\nAction Input: {"x": 42}'
    thought, action, action_input = loop._parse_response(content)
    assert thought == "Test"
    assert action == "test_tool"
    assert action_input == {"x": 42}


@pytest.mark.asyncio
async def test_in_memory_vector():
    """Test in-memory vector memory."""
    from corvustui.memory.vector import InMemoryVectorMemory

    memory = InMemoryVectorMemory()

    await memory.add("Python is a programming language", {"topic": "python"})
    await memory.add("JavaScript is for web development", {"topic": "javascript"})

    # In-memory uses simple keyword overlap (Jaccard similarity)
    # Use lower threshold for keyword-based search
    results = await memory.search("Python programming language", top_k=2, threshold=0.3)
    assert len(results) >= 1
    assert any("Python" in r.content for r in results)

    stats = await memory.get_stats()
    assert stats["total_entries"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])