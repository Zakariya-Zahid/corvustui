"""Animated spinner widget with Claude Code sparkle & OpenCode braille motifs."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static


class Spinner(Static):
    """Lightweight animated spinner using Textual interval timers."""

    STYLES: dict[str, list[str]] = {
        "braille": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
        "sparkle": ["✦", "✧", "✻", "✼", "✽", "✾", "✽", "✼", "✻", "✧"],
        "dots": ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
        "pulse": ["◐", "◓", "◑", "◒"],
    }

    frame_index = reactive(0)
    active = reactive(True)

    def __init__(
        self,
        label: str = "",
        *,
        style_name: str = "braille",
        interval: float = 0.08,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._style_name = style_name if style_name in self.STYLES else "braille"
        self._interval = interval
        self._timer = None

    def on_mount(self) -> None:
        if self.active:
            self._timer = self.set_interval(self._interval, self._advance)
        self._refresh_text()

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _advance(self) -> None:
        if not self.active:
            return
        frames = self.STYLES[self._style_name]
        self.frame_index = (self.frame_index + 1) % len(frames)

    def watch_frame_index(self, _value: int) -> None:
        self._refresh_text()

    def watch_active(self, is_active: bool) -> None:
        if not is_active and self._timer is not None:
            self._timer.stop()
            self._timer = None
        elif is_active and self._timer is None and self.is_mounted:
            self._timer = self.set_interval(self._interval, self._advance)
        self._refresh_text()

    def set_label(self, label: str) -> None:
        self._label = label
        self._refresh_text()

    def stop(self, final_text: str = "") -> None:
        self.active = False
        if final_text:
            self.update(final_text)

    def _refresh_text(self) -> None:
        frames = self.STYLES[self._style_name]
        char = frames[self.frame_index % len(frames)] if self.active else "✓"
        text = f"{char} {self._label}".strip() if self._label else char
        self.update(text)
