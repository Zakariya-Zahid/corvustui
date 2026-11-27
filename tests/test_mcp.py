"""Tests for the built-in MCP client, transports, config, and tool bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from corvustui.core import agent as agent_module
from corvustui.core.agent import AgentConfig, CorvusAgent
from corvustui.mcp import (
    HTTPTransport,
    MCPClient,
    MCPConfigError,
    MCPError,
    MCPManager,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTool,
    MCPTransportError,
    StdioTransport,
    build_tool_name,
    load_mcp_config,
)
from corvustui.mcp.protocol import (
    make_notification,
    make_request,
    parse_message,
    validate_response,
)
from corvustui.providers import ProviderType
from corvustui.safety import AutoApprover, SafetyPolicy
from corvustui.tools.registry import ToolRegistry
from tests.fakes import FakeProvider, text_response, tool_response

SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def server_command() -> list[str]:
    return [sys.executable, str(SERVER)]


# --- Protocol --------------------------------------------------------------


def test_make_request_and_notification_shapes():
    request = make_request(7, "tools/list", {})
    notification = make_notification("notifications/initialized")

    assert request == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/list",
        "params": {},
    }
    assert notification == {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }


def test_parse_message_rejects_bad_version():
    with pytest.raises(MCPProtocolError):
        parse_message({"jsonrpc": "1.0", "id": 1, "result": {}})


def test_validate_response_raises_jsonrpc_error():
    response = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "nope"},
    }

    with pytest.raises(MCPError) as exc_info:
        validate_response(response, 1)

    assert exc_info.value.code == -32601
    assert "nope" in str(exc_info.value)


def test_validate_response_rejects_mismatched_id():
    with pytest.raises(MCPProtocolError):
        validate_response(
            {"jsonrpc": "2.0", "id": 2, "result": {}},
            1,
        )


# --- Config ----------------------------------------------------------------


def test_load_json_config_and_expand_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TEST_TOKEN", "abc123")
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "fixture": {
                "command": sys.executable,
                "args": [str(SERVER)],
                "env": {"TOKEN": "${MCP_TEST_TOKEN}"},
                "cwd": ".",
            }
        }
    }))

    configs = load_mcp_config(config_path)

    assert len(configs) == 1
    config = configs[0]
    assert config.name == "fixture"
    assert config.transport == "stdio"
    assert config.command == (sys.executable, str(SERVER))
    assert config.env["TOKEN"] == "abc123"
    assert config.cwd == tmp_path.resolve()


def test_load_yaml_list_config(tmp_path):
    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        "servers:\n"
        "  - name: remote\n"
        "    url: https://example.test/mcp\n"
        "    headers:\n"
        "      X-Test: value\n"
    )

    configs = load_mcp_config(config_path)

    assert len(configs) == 1
    assert configs[0].transport == "http"
    assert configs[0].url == "https://example.test/mcp"
    assert configs[0].headers == {"X-Test": "value"}


def test_load_config_rejects_missing_file(tmp_path):
    with pytest.raises(MCPConfigError, match="not found"):
        load_mcp_config(tmp_path / "missing.json")


def test_load_config_rejects_unset_env(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_MISSING_TOKEN", raising=False)
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "bad": {
                "command": "true",
                "env": {"TOKEN": "${MCP_MISSING_TOKEN}"},
            }
        }
    }))

    with pytest.raises(MCPConfigError, match="unset environment variable"):
        load_mcp_config(config_path)


def test_config_rejects_command_and_url_together(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "bad": {
                "command": "true",
                "url": "https://example.test/mcp",
            }
        }
    }))

    with pytest.raises(MCPConfigError, match="both command and url"):
        load_mcp_config(config_path)


def test_config_rejects_stdio_without_command(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"mcpServers": {"bad": {"args": []}}}))

    with pytest.raises(MCPConfigError, match="no command"):
        load_mcp_config(config_path)


def test_config_expands_env_in_command_and_args(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TEST_COMMAND", sys.executable)
    monkeypatch.setenv("MCP_TEST_ARG", str(SERVER))
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "fixture": {
                "command": "${MCP_TEST_COMMAND}",
                "args": ["${MCP_TEST_ARG}"],
            }
        }
    }))

    configs = load_mcp_config(config_path)

    assert configs[0].command == (sys.executable, str(SERVER))


def test_config_uses_default_timeout(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {"fixture": {"command": "true"}},
    }))

    configs = load_mcp_config(config_path, default_timeout=7.5)

    assert configs[0].timeout == 7.5


def test_config_parses_inherit_env(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "fixture": {"command": "true", "inherit_env": False},
        },
    }))

    configs = load_mcp_config(config_path)

    assert configs[0].inherit_env is False


def test_config_rejects_duplicate_server_names_in_list(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "servers": [
            {"name": "same", "command": "true"},
            {"name": "same", "command": "true"},
        ],
    }))

    with pytest.raises(MCPConfigError, match="Duplicate MCP server name"):
        load_mcp_config(config_path)


# --- Tool names ------------------------------------------------------------


def test_build_tool_name_sanitises_and_deduplicates():
    assert build_tool_name("my server", "tool.name") == "mcp_my_server_tool_name"

    existing = {"mcp_fixture_echo"}
    assert build_tool_name("fixture", "echo", existing=existing) == "mcp_fixture_echo_2"


def test_build_tool_name_is_bounded():
    name = build_tool_name("server", "x" * 200)
    assert len(name) <= 64
    assert name.startswith("mcp_server_")


# --- stdio transport -------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_client_initializes_lists_and_calls_tools():
    transport = StdioTransport(server_command())
    client = MCPClient("fixture", transport, timeout=10)
    try:
        await client.connect()
        assert client.connected
        assert client.server_info["name"] == "fixture"
        assert client.capabilities == {"tools": {}}

        tools = await client.list_tools()
        assert {tool.name for tool in tools} >= {"echo", "add", "fail"}

        echo = await client.call_tool("echo", {"text": "hello"})
        assert not echo.is_error
        assert echo.text == "echo:hello"

        added = await client.call_tool("add", {"a": 2, "b": 3})
        assert added.text == "5.0"

        failed = await client.call_tool("fail", {})
        assert failed.is_error
        assert "intentional failure" in failed.text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_client_reports_unknown_tool_error():
    client = MCPClient("fixture", StdioTransport(server_command()), timeout=10)
    try:
        await client.connect()
        with pytest.raises(MCPError, match="unknown tool"):
            await client.call_tool("missing", {})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_client_timeout_is_actionable():
    client = MCPClient("fixture", StdioTransport(server_command()), timeout=0.1)
    try:
        await client.connect()
        with pytest.raises(MCPTimeoutError):
            await client.call_tool("slow", {"seconds": 1})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_client_reports_server_crash():
    client = MCPClient("fixture", StdioTransport(server_command()), timeout=5)
    try:
        await client.connect()
        with pytest.raises(MCPTransportError, match="closed stdout"):
            await client.call_tool("crash", {})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_transport_can_filter_inherited_environment(monkeypatch):
    monkeypatch.setenv("CORVUS_PARENT_ONLY", "hidden")
    transport = StdioTransport(
        server_command(),
        env={"CORVUS_CONFIGURED": "visible"},
        inherit_env=False,
    )
    client = MCPClient("fixture", transport, timeout=10)
    try:
        await client.connect()
        configured = await client.call_tool("env", {"name": "CORVUS_CONFIGURED"})
        parent = await client.call_tool("env", {"name": "CORVUS_PARENT_ONLY"})
        path = await client.call_tool("env", {"name": "PATH"})

        assert configured.text == "visible"
        assert parent.text == "<unset>"
        assert path.text
    finally:
        await client.close()


# --- HTTP transport --------------------------------------------------------


def http_handler(request: httpx.Request) -> httpx.Response:
    message = json.loads(request.content)
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "http-fixture"},
                },
            },
            headers={"mcp-session-id": "session-1"},
        )
    if method == "notifications/initialized":
        return httpx.Response(202)
    if method == "tools/list":
        assert request.headers.get("mcp-session-id") == "session-1"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [{
                        "name": "echo",
                        "description": "Echo",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }]
                },
            },
        )
    if method == "tools/call":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": f"http:{message['params']['arguments'].get('text', '')}",
                    }]
                },
            },
        )
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_http_client_handles_session_and_calls():
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(http_handler))
    transport = HTTPTransport("https://example.test/mcp", client=http_client)
    client = MCPClient("remote", transport, timeout=5)
    try:
        await client.connect()
        assert client.server_info["name"] == "http-fixture"

        tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["echo"]

        result = await client.call_tool("echo", {"text": "hello"})
        assert result.text == "http:hello"
    finally:
        await client.close()
        await http_client.aclose()


@pytest.mark.asyncio
async def test_http_initialize_does_not_advertise_unsupported_capabilities():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        if message.get("method") == "initialize":
            seen["capabilities"] = message["params"]["capabilities"]
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "http-fixture"},
                    },
                },
            )
        return httpx.Response(202)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HTTPTransport("https://example.test/mcp", client=http_client)
    client = MCPClient("remote", transport, timeout=5)
    try:
        await client.connect()
    finally:
        await client.close()
        await http_client.aclose()

    assert seen["capabilities"] == {}


def test_http_sse_parser_finds_matching_response():
    payload = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":9,"result":{"ok":true}}\n'
        "\n"
    )

    assert HTTPTransport._parse_sse(payload, 9)["result"] == {"ok": True}


# --- Manager and tool bridge ----------------------------------------------


@pytest.mark.asyncio
async def test_manager_registers_tools_and_reports_partial_failure(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "fixture": {"command": sys.executable, "args": [str(SERVER)]},
            "broken": {"command": "/definitely/not/a/command"},
        }
    }))
    configs = load_mcp_config(config_path)
    registry = ToolRegistry()
    manager = MCPManager(configs, registry)
    try:
        statuses = await manager.start()
        by_name = {status.name: status for status in statuses}

        assert by_name["fixture"].connected
        assert by_name["fixture"].tool_count >= 5
        assert not by_name["broken"].connected
        assert "not found" in (by_name["broken"].error or "").lower()

        tool = registry.get("mcp_fixture_echo")
        assert isinstance(tool, MCPTool)
        result = await tool.execute(text="hello")
        assert result.success
        assert result.output == "echo:hello"

        failed = await registry.get("mcp_fixture_fail").execute()
        assert not failed.success
        assert "intentional failure" in failed.error
    finally:
        await manager.stop()

    assert registry.get("mcp_fixture_echo") is None


@pytest.mark.asyncio
async def test_manager_redacts_startup_errors(tmp_path, monkeypatch):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {"fixture": {"command": "true"}},
    }))
    configs = load_mcp_config(config_path)

    async def fail(self):
        raise MCPTransportError("server rejected token=sk-test-1234567890abcdef")

    monkeypatch.setattr(MCPClient, "connect", fail)
    manager = MCPManager(configs, ToolRegistry())

    statuses = await manager.start()

    assert not statuses[0].connected
    assert "sk-test-1234567890abcdef" not in (statuses[0].error or "")
    assert "[REDACTED]" in (statuses[0].error or "")


@pytest.mark.asyncio
async def test_manager_blocks_http_when_network_disabled(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "remote": {"url": "https://example.test/mcp"},
        }
    }))
    configs = load_mcp_config(config_path)
    manager = MCPManager(configs, ToolRegistry(), network_enabled=False)

    statuses = await manager.start()

    assert len(statuses) == 1
    assert not statuses[0].connected
    assert "network access is disabled" in (statuses[0].error or "").lower()
    await manager.stop()


# --- Agent integration -----------------------------------------------------


@pytest.mark.asyncio
async def test_agent_starts_mcp_and_runs_discovered_tool(tmp_path, monkeypatch):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "fixture": {"command": sys.executable, "args": [str(SERVER)]},
        }
    }))

    provider = FakeProvider([
        tool_response(("mcp_fixture_echo", {"text": "hello"})),
        text_response("done"),
    ])
    monkeypatch.setattr(
        agent_module.ProviderFactory,
        "create",
        lambda provider_type, model=None, **kwargs: provider,
    )

    policy = SafetyPolicy(
        allowed_roots=(tmp_path,),
        auto_approve=True,
        audit_path=tmp_path / "audit.jsonl",
    )
    config = AgentConfig(
        provider_type=ProviderType.OLLAMA,
        model="fake",
        mcp_config=config_path,
        safety_policy=policy,
        approver=AutoApprover(),
    )
    agent = CorvusAgent(config)

    async with agent:
        assert agent.mcp_statuses
        assert agent.mcp_statuses[0].connected
        result = await agent.run("use echo")

    assert result.final_answer == "done"
    assert "mcp_fixture_echo" in agent.react_loop.system_prompt


@pytest.mark.asyncio
async def test_agent_surfaces_mcp_config_error(tmp_path, monkeypatch):
    provider = FakeProvider([])
    monkeypatch.setattr(
        agent_module.ProviderFactory,
        "create",
        lambda provider_type, model=None, **kwargs: provider,
    )
    config = AgentConfig(
        provider_type=ProviderType.OLLAMA,
        model="fake",
        mcp_config=tmp_path / "missing.json",
    )
    agent = CorvusAgent(config)

    with pytest.raises(MCPConfigError):
        async with agent:
            pass

    assert provider.exited == 1


@pytest.mark.asyncio
async def test_agent_preserves_custom_system_prompt_when_mcp_starts(tmp_path, monkeypatch):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "fixture": {"command": sys.executable, "args": [str(SERVER)]},
        }
    }))

    provider = FakeProvider([])
    monkeypatch.setattr(
        agent_module.ProviderFactory,
        "create",
        lambda provider_type, model=None, **kwargs: provider,
    )
    config = AgentConfig(
        provider_type=ProviderType.OLLAMA,
        model="fake",
        system_prompt="You are a custom Corvus.",
        mcp_config=config_path,
        safety_policy=SafetyPolicy(
            allowed_roots=(tmp_path,),
            auto_approve=True,
            audit_path=tmp_path / "audit.jsonl",
        ),
    )
    agent = CorvusAgent(config)

    async with agent:
        assert agent.mcp_statuses[0].connected
        assert agent.react_loop.system_prompt == "You are a custom Corvus."

    from corvustui.tools.base import FunctionTool

    agent.register_tool(FunctionTool(
        "custom_tool",
        "custom",
        lambda: "ok",
        {"type": "object", "properties": {}},
    ))
    assert agent.react_loop.system_prompt == "You are a custom Corvus."
