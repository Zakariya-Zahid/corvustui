"""OpenCode-inspired right Sidebar widget (modified files, MCP servers, shortcuts)."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from corvustui.todos import TodoItem, TodoStatus


class Sidebar(Vertical):
    """Collapsible workspace summary panel showing file changes, MCPs, and shortcuts."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._file_stats: dict[str, tuple[int, int]] = {}
        self._mcp_statuses: list[Any] = []
        self._todos: list[TodoItem] = []
        self._turn_tokens: int = 0

    def compose(self) -> ComposeResult:
        yield Static("⌬ SESSION", classes="sidebar-section-title", markup=False)
        yield Static("Connecting...", id="sb-session", classes="sidebar-item", markup=False)

        yield Static("☑ TASKS", classes="sidebar-section-title", markup=False)
        yield Static("No tasks yet", id="sb-todos", classes="sidebar-item", markup=False)

        yield Static("📂 MODIFIED", classes="sidebar-section-title", markup=False)
        yield Static("None", id="sb-files", classes="sidebar-item", markup=False)

        yield Static("🔌 MCP", classes="sidebar-section-title", markup=False)
        yield Static("None", id="sb-mcp", classes="sidebar-item", markup=False)

        yield Static("⌨ SHORTCUTS", classes="sidebar-section-title", markup=False)
        yield Static(
            "^O Models   ^F Focus\n^B Sidebar  ^L Clear\n^C Cancel   F1 Help",
            id="sb-shortcuts",
            classes="sidebar-item",
            markup=False,
        )

    def record_file_change(self, path: str, added: int = 0, removed: int = 0) -> None:
        prev_a, prev_r = self._file_stats.get(path, (0, 0))
        self._file_stats[path] = (prev_a + added, prev_r + removed)
        self._refresh_files()

    def set_mcp_statuses(self, statuses: list[Any]) -> None:
        self._mcp_statuses = list(statuses)
        self._refresh_mcp()

    def set_session_info(self, provider: str, model: str, total_tokens: int) -> None:
        self._turn_tokens = total_tokens
        try:
            self.query_one("#sb-session", Static).update(
                f"{provider}/{model}\nTokens: {total_tokens}"
            )
        except Exception:
            pass

    def set_todos(self, items: list[TodoItem]) -> None:
        self._todos = list(items)
        self._refresh_todos()

    def _refresh_todos(self) -> None:
        try:
            widget = self.query_one("#sb-todos", Static)
        except Exception:
            return
        if not self._todos:
            widget.update("No tasks yet")
            return

        completed = sum(1 for item in self._todos if item.status == TodoStatus.COMPLETED)
        total = len(self._todos)
        rendered = Text()
        rendered.append(
            f"{completed}/{total} complete",
            style="#7fd88f" if completed == total else "#e0af68",
        )
        for item in self._todos[:6]:
            rendered.append("\n")
            if item.status == TodoStatus.COMPLETED:
                rendered.append("✓ ", style="#7fd88f")
                rendered.append(item.content, style="strike #6b7085")
            elif item.status == TodoStatus.IN_PROGRESS:
                rendered.append("▸ ", style="bold #e0af68")
                rendered.append(item.display_text, style="bold #e4e6f0")
            else:
                rendered.append("○ ", style="#8c91a4")
                rendered.append(item.content, style="#8c91a4")
        if total > 6:
            rendered.append(f"\n+{total - 6} more", style="dim")
        widget.update(rendered)

    def _refresh_files(self) -> None:
        try:
            widget = self.query_one("#sb-files", Static)
        except Exception:
            return
        if not self._file_stats:
            widget.update("No edits yet")
            return
        rendered = Text()
        for idx, (path, (added, removed)) in enumerate(list(self._file_stats.items())[-10:]):
            if idx > 0:
                rendered.append("\n")
            short = path.split("/")[-1]
            rendered.append(f"• {short}", style="#e2e4ed")
            if added or removed:
                rendered.append(f" +{added}", style="#85e89d")
                rendered.append(f" -{removed}", style="#f97583")
        widget.update(rendered)

    def _refresh_mcp(self) -> None:
        try:
            widget = self.query_one("#sb-mcp", Static)
        except Exception:
            return
        if not self._mcp_statuses:
            widget.update("None configured")
            return
        rendered = Text()
        for idx, st in enumerate(self._mcp_statuses):
            if idx > 0:
                rendered.append("\n")
            if getattr(st, "connected", False):
                rendered.append("● ", style="#7fd88f")
                rendered.append(f"{st.name} ({st.tool_count})", style="#e2e4ed")
            else:
                rendered.append("○ ", style="#f7768e")
                rendered.append(f"{st.name} (err)", style="#8c91a4")
        widget.update(rendered)
