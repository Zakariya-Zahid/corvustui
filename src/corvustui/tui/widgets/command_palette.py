"""OpenCode-inspired Commands Palette modal (Ctrl+P)."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option


@dataclass(frozen=True, slots=True)
class PaletteCommand:
    id: str
    group: str
    label: str
    shortcut: str = ""
    keywords: str = ""


PALETTE_COMMANDS: list[PaletteCommand] = [
    PaletteCommand("switch_model", "Suggested", "Switch model", "ctrl+o", "model provider llm"),
    PaletteCommand("connect_provider", "Suggested", "Connect a provider (API key)", "ctrl+a", "connect api key cloud openai anthropic groq gemini openrouter"),
    PaletteCommand("clear_chat", "Suggested", "Clear session & context", "ctrl+l", "reset new"),
    PaletteCommand("toggle_focus", "Workspace", "Toggle focus mode (hide thinking)", "ctrl+f", "focus compact"),
    PaletteCommand("toggle_sidebar", "Workspace", "Toggle workspace sidebar", "ctrl+b", "sidebar files"),
    PaletteCommand("show_stats", "Workspace", "Show token & memory stats", "/stats", "tokens context"),
    PaletteCommand("show_tools", "Workspace", "List available tools", "/tools", "tools"),
    PaletteCommand("show_mcp", "Workspace", "Show MCP server status", "/mcp", "mcp servers"),
    PaletteCommand("show_safety", "Workspace", "Show active safety policy", "/safety", "safety guardrails"),
    PaletteCommand("queue_status", "Queue", "Inspect queued prompts", "/queue", "queue"),
    PaletteCommand("queue_pause", "Queue", "Pause / resume message queue", "/queue pause", "pause resume"),
    PaletteCommand("queue_run", "Queue", "Skip wait timers & run queued now", "/queue run", "wait resume"),
    PaletteCommand("show_help", "System", "Help & keyboard shortcuts", "f1", "help keys"),
    PaletteCommand("quit", "System", "Exit CorvusTUI", "ctrl+c", "quit exit"),
]


class CommandPaletteScreen(ModalScreen[str | None]):
    """Floating centered Commands modal matching OpenCode's Ctrl+P palette."""

    CSS = """
    CommandPaletteScreen {
        align: center middle;
        background: rgba(10, 10, 14, 0.65);
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="palette-dialog", classes="opencode-modal"):
            with Horizontal(classes="modal-header"):
                yield Label("Commands", classes="modal-title", markup=False)
                yield Label("esc", classes="modal-esc", markup=False)
            yield Input(placeholder="Search", id="palette-search", classes="modal-search")
            yield OptionList(id="palette-list", classes="opencode-list")

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#palette-search", Input).focus()

    def _populate(self, query: str) -> None:
        ol = self.query_one("#palette-list", OptionList)
        ol.clear_options()
        q = query.strip().lower()

        groups: dict[str, list[PaletteCommand]] = {}
        for cmd in PALETTE_COMMANDS:
            haystack = f"{cmd.label} {cmd.group} {cmd.shortcut} {cmd.keywords}".lower()
            if q and q not in haystack:
                continue
            groups.setdefault(cmd.group, []).append(cmd)

        first_selectable: int | None = None
        idx = 0
        for group_name, cmds in groups.items():
            ol.add_option(Option(Text(group_name, style="bold #bb9af7"), disabled=True))
            idx += 1
            for cmd in cmds:
                row = Text()
                row.append(f"{cmd.label:<42}", style="")
                if cmd.shortcut:
                    row.append(f"{cmd.shortcut:>12}", style="dim")
                ol.add_option(Option(row, id=cmd.id))
                if first_selectable is None:
                    first_selectable = idx
                idx += 1

        if first_selectable is not None:
            ol.highlighted = first_selectable

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-search":
            self._populate(event.value)

    def on_key(self, event: events.Key) -> None:
        ol = self.query_one("#palette-list", OptionList)
        if event.key == "down":
            event.prevent_default()
            ol.action_cursor_down()
        elif event.key == "up":
            event.prevent_default()
            ol.action_cursor_up()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "palette-search":
            return
        ol = self.query_one("#palette-list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id and not opt.disabled:
                self.dismiss(opt.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
