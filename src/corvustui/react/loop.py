"""ReAct (Reason + Act) execution loop with error recovery."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from rich.console import Console

from corvustui.config.settings import get_settings
from corvustui.formatting import clean_final_answer
from corvustui.providers import (
    BaseProvider,
    CompletionResponse,
    FinishReason,
    Message,
    MessageRole,
    ProviderAuthError,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    ToolCall,
    Usage,
)
from corvustui.safety.guard import SafetyDecision, SafetyGuard
from corvustui.tools.registry import ToolRegistry

# Custom tools may return arbitrarily large strings; keep one observation
# bounded so a single tool call cannot exhaust the context window.
MAX_OBSERVATION_CHARS = 12_000


def _bound_observation(text: str) -> str:
    if len(text) <= MAX_OBSERVATION_CHARS:
        return text
    omitted = len(text) - MAX_OBSERVATION_CHARS
    return text[:MAX_OBSERVATION_CHARS] + (
        f"\n\n... [observation truncated, {omitted} more characters]"
    )


class IterationStatus(str, Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    ERROR = "error"
    MAX_ITERATIONS = "max_iterations"


@dataclass(slots=True)
class ReActEvent:
    """Granular lifecycle event emitted during ReAct execution."""

    kind: str  # thinking_start, thinking_chunk, thinking_end, tool_start, tool_end, response_chunk
    iteration: int = 1
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReActStep:
    """Single ReAct iteration step."""

    iteration: int
    thought: str | None = None
    action: str | None = None
    action_input: dict[str, Any] | None = None
    observation: str | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(slots=True)
class ReActResult:
    """Final result of ReAct execution."""

    final_answer: str
    steps: list[ReActStep]
    total_iterations: int
    status: IterationStatus
    total_tokens: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ReActLoop:
    """
    ReAct (Reasoning + Acting) execution loop.

    Implements the classic ReAct pattern:
    Thought -> Action -> Observation -> (repeat)
    With error recovery and auto-repair for malformed outputs.
    """

    def __init__(
        self,
        provider: BaseProvider,
        tools: ToolRegistry,
        system_prompt: str | None = None,
        max_iterations: int | None = None,
        tool_timeout: float | None = None,
        on_step: Callable[[ReActStep], None] | None = None,
        guard: SafetyGuard | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.system_prompt = system_prompt or self._default_system_prompt()
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else get_settings().max_iterations
        )
        self.tool_timeout = tool_timeout or get_settings().tool_timeout
        self.on_step = on_step
        self.guard = guard
        self.console = Console()

        # Per-run state. These are reset at the start of every run because the
        # same loop instance is reused for every turn in interactive mode.
        self.steps: list[ReActStep] = []
        self.total_tokens = 0
        self.all_tool_calls: list[ToolCall] = []

    def _default_system_prompt(self) -> str:
        tool_lines = []
        for tool in self.tools.get_all():
            schema = json.dumps(tool.parameters, separators=(",", ":"))
            tool_lines.append(f"- {tool.name}: {tool.description}\n  parameters: {schema}")

        tool_descriptions = "\n".join(tool_lines)
        return f"""You are CorvusTUI, an AI assistant using the ReAct pattern.

Tools:
{tool_descriptions}

Format:
Thought: your reasoning
Action: tool_name
Action Input: {{"param": "value"}}
Observation: result (provided by the system)

Rules:
- For simple math/facts/general knowledge: answer directly, NO tools.
- For current/specific info you don't know: USE TOOLS.
- For tasks that need three or more steps, first call `todo_write` with the
  complete plan. Update the list as you work, keep exactly one item
  `in_progress`, and mark items `completed` immediately.
- Use only the exact tool names listed above.
- Use the exact parameter names from each tool's parameters schema.
- Use as many tool calls as the task needs; there is no fixed iteration cap.
- If a tool fails: try an alternative, then answer from your own knowledge.
- Never write "TOOL_CALL:" or "Action:" in your final answer.
- The final answer MUST be clean Markdown: short headings, bullet lists, and
  fenced code blocks. Do not include Thought/Action/Observation scaffolding."""

    async def run(
        self,
        user_input: str,
        history: list[Message] | None = None,
        extra_system: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
        on_event: Callable[[ReActEvent], None] | None = None,
    ) -> ReActResult:
        """Execute the ReAct loop for a user query.

        Args:
            user_input: The new user message.
            history: Previous conversation messages (without the new user input
                and without the ReAct system prompt).
            extra_system: Optional extra system context, e.g. RAG memories.
            on_chunk: Optional callback invoked with each streamed content
                fragment. Streaming also emits intermediate ReAct text, so
                callers that only want a final answer should use ``run``
                without this callback.
        """
        self.steps = []
        self.total_tokens = 0
        self.all_tool_calls = []

        system_content = self.system_prompt
        if extra_system:
            system_content = f"{system_content}\n\n{extra_system}"

        messages: list[Message] = [Message(role=MessageRole.SYSTEM, content=system_content)]
        if history:
            messages.extend(history)
        messages.append(Message(role=MessageRole.USER, content=user_input))

        consecutive_errors = 0

        iteration = 0
        while True:
            iteration += 1
            if self.max_iterations is not None and iteration > self.max_iterations:
                break
            step = ReActStep(iteration=iteration)

            try:
                response = await self.provider.complete(
                    messages=messages,
                    tools=self.tools.get_definitions(),
                    stream=on_chunk is not None or on_event is not None,
                    temperature=0.7,
                )
                if (on_chunk is not None or on_event is not None) and not isinstance(response, CompletionResponse):
                    response = await self._collect_stream(
                        response,
                        on_chunk,
                        on_event=on_event,
                        iteration=iteration,
                    )
                elif not isinstance(response, CompletionResponse):
                    raise TypeError(
                        "Provider returned a streaming response for a "
                        "non-streaming request."
                    )
                else:
                    if on_event is not None and getattr(response, "thinking", None):
                        on_event(ReActEvent("thinking_start", iteration=iteration))
                        on_event(ReActEvent("thinking_chunk", iteration=iteration, data={"text": response.thinking}))
                        on_event(ReActEvent("thinking_end", iteration=iteration, data={"text": response.thinking}))
                    if response.content:
                        # A provider may ignore the stream flag and return a
                        # complete response.  Surface it as one chunk so callers
                        # still receive the answer exactly once.
                        if on_chunk is not None:
                            on_chunk(response.content)
                        if on_event is not None and not response.tool_calls:
                            on_event(ReActEvent("response_chunk", iteration=iteration, data={"text": response.content}))

                self.total_tokens += response.usage.total_tokens
                if response.tool_calls:
                    self.all_tool_calls.extend(response.tool_calls)

                content = response.content or ""

                if response.tool_calls:
                    if on_event is not None:
                        on_event(ReActEvent("response_reset", iteration=iteration))
                    consecutive_errors = 0
                    names = [tc.name for tc in response.tool_calls]
                    step.thought = getattr(response, "thinking", None) or content or f"Using tools: {', '.join(names)}"
                    step.action = names[0] if len(names) == 1 else "multi_tool"
                    step.action_input = (
                        response.tool_calls[0].arguments
                        if len(names) == 1
                        else {tc.name: tc.arguments for tc in response.tool_calls}
                    )

                    observations = []
                    messages.append(Message(
                        role=MessageRole.ASSISTANT,
                        content=content or None,
                        tool_calls=response.tool_calls,
                    ))

                    for tc in response.tool_calls:
                        call_id = tc.id or f"call_{iteration}_{tc.name}"
                        if on_event is not None:
                            on_event(ReActEvent(
                                "tool_start",
                                iteration=iteration,
                                data={"call_id": call_id, "tool": tc.name, "arguments": tc.arguments},
                            ))
                        t0 = time.monotonic()
                        observation = await self._execute_tool(tc.name, tc.arguments)
                        duration_ms = (time.monotonic() - t0) * 1000
                        success = not observation.startswith(("Error:", "Tool error:", "Safety blocked:"))
                        if on_event is not None:
                            on_event(ReActEvent(
                                "tool_end",
                                iteration=iteration,
                                data={
                                    "call_id": call_id,
                                    "tool": tc.name,
                                    "output": observation,
                                    "success": success,
                                    "duration_ms": duration_ms,
                                },
                            ))
                        observations.append(f"{tc.name}: {observation}")
                        messages.append(Message(
                            role=MessageRole.TOOL,
                            content=observation,
                            name=tc.name,
                            tool_call_id=tc.id,
                        ))

                    step.observation = "\n\n".join(observations)
                else:
                    thought, action, action_input = self._parse_response(content)
                    if not thought and getattr(response, "thinking", None):
                        thought = response.thinking

                    if action is None:
                        answer = clean_final_answer(content)
                        if not answer:
                            raise ValueError("Model returned an empty response with no tool call.")

                        step.thought = thought
                        step.observation = "Task complete"
                        self.steps.append(step)
                        if self.on_step:
                            self.on_step(step)
                        return ReActResult(
                            final_answer=answer,
                            steps=self.steps,
                            total_iterations=iteration,
                            status=IterationStatus.COMPLETE,
                            total_tokens=self.total_tokens,
                            tool_calls=self.all_tool_calls,
                        )

                    if on_event is not None:
                        on_event(ReActEvent("response_reset", iteration=iteration))
                    consecutive_errors = 0
                    step.thought = thought
                    step.action = action
                    step.action_input = action_input
                    call_id = f"call_{iteration}_{action}"
                    if on_event is not None:
                        on_event(ReActEvent(
                            "tool_start",
                            iteration=iteration,
                            data={"call_id": call_id, "tool": action, "arguments": action_input or {}},
                        ))
                    t0 = time.monotonic()
                    observation = await self._execute_tool(action, action_input)
                    duration_ms = (time.monotonic() - t0) * 1000
                    success = not observation.startswith(("Error:", "Tool error:", "Safety blocked:"))
                    if on_event is not None:
                        on_event(ReActEvent(
                            "tool_end",
                            iteration=iteration,
                            data={
                                "call_id": call_id,
                                "tool": action,
                                "output": observation,
                                "success": success,
                                "duration_ms": duration_ms,
                            },
                        ))
                    step.observation = observation

                    # Text-based ReAct does not have a native tool_call_id.
                    # Sending a TOOL message without a matching assistant
                    # tool_calls entry is rejected by OpenAI-compatible APIs,
                    # so return the observation as a user message instead.
                    messages.append(Message(role=MessageRole.ASSISTANT, content=content))
                    messages.append(Message(
                        role=MessageRole.USER,
                        content=f"Observation ({action}):\n{observation}",
                    ))

            except (ProviderAuthError, ProviderModelNotFoundError, ProviderUnavailableError) as exc:
                step.error = str(exc)
                step.observation = f"Error: {exc}"
                self.steps.append(step)
                if self.on_step:
                    self.on_step(step)
                return ReActResult(
                    final_answer=f"Provider error: {exc}",
                    steps=self.steps,
                    total_iterations=iteration,
                    status=IterationStatus.ERROR,
                    total_tokens=self.total_tokens,
                    tool_calls=self.all_tool_calls,
                    errors=[str(exc)],
                )
            except Exception as exc:
                consecutive_errors += 1
                step.error = str(exc)
                step.observation = f"Error: {exc}"
                self.steps.append(step)
                if self.on_step:
                    self.on_step(step)

                if isinstance(exc, ProviderRateLimitError) and consecutive_errors >= 2:
                    return ReActResult(
                        final_answer=f"Provider rate limit: {exc}",
                        steps=self.steps,
                        total_iterations=iteration,
                        status=IterationStatus.ERROR,
                        total_tokens=self.total_tokens,
                        tool_calls=self.all_tool_calls,
                        errors=[str(exc)],
                    )

                if consecutive_errors >= max(1, get_settings().max_tool_retries):
                    return ReActResult(
                        final_answer=(
                            f"Stopped after {consecutive_errors} consecutive errors. "
                            f"Last error: {exc}"
                        ),
                        steps=self.steps,
                        total_iterations=iteration,
                        status=IterationStatus.ERROR,
                        total_tokens=self.total_tokens,
                        tool_calls=self.all_tool_calls,
                        errors=[str(exc)],
                    )

                messages.append(Message(role=MessageRole.ASSISTANT, content=f"Error: {exc}"))
                messages.append(Message(
                    role=MessageRole.USER,
                    content="That attempt failed. Try a different tool or answer from your own knowledge.",
                ))
                continue

            self.steps.append(step)
            if self.on_step:
                self.on_step(step)

        last_error = self.steps[-1].error if self.steps else None
        limit = self.max_iterations if self.max_iterations is not None else iteration
        message = f"Maximum iterations ({limit}) reached without a final answer."
        if last_error:
            message += f" Last error: {last_error}"

        return ReActResult(
            final_answer=message,
            steps=self.steps,
            total_iterations=limit,
            status=IterationStatus.MAX_ITERATIONS,
            total_tokens=self.total_tokens,
            tool_calls=self.all_tool_calls,
            errors=[last_error] if last_error else ["Max iterations exceeded"],
        )

    async def _collect_stream(
        self,
        stream,
        on_chunk: Callable[[str], None] | None,
        *,
        on_event: Callable[[ReActEvent], None] | None = None,
        iteration: int = 1,
    ) -> CompletionResponse:
        """Consume a provider stream and rebuild one completion response."""
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        finish_reason = FinishReason.STOP
        total_tokens = 0
        thinking_active = False
        in_think_tag = False
        tag_buffer = ""

        def _emit_thinking(text: str) -> None:
            nonlocal thinking_active
            if not text:
                return
            thinking_parts.append(text)
            if on_event is not None:
                if not thinking_active:
                    thinking_active = True
                    on_event(ReActEvent("thinking_start", iteration=iteration))
                on_event(ReActEvent("thinking_chunk", iteration=iteration, data={"text": text}))

        def _close_thinking() -> None:
            nonlocal thinking_active
            if thinking_active and on_event is not None:
                thinking_active = False
                on_event(ReActEvent(
                    "thinking_end",
                    iteration=iteration,
                    data={"text": "".join(thinking_parts)},
                ))

        async for chunk in stream:
            if getattr(chunk, "thinking", None):
                _emit_thinking(chunk.thinking)
            if chunk.content:
                content_parts.append(chunk.content)
                if on_chunk is not None:
                    on_chunk(chunk.content)

                if on_event is not None:
                    tag_buffer += chunk.content
                    while tag_buffer:
                        if in_think_tag:
                            end_idx = tag_buffer.find("</think>")
                            if end_idx != -1:
                                _emit_thinking(tag_buffer[:end_idx])
                                _close_thinking()
                                in_think_tag = False
                                tag_buffer = tag_buffer[end_idx + 8:]
                            elif len(tag_buffer) > 8:
                                flush_len = len(tag_buffer) - 8
                                _emit_thinking(tag_buffer[:flush_len])
                                tag_buffer = tag_buffer[flush_len:]
                                break
                            else:
                                break
                        else:
                            start_idx = tag_buffer.find("<think>")
                            if start_idx != -1:
                                if start_idx > 0:
                                    _close_thinking()
                                    on_event(ReActEvent("response_chunk", iteration=iteration, data={"text": tag_buffer[:start_idx]}))
                                in_think_tag = True
                                tag_buffer = tag_buffer[start_idx + 7:]
                            elif "<" in tag_buffer and len(tag_buffer) - tag_buffer.rfind("<") < 7:
                                safe_end = tag_buffer.rfind("<")
                                if safe_end > 0:
                                    _close_thinking()
                                    on_event(ReActEvent("response_chunk", iteration=iteration, data={"text": tag_buffer[:safe_end]}))
                                    tag_buffer = tag_buffer[safe_end:]
                                break
                            else:
                                _close_thinking()
                                on_event(ReActEvent("response_chunk", iteration=iteration, data={"text": tag_buffer}))
                                tag_buffer = ""
            if chunk.tool_calls:
                _close_thinking()
                tool_calls.extend(chunk.tool_calls)
            if chunk.usage:
                total_tokens = chunk.usage.total_tokens
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason

        if tag_buffer and on_event is not None:
            if in_think_tag:
                _emit_thinking(tag_buffer)
            else:
                _close_thinking()
                on_event(ReActEvent("response_chunk", iteration=iteration, data={"text": tag_buffer}))
        _close_thinking()

        return CompletionResponse(
            content="".join(content_parts) or None,
            thinking="".join(thinking_parts) or None,
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
            usage=Usage(total_tokens=total_tokens),
            raw={},
            model=self.provider.model,
            provider=self.provider.provider_type,
        )

    def _parse_response(self, content: str) -> tuple[str | None, str | None, dict | None]:
        """Parse a text-based ReAct response.

        Handles the documented Thought/Action/Action Input format as well as
        common malformed variants produced by smaller models:
          Action: tool_name({"arg": "value"})
          TOOL_CALL:tool_name({"arg": "value"})
        """
        thought_match = re.search(
            r"Thought:\s*(.+?)(?:\nAction:|\nTOOL_CALL:|$)",
            content,
            re.DOTALL,
        )
        thought = thought_match.group(1).strip() if thought_match else None

        # Malformed "TOOL_CALL:tool({...})" output.
        tool_call_match = re.search(r"TOOL_CALL:\s*(\w+)\s*\((.*)\)", content, re.DOTALL)
        if tool_call_match:
            action = tool_call_match.group(1).strip()
            action_input = self._parse_json(tool_call_match.group(2).strip())
            if action_input is not None:
                return thought, action, action_input

        # Documented "Action: tool_name" plus optional "Action Input: {...}".
        action_match = re.search(r"Action:\s*(\w+)(?:\s*\((.*)\))?", content, re.DOTALL)
        action = action_match.group(1).strip() if action_match else None
        inline_input = action_match.group(2).strip() if action_match and action_match.group(2) else None

        if action and action.lower() == "none":
            return thought, None, None
        if not action:
            return thought, None, None

        action_input = None
        input_match = re.search(r"Action Input:\s*(.+)$", content, re.DOTALL)
        if input_match:
            action_input = self._parse_json(input_match.group(1).strip())

        if action_input is None and inline_input:
            action_input = self._parse_json(inline_input)

        return thought, action, action_input

    def _parse_json(self, raw: str) -> dict | None:
        """Parse a JSON object from text, tolerating prose around it."""
        raw = raw.strip()
        start = raw.find("{")
        if start == -1:
            return None

        # raw_decode stops at the end of the first complete JSON object,
        # which handles nested objects and trailing prose.
        try:
            value, _ = json.JSONDecoder().raw_decode(raw[start:])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

        return self._repair_json(raw[start:])

    def _repair_json(self, json_str: str) -> dict | None:
        """Attempt to repair malformed JSON."""
        json_str = json_str.strip()
        json_str = re.sub(r"'([^']*)':", r'"\1":', json_str)
        json_str = re.sub(r":\s*'([^']*)'", r': "\1"', json_str)
        json_str = re.sub(r",\s*}", "}", json_str)
        json_str = re.sub(r",\s*]", "]", json_str)

        try:
            value = json.loads(json_str)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    async def _execute_tool(self, action: str, action_input: dict | None) -> str:
        """Execute a tool and return the observation.

        Every call passes through the safety guard when one is configured.
        Denied calls are audited and never reach ``tool.execute``.
        """
        tool = self.tools.get(action)
        if not tool:
            available = ", ".join(t.name for t in self.tools.get_all())
            return f"Error: Unknown tool '{action}'. Available tools: {available}"

        if action_input is None:
            action_input = {}
        if not isinstance(action_input, dict):
            return (
                f"Error: Tool '{action}' expects an object of arguments, "
                f"got {type(action_input).__name__}"
            )

        guard = self.guard
        if guard is not None:
            try:
                decision = await guard.authorize(tool, action_input)
            except Exception as exc:
                reason = guard.redact(
                    f"Safety check for tool '{action}' failed: {exc}"
                )
                failed = SafetyDecision(False, reason, "safety_error")
                await guard.record(
                    tool=action,
                    arguments=action_input,
                    decision=failed,
                    success=False,
                    error=reason,
                )
                return f"Error: {reason}"
            if not decision.allowed:
                observation = guard.redact(
                    f"Safety blocked: {decision.reason}"
                )
                await guard.record(
                    tool=action,
                    arguments=action_input,
                    decision=decision,
                    success=False,
                    error=observation,
                )
                return _bound_observation(observation)

        started = time.monotonic()
        try:
            import asyncio

            result = await asyncio.wait_for(
                tool.execute(**action_input),
                timeout=self.tool_timeout,
            )
            if result.success:
                raw_output = result.output or "Tool executed successfully (no output)"
                output = guard.redact(raw_output) if guard is not None else raw_output
                observation = _bound_observation(output)
            else:
                raw_error = result.error or "unknown error"
                error = guard.redact(raw_error) if guard is not None else raw_error
                observation = _bound_observation(f"Tool error: {error}")

            if guard is not None:
                await guard.record(
                    tool=action,
                    arguments=action_input,
                    decision=decision,
                    success=result.success,
                    output=result.output,
                    error=result.error,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            return observation
        except TimeoutError:
            observation = f"Error: Tool '{action}' timed out after {self.tool_timeout}s"
            if guard is not None:
                observation = guard.redact(observation)
                await guard.record(
                    tool=action,
                    arguments=action_input,
                    decision=decision,
                    success=False,
                    error=observation,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            return observation
        except TypeError as exc:
            observation = f"Error: Invalid arguments for tool '{action}': {exc}"
            if guard is not None:
                observation = guard.redact(observation)
                await guard.record(
                    tool=action,
                    arguments=action_input,
                    decision=decision,
                    success=False,
                    error=observation,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            return observation
        except Exception as exc:
            observation = f"Error executing tool '{action}': {exc}"
            if guard is not None:
                observation = guard.redact(observation)
                await guard.record(
                    tool=action,
                    arguments=action_input,
                    decision=decision,
                    success=False,
                    error=observation,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            return observation

    def _generate_recovery_message(self, error: str, step: ReActStep) -> str:
        """Generate a recovery message for the model."""
        return f"""Previous attempt failed:
Thought: {step.thought}
Action: {step.action}
Action Input: {step.action_input}
Error: {error}

Please analyze the error and try a different approach. You may need to:
- Use a different tool
- Fix the parameters
- Break the task into smaller steps
- Check if the tool is available"""
