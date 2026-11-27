"""QueueBar widget showing queued, delayed (wait/sleep), and parallel prompts."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Static


class QueueActionRequested(Message):
    """Emitted when user clicks Pause, Resume, Run Now, or Clear on the queue bar."""

    def __init__(self, action: str) -> None:
        super().__init__()
        self.action = action


class QueueBar(Widget):
    """Displays pending queued messages and wait/sleep countdowns above the prompt."""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("", id="queue-summary", markup=False)
            yield Button("Run Now", id="queue-run-now", variant="primary")
            yield Button("Pause", id="queue-pause", variant="default")
            yield Button("Clear", id="queue-clear", variant="error")

    def on_mount(self) -> None:
        self.add_class("hidden")

    def update_queue(self, pending: list[dict[str, Any]], paused: bool) -> None:
        if not pending:
            self.add_class("hidden")
            return

        self.remove_class("hidden")
        try:
            summary = self.query_one("#queue-summary", Static)
            pause_btn = self.query_one("#queue-pause", Button)
            run_btn = self.query_one("#queue-run-now", Button)
        except Exception:
            return

        pause_btn.label = "Resume" if paused else "Pause"

        first = pending[0]
        preview = first["text"].replace("\n", " ")
        if len(preview) > 42:
            preview = preview[:39] + "..."

        has_wait = any(item["remaining_wait"] > 0 for item in pending)
        run_btn.display = has_wait or paused

        line = Text()
        if paused:
            line.append("⏸ Queue Paused ", style="bold #e0af68")
        else:
            line.append("📋 Queue ", style="bold #7dcfff")
        line.append(f"[{len(pending)} pending] ", style="dim")

        if first["remaining_wait"] > 0:
            line.append(f"⏳ Wait {first['remaining_wait']:.1f}s → ", style="bold #f1fa8c")
        elif first["parallel"]:
            line.append("⚡ Parallel → ", style="bold #bb9af7")
        else:
            line.append("Next → ", style="dim")

        line.append(f'"{preview}"', style="#e2e4ed")
        if len(pending) > 1:
            line.append(f" (+{len(pending) - 1} more)", style="dim")

        summary.update(line)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "queue-pause":
            self.post_message(QueueActionRequested("toggle_pause"))
        elif event.button.id == "queue-run-now":
            self.post_message(QueueActionRequested("run_now"))
        elif event.button.id == "queue-clear":
            self.post_message(QueueActionRequested("clear"))
