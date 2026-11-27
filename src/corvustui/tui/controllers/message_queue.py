"""Parallel & time-delayed message queue with wait/resume semantics."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class QueuedMessage:
    """Single item in the TUI message queue."""

    id: str
    text: str
    delay_seconds: float = 0.0
    parallel: bool = False
    priority: int = 0
    created_at: float = field(default_factory=time.monotonic)
    ready_at: float = field(default_factory=time.monotonic)
    status: str = "queued"  # queued, waiting, running, paused

    def remaining_wait(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        return max(0.0, self.ready_at - current)

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "delay_seconds": self.delay_seconds,
            "remaining_wait": round(self.remaining_wait(), 1),
            "parallel": self.parallel,
            "status": self.status,
        }


class MessageQueue:
    """Manages sequential, delayed (wait/sleep), and parallel prompt execution."""

    def __init__(self, on_change: Callable[[list[dict[str, Any]], bool], None] | None = None) -> None:
        self._items: list[QueuedMessage] = []
        self._paused: bool = False
        self._on_change = on_change
        self._notify_event = asyncio.Event()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def pending_count(self) -> int:
        return len(self._items)

    def enqueue(
        self,
        text: str,
        *,
        delay_seconds: float = 0.0,
        parallel: bool = False,
        priority: int = 0,
    ) -> QueuedMessage:
        now = time.monotonic()
        delay = max(0.0, float(delay_seconds))
        item = QueuedMessage(
            id=uuid.uuid4().hex[:8],
            text=text,
            delay_seconds=delay,
            parallel=parallel,
            priority=priority,
            created_at=now,
            ready_at=now + delay,
            status="waiting" if delay > 0 else "queued",
        )
        self._items.append(item)
        # Higher priority items move earlier while preserving FIFO within same priority
        self._items.sort(key=lambda m: (-m.priority, m.created_at))
        self._emit()
        self._notify_event.set()
        return item

    def pop_ready(self) -> QueuedMessage | None:
        """Return and remove the next ready message if queue is not paused."""
        if self._paused or not self._items:
            return None
        now = time.monotonic()
        for idx, item in enumerate(self._items):
            if item.ready_at <= now:
                popped = self._items.pop(idx)
                popped.status = "running"
                self._emit()
                return popped
        return None

    def pop_parallel_ready(self) -> list[QueuedMessage]:
        """Return all ready items marked as parallel."""
        if self._paused or not self._items:
            return []
        now = time.monotonic()
        ready: list[QueuedMessage] = []
        remaining: list[QueuedMessage] = []
        for item in self._items:
            if item.parallel and item.ready_at <= now:
                item.status = "running"
                ready.append(item)
            else:
                remaining.append(item)
        if ready:
            self._items = remaining
            self._emit()
        return ready

    def next_wait_seconds(self) -> float | None:
        """Seconds until the next delayed item becomes ready, or None."""
        if self._paused or not self._items:
            return None
        now = time.monotonic()
        waits = [item.remaining_wait(now) for item in self._items]
        return min(waits) if waits else None

    def skip_waits(self) -> int:
        """Make all waiting items immediately ready (user clicked Resume Now)."""
        now = time.monotonic()
        count = 0
        for item in self._items:
            if item.ready_at > now:
                item.ready_at = now
                item.status = "queued"
                count += 1
        self._paused = False
        self._emit()
        self._notify_event.set()
        return count

    def pause(self) -> None:
        self._paused = True
        self._emit()

    def resume(self) -> None:
        self._paused = False
        self._emit()
        self._notify_event.set()

    def remove(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != item_id]
        if len(self._items) != before:
            self._emit()
            return True
        return False

    def clear(self) -> int:
        count = len(self._items)
        self._items.clear()
        self._emit()
        return count

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "id": item.id,
                "text": item.text,
                "delay_seconds": item.delay_seconds,
                "remaining_wait": round(item.remaining_wait(now), 1),
                "parallel": item.parallel,
                "status": "paused" if self._paused else ("waiting" if item.remaining_wait(now) > 0 else "queued"),
            }
            for item in self._items
        ]

    def _emit(self) -> None:
        if self._on_change is not None:
            self._on_change(self.snapshot(), self._paused)
