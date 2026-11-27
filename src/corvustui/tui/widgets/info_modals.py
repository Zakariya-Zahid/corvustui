"""OpenCode-style floating modals for /tools, /mcp, /stats, /safety, and /queue."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from corvustui.safety import redact_secrets


class InfoModalScreen(ModalScreen[None]):
    """Clean OpenCode-styled floating modal for structured info (/stats, /mcp)."""

    CSS = """
    InfoModalScreen {
        align: center middle;
        background: rgba(10, 10, 14, 0.65);
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def __init__(self, title: str, rows: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self._title = title
        self._rows = rows  # (label, value, style)

    def compose(self) -> ComposeResult:
        with Container(classes="opencode-modal"):
            with Horizontal(classes="modal-header"):
                yield Label(self._title, classes="modal-title", markup=False)
                yield Label("esc", classes="modal-esc", markup=False)
            with VerticalScroll(id="info-modal-scroll"):
                yield Static(self._build_content(), id="info-modal-body", markup=False)
            yield Static("Esc or q to close", classes="modal-footer-hint", markup=False)

    def _build_content(self) -> Text:
        t = Text()
        for idx, (label, value, style) in enumerate(self._rows):
            if idx > 0:
                t.append("\n")
            if not label and not value:
                continue
            if label:
                t.append(f"{label:<22}", style="bold #8c91a4")
            t.append(redact_secrets(value), style=style or "#e4e6f0")
        return t

    def action_close(self) -> None:
        self.dismiss(None)


class ToolsModalScreen(ModalScreen[None]):
    """Searchable OpenCode-style modal for /tools."""

    CSS = """
    ToolsModalScreen {
        align: center middle;
        background: rgba(10, 10, 14, 0.65);
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def __init__(self, tools: list[Any]) -> None:
        super().__init__()
        self._tools = list(tools)

    def compose(self) -> ComposeResult:
        with Container(classes="opencode-modal"):
            with Horizontal(classes="modal-header"):
                yield Label(f"Available Tools ({len(self._tools)})", classes="modal-title", markup=False)
                yield Label("esc", classes="modal-esc", markup=False)
            yield Input(placeholder="Filter tools...", id="tools-search", classes="modal-search")
            yield OptionList(id="tools-list", classes="opencode-list")
            yield Static("", id="tools-detail", classes="modal-footer-hint", markup=False)

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#tools-search", Input).focus()

    def _populate(self, query: str) -> None:
        ol = self.query_one("#tools-list", OptionList)
        ol.clear_options()
        q = query.strip().lower()
        shown = 0
        for tool in self._tools:
            name = getattr(tool, "name", "unknown")
            desc = redact_secrets(getattr(tool, "description", ""))
            if q and q not in name.lower() and q not in desc.lower():
                continue
            approval = "approval" if getattr(tool, "requires_approval", False) else "auto"
            row = Text()
            row.append(f"{name:<22}", style="bold")
            short_desc = desc if len(desc) <= 32 else desc[:29] + "..."
            row.append(f"{short_desc:<33}", style="dim")
            row.append(f"[{approval}]", style="#e0af68" if approval == "approval" else "#7fd88f")
            ol.add_option(Option(row, id=name))
            shown += 1
        if shown > 0:
            ol.highlighted = 0
            self._update_detail(0)
        else:
            ol.add_option(Option(Text("No matching tools", style="dim"), disabled=True))
            self.query_one("#tools-detail", Static).update("")

    def _update_detail(self, index: int) -> None:
        ol = self.query_one("#tools-list", OptionList)
        if index < 0 or index >= ol.option_count:
            return
        opt = ol.get_option_at_index(index)
        if not opt.id:
            return
        for tool in self._tools:
            if getattr(tool, "name", "") == opt.id:
                desc = redact_secrets(getattr(tool, "description", ""))
                self.query_one("#tools-detail", Static).update(desc[:90])
                return

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "tools-search":
            self._populate(event.value)

    def on_key(self, event: events.Key) -> None:
        ol = self.query_one("#tools-list", OptionList)
        if event.key == "down":
            event.prevent_default()
            ol.action_cursor_down()
            if ol.highlighted is not None:
                self._update_detail(ol.highlighted)
        elif event.key == "up":
            event.prevent_default()
            ol.action_cursor_up()
            if ol.highlighted is not None:
                self._update_detail(ol.highlighted)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._update_detail(event.option_index)

    def action_close(self) -> None:
        self.dismiss(None)


class QueueModalScreen(ModalScreen[str | None]):
    """Interactive modal for /queue showing queued/delayed/parallel items and controls."""

    CSS = """
    QueueModalScreen {
        align: center middle;
        background: rgba(10, 10, 14, 0.65);
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def __init__(self, pending: list[dict[str, Any]], paused: bool) -> None:
        super().__init__()
        self._pending = pending
        self._paused = paused

    def compose(self) -> ComposeResult:
        with Container(classes="opencode-modal"):
            with Horizontal(classes="modal-header"):
                state = "Paused" if self._paused else "Active"
                yield Label(
                    f"Message Queue ({len(self._pending)} pending · {state})",
                    classes="modal-title",
                    markup=False,
                )
                yield Label("esc", classes="modal-esc", markup=False)
            yield OptionList(id="queue-modal-list", classes="opencode-list")
            with Horizontal(id="connect-form-buttons"):
                yield Button("Run Now", id="q-run", variant="primary")
                yield Button("Resume" if self._paused else "Pause", id="q-pause", variant="default")
                yield Button("Clear All", id="q-clear", variant="error")
                yield Button("Close", id="q-close", variant="default")

    def on_mount(self) -> None:
        ol = self.query_one("#queue-modal-list", OptionList)
        if not self._pending:
            ol.add_option(Option(Text("Queue is currently empty.", style="dim"), disabled=True))
        else:
            for idx, item in enumerate(self._pending, start=1):
                row = Text()
                row.append(f"{idx}. ", style="dim")
                if item["remaining_wait"] > 0:
                    row.append(f"[wait {item['remaining_wait']}s] ", style="#f1fa8c")
                elif item["parallel"]:
                    row.append("[parallel] ", style="#bb9af7")
                else:
                    row.append(f"[{item['status']}] ", style="#7dcfff")
                txt = item["text"].replace("\n", " ")
                if len(txt) > 44:
                    txt = txt[:41] + "..."
                row.append(txt, style="#e4e6f0")
                ol.add_option(Option(row, id=item["id"]))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "q-run":
            self.dismiss("run")
        elif event.button.id == "q-pause":
            self.dismiss("resume" if self._paused else "pause")
        elif event.button.id == "q-clear":
            self.dismiss("clear")
        else:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class SafetyModalScreen(ModalScreen[str | None]):
    """Interactive /safety policy modal allowing live toggles for outside roots & shell mode."""

    CSS = """
    SafetyModalScreen {
        align: center middle;
        background: rgba(10, 10, 14, 0.65);
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def __init__(self, policy: Any) -> None:
        super().__init__()
        self._policy = policy

    def compose(self) -> ComposeResult:
        with Container(classes="opencode-modal"):
            with Horizontal(classes="modal-header"):
                yield Label("Safety Policy & Sandbox", classes="modal-title", markup=False)
                yield Label("esc", classes="modal-esc", markup=False)
            with VerticalScroll(id="info-modal-scroll"):
                yield Static(self._build_content(), id="safety-modal-body", markup=False)
            with Horizontal(id="connect-form-buttons"):
                outside_label = (
                    "Restrict to CWD"
                    if getattr(self._policy, "allow_outside_roots", False)
                    else "Allow Outside Paths"
                )
                shell_mode = getattr(self._policy, "shell_mode", "deny_dangerous")
                shell_label = (
                    "Shell: Safe Mode"
                    if shell_mode == "unrestricted"
                    else "Shell: Unrestricted"
                )
                yield Button(outside_label, id="sf-toggle-outside", variant="default")
                yield Button(shell_label, id="sf-toggle-shell", variant="default")
                yield Button("Close", id="sf-close", variant="primary")

    def _build_content(self) -> Text:
        p = self._policy
        if p is None:
            return Text("Safety guard is not available.", style="dim")
        roots = ", ".join(str(root) for root in p.allowed_roots) or "(cwd)"
        rows = [
            ("Enabled", str(p.enabled), "#7fd88f" if p.enabled else "#f7768e"),
            ("Shell Mode", str(p.shell_mode), "#e0af68" if p.shell_mode != "unrestricted" else "#f7768e"),
            ("Outside Workspace", "Allowed" if p.allow_outside_roots else "Ask via confirmation", "#7dcfff"),
            ("Approval Required", str(p.require_approval), "#7dcfff"),
            ("Network Enabled", str(p.network_enabled), "#e4e6f0"),
            ("Allowed Roots", roots, "dim"),
            ("Audit Log", str(p.audit_path), "dim"),
        ]
        t = Text()
        for idx, (label, value, style) in enumerate(rows):
            if idx > 0:
                t.append("\n")
            t.append(f"{label:<22}", style="bold #8c91a4")
            t.append(redact_secrets(value), style=style)
        return t

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sf-toggle-outside":
            self.dismiss("toggle_outside")
        elif event.button.id == "sf-toggle-shell":
            self.dismiss("toggle_shell")
        else:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
