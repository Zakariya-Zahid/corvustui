"""Minimal JSON-RPC 2.0 types and validation for MCP.

CorvusTUI implements the MCP wire protocol directly instead of depending on
the optional official SDK.  This keeps the base install dependency-free and
makes the transport behaviour testable with a small local server.
"""

from __future__ import annotations

import json
from typing import Any

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 2.0 standard error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class MCPError(Exception):
    """Base error for MCP configuration, transport, and protocol failures."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        data: Any = None,
        server: str | None = None,
    ):
        self.message = message
        self.code = code
        self.data = data
        self.server = server
        prefix = f"[{server}] " if server else ""
        super().__init__(f"{prefix}{message}")


class MCPTimeoutError(MCPError):
    """The server did not answer before the configured timeout."""


class MCPProtocolError(MCPError):
    """A peer sent a malformed or unexpected JSON-RPC message."""


class MCPTransportError(MCPError):
    """The transport could not start, send, or receive."""


class MCPConfigError(MCPError):
    """The MCP server configuration is missing or invalid."""


def make_request(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def make_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def make_error_response(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": error,
    }


def make_result_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "result": result,
    }


def parse_message(raw: str | bytes | dict[str, Any]) -> dict[str, Any]:
    """Parse and minimally validate one JSON-RPC message."""

    if isinstance(raw, dict):
        message = raw
    else:
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            message = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MCPProtocolError(f"Invalid JSON-RPC payload: {exc}") from exc

    if not isinstance(message, dict):
        raise MCPProtocolError("JSON-RPC message must be an object")
    if message.get("jsonrpc") != JSONRPC_VERSION:
        raise MCPProtocolError("JSON-RPC message is missing jsonrpc='2.0'")

    has_method = isinstance(message.get("method"), str)
    has_result = "result" in message
    has_error = "error" in message
    has_id = "id" in message

    if has_method:
        if has_result or has_error:
            raise MCPProtocolError("A JSON-RPC request cannot contain result or error")
        return message

    if not has_id:
        raise MCPProtocolError("A JSON-RPC response must contain an id")
    if has_result == has_error:
        raise MCPProtocolError("A JSON-RPC response must contain exactly one of result or error")
    if has_error:
        error = message.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), int):
            raise MCPProtocolError("JSON-RPC error must contain an integer code")
        if not isinstance(error.get("message"), str):
            raise MCPProtocolError("JSON-RPC error must contain a message")
    return message


def validate_response(message: dict[str, Any], request_id: int) -> Any:
    """Validate a response for ``request_id`` and return its result."""

    parsed = parse_message(message)
    if "method" in parsed:
        raise MCPProtocolError("Expected a response, received a request")
    if parsed.get("id") != request_id:
        raise MCPProtocolError(
            f"Response id {parsed.get('id')!r} does not match request id {request_id!r}"
        )
    if "error" in parsed:
        error = parsed["error"]
        raise MCPError(
            f"JSON-RPC error {error['code']}: {error['message']}",
            code=error["code"],
            data=error.get("data"),
        )
    return parsed.get("result", {})
