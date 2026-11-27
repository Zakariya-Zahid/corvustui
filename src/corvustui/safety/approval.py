"""Approval interfaces for tools with side effects."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty

from corvustui.safety.redact import redact_secrets


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """A human-readable request to run a tool."""

    tool: str
    arguments: dict[str, Any]
    reason: str = ""


class Approver(Protocol):
    """Asynchronous approval callback."""

    async def approve(self, request: ApprovalRequest) -> bool: ...


class AutoApprover:
    """Approve every request (used by ``--yes`` and controlled tests)."""

    async def approve(self, request: ApprovalRequest) -> bool:
        return True


class DenyApprover:
    """Deny every request; the safe default for non-interactive runs."""

    async def approve(self, request: ApprovalRequest) -> bool:
        return False


class CallbackApprover:
    """Adapt a synchronous or asynchronous callback to the Approver protocol."""

    def __init__(
        self,
        callback: Callable[[ApprovalRequest], bool | Awaitable[bool]],
    ):
        self.callback = callback

    async def approve(self, request: ApprovalRequest) -> bool:
        result = self.callback(request)
        if hasattr(result, "__await__"):
            return bool(await result)  # type: ignore[misc]
        return bool(result)


class CLIApprover:
    """Prompt on the terminal; deny when stdin is not interactive."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    async def approve(self, request: ApprovalRequest) -> bool:
        if not sys.stdin.isatty():
            return False

        safe_arguments = {
            key: redact_secrets(str(value)) for key, value in request.arguments.items()
        }
        self.console.print(Panel(
            Pretty(safe_arguments),
            title=f"Approve tool: {request.tool}",
            subtitle=request.reason or "This tool can modify files or run commands.",
            border_style="yellow",
        ))

        try:
            from rich.prompt import Prompt

            answer = Prompt.ask(
                "Run this tool?",
                choices=["y", "n"],
                default="n",
                console=self.console,
            )
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() == "y"
