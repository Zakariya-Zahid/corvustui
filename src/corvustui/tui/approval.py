"""OpenCode-style confirmation modal for privileged/side-effecting actions (sudo, outside-root, shell, edits)."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from rich.pretty import Pretty
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from corvustui.safety import ApprovalRequest, redact_secrets

if TYPE_CHECKING:  # pragma: no cover - import cycle only for typing
    from corvustui.tui.app import CorvusTUIApp

_SUDO_PATTERN = re.compile(r"(?:^|[;|&(\s])(?:sudo|doas|pkexec|su)(?:\s|$)")
_DESTRUCTIVE_PATTERN = re.compile(
    r"(?:^|[;|&(\s])(?:rm|chmod|chown|systemctl|service|mkfs|dd|kill|pkill|apt|apt-get|dnf|pacman)(?:\s|$)"
)


def _classify_risk(
    tool: str,
    arguments: dict[str, str],
    reason: str = "",
) -> tuple[str, str, str, bool, bool]:
    """Return (badge_label, badge_color, explanation, is_elevated, is_outside_root)."""
    is_outside = "outside the allowed roots" in reason.lower()
    if tool == "shell":
        cmd = (arguments.get("command") or arguments.get("sh") or "").strip()
        if _SUDO_PATTERN.search(cmd):
            return (
                "ELEVATED ACCESS (SUDO)",
                "#f7768e",
                "This command requests elevated/root privileges. Session-wide auto-allow is disabled for sudo.",
                True,
                is_outside,
            )
        if is_outside:
            return (
                "OUTSIDE WORKSPACE ROOT",
                "#ff9e64",
                reason,
                False,
                True,
            )
        if _DESTRUCTIVE_PATTERN.search(cmd):
            return (
                "PRIVILEGED / SYSTEM SHELL",
                "#ff9e64",
                "This command modifies system state, permissions, packages, or deletes files.",
                False,
                False,
            )
        return (
            "SHELL EXECUTION",
            "#e0af68",
            "This shell command will execute in your local workspace environment.",
            False,
            False,
        )
    if is_outside:
        return (
            "OUTSIDE WORKSPACE ROOT",
            "#ff9e64",
            reason,
            False,
            True,
        )
    if tool in {"edit_file", "write_file"}:
        return (
            "FILE MODIFICATION",
            "#5c9cf5",
            "This operation will create or modify a file on disk.",
            False,
            False,
        )
    if tool.startswith("mcp_"):
        return (
            "EXTERNAL MCP TOOL",
            "#bb9af7",
            "This tool invokes an external MCP server.",
            False,
            False,
        )
    return (
        "ACTION CONFIRMATION",
        "#fab283",
        "This tool has side effects and requires your confirmation.",
        False,
        False,
    )


class ApprovalScreen(ModalScreen[bool]):
    """OpenCode-styled confirmation modal with risk classification & syntax preview."""

    CSS = """
    ApprovalScreen {
        align: center middle;
        background: rgba(10, 10, 14, 0.72);
    }

    #approval-dialog {
        width: 78;
        max-width: 92%;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        background: #1a1b22;
        border: solid #2c2f40;
    }

    #approval-dialog.elevated {
        border: solid #f7768e;
    }

    #approval-dialog.outside {
        border: solid #ff9e64;
    }

    #approval-header {
        height: 1;
        layout: horizontal;
        margin-bottom: 1;
    }

    #approval-title {
        width: 1fr;
        text-style: bold;
        color: #e4e6f0;
    }

    #approval-esc {
        width: auto;
        color: #565a6e;
    }

    #approval-badge {
        height: 1;
        margin-bottom: 1;
    }

    #approval-body-scroll {
        height: auto;
        max-height: 15;
        background: #121319;
        border-left: thick #fab283;
        padding: 0 1;
        margin-bottom: 1;
        scrollbar-size: 1 1;
        scrollbar-color: #4a4f69;
        scrollbar-color-hover: #fab283;
    }

    #approval-dialog.elevated #approval-body-scroll {
        border-left: thick #f7768e;
    }

    #approval-dialog.outside #approval-body-scroll {
        border-left: thick #ff9e64;
    }

    #approval-reason {
        color: #8c91a4;
        margin-bottom: 1;
    }

    #approval-buttons {
        height: auto;
        layout: horizontal;
        align-horizontal: right;
    }

    #approval-buttons Button {
        margin-left: 1;
        min-width: 14;
        height: 1;
        border: none;
        padding: 0 2;
    }

    #deny {
        background: #2b2026;
        color: #f7768e;
    }
    #deny:hover, #deny:focus {
        background: #f7768e;
        color: #141418;
        text-style: bold;
    }

    #allow-session {
        background: #252734;
        color: #c8ccd9;
    }
    #allow-session:hover, #allow-session:focus {
        background: #5c9cf5;
        color: #141418;
        text-style: bold;
    }

    #approve {
        background: #1f3327;
        color: #7fd88f;
    }
    #approve:hover, #approve:focus {
        background: #fab283;
        color: #141418;
        text-style: bold;
    }

    #approval-shortcut-hint {
        height: 1;
        color: #565a6e;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("y", "approve", "Approve"),
        Binding("s", "approve_session", "Allow for session"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Deny"),
    ]

    def __init__(
        self,
        request: ApprovalRequest,
        *,
        on_allow_session=None,
        on_allow_outside_session=None,
    ):
        super().__init__()
        self.request = request
        self._on_allow_session = on_allow_session
        self._on_allow_outside_session = on_allow_outside_session
        self._is_elevated = False
        self._is_outside = False

    def compose(self) -> ComposeResult:
        safe_arguments = {
            key: redact_secrets(str(value))
            for key, value in self.request.arguments.items()
        }
        badge_label, badge_color, default_reason, is_elevated, is_outside = _classify_risk(
            self.request.tool, safe_arguments, self.request.reason or ""
        )
        self._is_elevated = is_elevated
        self._is_outside = is_outside

        badge_text = Text()
        badge_text.append(f" ● {badge_label} ", style=f"bold #141418 on {badge_color}")
        badge_text.append(f"  tool: {self.request.tool}", style="bold #7dcfff")
        if cwd := safe_arguments.get("cwd"):
            badge_text.append(f"  cwd: {cwd}", style="dim")

        reason_text = redact_secrets(
            default_reason if (is_elevated or is_outside) else (self.request.reason or default_reason)
        )

        classes = "elevated" if is_elevated else ("outside" if is_outside else "")
        session_label = "Allow outside paths (s)" if is_outside else "Allow session (s)"

        with Container(id="approval-dialog", classes=classes):
            with Horizontal(id="approval-header"):
                yield Label("Confirm Action", id="approval-title", markup=False)
                yield Label("esc", id="approval-esc", markup=False)
            yield Static(badge_text, id="approval-badge", markup=False)
            with VerticalScroll(id="approval-body-scroll"):
                yield Static(
                    self._render_preview(safe_arguments),
                    id="approval-body",
                    markup=False,
                )
            yield Static(reason_text, id="approval-reason", markup=False)
            with Horizontal(id="approval-buttons"):
                yield Button("Deny (n)", id="deny")
                if not is_elevated:
                    yield Button(session_label, id="allow-session")
                yield Button("Approve (y)", id="approve")
            hint = (
                "y approve once   ·   n/esc deny"
                if is_elevated
                else (
                    "y allow once   ·   s allow outside paths for session   ·   n/esc deny"
                    if is_outside
                    else "y approve   ·   s allow tool for session   ·   n/esc deny"
                )
            )
            yield Static(hint, id="approval-shortcut-hint", markup=False)

    def on_mount(self) -> None:
        try:
            self.query_one("#approve", Button).focus()
        except Exception:
            pass

    def _render_preview(self, safe_arguments: dict[str, str]):
        tool = self.request.tool
        if tool == "shell":
            cmd = safe_arguments.get("command") or safe_arguments.get("sh") or ""
            if cmd:
                return Syntax(cmd, "bash", theme="monokai", word_wrap=True, background_color="#121319")
        if tool == "write_file":
            path = safe_arguments.get("path") or safe_arguments.get("file_path") or safe_arguments.get("output_file") or ""
            content = safe_arguments.get("content") or safe_arguments.get("value") or ""
            preview = f"# {path}\n{content[:1500]}"
            return Syntax(preview, "text", theme="monokai", word_wrap=True, background_color="#121319")
        if tool == "edit_file":
            old_s = safe_arguments.get("old_string", "")
            new_s = safe_arguments.get("new_string", "")
            path = safe_arguments.get("path") or safe_arguments.get("file_path") or ""
            preview = f"--- {path}\n+++ {path}\n" + "\n".join(
                [f"- {line}" for line in old_s.splitlines()]
                + [f"+ {line}" for line in new_s.splitlines()]
            )
            return Syntax(preview, "diff", theme="monokai", word_wrap=True, background_color="#121319")
        return Pretty(safe_arguments)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "allow-session":
            self.action_approve_session()
        else:
            self.dismiss(event.button.id == "approve")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_approve_session(self) -> None:
        if self._is_elevated:
            self.dismiss(True)
            return
        if self._is_outside:
            if self._on_allow_outside_session is not None:
                self._on_allow_outside_session()
            self.dismiss(True)
            return
        if self._on_allow_session is not None:
            self._on_allow_session(self.request.tool)
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


class TUIApprover:
    """Approver that displays :class:`ApprovalScreen` in the running app."""

    def __init__(self, app: "CorvusTUIApp"):
        self.app = app
        self.session_allowed_tools: set[str] = set()

    def _allow_outside_for_session(self) -> None:
        safety = getattr(getattr(self.app, "agent", None), "safety", None)
        if safety is not None and getattr(safety, "policy", None) is not None:
            safety.policy = replace(safety.policy, allow_outside_roots=True)

    async def approve(self, request: ApprovalRequest) -> bool:
        cmd = str(request.arguments.get("command") or request.arguments.get("sh") or "")
        is_elevated = request.tool == "shell" and bool(_SUDO_PATTERN.search(cmd))
        is_outside = "outside the allowed roots" in (request.reason or "").lower()
        if not is_elevated and not is_outside and request.tool in self.session_allowed_tools:
            return True
        try:
            result = await self.app.push_screen_wait(
                ApprovalScreen(
                    request,
                    on_allow_session=self.session_allowed_tools.add,
                    on_allow_outside_session=self._allow_outside_for_session,
                )
            )
        except Exception:
            return False
        return bool(result)
