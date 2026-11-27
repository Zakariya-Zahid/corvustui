"""MCP transports: newline-delimited stdio and Streamable HTTP."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any, Callable

import httpx

from corvustui.mcp.protocol import (
    MCP_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTransportError,
    make_error_response,
    make_result_response,
    parse_message,
    validate_response,
)

MAX_STDERR_LINES = 80

# Environment variables that are safe/useful for a child process.  When
# ``inherit_env`` is disabled, only these keys are copied from the parent;
# provider API keys and other unrelated secrets stay out of the MCP server.
_SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TZ",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
)


class BaseTransport(ABC):
    """A request/response JSON-RPC transport."""

    transport_name = "unknown"

    @abstractmethod
    async def start(self) -> None:
        """Start the transport."""

    @abstractmethod
    async def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Send a request message and return the matching response."""

    @abstractmethod
    async def notify(self, message: dict[str, Any]) -> None:
        """Send a notification (no response expected)."""

    @abstractmethod
    async def close(self) -> None:
        """Stop the transport and release resources."""

    @property
    def stderr_tail(self) -> str:
        return ""


class StdioTransport(BaseTransport):
    """Launch a subprocess and speak newline-delimited JSON-RPC over stdio."""

    transport_name = "stdio"

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        inherit_env: bool = True,
        on_notification: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.command = list(command)
        self.cwd = Path(cwd) if cwd is not None else None
        self.env = dict(env or {})
        self.inherit_env = inherit_env
        self.on_notification = on_notification

        self._process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[Any, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._stderr_lines: deque[str] = deque(maxlen=MAX_STDERR_LINES)
        self._protocol_errors: deque[str] = deque(maxlen=10)

    async def start(self) -> None:
        if self._process is not None:
            return
        if not self.command:
            raise MCPTransportError("stdio transport requires a command")

        if self.inherit_env:
            environment = dict(os.environ)
        else:
            environment = {
                key: os.environ[key]
                for key in _SAFE_ENV_KEYS
                if key in os.environ
            }
        environment.update(self.env)
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd) if self.cwd else None,
                env=environment,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise MCPTransportError(
                f"MCP command not found: {self.command[0]}. "
                "Install it or fix the command in the MCP config."
            ) from exc
        except OSError as exc:
            raise MCPTransportError(
                f"Could not start MCP command {self.command[0]!r}: {exc}"
            ) from exc

        self._read_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        await self.start()
        request_id = message.get("id")
        if request_id is None:
            raise MCPTransportError("JSON-RPC request is missing an id")
        if request_id in self._pending:
            raise MCPTransportError(f"Duplicate JSON-RPC request id: {request_id}")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send(message)
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            detail = self.stderr_tail
            suffix = f" Server stderr: {detail}" if detail else ""
            raise MCPTimeoutError(
                f"MCP server did not answer {message.get('method')!r} "
                f"within {timeout:g}s.{suffix}"
            ) from exc
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, message: dict[str, Any]) -> None:
        await self.start()
        await self._send(message)

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                    await process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            if process.returncode is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except TimeoutError:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        process.kill()
                    await process.wait()

        for task in (self._read_task, self._stderr_task):
            if task is not None:
                task.cancel()
        for task in (self._read_task, self._stderr_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._read_task = None
        self._stderr_task = None
        self._fail_pending(MCPTransportError("MCP transport closed"))

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_lines).strip()

    @property
    def protocol_errors(self) -> list[str]:
        return list(self._protocol_errors)

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPTransportError("MCP stdio process is not running")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            try:
                process.stdin.write(payload.encode("utf-8"))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise MCPTransportError(
                    f"MCP server closed its input. {self.stderr_tail}"
                ) from exc

    async def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        while True:
            try:
                line = await process.stdout.readline()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._fail_pending(MCPTransportError(f"MCP stdout read failed: {exc}"))
                return

            if not line:
                detail = self.stderr_tail
                suffix = f" Server stderr: {detail}" if detail else ""
                self._fail_pending(
                    MCPTransportError(f"MCP server closed stdout.{suffix}")
                )
                return

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = parse_message(text)
            except MCPProtocolError as exc:
                self._protocol_errors.append(str(exc))
                continue

            if "method" in message:
                if "id" in message:
                    await self._answer_server_request(message)
                else:
                    if self.on_notification is not None:
                        self.on_notification(message)
                continue

            request_id = message.get("id")
            future = self._pending.pop(request_id, None)
            if future is not None and not future.done():
                future.set_result(message)

    async def _answer_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "ping":
            response = make_result_response(message.get("id"), {})
        else:
            response = make_error_response(
                message.get("id"),
                METHOD_NOT_FOUND,
                f"CorvusTUI does not implement server request {method!r}",
            )
        try:
            await self._send(response)
        except MCPTransportError:
            pass

    async def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            try:
                line = await process.stderr.readline()
            except asyncio.CancelledError:
                raise
            except Exception:
                return
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_lines.append(text)

    def _fail_pending(self, error: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


class HTTPTransport(BaseTransport):
    """MCP Streamable HTTP transport (JSON or SSE responses)."""

    transport_name = "http"

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._session_id: str | None = None
        self._protocol_version = MCP_PROTOCOL_VERSION
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        self._started = True

    async def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        await self.start()
        assert self._client is not None
        try:
            response = await self._client.post(
                self.url,
                json=message,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise MCPTimeoutError(
                f"MCP HTTP request {message.get('method')!r} timed out after {timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise MCPTransportError(f"MCP HTTP request failed: {exc}") from exc

        self._remember_session(response)
        if response.status_code >= 400:
            raise MCPTransportError(
                f"MCP HTTP {response.status_code} for {message.get('method')!r}: "
                f"{response.text[:500]}"
            )

        request_id = message.get("id")
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            payload = self._parse_sse(response.text, request_id)
        else:
            if not response.content:
                raise MCPTransportError(
                    f"MCP HTTP server returned an empty response for {message.get('method')!r}."
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise MCPProtocolError(
                    f"MCP HTTP response was not JSON: {response.text[:300]}"
                ) from exc

        if isinstance(payload, list):
            matches = [
                item for item in payload
                if isinstance(item, dict) and item.get("id") == request_id
            ]
            if not matches:
                raise MCPProtocolError(
                    f"MCP HTTP response did not include request id {request_id!r}"
                )
            payload = matches[0]
        if not isinstance(payload, dict):
            raise MCPProtocolError("MCP HTTP response must be a JSON object")
        validate_response(payload, request_id)
        return payload

    async def notify(self, message: dict[str, Any]) -> None:
        await self.start()
        assert self._client is not None
        try:
            response = await self._client.post(
                self.url,
                json=message,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise MCPTransportError(f"MCP HTTP notification failed: {exc}") from exc
        self._remember_session(response)
        if response.status_code >= 400:
            raise MCPTransportError(
                f"MCP HTTP {response.status_code} for notification: {response.text[:500]}"
            )

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None
        self._started = False

    def set_protocol_version(self, version: str) -> None:
        self._protocol_version = version

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self._protocol_version,
        }
        headers.update(self.headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _remember_session(self, response: httpx.Response) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

    @staticmethod
    def _parse_sse(text: str, request_id: Any) -> dict[str, Any]:
        data_lines: list[str] = []
        for line in text.splitlines():
            if not line.strip():
                if data_lines:
                    payload = "\n".join(data_lines)
                    data_lines.clear()
                    try:
                        message = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(message, dict) and message.get("id") == request_id:
                        return message
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if data_lines:
            try:
                message = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                message = None
            if isinstance(message, dict) and message.get("id") == request_id:
                return message

        raise MCPProtocolError(
            f"MCP SSE stream did not include a response for request id {request_id!r}"
        )
