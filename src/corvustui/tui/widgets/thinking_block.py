"""Interactive collapsible & streaming ThinkingBlock widget."""

from __future__ import annotations

import time

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from corvustui.safety import redact_secrets


class ThinkingBlock(Widget, can_focus=True):
    """Collapsible reasoning/thinking block that streams text live when expanded."""

    BINDINGS = [
        Binding("enter", "toggle", "Toggle thinking", show=False),
        Binding("space", "toggle", "Toggle thinking", show=False),
    ]

    SPARKLE_FRAMES = ["✦", "✧", "✻", "✼", "✽", "✾", "✽", "✼"]

    expanded = reactive(False)
    streaming = reactive(True)

    def __init__(self, iteration: int = 1, *, expanded: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.iteration = iteration
        self._buffer: str = ""
        self._started_at: float = time.monotonic()
        self._elapsed: float = 0.0
        self._frame: int = 0
        self._timer = None
        self._content_render_scheduled: bool = False
        self.expanded = expanded

    def compose(self) -> ComposeResult:
        yield Static("", classes="thinking-header", id="thinking-header", markup=False)
        yield Static("", classes="thinking-content", id="thinking-content", markup=False)

    def on_mount(self) -> None:
        if self.streaming:
            self.add_class("streaming")
            self._timer = self.set_interval(0.1, self._tick)
        else:
            self.remove_class("streaming")
        if self.expanded:
            self.add_class("expanded")
        self._refresh_header()
        self._refresh_content()

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        if not self.streaming:
            return
        self._elapsed = time.monotonic() - self._started_at
        self._frame = (self._frame + 1) % len(self.SPARKLE_FRAMES)
        self._refresh_header()

    def append_chunk(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        if not self.is_mounted:
            return
        if self.expanded and not self._content_render_scheduled:
            self._content_render_scheduled = True
            self.set_timer(0.06, self._flush_content)

    def _flush_content(self) -> None:
        self._content_render_scheduled = False
        self._refresh_header()
        if self.expanded:
            self._refresh_content()

    def complete(self, full_text: str | None = None) -> None:
        if full_text and len(full_text) >= len(self._buffer):
            self._buffer = full_text
        self._elapsed = max(self._elapsed, time.monotonic() - self._started_at)
        self.streaming = False
        self._content_render_scheduled = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if not self.is_mounted:
            return
        self.remove_class("streaming")
        self._refresh_header()
        if self.expanded:
            self._refresh_content()

    def toggle(self) -> None:
        self.expanded = not self.expanded

    def action_toggle(self) -> None:
        self.toggle()

    def on_click(self, _event: events.Click) -> None:
        self.toggle()

    def watch_expanded(self, is_expanded: bool) -> None:
        if is_expanded:
            self.add_class("expanded")
        else:
            self.remove_class("expanded")
        if self.is_mounted:
            self._refresh_header()
            self._refresh_content()

    def _refresh_header(self) -> None:
        if not self.is_mounted:
            return
        try:
            header = self.query_one("#thinking-header", Static)
        except Exception:
            return

        arrow = "▾" if self.expanded else "▸"
        hint = "collapse" if self.expanded else "expand"
        line_count = len(self._buffer.splitlines()) if self._buffer else 0

        text = Text()
        if self.streaming:
            spinner = self.SPARKLE_FRAMES[self._frame % len(self.SPARKLE_FRAMES)]
            text.append(f"{arrow} {spinner} ", style="bold #e0af68")
            text.append(f"Thinking {self._elapsed:.1f}s ", style="italic #c8ccd9")
            if self._buffer and not self.expanded:
                preview = redact_secrets(self._buffer.strip().splitlines()[-1])
                if len(preview) > 48:
                    preview = preview[:48] + "…"
                text.append(f"· {preview} ", style="dim italic")
            text.append(f"(click to {hint})", style="dim")
        else:
            text.append(f"{arrow} 💭 ", style="#7c8196")
            text.append(f"Thought {self._elapsed:.1f}s", style="italic #7c8196")
            if line_count > 0:
                text.append(f" · {line_count}L", style="#565a6e")
            text.append(f" · click to {hint}", style="#4f5368")

        header.update(text)

    def _refresh_content(self) -> None:
        if not self.expanded or not self.is_mounted:
            return
        try:
            body = self.query_one("#thinking-content", Static)
        except Exception:
            return

        safe_text = redact_secrets(self._buffer.strip()) or "..."
        content_render = Text()
        for idx, line in enumerate(safe_text.splitlines()):
            if idx > 0:
                content_render.append("\n")
            content_render.append("│ ", style="#5c6070")
            content_render.append(line, style="italic #7c8196")
        if self.streaming:
            content_render.append(" ▌", style="bold #e0af68")
        body.update(content_render)
        body.scroll_end(animate=False)
