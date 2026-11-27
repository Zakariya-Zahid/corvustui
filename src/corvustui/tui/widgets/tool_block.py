"""3-state lifecycle ToolBlock widget (Running -> Success/Collapsed -> Error)."""

from __future__ import annotations

import json
import time
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from corvustui.safety import redact_secrets
from corvustui.tui.widgets.code_block import CodeBlock, detect_language
from corvustui.tui.widgets.diff_view import DiffView, compute_diff_stats

TOOL_ICONS: dict[str, str] = {
    "read_file": "📖",
    "write_file": "✏️",
    "edit_file": "📝",
    "list_files": "📂",
    "shell": "💻",
    "web_search": "🔍",
    "duckduckgo_search": "🦆",
}

TOOL_ACTIVE_VERBS: dict[str, str] = {
    "read_file": "Reading file",
    "write_file": "Writing file",
    "edit_file": "Applying diff",
    "list_files": "Listing directory",
    "shell": "Executing command",
    "web_search": "Searching web",
    "duckduckgo_search": "Searching DuckDuckGo",
}

BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _tool_icon(name: str) -> str:
    if name in TOOL_ICONS:
        return TOOL_ICONS[name]
    if name.startswith("mcp_"):
        return "🔌"
    return "🔧"


def _primary_param(name: str, args: dict[str, Any]) -> str:
    for key in ("path", "file_path", "output_file", "command", "sh", "query", "search", "directory"):
        if key in args and args[key]:
            val = redact_secrets(str(args[key]))
            if len(val) > 56:
                val = val[:53] + "..."
            return f'{key}="{val}"'
    if args:
        compact = redact_secrets(json.dumps(args, separators=(",", ":")))
        if len(compact) > 56:
            compact = compact[:53] + "..."
        return compact
    return ""


class ToolBlock(Widget, can_focus=True):
    """Compact 3-state card representing one tool invocation and its result."""

    BINDINGS = [
        Binding("enter", "toggle_expand", "Expand/Collapse output", show=False),
        Binding("space", "toggle_expand", "Expand/Collapse output", show=False),
    ]

    state = reactive("running")  # running, success, error
    expanded = reactive(False)

    DEFAULT_PREVIEW_LINES = 8

    def __init__(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.call_id = call_id
        self.tool_name = tool_name
        self.arguments = arguments or {}
        self._output: str = ""
        self._duration_ms: float = 0.0
        self._started_at: float = time.monotonic()
        self._frame: int = 0
        self._timer = None
        self._content_widget: Widget | None = None
        self._total_output_lines: int = 0
        self._completed: bool = False

    def compose(self) -> ComposeResult:
        yield Static("", classes="tool-header", id="tool-header", markup=False)
        if len(self.arguments) > 1:
            yield Static("", classes="tool-args", id="tool-args", markup=False)
        yield Vertical(classes="tool-body hidden", id="tool-body")
        yield Static("", classes="tool-expand-hint hidden", id="tool-expand-hint", markup=False)

    def on_mount(self) -> None:
        self._apply_state_classes()
        if self.state == "running":
            self._timer = self.set_interval(0.08, self._tick)
        self._render_header()
        self._render_args()
        if self._completed:
            self._render_output()
        self._update_hint()

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _apply_state_classes(self) -> None:
        self.remove_class("state-running", "state-success", "state-error")
        self.add_class(f"state-{self.state}")

    def _tick(self) -> None:
        if self.state != "running":
            return
        self._frame = (self._frame + 1) % len(BRAILLE_FRAMES)
        self._duration_ms = (time.monotonic() - self._started_at) * 1000
        self._render_header()

    def complete(self, output: str, *, success: bool, duration_ms: float = 0.0) -> None:
        self._completed = True
        self._output = redact_secrets(output or "")
        self._total_output_lines = len(self._output.splitlines()) if self._output else 0
        self._duration_ms = (
            duration_ms if duration_ms > 0 else (time.monotonic() - self._started_at) * 1000
        )
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

        if success:
            self.state = "success"
            # Like Claude Code: default collapsed for file reads/listings, expanded for diffs
            self.expanded = self.tool_name in {"edit_file"}
        else:
            self.state = "error"
            self.expanded = True

        if self.is_mounted:
            self._apply_state_classes()
            self._render_header()
            self._render_output()
            self._update_hint()

    def toggle_expand(self) -> None:
        if self.state == "running":
            return
        self.expanded = not self.expanded

    def action_toggle_expand(self) -> None:
        self.toggle_expand()

    def on_click(self, _event: events.Click) -> None:
        self.toggle_expand()

    def watch_expanded(self, is_expanded: bool) -> None:
        if not self.is_mounted:
            return
        if self._content_widget is not None and hasattr(self._content_widget, "set_expanded"):
            self._content_widget.set_expanded(is_expanded)
        self._render_header()
        self._update_body_visibility()
        self._update_hint()

    def _update_body_visibility(self) -> None:
        try:
            body = self.query_one("#tool-body", Vertical)
        except Exception:
            return
        # Show body if expanded, error, edit_file diff, or short shell output
        show_body = (
            self._completed
            and bool(self._output)
            and (
                self.expanded
                or self.state == "error"
                or self.tool_name == "edit_file"
                or (self.tool_name == "shell" and self._total_output_lines <= 5)
            )
        )
        if show_body:
            body.remove_class("hidden")
        else:
            body.add_class("hidden")
        body.display = show_body

    def _render_header(self) -> None:
        if not self.is_mounted:
            return
        try:
            header = self.query_one("#tool-header", Static)
        except Exception:
            return

        icon = _tool_icon(self.tool_name)
        primary = _primary_param(self.tool_name, self.arguments)
        elapsed_s = self._duration_ms / 1000.0

        line = Text()
        if self.state == "running":
            spin = BRAILLE_FRAMES[self._frame % len(BRAILLE_FRAMES)]
            verb = TOOL_ACTIVE_VERBS.get(self.tool_name, f"Running {self.tool_name}")
            line.append(f"{spin} ", style="bold #e0af68")
            line.append(f"{icon} {self.tool_name}", style="bold #7dcfff")
            if primary:
                line.append(f"  {primary}", style="dim")
            line.append(f"  · {verb}... {elapsed_s:.1f}s", style="italic #e0af68")
        elif self.state == "success":
            arrow = "▾" if self.expanded else "▸"
            line.append(f"{arrow} ", style="#7fd88f")
            line.append(f"{icon} {self.tool_name}", style="bold #7dcfff")
            if primary:
                line.append(f"  {primary}", style="dim")
            if self.tool_name == "edit_file" and self._output:
                added, removed = compute_diff_stats(self._output)
                if added or removed:
                    line.append(f"  +{added}", style="bold #85e89d")
                    line.append(f" -{removed}", style="bold #f97583")
            elif self._total_output_lines > 0:
                line.append(f"  ({self._total_output_lines}L)", style="#6b7085")
            line.append(f"  ✓ {elapsed_s:.2f}s", style="bold #7fd88f")
        else:
            line.append("✖ ", style="bold #f7768e")
            line.append(f"{icon} {self.tool_name}", style="bold #f7768e")
            if primary:
                line.append(f"  {primary}", style="dim")
            line.append(f"  failed ({elapsed_s:.2f}s)", style="#f7768e")

        header.update(line)

    def _render_args(self) -> None:
        if not self.is_mounted:
            return
        try:
            args_widget = self.query_one("#tool-args", Static)
        except Exception:
            return
        if len(self.arguments) <= 1:
            args_widget.display = False
            return
        args_widget.display = True
        extra = {k: redact_secrets(str(v)) for k, v in list(self.arguments.items())[1:4]}
        parts = [f"{k}={v[:40] + '...' if len(v) > 40 else v}" for k, v in extra.items()]
        args_widget.update(Text("  " + " | ".join(parts), style="dim"))

    def _render_output(self) -> None:
        if not self.is_mounted:
            return
        try:
            body = self.query_one("#tool-body", Vertical)
        except Exception:
            return

        body.remove_children()
        self._content_widget = None
        if not self._output:
            self._update_body_visibility()
            return

        max_lines = None if self.expanded else self.DEFAULT_PREVIEW_LINES

        if self.tool_name == "edit_file" or ("@@" in self._output and "```diff" in self._output):
            diff_text = self._output
            if "```diff" in diff_text:
                parts = diff_text.split("```diff", 1)
                if len(parts) > 1:
                    diff_text = parts[1].split("```", 1)[0].strip()
            viewer: Widget = DiffView(diff_text, max_lines=max_lines)
        elif self.tool_name == "read_file":
            path = self.arguments.get("path") or self.arguments.get("file_path")
            lang = detect_language(str(path) if path else None, fallback="text")
            viewer = CodeBlock(self._output, language=lang, line_numbers=True, max_lines=max_lines)
        elif self.tool_name == "shell":
            viewer = CodeBlock(self._output, language="bash", line_numbers=False, max_lines=max_lines)
        else:
            viewer = CodeBlock(self._output, language="text", line_numbers=False, max_lines=max_lines)

        self._content_widget = viewer
        body.mount(viewer)
        self._update_body_visibility()

    def _update_hint(self) -> None:
        if not self.is_mounted:
            return
        try:
            hint = self.query_one("#tool-expand-hint", Static)
        except Exception:
            return

        if not self._completed or not self._output:
            hint.add_class("hidden")
            hint.display = False
            return

        body_visible = not self.query_one("#tool-body", Vertical).has_class("hidden")
        if not body_visible:
            hint.add_class("hidden")
            hint.display = False
            return

        if self._total_output_lines <= self.DEFAULT_PREVIEW_LINES:
            hint.add_class("hidden")
            hint.display = False
            return

        hint.remove_class("hidden")
        hint.display = True
        if self.expanded:
            hint.update(Text("▴ Collapse output", style="#5c9cf5"))
        else:
            hidden = self._total_output_lines - self.DEFAULT_PREVIEW_LINES
            hint.update(Text(f"▾ +{hidden} more lines — click to expand", style="#5c9cf5"))
