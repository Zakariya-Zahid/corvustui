"""Custom Textual events decoupling agent turn execution from UI widgets."""

from __future__ import annotations

from typing import Any

from textual.message import Message

from corvustui.react.loop import ReActResult


class ThinkingStarted(Message):
    """Emitted when the model begins a reasoning/thinking phase."""

    def __init__(self, turn_id: str, iteration: int) -> None:
        super().__init__()
        self.turn_id = turn_id
        self.iteration = iteration


class ThinkingChunk(Message):
    """Emitted for each streamed chunk of thinking text."""

    def __init__(self, turn_id: str, iteration: int, text: str) -> None:
        super().__init__()
        self.turn_id = turn_id
        self.iteration = iteration
        self.text = text


class ThinkingCompleted(Message):
    """Emitted when a thinking phase completes."""

    def __init__(self, turn_id: str, iteration: int, full_text: str) -> None:
        super().__init__()
        self.turn_id = turn_id
        self.iteration = iteration
        self.full_text = full_text


class ToolStarted(Message):
    """Emitted when a tool call begins executing."""

    def __init__(
        self,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        super().__init__()
        self.turn_id = turn_id
        self.call_id = call_id
        self.tool_name = tool_name
        self.arguments = arguments


class ToolCompleted(Message):
    """Emitted when a tool call finishes executing."""

    def __init__(
        self,
        turn_id: str,
        call_id: str,
        tool_name: str,
        output: str,
        success: bool,
        duration_ms: float,
    ) -> None:
        super().__init__()
        self.turn_id = turn_id
        self.call_id = call_id
        self.tool_name = tool_name
        self.output = output
        self.success = success
        self.duration_ms = duration_ms


class ResponseChunk(Message):
    """Emitted for each streamed fragment of assistant response content."""

    def __init__(self, turn_id: str, text: str) -> None:
        super().__init__()
        self.turn_id = turn_id
        self.text = text


class TurnCompleted(Message):
    """Emitted when a turn finishes successfully or reaches max iterations."""

    def __init__(self, turn_id: str, result: ReActResult, duration_s: float) -> None:
        super().__init__()
        self.turn_id = turn_id
        self.result = result
        self.duration_s = duration_s


class TurnFailed(Message):
    """Emitted when a turn fails with an unrecoverable error."""

    def __init__(self, turn_id: str, error: str, duration_s: float) -> None:
        super().__init__()
        self.turn_id = turn_id
        self.error = error
        self.duration_s = duration_s


class QueueUpdated(Message):
    """Emitted whenever queued, delayed, or parallel prompts change."""

    def __init__(self, pending: list[dict[str, Any]], paused: bool) -> None:
        super().__init__()
        self.pending = pending
        self.paused = paused


class StatusChanged(Message):
    """Emitted when the global status indicator changes."""

    def __init__(self, state: str, detail: str = "") -> None:
        super().__init__()
        self.state = state
        self.detail = detail
