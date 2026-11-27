"""MCP client: initialize, discover tools, and invoke them."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from corvustui.mcp.protocol import (
    MCP_PROTOCOL_VERSION,
    MCPError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTransportError,
    make_notification,
    make_request,
    validate_response,
)
from corvustui.mcp.transport import BaseTransport, HTTPTransport

DEFAULT_CLIENT_INFO = {"name": "corvustui", "version": "0.1.0"}


@dataclass(frozen=True, slots=True)
class MCPToolInfo:
    """A tool advertised by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MCPToolResult:
    """Normalised result of an MCP ``tools/call``."""

    text: str
    is_error: bool
    content: list[dict[str, Any]]
    structured: Any = None


class MCPClient:
    """One MCP server connection over a transport."""

    def __init__(
        self,
        server_name: str,
        transport: BaseTransport,
        *,
        protocol_version: str = MCP_PROTOCOL_VERSION,
        timeout: float = 30.0,
        client_info: dict[str, str] | None = None,
    ):
        self.server_name = server_name
        self.transport = transport
        self.protocol_version = protocol_version
        self.timeout = timeout
        self.client_info = dict(client_info or DEFAULT_CLIENT_INFO)

        self.connected = False
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self._next_id = 1

    async def connect(self) -> None:
        if self.connected:
            return

        params = {
            "protocolVersion": self.protocol_version,
            # CorvusTUI does not implement roots, sampling, or elicitation;
            # advertising them would invite requests we cannot answer.
            "capabilities": {},
            "clientInfo": self.client_info,
        }
        try:
            result = await self._request("initialize", params)
        except Exception:
            await self.transport.close()
            raise

        if not isinstance(result, dict):
            await self.transport.close()
            raise MCPProtocolError(
                "initialize result must be an object",
                server=self.server_name,
            )

        negotiated = result.get("protocolVersion")
        if not isinstance(negotiated, str) or not negotiated:
            await self.transport.close()
            raise MCPProtocolError(
                "initialize result is missing protocolVersion",
                server=self.server_name,
            )

        capabilities = result.get("capabilities", {})
        if not isinstance(capabilities, dict):
            await self.transport.close()
            raise MCPProtocolError(
                "initialize capabilities must be an object",
                server=self.server_name,
            )
        server_info = result.get("serverInfo", {})
        if not isinstance(server_info, dict):
            await self.transport.close()
            raise MCPProtocolError(
                "initialize serverInfo must be an object",
                server=self.server_name,
            )

        self.protocol_version = negotiated
        self.capabilities = capabilities
        self.server_info = server_info
        if isinstance(self.transport, HTTPTransport):
            self.transport.set_protocol_version(negotiated)

        await self.transport.notify(make_notification("notifications/initialized"))
        self.connected = True

    async def list_tools(self) -> list[MCPToolInfo]:
        await self._ensure_connected()
        result = await self._request("tools/list", {})
        if not isinstance(result, dict):
            raise MCPProtocolError(
                "tools/list result must be an object",
                server=self.server_name,
            )

        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list):
            raise MCPProtocolError(
                "tools/list result.tools must be a list",
                server=self.server_name,
            )

        tools: list[MCPToolInfo] = []
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise MCPProtocolError(
                    "Each MCP tool must be an object",
                    server=self.server_name,
                )
            name = raw.get("name")
            if not isinstance(name, str) or not name.strip():
                raise MCPProtocolError(
                    "Each MCP tool must have a non-empty string name",
                    server=self.server_name,
                )
            description = raw.get("description", "")
            if not isinstance(description, str):
                description = str(description)
            schema = raw.get("inputSchema") or raw.get("input_schema") or {
                "type": "object",
                "properties": {},
            }
            if not isinstance(schema, dict):
                raise MCPProtocolError(
                    f"MCP tool '{name}' inputSchema must be an object",
                    server=self.server_name,
                )
            tools.append(
                MCPToolInfo(
                    name=name,
                    description=description,
                    input_schema=schema,
                    raw=raw,
                )
            )
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        await self._ensure_connected()
        if not isinstance(arguments, dict):
            raise MCPError(
                "MCP tool arguments must be an object",
                server=self.server_name,
            )

        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if not isinstance(result, dict):
            raise MCPProtocolError(
                "tools/call result must be an object",
                server=self.server_name,
            )

        content = result.get("content", [])
        if not isinstance(content, list):
            raise MCPProtocolError(
                "tools/call result.content must be a list",
                server=self.server_name,
            )

        structured = result.get("structuredContent")
        text = _render_content(content)
        if not text and structured is not None:
            text = json.dumps(structured, ensure_ascii=False, indent=2)

        is_error = bool(result.get("isError", False))
        if not text:
            text = (
                "MCP tool returned an error without details."
                if is_error
                else "MCP tool returned no text content."
            )
        return MCPToolResult(
            text=text,
            is_error=is_error,
            content=[item for item in content if isinstance(item, dict)],
            structured=structured,
        )

    async def ping(self) -> None:
        await self._ensure_connected()
        await self._request("ping", {})

    async def close(self) -> None:
        self.connected = False
        await self.transport.close()

    async def _ensure_connected(self) -> None:
        if not self.connected:
            await self.connect()

    async def _request(self, method: str, params: dict[str, Any] | None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        message = make_request(request_id, method, params)
        try:
            response = await self.transport.request(message, self.timeout)
            return validate_response(response, request_id)
        except MCPTimeoutError as exc:
            raise MCPTimeoutError(str(exc), server=self.server_name) from exc
        except MCPProtocolError as exc:
            raise MCPProtocolError(str(exc), server=self.server_name) from exc
        except MCPTransportError as exc:
            raise MCPTransportError(str(exc), server=self.server_name) from exc
        except MCPError as exc:
            if exc.server:
                raise
            raise MCPError(
                exc.message,
                code=exc.code,
                data=exc.data,
                server=self.server_name,
            ) from exc


def _render_content(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue

        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type == "image":
            mime = block.get("mimeType", "unknown")
            data = block.get("data", "")
            parts.append(f"[image: {mime}, {len(str(data))} base64 chars]")
        elif block_type == "audio":
            mime = block.get("mimeType", "unknown")
            data = block.get("data", "")
            parts.append(f"[audio: {mime}, {len(str(data))} base64 chars]")
        elif block_type == "resource":
            resource = block.get("resource") or {}
            if isinstance(resource, dict) and isinstance(resource.get("text"), str):
                parts.append(resource["text"])
            elif isinstance(resource, dict):
                parts.append(
                    f"[resource: {resource.get('uri', 'unknown')} "
                    f"({resource.get('mimeType', 'unknown')})]"
                )
            else:
                parts.append(str(resource))
        elif block_type == "resource_link":
            parts.append(
                f"[resource link: {block.get('uri', 'unknown')} "
                f"({block.get('name', 'unnamed')})]"
            )
        else:
            parts.append(json.dumps(block, ensure_ascii=False))

    return "\n\n".join(part for part in parts if part)
