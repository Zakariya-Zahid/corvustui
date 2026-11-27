"""Built-in MCP client, transports, configuration, and tool bridge."""

from corvustui.mcp.client import MCPClient, MCPToolInfo, MCPToolResult
from corvustui.mcp.config import MCPServerConfig, load_mcp_config
from corvustui.mcp.manager import MCPManager, MCPServerStatus
from corvustui.mcp.protocol import (
    MCP_PROTOCOL_VERSION,
    MCPConfigError,
    MCPError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTransportError,
)
from corvustui.mcp.tools import MCPTool, build_tool_name
from corvustui.mcp.transport import BaseTransport, HTTPTransport, StdioTransport

__all__ = [
    "MCPClient",
    "MCPToolInfo",
    "MCPToolResult",
    "MCPServerConfig",
    "load_mcp_config",
    "MCPManager",
    "MCPServerStatus",
    "MCP_PROTOCOL_VERSION",
    "MCPConfigError",
    "MCPError",
    "MCPProtocolError",
    "MCPTimeoutError",
    "MCPTransportError",
    "MCPTool",
    "build_tool_name",
    "BaseTransport",
    "HTTPTransport",
    "StdioTransport",
]
