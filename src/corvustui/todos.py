"""Shared task-list model used by the todo_write tool and the TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

MAX_TODOS = 100


class TodoStatus(str, Enum):
    """Lifecycle state of a single task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


_STATUS_ALIASES = {
    "pending": TodoStatus.PENDING,
    "todo": TodoStatus.PENDING,
    "not_started": TodoStatus.PENDING,
    "not started": TodoStatus.PENDING,
    "in_progress": TodoStatus.IN_PROGRESS,
    "in-progress": TodoStatus.IN_PROGRESS,
    "inprogress": TodoStatus.IN_PROGRESS,
    "active": TodoStatus.IN_PROGRESS,
    "doing": TodoStatus.IN_PROGRESS,
    "completed": TodoStatus.COMPLETED,
    "complete": TodoStatus.COMPLETED,
    "done": TodoStatus.COMPLETED,
    "finished": TodoStatus.COMPLETED,
}


def normalize_status(value: Any) -> TodoStatus | None:
    if isinstance(value, TodoStatus):
        return value
    if not isinstance(value, str):
        return None
    return _STATUS_ALIASES.get(value.strip().lower())


@dataclass(slots=True)
class TodoItem:
    """One task in the agent's plan."""

    content: str
    status: TodoStatus = TodoStatus.PENDING
    active_form: str | None = None

    @property
    def display_text(self) -> str:
        if self.status == TodoStatus.IN_PROGRESS and self.active_form:
            return self.active_form
        return self.content


@dataclass
class TodoStore:
    """Mutable ordered task list.  The model replaces the whole list on each update."""

    items: list[TodoItem] = field(default_factory=list)

    def replace(self, items: list[TodoItem]) -> None:
        self.items = list(items)

    def clear(self) -> None:
        self.items = []

    def counts(self) -> tuple[int, int]:
        """Return ``(completed, total)``."""
        completed = sum(1 for item in self.items if item.status == TodoStatus.COMPLETED)
        return completed, len(self.items)

    @property
    def is_complete(self) -> bool:
        return bool(self.items) and all(
            item.status == TodoStatus.COMPLETED for item in self.items
        )

    def current(self) -> TodoItem | None:
        for item in self.items:
            if item.status == TodoStatus.IN_PROGRESS:
                return item
        for item in self.items:
            if item.status == TodoStatus.PENDING:
                return item
        return None

    def replace_from_arguments(self, arguments: Any) -> list[TodoItem] | None:
        """Validate a tool-call payload and replace the list.

        Returns the new items, or ``None`` when the payload is malformed.  A
        malformed payload must not destroy the previous plan.
        """
        if not isinstance(arguments, dict):
            return None

        raw_items = arguments.get("todos")
        if raw_items is None:
            raw_items = arguments.get("items")
        if raw_items is None:
            raw_items = arguments.get("tasks")
        if not isinstance(raw_items, list):
            return None
        if len(raw_items) > MAX_TODOS:
            return None

        parsed: list[TodoItem] = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                return None
            content = str(entry.get("content", "")).strip()
            if not content:
                return None
            status = normalize_status(entry.get("status", TodoStatus.PENDING.value))
            if status is None:
                return None
            active_form = entry.get("activeForm") or entry.get("active_form")
            active_text = str(active_form).strip() if active_form else None
            parsed.append(
                TodoItem(
                    content=content,
                    status=status,
                    active_form=active_text or None,
                )
            )

        self.items = parsed
        return list(self.items)

    def render_plain(self) -> str:
        """Plain-text summary returned to the model as a tool observation."""
        if not self.items:
            return "Task list cleared."

        completed, total = self.counts()
        icons = {
            TodoStatus.PENDING: "[ ]",
            TodoStatus.IN_PROGRESS: "[>]",
            TodoStatus.COMPLETED: "[x]",
        }
        lines = [f"Tasks {completed}/{total} complete"]
        for item in self.items:
            lines.append(f"{icons[item.status]} {item.display_text}")
        return "\n".join(lines)


__all__ = [
    "MAX_TODOS",
    "TodoItem",
    "TodoStatus",
    "TodoStore",
    "normalize_status",
]
