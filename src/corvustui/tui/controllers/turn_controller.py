"""Turn controller bridging CorvusAgent ReAct execution with TUI events."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from corvustui.react.loop import IterationStatus, ReActEvent, ReActResult, ReActStep
from corvustui.tui.events import (
    ResponseChunk,
    ResponseReset,
    StatusChanged,
    ThinkingChunk,
    ThinkingCompleted,
    ThinkingStarted,
    ToolCompleted,
    ToolStarted,
    TurnCompleted,
    TurnFailed,
)


class TurnController:
    """Executes an agent turn and translates callbacks into Textual messages."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._active_tasks: dict[str, asyncio.Task[ReActResult]] = {}

    @property
    def is_running(self) -> bool:
        return any(not task.done() for task in self._active_tasks.values())

    def cancel_all(self) -> int:
        cancelled = 0
        for task in list(self._active_tasks.values()):
            if not task.done():
                task.cancel()
                cancelled += 1
        return cancelled

    async def execute_turn(self, agent: Any, prompt: str, turn_id: str) -> ReActResult | None:
        start_time = time.monotonic()
        self.app.post_message(StatusChanged("thinking", "Thinking..."))

        streamed_events_seen = False
        saw_thinking_event = False
        saw_response_event = False

        def on_event(event: ReActEvent) -> None:
            nonlocal streamed_events_seen, saw_thinking_event, saw_response_event
            streamed_events_seen = True
            if event.kind == "thinking_start":
                saw_thinking_event = True
                self.app.post_message(ThinkingStarted(turn_id, event.iteration))
                self.app.post_message(StatusChanged("thinking", "Reasoning..."))
            elif event.kind == "thinking_chunk":
                saw_thinking_event = True
                self.app.post_message(ThinkingChunk(turn_id, event.iteration, event.data.get("text", "")))
            elif event.kind == "thinking_end":
                self.app.post_message(ThinkingCompleted(turn_id, event.iteration, event.data.get("text", "")))
            elif event.kind == "tool_start":
                tool_name = event.data.get("tool", "tool")
                call_id = event.data.get("call_id", "")
                args = event.data.get("arguments", {})
                self.app.post_message(ToolStarted(turn_id, call_id, tool_name, args))
                self.app.post_message(StatusChanged("tool", f"Running {tool_name}..."))
            elif event.kind == "tool_end":
                tool_name = event.data.get("tool", "tool")
                call_id = event.data.get("call_id", "")
                output = event.data.get("output", "")
                success = bool(event.data.get("success", True))
                duration_ms = float(event.data.get("duration_ms", 0.0))
                self.app.post_message(
                    ToolCompleted(turn_id, call_id, tool_name, output, success, duration_ms)
                )
                self.app.post_message(StatusChanged("thinking", "Processing result..."))
            elif event.kind == "response_chunk":
                saw_response_event = True
                self.app.post_message(ResponseChunk(turn_id, event.data.get("text", "")))
                self.app.post_message(StatusChanged("streaming", "Streaming..."))
            elif event.kind == "response_reset":
                self.app.post_message(ResponseReset(turn_id))
                self.app.post_message(StatusChanged("thinking", "Thinking..."))

        def on_chunk(chunk: str) -> None:
            # Used as fallback when an injected fake agent does not emit ReActEvents
            if not streamed_events_seen and chunk:
                self.app.post_message(ResponseChunk(turn_id, chunk))
                self.app.post_message(StatusChanged("streaming", "Streaming..."))

        def on_step(step: ReActStep) -> None:
            # Fallback if an agent/fake only uses on_step and not on_event
            if streamed_events_seen:
                return
            if step.thought:
                self.app.post_message(ThinkingStarted(turn_id, step.iteration))
                self.app.post_message(ThinkingChunk(turn_id, step.iteration, step.thought))
                self.app.post_message(ThinkingCompleted(turn_id, step.iteration, step.thought))
            if step.action:
                call_id = f"step_{step.iteration}_{step.action}"
                self.app.post_message(
                    ToolStarted(turn_id, call_id, step.action, step.action_input or {})
                )
                obs = step.observation or step.error or ""
                success = step.error is None and not obs.startswith(("Error:", "Tool error:", "Safety blocked:"))
                self.app.post_message(
                    ToolCompleted(turn_id, call_id, step.action, obs, success, 0.0)
                )

        async def _run() -> ReActResult:
            run_streaming = getattr(agent, "run_streaming", None)
            if run_streaming is not None:
                try:
                    return await run_streaming(
                        prompt,
                        on_chunk,
                        on_step=on_step,
                        on_event=on_event,
                    )
                except TypeError:
                    # Compatibility with fake agents whose run_streaming lacks on_event
                    return await run_streaming(prompt, on_chunk, on_step=on_step)
            return await agent.run(prompt, on_step=on_step)

        task = asyncio.current_task()
        if task is not None:
            self._active_tasks[turn_id] = task

        try:
            result = await _run()
            elapsed = time.monotonic() - start_time
            if result.status == IterationStatus.ERROR:
                self.app.post_message(TurnFailed(turn_id, result.final_answer, elapsed))
            else:
                self.app.post_message(TurnCompleted(turn_id, result, elapsed))
            return result
        except asyncio.CancelledError:
            elapsed = time.monotonic() - start_time
            self.app.post_message(TurnFailed(turn_id, "Generation cancelled by user.", elapsed))
            return None
        except Exception as exc:
            elapsed = time.monotonic() - start_time
            self.app.post_message(TurnFailed(turn_id, str(exc), elapsed))
            return None
        finally:
            self._active_tasks.pop(turn_id, None)
            if not self.is_running:
                self.app.post_message(StatusChanged("ready", "Ready"))
