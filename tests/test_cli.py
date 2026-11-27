"""Tests for CLI model listing and provider routing."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from corvustui import cli as cli_module
from corvustui.providers import (
    ProviderModelNotFoundError,
    ProviderType,
    ProviderUnavailableError,
)


@pytest.mark.asyncio
async def test_model_status_unreachable_provider_is_actionable(monkeypatch):
    async def fake_fetch(provider_type, timeout=15.0):
        raise ProviderUnavailableError(
            "Ollama is not reachable at http://localhost:11434. "
            "Start it with 'ollama serve' and try again.",
            provider_type,
        )

    monkeypatch.setattr(cli_module, "_fetch_models", fake_fetch)

    models, error = await cli_module._model_status(ProviderType.OLLAMA)

    assert models == []
    assert error is not None
    assert "ollama serve" in error.lower()


@pytest.mark.asyncio
async def test_model_status_ollama_empty_is_not_a_fake_list(monkeypatch):
    async def fake_fetch(provider_type, timeout=15.0):
        return []

    monkeypatch.setattr(cli_module, "_fetch_models", fake_fetch)

    models, error = await cli_module._model_status(ProviderType.OLLAMA)

    assert models == []
    assert error is not None
    assert "no models are installed" in error.lower()
    assert "ollama pull" in error.lower()


@pytest.mark.asyncio
async def test_model_status_returns_live_models(monkeypatch):
    async def fake_fetch(provider_type, timeout=15.0):
        return ["qwen3.5:0.8b", "llama3.2:3b"]

    monkeypatch.setattr(cli_module, "_fetch_models", fake_fetch)

    models, error = await cli_module._model_status(ProviderType.OLLAMA)

    assert error is None
    assert models == ["qwen3.5:0.8b", "llama3.2:3b"]


@pytest.mark.asyncio
async def test_model_status_empty_cloud_list(monkeypatch):
    async def fake_fetch(provider_type, timeout=15.0):
        return []

    monkeypatch.setattr(cli_module, "_fetch_models", fake_fetch)

    models, error = await cli_module._model_status(ProviderType.OPENAI)

    assert models == []
    assert error is not None
    assert "empty model list" in error.lower()


def test_model_error_message_for_not_supported_listing():
    message = cli_module._model_error_message(
        ProviderType.OLLAMA,
        NotImplementedError("no listing"),
    )
    assert "does not support dynamic model listing" in message.lower()
    assert "--model" in message


def test_models_command_shows_dynamic_names(monkeypatch):
    async def fake_status(provider_type):
        return ["dynamic-model-a", "dynamic-model-b"], None

    monkeypatch.setattr(cli_module, "_model_status", fake_status)

    result = CliRunner().invoke(
        cli_module.cli,
        ["models", "--provider", "ollama"],
    )

    assert result.exit_code == 0, result.output
    assert "dynamic-model-a" in result.output
    assert "dynamic-model-b" in result.output


def test_models_command_shows_error_and_no_invented_names(monkeypatch):
    async def fake_status(provider_type):
        return [], "Ollama is running but no models are installed. Install one with 'ollama pull <model>'."

    monkeypatch.setattr(cli_module, "_model_status", fake_status)

    result = CliRunner().invoke(
        cli_module.cli,
        ["models", "--provider", "ollama"],
    )

    assert result.exit_code == 0, result.output
    assert "no models are installed" in result.output.lower()
    assert "llama3" not in result.output.lower()
    assert "qwen" not in result.output.lower()


def test_models_command_all_providers_uses_live_status(monkeypatch):
    async def fake_status(provider_type):
        if provider_type == ProviderType.OLLAMA:
            return ["local-model:1b"], None
        return [], "not configured"

    monkeypatch.setattr(cli_module, "_model_status", fake_status)

    result = CliRunner().invoke(cli_module.cli, ["models"])

    assert result.exit_code == 0, result.output
    assert "local-model:1b" in result.output
    assert "not configured" in result.output


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("qwen3.5:0.8b", ProviderType.OLLAMA),
        ("gemma2:2b", ProviderType.OLLAMA),
        ("mixtral:8x7b", ProviderType.OLLAMA),
        ("hf.co/bartowski/model-GGUF:Q4_K_M", ProviderType.OLLAMA),
        ("openai/gpt-4o-mini", ProviderType.OPENROUTER),
        ("gpt-4o", ProviderType.OPENAI),
        ("o3-mini", ProviderType.OPENAI),
        ("claude-3-5-sonnet-latest", ProviderType.ANTHROPIC),
        ("gemini-1.5-flash", ProviderType.GEMINI),
        ("grok-2-latest", ProviderType.GROK),
        ("llama-3.1-8b-instant", ProviderType.GROQ),
        ("llama-3.3-70b-versatile", ProviderType.GROQ),
        ("mixtral-8x7b-32768", ProviderType.GROQ),
    ],
)
def test_detect_provider_from_model(model, expected):
    assert cli_module.detect_provider_from_model(model) == expected


def _capture_run_single(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_run_single(
        provider,
        model,
        prompt,
        max_iterations,
        enable_rag=False,
        rag_path=None,
        **kwargs,
    ):
        captured.update(
            provider=provider,
            model=model,
            prompt=prompt,
            max_iterations=max_iterations,
            enable_rag=enable_rag,
            rag_path=rag_path,
            **kwargs,
        )

    monkeypatch.setattr(cli_module, "run_single", fake_run_single)
    return captured


def test_run_infers_provider_from_global_model(monkeypatch):
    captured = _capture_run_single(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--model", "openai/gpt-4o-mini", "run", "hello"],
    )

    assert result.exit_code == 0, result.output
    assert captured["provider"] == ProviderType.OPENROUTER
    assert captured["model"] == "openai/gpt-4o-mini"


def test_run_infers_provider_from_subcommand_model(monkeypatch):
    captured = _capture_run_single(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["run", "--model", "claude-3-5-sonnet-latest", "hello"],
    )

    assert result.exit_code == 0, result.output
    assert captured["provider"] == ProviderType.ANTHROPIC


def test_run_keeps_explicit_provider(monkeypatch):
    captured = _capture_run_single(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--provider", "ollama", "--model", "gpt-4o", "run", "hello"],
    )

    assert result.exit_code == 0, result.output
    assert captured["provider"] == ProviderType.OLLAMA


def test_run_defaults_to_configured_provider(monkeypatch):
    captured = _capture_run_single(monkeypatch)

    result = CliRunner().invoke(cli_module.cli, ["run", "hello"])

    assert result.exit_code == 0, result.output
    assert captured["provider"] == cli_module.settings.default_provider
    assert captured["model"] is None


def test_run_propagates_parent_max_iterations(monkeypatch):
    captured = _capture_run_single(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--max-iterations", "7", "run", "hello"],
    )

    assert result.exit_code == 0, result.output
    assert captured["max_iterations"] == 7


def test_run_propagates_global_rag_options(monkeypatch):
    captured = _capture_run_single(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--rag", "--rag-path", "/tmp/corvus-memory", "run", "hello"],
    )

    assert result.exit_code == 0, result.output
    assert captured["enable_rag"] is True
    assert captured["rag_path"] == Path("/tmp/corvus-memory")


def test_installed_model_name_accepts_latest_tag():
    assert cli_module._installed_model_name("llama3.1", ["llama3.1:latest"]) == "llama3.1:latest"
    assert cli_module._installed_model_name("llama3.1:8b", ["llama3.1:8b"]) == "llama3.1:8b"
    assert cli_module._installed_model_name("missing", ["qwen3.5:0.8b"]) is None


@pytest.mark.asyncio
async def test_fallback_ollama_model_auto_selects_only_installed(monkeypatch):
    async def fake_status(provider_type):
        return ["qwen3.5:0.8b"], None

    monkeypatch.setattr(cli_module, "_model_status", fake_status)

    selected = await cli_module._fallback_ollama_model("llama3.1:8b")

    assert selected == "qwen3.5:0.8b"


@pytest.mark.asyncio
async def test_fallback_ollama_model_requires_choice_when_non_interactive(monkeypatch):
    async def fake_status(provider_type):
        return ["qwen3.5:0.8b", "llama3.2:3b"], None

    monkeypatch.setattr(cli_module, "_model_status", fake_status)
    monkeypatch.setattr(
        cli_module.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: False),
    )

    selected = await cli_module._fallback_ollama_model("llama3.1:8b")

    assert selected is None


@pytest.mark.asyncio
async def test_interactive_starts_with_installed_model_when_default_missing(monkeypatch):
    class FakeAgent:
        instances = []

        def __init__(self, config):
            self.config = config
            self.initial_model = config.model
            self.provider = SimpleNamespace(
                provider_type=config.provider_type,
                model=config.model,
            )
            FakeAgent.instances.append(self)

        async def __aenter__(self):
            if len(FakeAgent.instances) == 1:
                raise ProviderModelNotFoundError(
                    "Model 'llama3.1:8b' is not installed in Ollama.",
                    ProviderType.OLLAMA,
                )
            self.provider.model = self.config.model
            return self

        async def __aexit__(self, *args):
            pass

    async def fake_status(provider_type):
        return ["qwen3.5:0.8b"], None

    monkeypatch.setattr(cli_module, "CorvusAgent", FakeAgent)
    monkeypatch.setattr(cli_module, "_model_status", fake_status)
    monkeypatch.setattr(cli_module.Prompt, "ask", staticmethod(lambda *args, **kwargs: "exit"))

    await cli_module.run_interactive(
        provider=ProviderType.OLLAMA,
        model=None,
        max_iterations=3,
        enable_rag=False,
        rag_path=None,
    )

    assert len(FakeAgent.instances) == 2
    assert FakeAgent.instances[0].initial_model is None
    assert FakeAgent.instances[1].initial_model == "qwen3.5:0.8b"


@pytest.mark.asyncio
async def test_run_single_falls_back_to_only_installed_ollama_model(monkeypatch):
    class FakeAgent:
        instances = []

        def __init__(self, config):
            self.config = config
            self.initial_model = config.model
            FakeAgent.instances.append(self)

        async def __aenter__(self):
            if len(FakeAgent.instances) == 1:
                raise ProviderModelNotFoundError(
                    "Model 'llama3.1:8b' is not installed in Ollama.",
                    ProviderType.OLLAMA,
                )
            return self

        async def __aexit__(self, *args):
            pass

        async def run(self, prompt, on_step=None):
            return SimpleNamespace(
                final_answer="ok",
                status=SimpleNamespace(value="complete"),
            )

    async def fake_status(provider_type):
        return ["qwen3.5:0.8b"], None

    monkeypatch.setattr(cli_module, "CorvusAgent", FakeAgent)
    monkeypatch.setattr(cli_module, "_model_status", fake_status)

    await cli_module.run_single(
        provider=ProviderType.OLLAMA,
        model=None,
        prompt="hi",
        max_iterations=3,
    )

    assert len(FakeAgent.instances) == 2
    assert FakeAgent.instances[0].initial_model is None
    assert FakeAgent.instances[1].initial_model == "qwen3.5:0.8b"


@pytest.mark.asyncio
async def test_run_single_never_replaces_an_explicit_missing_model(monkeypatch):
    class FakeAgent:
        instances = []

        def __init__(self, config):
            self.config = config
            FakeAgent.instances.append(self)

        async def __aenter__(self):
            raise ProviderModelNotFoundError(
                "Model 'missing:1' is not installed in Ollama.",
                ProviderType.OLLAMA,
            )

        async def __aexit__(self, *args):
            pass

    async def fake_status(provider_type):
        return ["qwen3.5:0.8b"], None

    monkeypatch.setattr(cli_module, "CorvusAgent", FakeAgent)
    monkeypatch.setattr(cli_module, "_model_status", fake_status)

    with pytest.raises(cli_module.click.ClickException):
        await cli_module.run_single(
            provider=ProviderType.OLLAMA,
            model="missing:1",
            prompt="hi",
            max_iterations=3,
        )

    assert len(FakeAgent.instances) == 1


def test_run_propagates_safety_and_mcp_flags(monkeypatch, tmp_path):
    captured = _capture_run_single(monkeypatch)
    audit = tmp_path / "audit.jsonl"

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "--yes",
            "--allow-path", str(tmp_path),
            "--audit-log", str(audit),
            "--mcp-config", "/tmp/mcp.json",
            "run", "hello",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["auto_approve"] is True
    assert captured["allow_paths"] == (str(tmp_path),)
    assert captured["enable_safety"] is True
    assert captured["audit_log"] == audit
    assert captured["mcp_config"] == Path("/tmp/mcp.json")
    assert captured["enable_mcp"] is True


def test_run_no_safety_and_no_mcp_flags(monkeypatch):
    captured = _capture_run_single(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--no-safety", "--no-mcp", "run", "hello"],
    )

    assert result.exit_code == 0, result.output
    assert captured["enable_safety"] is False
    assert captured["enable_mcp"] is False


def test_run_respects_safety_enabled_setting(monkeypatch):
    captured = _capture_run_single(monkeypatch)
    monkeypatch.setattr(cli_module.settings, "safety_enabled", False)

    result = CliRunner().invoke(cli_module.cli, ["run", "hello"])

    assert result.exit_code == 0, result.output
    assert captured["enable_safety"] is False


def test_run_local_yes_flag(monkeypatch):
    captured = _capture_run_single(monkeypatch)

    result = CliRunner().invoke(cli_module.cli, ["run", "--yes", "hello"])

    assert result.exit_code == 0, result.output
    assert captured["auto_approve"] is True


def _write_mcp_config(tmp_path: Path) -> Path:
    server = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "fixture": {"command": sys.executable, "args": [str(server)]},
        }
    }))
    return config_path


def test_mcp_list_command(tmp_path):
    config_path = _write_mcp_config(tmp_path)

    result = CliRunner().invoke(
        cli_module.cli,
        ["mcp", "list", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "fixture" in result.output
    assert "stdio" in result.output


def test_mcp_list_redacts_secret_in_command_args(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "fixture": {
                "command": "true",
                "args": ["api_key=sk-test-1234567890abcdef"],
            },
        }
    }))

    result = CliRunner().invoke(
        cli_module.cli,
        ["mcp", "list", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "sk-test-1234567890abcdef" not in result.output
    assert "[REDACTED]" in result.output


def test_mcp_tools_and_call_commands(tmp_path):
    config_path = _write_mcp_config(tmp_path)
    runner = CliRunner()

    tools_result = runner.invoke(
        cli_module.cli,
        ["mcp", "tools", "--config", str(config_path)],
    )
    assert tools_result.exit_code == 0, tools_result.output
    assert "mcp_fixture_echo" in tools_result.output

    call_result = runner.invoke(
        cli_module.cli,
        [
            "mcp", "call", "fixture", "echo",
            "--args", '{"text": "hello"}',
            "--config", str(config_path),
        ],
    )
    assert call_result.exit_code == 0, call_result.output
    assert "echo:hello" in call_result.output


def test_mcp_call_rejects_invalid_args(tmp_path):
    config_path = _write_mcp_config(tmp_path)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "mcp", "call", "fixture", "echo",
            "--args", "not-json",
            "--config", str(config_path),
        ],
    )

    assert result.exit_code != 0
    assert "JSON object" in result.output


def test_mcp_commands_require_config(monkeypatch):
    monkeypatch.setattr(cli_module.settings, "mcp_servers_config", None)

    result = CliRunner().invoke(cli_module.cli, ["mcp", "list"])

    assert result.exit_code != 0
    assert "No MCP config configured" in result.output


def test_print_step_redacts_secrets(monkeypatch):
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    monkeypatch.setattr(
        cli_module,
        "console",
        Console(file=buffer, force_terminal=False, width=200),
    )
    secret = "sk-test-1234567890abcdef"
    cli_module.print_step(SimpleNamespace(
        thought=f"Use api_key={secret}",
        action="shell",
        action_input={"command": f"echo api_key={secret}"},
        observation=f"result api_key={secret}",
        error=f"token={secret}",
    ))

    output = buffer.getvalue()
    assert secret not in output
    assert "[REDACTED]" in output


def test_mcp_call_redacts_secret_result(tmp_path):
    config_path = _write_mcp_config(tmp_path)

    result = CliRunner().invoke(
        cli_module.cli,
        ["mcp", "call", "fixture", "secret", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "sk-test-1234567890abcdef" not in result.output
    assert "[REDACTED]" in result.output
