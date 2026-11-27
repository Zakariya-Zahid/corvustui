"""Main CorvusTUI agent - ties together provider, ReAct loop, context, and tools."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from corvustui.config.settings import get_settings
from corvustui.context.manager import ContextManager
from corvustui.mcp.config import load_mcp_config
from corvustui.mcp.manager import MCPManager, MCPServerStatus
from corvustui.memory.vector import ChromaMemory, InMemoryVectorMemory, VectorMemory
from corvustui.providers import ProviderFactory, ProviderType
from corvustui.react.loop import IterationStatus, ReActEvent, ReActLoop, ReActResult
from corvustui.safety import Approver, DenyApprover, SafetyGuard, SafetyPolicy
from corvustui.tools.base import Tool
from corvustui.tools.builtin import register_builtin_tools
from corvustui.tools.registry import ToolRegistry


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
        register_builtin_tools(self.tools)

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

        # Relevant memories are passed as extra system context for this run
        # instead of permanently mutating the conversation system message.
        extra_system = None
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
                extra_system = "Relevant memories:\n" + "\n\n".join([
                    f"[Memory {i+1}]: {m.content}" for i, m in enumerate(memories)
                ])

        # Run ReAct loop with conversation history and memory context.
        self.react_loop.on_step = on_step
        result = await self.react_loop.run(
            user_input,
            history=history,
            extra_system=extra_system,
            on_chunk=on_chunk,
            on_event=on_event,
        )

        # Store the completed turn. Do not persist tool_calls here: the loop
        # does not keep the matching tool result messages, and an assistant
        # message with tool_calls but no tool results is invalid on replay.
        self.context.add_user(user_input)
        self.context.add_assistant(result.final_answer)

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