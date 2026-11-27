"""Expose MCP tools through the native CorvusTUI Tool interface."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from corvustui.mcp.client import MCPClient, MCPToolInfo
from corvustui.mcp.protocol import MCPError
from corvustui.tools.base import Tool, ToolResult

_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


class MCPTool(Tool):
    """A tool discovered from an MCP server."""

    requires_approval = True

    def __init__(
        self,
        client: MCPClient,
        info: MCPToolInfo,
        *,
        name: str | None = None,
    ):
        self.client = client
        self.server_name = client.server_name
        self.remote_name = info.name
        self.name = name or build_tool_name(client.server_name, info.name)
        self.description = (
            f"[MCP:{client.server_name}] {info.description or info.name}"
        )
        self.parameters = _normalise_schema(info.input_schema)

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = await self.client.call_tool(self.remote_name, kwargs)
        except MCPError as exc:
            return ToolResult.err(
                str(exc),
                server=self.server_name,
                remote_tool=self.remote_name,
            )
        except Exception as exc:
            return ToolResult.err(
                f"MCP tool '{self.remote_name}' failed: {exc}",
                server=self.server_name,
                remote_tool=self.remote_name,
            )

        metadata = {
            "server": self.server_name,
            "remote_tool": self.remote_name,
            "content_types": [
                block.get("type", "unknown")
                for block in result.content
                if isinstance(block, dict)
            ],
        }
        if result.is_error:
            return ToolResult.err(result.text, **metadata)
        return ToolResult.ok(result.text, **metadata)


def build_tool_name(
    server_name: str,
    tool_name: str,
    *,
    existing: set[str] | None = None,
) -> str:
    """Create an OpenAI-compatible, collision-free tool name."""

    existing = existing or set()
    raw = f"mcp_{server_name}_{tool_name}"
    base = _NAME_PATTERN.sub("_", raw).strip("_")
    base = re.sub(r"_+", "_", base) or "mcp_tool"
    if len(base) > 64:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        base = f"{base[:55]}_{digest}"

    candidate = base
    suffix_index = 2
    while candidate in existing:
        suffix = f"_{suffix_index}"
        candidate = f"{base[: 64 - len(suffix)]}{suffix}"
        suffix_index += 1
    return candidate


def _normalise_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = dict(schema or {})
    if result.get("type") != "object":
        result["type"] = "object"
    if not isinstance(result.get("properties"), dict):
        result["properties"] = {}
    if not isinstance(result.get("required"), list):
        result["required"] = []
    return result
