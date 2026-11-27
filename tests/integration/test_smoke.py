"""Cross-module smoke tests run by the CI integration lane."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from corvustui import cli as cli_module
from corvustui.core import agent as agent_module
from corvustui.core.agent import AgentConfig, CorvusAgent
from corvustui.providers import ProviderType
from tests.fakes import FakeProvider, text_response, tool_response

pytestmark = pytest.mark.integration


def test_cli_help_smoke() -> None:
    result = CliRunner().invoke(cli_module.cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
    assert "run" in result.output
    assert "tui" in result.output


@pytest.mark.asyncio
async def test_agent_executes_registered_tool_end_to_end(monkeypatch) -> None:
    provider = FakeProvider(
        [
            tool_response(("echo", {"value": "hello"})),
            text_response("finished"),
        ]
    )

    def fake_create(*args, **kwargs):
        return provider

    monkeypatch.setattr(agent_module.ProviderFactory, "create", fake_create)

    calls: list[str] = []

    async def echo(value: str) -> str:
        calls.append(value)
        return f"echo:{value}"

    agent = CorvusAgent(
        AgentConfig(
            provider_type=ProviderType.OLLAMA,
            model="fake-model",
            enable_mcp=False,
        )
    )
    agent.register_function_tool(
        "echo",
        "Echo a value",
        echo,
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )

    async with agent:
        result = await agent.run("use the echo tool")

    assert result.final_answer == "finished"
    assert calls == ["hello"]
    assert len(provider.calls) == 2
