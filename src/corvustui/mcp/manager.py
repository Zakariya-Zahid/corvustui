"""Manage configured MCP servers and register their tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from corvustui.mcp.client import MCPClient
from corvustui.mcp.config import MCPServerConfig
from corvustui.mcp.tools import MCPTool, build_tool_name
from corvustui.mcp.transport import HTTPTransport, StdioTransport
from corvustui.safety.redact import redact_secrets
from corvustui.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class MCPServerStatus:
    """Startup result for one configured server."""

    name: str
    transport: str
    connected: bool
    tool_count: int = 0
    tools: tuple[str, ...] = ()
    error: str | None = None
    server_info: dict[str, Any] = field(default_factory=dict)


class MCPManager:
    """Connect to MCP servers, discover tools, and close them cleanly."""

    def __init__(
        self,
        configs: Iterable[MCPServerConfig],
        registry: ToolRegistry | None = None,
        *,
        network_enabled: bool = True,
    ):
        self.configs = list(configs)
        self.registry = registry if registry is not None else ToolRegistry()
        self.network_enabled = network_enabled
        self._clients: dict[str, MCPClient] = {}
        self._statuses: list[MCPServerStatus] = []
        self._started = False

    @property
    def statuses(self) -> list[MCPServerStatus]:
        return list(self._statuses)

    @property
    def connected_servers(self) -> int:
        return sum(1 for status in self._statuses if status.connected)

    @property
    def errors(self) -> list[str]:
        return [status.error for status in self._statuses if status.error]

    async def start(self) -> list[MCPServerStatus]:
        if self._started:
            return self.statuses
        self._started = True
        self._statuses = []

        for config in self.configs:
            if not config.enabled:
                self._statuses.append(
                    MCPServerStatus(
                        name=config.name,
                        transport=config.transport,
                        connected=False,
                        error="disabled in MCP config",
                    )
                )
                continue

            if config.transport == "http" and not self.network_enabled:
                self._statuses.append(
                    MCPServerStatus(
                        name=config.name,
                        transport=config.transport,
                        connected=False,
                        error="HTTP MCP server blocked because network access is disabled",
                    )
                )
                continue

            client = self._make_client(config)
            try:
                await client.connect()
                tools = await client.list_tools()
            except Exception as exc:
                try:
                    await client.close()
                except Exception:
                    pass
                self._statuses.append(
                    MCPServerStatus(
                        name=config.name,
                        transport=config.transport,
                        connected=False,
                        error=redact_secrets(str(exc)),
                    )
                )
                continue

            registered: list[str] = []
            for info in tools:
                existing = {tool.name for tool in self.registry.get_all()}
                name = build_tool_name(
                    config.name,
                    info.name,
                    existing=existing | set(registered),
                )
                self.registry.register(MCPTool(client, info, name=name))
                registered.append(name)

            self._clients[config.name] = client
            self._statuses.append(
                MCPServerStatus(
                    name=config.name,
                    transport=config.transport,
                    connected=True,
                    tool_count=len(registered),
                    tools=tuple(registered),
                    server_info=dict(client.server_info),
                )
            )

        return self.statuses

    async def stop(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass
        for status in self._statuses:
            for name in status.tools:
                self.registry.unregister(name)
        self._started = False

    async def __aenter__(self) -> MCPManager:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    def _make_client(self, config: MCPServerConfig) -> MCPClient:
        if config.transport == "http":
            transport = HTTPTransport(
                config.url or "",
                headers=config.headers,
                timeout=config.timeout,
            )
        else:
            transport = StdioTransport(
                list(config.command or ()),
                cwd=config.cwd,
                env=config.env,
                inherit_env=config.inherit_env,
            )
        return MCPClient(config.name, transport, timeout=config.timeout)
