"""Main CorvusTUI agent - ties together provider, ReAct loop, context, and tools."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from corvustui.config.settings import get_settings
from corvustui.context.manager import ContextManager
from corvustui.mcp.config import load_mcp_config
from corvustui.mcp.manager import MCPManager, MCPServerStatus
from corvustui.memory.vector import ChromaMemory, InMemoryVectorMemory, VectorMemory
from corvustui.providers import ProviderFactory, ProviderType
from corvustui.react.loop import (
    IterationStatus,
    ReActEvent,
    ReActLoop,
    ReActResult,
    ReActStep,
)
from corvustui.safety import Approver, DenyApprover, SafetyGuard, SafetyPolicy
from corvustui.todos import TodoStore
from corvustui.tools.base import Tool
from corvustui.tools.builtin import register_builtin_tools
from corvustui.tools.registry import ToolRegistry

# Keep the per-session work log small enough to re-inject every turn without
# crowding out the actual conversation.
_MAX_WORK_LOG_ENTRIES = 25
_MAX_WORK_LOG_ARG_CHARS = 180
_MAX_WORK_LOG_OBSERVATION_CHARS = 350


def _compact(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _format_work_step(step: ReActStep) -> str | None:
    """Turn one completed tool step into a compact, replay-safe log line."""
    if not step.action:
        return None
    try:
        arguments = json.dumps(step.action_input or {}, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        arguments = str(step.action_input or {})
    outcome = step.observation or step.error or ""
    failed = bool(step.error) or outcome.startswith(
        ("Error:", "Tool error:", "Safety blocked:")
    )
    status = "error" if failed else "ok"
    return (
        f"- {step.action}({_compact(arguments, _MAX_WORK_LOG_ARG_CHARS)}) "
        f"[{status}] -> {_compact(outcome, _MAX_WORK_LOG_OBSERVATION_CHARS)}"
    )


@dataclass(slots=True)
class AgentConfig:
    """Configuration for agent initialization."""

    provider_type: ProviderType = ProviderType.OLLAMA
    model: str | None = None
    system_prompt: str | None = None
    max_iterations: int | None = None
    tool_timeout: float | None = None
    max_context_tokens: int | None = None
    reserved_tokens: int | None = None
    enable_rag: bool = False
    rag_path: Path | None = None
    embedding_model: str = "all-MiniLM-L6-v2"
    rag_top_k: int | None = None
    rag_similarity_threshold: float | None = None
    enable_safety: bool = True
    safety_policy: SafetyPolicy | None = None
    approver: Approver | None = None
    enable_mcp: bool = True
    mcp_config: Path | None = None


class CorvusAgent:
    """
    Main CorvusTUI agent that orchestrates:
    - LLM provider
    - ReAct execution loop
    - Context management
    - Tool registry
    - Vector memory (RAG)
    """

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or AgentConfig()
        self.settings = get_settings()

        # Initialize provider
        self.provider = ProviderFactory.create(
            self.config.provider_type,
            model=self.config.model,
        )

        # Initialize tools
        self.tools = ToolRegistry()
        self.todos = TodoStore()
        self.work_log: list[str] = []
        register_builtin_tools(self.tools, todo_store=self.todos)

        # Initialize safety guard.  The default is deny-by-default for tools
        # with side effects when no interactive approver is supplied.
        policy = self.config.safety_policy or SafetyPolicy.from_settings(self.settings)
        if not self.config.enable_safety:
            policy = replace(policy, enabled=False)
        self.safety = SafetyGuard(
            policy,
            approver=self.config.approver or DenyApprover(),
        )

        # MCP servers are connected in __aenter__ so provider validation can
        # fail first without spawning subprocesses.
        self.mcp_manager: MCPManager | None = None
        self.mcp_statuses: list[MCPServerStatus] = []

        # Initialize context manager
        self.context = ContextManager(
            provider=self.provider,
            max_tokens=self.config.max_context_tokens,
            reserved_tokens=self.config.reserved_tokens,
        )

        # Initialize memory (RAG)
        self.memory: VectorMemory | None = None
        if self.config.enable_rag:
            if self.config.rag_path:
                self.memory = ChromaMemory(
                    persist_path=str(self.config.rag_path),
                    embedding_model=self.config.embedding_model,
                )
            else:
                self.memory = InMemoryVectorMemory()

        # Initialize ReAct loop
        self.react_loop = ReActLoop(
            provider=self.provider,
            tools=self.tools,
            system_prompt=self.config.system_prompt,
            max_iterations=self.config.max_iterations,
            tool_timeout=self.config.tool_timeout,
            guard=self.safety,
        )

        # Set up context with system prompt
        if self.config.system_prompt:
            self.context.add_system(self.config.system_prompt)

    async def __aenter__(self) -> CorvusAgent:
        await self.provider.__aenter__()
        try:
            await self.provider.validate_model()
            await self._start_mcp()
        except Exception:
            if self.mcp_manager is not None:
                await self.mcp_manager.stop()
                self.mcp_manager = None
            await self.provider.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *args) -> None:
        try:
            if self.mcp_manager is not None:
                await self.mcp_manager.stop()
                self.mcp_manager = None
        finally:
            await self.provider.__aexit__(*args)

    async def _start_mcp(self) -> None:
        if not self.config.enable_mcp or not self.settings.mcp_enabled:
            return

        config_path = self.config.mcp_config or self.settings.mcp_servers_config
        if config_path is None:
            return

        configs = load_mcp_config(
            config_path,
            default_timeout=self.settings.mcp_timeout,
        )
        self.mcp_manager = MCPManager(
            configs,
            self.tools,
            network_enabled=self.safety.policy.network_enabled,
        )
        self.mcp_statuses = await self.mcp_manager.start()
        self._refresh_system_prompt()

    def _refresh_system_prompt(self) -> None:
        """Refresh the default prompt without discarding a custom one."""

        if self.config.system_prompt:
            self.react_loop.system_prompt = self.config.system_prompt
        else:
            self.react_loop.system_prompt = self.react_loop._default_system_prompt()

    async def switch_model(self, provider_type: ProviderType, model: str | None) -> None:
        """Switch provider/model without leaking the previous HTTP client.

        The old provider is only closed after the new one has been created and
        validated, so a failed switch leaves the agent usable.
        """
        new_provider = ProviderFactory.create(provider_type, model=model)
        await new_provider.__aenter__()
        try:
            await new_provider.validate_model()
        except Exception:
            await new_provider.__aexit__(None, None, None)
            raise

        old_provider = self.provider
        self.provider = new_provider
        self.react_loop.provider = new_provider
        self.context.provider = new_provider
        self.config.provider_type = provider_type
        self.config.model = model
        await old_provider.__aexit__(None, None, None)

    def register_tool(self, tool: Tool) -> None:
        """Register a custom tool."""
        self.tools.register(tool)
        self._refresh_system_prompt()

    def register_function_tool(
        self,
        name: str,
        description: str,
        func: Callable,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Register a function as a tool."""
        self.tools.register_function(name, description, func, parameters)
        self._refresh_system_prompt()

    def reset_session(self) -> None:
        """Clear conversation, tasks, and the work log together."""
        self.context.clear()
        self.todos.clear()
        self.work_log.clear()

    def _session_context(self) -> str | None:
        """Describe the current plan and completed actions for the next turn."""
        blocks: list[str] = []
        if self.todos.items:
            blocks.append(
                "Current task list (already planned; update it in place instead "
                "of starting over):\n" + self.todos.render_plain()
            )
        if self.work_log:
            blocks.append(
                "Work already completed in this session (do not repeat it unless "
                "verification fails):\n" + "\n".join(self.work_log)
            )
        return "\n\n".join(blocks) or None

    def _record_work(self, steps: list[ReActStep]) -> None:
        for step in steps:
            entry = _format_work_step(step)
            if entry:
                self.work_log.append(entry)
        if len(self.work_log) > _MAX_WORK_LOG_ENTRIES:
            self.work_log = self.work_log[-_MAX_WORK_LOG_ENTRIES:]

    def _record_interrupted_turn(self, user_input: str, reason: str) -> None:
        """Keep a failed turn's progress so the next prompt can resume.

        Provider rate limits and cancellations raise before a ``ReActResult``
        exists, but the loop has usually completed some tool calls by then.
        Store those steps plus a short marker instead of dropping the turn.
        """
        self._record_work(self.react_loop.steps)
        self.context.add_user(user_input)
        detail = f": {reason}" if reason else ""
        self.context.add_assistant(
            f"[Turn interrupted before completion{detail}. Completed actions "
            "are recorded in the session work log; continue from that state "
            "instead of starting over.]"
        )

    async def run(
        self,
        user_input: str,
        on_step: Callable | None = None,
        *,
        on_chunk: Callable[[str], None] | None = None,
        on_event: Callable[[ReActEvent], None] | None = None,
    ) -> ReActResult:
        """Run the agent on a user query.

        When ``on_chunk`` is supplied the provider is asked to stream and each
        content fragment is passed to the callback.  The final result is still
        returned, so callers can choose to display either streamed chunks or
        the completed answer.
        """
        # Previous turns only; the ReAct loop adds the new user message itself.
        # get_history() keeps the rolling summary but omits the main system
        # prompt, which the ReAct loop already owns.
        history = self.context.get_history()

        # Re-inject the task list and completed actions every turn.  Without
        # this, a rate-limited or summarized turn leaves the model with only
        # the final answer and it starts the same work again.
        extra_blocks: list[str] = []
        session_context = self._session_context()
        if session_context:
            extra_blocks.append(session_context)

        # Relevant memories are passed as extra system context for this run
        # instead of permanently mutating the conversation system message.
        if self.memory:
            top_k = self.config.rag_top_k or self.settings.rag_top_k
            threshold = (
                self.config.rag_similarity_threshold
                if self.config.rag_similarity_threshold is not None
                else self.settings.rag_similarity_threshold
            )
            memories = await self.memory.search(
                user_input,
                top_k=top_k,
                threshold=threshold,
            )
            if memories:
                extra_blocks.append(
                    "Relevant memories:\n"
                    + "\n\n".join(
                        f"[Memory {i+1}]: {m.content}"
                        for i, m in enumerate(memories)
                    )
                )

        # Run ReAct loop with conversation history and memory context.
        self.react_loop.on_step = on_step
        try:
            result = await self.react_loop.run(
                user_input,
                history=history,
                extra_system="\n\n".join(extra_blocks) or None,
                on_chunk=on_chunk,
                on_event=on_event,
            )
        except asyncio.CancelledError:
            self._record_interrupted_turn(user_input, "cancelled")
            raise
        except Exception as exc:
            self._record_interrupted_turn(
                user_input,
                str(exc) or type(exc).__name__,
            )
            raise

        # Record tool activity as plain text.  A partial turn can leave
        # unmatched tool calls, so a compact log is safer to replay than a raw
        # tool transcript and still tells the next turn what already exists.
        self._record_work(result.steps)

        self.context.add_user(user_input)
        if result.status == IterationStatus.COMPLETE:
            self.context.add_assistant(result.final_answer)
        else:
            self.context.add_assistant(
                f"[Turn interrupted: {result.final_answer}. "
                "Completed actions are recorded in the session work log; "
                "continue from that state instead of starting over.]"
            )

        # Check if summarization needed
        await self.context.summarize_if_needed()

        # Store successful interactions in memory
        if self.memory and result.status == IterationStatus.COMPLETE:
            await self.memory.add(
                f"User: {user_input}\nAssistant: {result.final_answer}",
                metadata={"type": "interaction", "iterations": result.total_iterations},
            )

        return result

    async def run_streaming(
        self,
        user_input: str,
        on_chunk: Callable[[str], None],
        on_step: Callable | None = None,
        on_event: Callable[[ReActEvent], None] | None = None,
    ) -> ReActResult:
        """Run with real token streaming from the provider.

        ``on_chunk`` receives every content fragment, including intermediate
        ReAct text on iterations that call tools.  The completed result is
        returned as well.
        """
        return await self.run(user_input, on_step, on_chunk=on_chunk, on_event=on_event)

    def get_context_stats(self) -> dict[str, Any]:
        """Get context statistics."""
        stats = self.context.get_stats()
        return {
            "total_tokens": stats.total_tokens,
            "max_tokens": stats.max_tokens,
            "available_tokens": stats.tokens_remaining,
            "usage_ratio": stats.usage_ratio,
            "message_count": stats.message_count,
            "is_near_limit": stats.is_near_limit,
        }

    async def get_memory_stats(self) -> dict[str, Any] | None:
        """Get memory statistics."""
        if self.memory:
            return await self.memory.get_stats()
        return None