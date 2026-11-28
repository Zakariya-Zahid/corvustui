"""Compact task strip shown above the prompt while the agent works."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from corvustui.todos import TodoItem, TodoStatus


class TodoBar(Static):
    """Claude Code-style task summary with the active and upcoming items."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", markup=False, **kwargs)

    def on_mount(self) -> None:
        self.display = False

    def update_todos(self, items: list[TodoItem]) -> None:
        if not items:
            self.display = False
            return

        completed = sum(1 for item in items if item.status == TodoStatus.COMPLETED)
        total = len(items)
        self.display = True

        text = Text()
        text.append("☑ Tasks ", style="bold #fab283")
        text.append(f"{completed}/{total}", style="#7fd88f" if completed == total else "#e0af68")
        text.append("  ", style="dim")

        bar_width = min(total, 10)
        filled = round(bar_width * completed / total) if total else 0
        text.append("▰" * filled, style="#7fd88f")
        text.append("▱" * (bar_width - filled), style="#3b3f52")

        active = [item for item in items if item.status == TodoStatus.IN_PROGRESS]
        pending = [item for item in items if item.status == TodoStatus.PENDING]
        visible = active[:1] + pending[:2]

        if not visible and completed == total:
            text.append("\n✓ ", style="bold #7fd88f")
            text.append("All tasks complete", style="#7fd88f")
        else:
            for item in visible:
                text.append("\n")
                if item.status == TodoStatus.IN_PROGRESS:
                    text.append("▸ ", style="bold #e0af68")
                    text.append(item.display_text, style="bold #e4e6f0")
                else:
                    text.append("☐ ", style="#8c91a4")
                    text.append(item.content, style="#8c91a4")

        self.update(text)


__all__ = ["TodoBar"]
