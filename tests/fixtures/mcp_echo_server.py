"""A tiny MCP stdio server used by the integration tests.

It implements the subset of MCP that CorvusTUI needs: initialize, tools/list,
tools/call, and ping.  Logs go to stderr so stdout stays a clean JSON-RPC
stream.
"""

from __future__ import annotations

import json
import os
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "fail",
        "description": "Always fail",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "secret",
        "description": "Return a fake secret for redaction tests",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slow",
        "description": "Sleep before answering",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
        },
    },
    {
        "name": "crash",
        "description": "Exit the server",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "env",
        "description": "Report selected environment variables",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    },
]


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "id" not in message:
            # notifications/initialized and similar notifications.
            continue

        request_id = message["id"]
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1.0"},
                },
            })
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                text = f"echo:{arguments.get('text', '')}"
                result = {"content": [{"type": "text", "text": text}]}
            elif name == "add":
                total = float(arguments.get("a", 0)) + float(arguments.get("b", 0))
                result = {"content": [{"type": "text", "text": str(total)}]}
            elif name == "fail":
                result = {
                    "isError": True,
                    "content": [{"type": "text", "text": "intentional failure"}],
                }
            elif name == "secret":
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": "api_key=sk-test-1234567890abcdef",
                        }
                    ]
                }
            elif name == "slow":
                time.sleep(float(arguments.get("seconds", 1)))
                result = {"content": [{"type": "text", "text": "slow done"}]}
            elif name == "env":
                value = os.environ.get(str(arguments.get("name", "")))
                result = {
                    "content": [{
                        "type": "text",
                        "text": value if value is not None else "<unset>",
                    }]
                }
            elif name == "crash":
                sys.stderr.write("fixture crashing on request\n")
                sys.stderr.flush()
                sys.exit(3)
            else:
                send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"unknown tool {name}"},
                })
                continue
            send({"jsonrpc": "2.0", "id": request_id, "result": result})
        else:
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"unknown method {method}"},
            })


if __name__ == "__main__":
    main()
