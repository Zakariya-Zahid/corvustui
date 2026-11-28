"""Consumer-grade Textual TUI for CorvusTUI (Claude Code & OpenCode inspired)."""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from rich.text import Text
from rich.theme import Theme
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from corvustui.config.settings import Settings, get_settings
from corvustui.core.agent import AgentConfig, CorvusAgent
from corvustui.providers import (
    ProviderFactory,
    ProviderModelNotFoundError,
    ProviderType,
)
from corvustui.react.loop import ReActLoop
from corvustui.safety import redact_secrets
from corvustui.todos import TodoStatus, TodoStore
from corvustui.tui.approval import TUIApprover
from corvustui.tui.controllers import (
    MessageQueue,
    PromptHistory,
    TUIPrefs,
    TurnController,
    discover_all_models,
)
from corvustui.tui.events import (
    QueueUpdated,
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
from corvustui.tui.widgets import (
    ChatView,
    CommandPaletteScreen,
    ConnectProviderScreen,
    HelpScreen,
    InfoModalScreen,
    InputArea,
    ModelPickerResult,
    ModelPickerScreen,
    PromptSubmitted,
    QueueActionRequested,
    QueueBar,
    QueueModalScreen,
    SafetyModalScreen,
    Sidebar,
    StatusBar,
    TodoBar,
    ToolsModalScreen,
)
from corvustui.tui.widgets.diff_view import compute_diff_stats


class TUIUnavailableError(RuntimeError):
    """Raised when the TUI cannot start because Textual is unavailable."""


def _detect_git_branch() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            text=True,
        ).strip()
        return out
    except Exception:
        return ""


class CorvusTUIApp(App[None]):
    """Interactive consumer-friendly TUI with streaming thinking, tool cards, and queueing."""

    TITLE = "CorvusTUI"
    SUB_TITLE = "Agentic Terminal Interface"
    CSS_PATH = Path(__file__).parent / "styles" / "app.tcss"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+c", "cancel_or_quit", "Cancel / Quit", priority=True),
        Binding("ctrl+p", "open_command_palette", "Commands", priority=True),
        Binding("ctrl+o", "open_model_picker", "Models", priority=True),
        Binding("ctrl+l", "clear_chat", "Clear chat"),
        Binding("ctrl+f", "toggle_focus_mode", "Focus mode"),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("f1", "show_help", "Help"),
        Binding("ctrl+question_mark", "show_help", "Help", show=False),
        Binding("pageup", "scroll_chat_up", "Scroll Up", show=False, priority=True),
        Binding("pagedown", "scroll_chat_down", "Scroll Down", show=False, priority=True),
        Binding("ctrl+up", "scroll_chat_line_up", "Line Up", show=False, priority=True),
        Binding("ctrl+down", "scroll_chat_line_down", "Line Down", show=False, priority=True),
        Binding("ctrl+u", "scroll_chat_half_up", "Half Page Up", show=False, priority=True),
        Binding("ctrl+d", "scroll_chat_half_down", "Half Page Down", show=False, priority=True),
        Binding("ctrl+home", "scroll_chat_top", "Top", show=False, priority=True),
        Binding("ctrl+end", "scroll_chat_bottom", "Bottom", show=False, priority=True),
    ]

    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        agent: CorvusAgent | None = None,
        settings: Settings | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or get_settings()
        self.config = config or AgentConfig()
        self.agent = agent
        self._owns_agent = agent is None
        self._entered = False
        self._agent_ready = False
        self._busy = False
        self._status_message = "Starting..."
        self._git_branch = _detect_git_branch()
        self._history = PromptHistory()
        self._prefs = TUIPrefs()
        self._queue = MessageQueue(on_change=self._on_queue_changed)
        self._todos = TodoStore()
        self._turn_controller = TurnController(self)
        self._queue_timer = None
        self._active_parallel_tasks: set[asyncio.Task[Any]] = set()

    # -- composition and lifecycle -----------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="top-bar", markup=False)
        with Horizontal(id="workspace"):
            yield ChatView(id="chat-view")
            yield Sidebar(id="sidebar")
        yield TodoBar(id="todo-bar")
        yield QueueBar(id="queue-bar")
        yield InputArea(history=self._history, id="input-area")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self.console.push_theme(Theme({
            "markdown.code": "bold #e0af68 on #1f2330",
            "markdown.link": "underline #7dcfff",
            "markdown.link_url": "dim #5c9cf5",
            "markdown.item.bullet": "bold #fab283",
            "markdown.item.number": "bold #fab283",
            "markdown.block_quote": "italic #8c91a4",
            "markdown.hr": "#3b3f52",
            "markdown.table.header": "bold #fab283",
            "markdown.table.border": "#3b3f52",
        }))
        self._refresh_top_bar()
        self._queue_timer = self.set_interval(0.25, self._queue_tick)
        self.start_agent()

    async def on_unmount(self) -> None:
        if self._queue_timer is not None:
            self._queue_timer.stop()
            self._queue_timer = None
        self._turn_controller.cancel_all()
        for task in list(self._active_parallel_tasks):
            task.cancel()
        if self.agent is not None and self._entered:
            self._entered = False
            try:
                await self.agent.__aexit__(None, None, None)
            except Exception:
                pass

    @property
    def _chat(self) -> ChatView:
        return self.query_one("#chat-view", ChatView)

    @property
    def _sidebar(self) -> Sidebar:
        return self.query_one("#sidebar", Sidebar)

    @property
    def _status_bar(self) -> StatusBar:
        return self.query_one("#status-bar", StatusBar)

    @property
    def _input_area(self) -> InputArea:
        return self.query_one("#input-area", InputArea)

    @property
    def _queue_bar(self) -> QueueBar:
        return self.query_one("#queue-bar", QueueBar)

    @property
    def _todo_bar(self) -> TodoBar:
        return self.query_one("#todo-bar", TodoBar)

    @staticmethod
    def _short_cwd() -> str:
        return str(Path.cwd()).replace(str(Path.home()), "~")

    def _refresh_top_bar(self) -> None:
        try:
            top = self.query_one("#top-bar", Static)
        except Exception:
            return
        cwd = self._short_cwd()
        line = Text()
        line.append("⌬ CorvusTUI", style="bold #fab283")
        line.append("  │  ", style="dim")
        line.append(f"📁 {cwd}", style="#8c91a4")
        if self._git_branch:
            line.append(f"  🌿 {self._git_branch}", style="#7fd88f")
        top.update(line)

    @work(exclusive=True, group="startup")
    async def start_agent(self) -> None:
        # Clear before entering the agent: the Ollama fallback may append a
        # status message, and clearing afterwards would remove it while its
        # widgets are still mounting.
        self._chat.clear_messages()
        try:
            await self._enter_agent()
        except Exception as exc:
            self._chat.append_error(f"Startup failed: {exc}")
            self._set_status("Startup failed", state="error")
            return

        self._agent_ready = True
        assert self.agent is not None
        self._sync_todos_from_agent()
        provider = getattr(self.agent.provider, "provider_type", None)
        model = getattr(self.agent.provider, "model", "unknown")
        provider_name = provider.value if provider is not None else "unknown"
        statuses = getattr(self.agent, "mcp_statuses", [])
        connected = sum(1 for s in statuses if getattr(s, "connected", False))

        self._status_bar.set_model_info(provider_name, model, mcp_count=connected)
        self._input_area.set_model_meta(provider_name, model, self._short_cwd())
        self._sidebar.set_mcp_statuses(statuses)
        self._sync_token_stats()

        for status in statuses:
            if not status.connected:
                self._chat.append_error(
                    f"MCP error ({status.name}): {status.error or 'not connected'}"
                )

        self._set_status("Ready", state="ready")
        self._input_area.focus_input()

    async def _enter_agent(self) -> None:
        self.agent = self._make_agent()
        try:
            await self.agent.__aenter__()
        except ProviderModelNotFoundError:
            if (
                not self._owns_agent
                or self.config.model is not None
                or self.config.provider_type != ProviderType.OLLAMA
            ):
                raise
            fallback = await self._fallback_ollama_model()
            if fallback is None:
                raise
            self.config = replace(self.config, model=fallback)
            self.agent = self._make_agent()
            await self.agent.__aenter__()
        self._entered = True

    def _make_agent(self) -> CorvusAgent:
        if not self._owns_agent and self.agent is not None:
            return self.agent
        config = self.config
        if config.approver is None:
            config = replace(config, approver=TUIApprover(self))
        agent = CorvusAgent(config)
        if getattr(agent, "safety", None) is not None:
            agent.safety.prompt_outside_roots = True
        return agent

    async def _fallback_ollama_model(self) -> str | None:
        provider = ProviderFactory.create(ProviderType.OLLAMA)
        await provider.__aenter__()
        try:
            models = await asyncio.wait_for(provider.list_models(), timeout=15)
        except Exception as exc:
            self._chat.append_error(f"Could not list Ollama models: {exc}")
            return None
        finally:
            await provider.__aexit__(None, None, None)

        if not models:
            self._chat.append_error(
                "Ollama is running but no local models are installed. "
                "Install one with 'ollama pull <model>' or use /connect to add a cloud provider."
            )
            return None
        # The configured default is missing.  Never leave the TUI stuck at
        # startup: use the only installed model, or the first one when several
        # are available, and tell the user how to switch.
        selected = models[0]
        if len(models) == 1:
            self._chat.append_system(
                f"Default model '{self.settings.default_model}' is not installed. "
                f"Using the only installed model: {selected}"
            )
        else:
            self._chat.append_system(
                f"Default model '{self.settings.default_model}' is not installed. "
                f"Using '{selected}'. Press Ctrl+O or type /models <name> to switch. "
                f"Installed: {', '.join(models)}"
            )
        return selected

    # -- prompt submission & queue handling --------------------------------

    async def on_prompt_submitted(self, event: PromptSubmitted) -> None:
        text = event.text.strip()
        if not text:
            return

        if text.startswith("/"):
            await self._handle_command(text)
            return

        if not self._agent_ready or self.agent is None:
            self._chat.append_error("The agent is not ready yet.")
            return

        if self._busy:
            item = self._queue.enqueue(text)
            self._chat.append_system(
                f"Queued prompt (#{self._queue.pending_count}, id={item.id}) — will run automatically when current turn finishes."
            )
            return

        self._start_turn(text)

    def _start_turn(self, text: str) -> None:
        assert self.agent is not None
        self._busy = True
        self._input_area.set_busy(True, self._queue.pending_count)
        turn_id = uuid.uuid4().hex[:10]
        model = getattr(self.agent.provider, "model", "assistant")
        self._chat.append_user(text)
        self._chat.start_assistant_turn(turn_id, model)
        self._run_turn_worker(text, turn_id)

    @work(exclusive=True, group="agent")
    async def _run_turn_worker(self, text: str, turn_id: str) -> None:
        assert self.agent is not None
        try:
            await self._turn_controller.execute_turn(self.agent, text, turn_id)
        finally:
            self._busy = False
            if self.is_running:
                try:
                    self._input_area.set_busy(False, self._queue.pending_count)
                    self._sync_token_stats()
                    self._dispatch_next_queued()
                except Exception:
                    pass

    def _start_parallel_turn(self, text: str) -> None:
        """Run a prompt in parallel using an isolated ReActLoop sharing provider/tools."""
        if not self._agent_ready or self.agent is None:
            return
        turn_id = f"par_{uuid.uuid4().hex[:8]}"
        model = getattr(self.agent.provider, "model", "assistant")
        self._chat.append_user(f"[Parallel] {text}")
        self._chat.start_assistant_turn(turn_id, f"{model} (parallel)")

        async def _run_parallel() -> None:
            assert self.agent is not None
            if hasattr(self.agent, "react_loop") and hasattr(self.agent, "provider"):
                loop = ReActLoop(
                    provider=self.agent.provider,
                    tools=self.agent.tools,
                    system_prompt=self.agent.react_loop.system_prompt,
                    max_iterations=self.agent.react_loop.max_iterations,
                    tool_timeout=self.agent.react_loop.tool_timeout,
                    guard=getattr(self.agent, "safety", None),
                )

                class _ParallelAgentShim:
                    async def run_streaming(self, prompt, on_chunk, on_step=None, on_event=None):
                        loop.on_step = on_step
                        return await loop.run(prompt, on_chunk=on_chunk, on_event=on_event)

                await self._turn_controller.execute_turn(_ParallelAgentShim(), text, turn_id)
            else:
                await self._turn_controller.execute_turn(self.agent, text, turn_id)

        task = asyncio.create_task(_run_parallel())
        self._active_parallel_tasks.add(task)
        task.add_done_callback(self._active_parallel_tasks.discard)

    def _on_queue_changed(self, pending: list[dict[str, Any]], paused: bool) -> None:
        self.post_message(QueueUpdated(pending, paused))

    @property
    def _chat_or_none(self) -> ChatView | None:
        if not self.is_running:
            return None
        try:
            return self.query_one("#chat-view", ChatView)
        except Exception:
            return None

    def _queue_tick(self) -> None:
        if not self.is_running or self._queue.pending_count == 0:
            return
        self._queue_bar.update_queue(self._queue.snapshot(), self._queue.paused)
        for par_item in self._queue.pop_parallel_ready():
            self._start_parallel_turn(par_item.text)
        if not self._busy:
            self._dispatch_next_queued()

    def _dispatch_next_queued(self) -> None:
        if not self.is_running or self._busy or not self._agent_ready:
            return
        next_item = self._queue.pop_ready()
        if next_item is not None:
            if next_item.parallel:
                self._start_parallel_turn(next_item.text)
            else:
                self._start_turn(next_item.text)

    def on_queue_updated(self, event: QueueUpdated) -> None:
        if not self.is_running:
            return
        try:
            self._queue_bar.update_queue(event.pending, event.paused)
            self._input_area.set_busy(self._busy, len(event.pending))
        except Exception:
            pass

    def on_queue_action_requested(self, event: QueueActionRequested) -> None:
        if event.action == "toggle_pause":
            if self._queue.paused:
                self._queue.resume()
                self._dispatch_next_queued()
            else:
                self._queue.pause()
        elif event.action == "run_now":
            self._queue.skip_waits()
            self._dispatch_next_queued()
        elif event.action == "clear":
            cleared = self._queue.clear()
            self._chat.append_system(f"Cleared {cleared} queued message(s).")

    # -- TurnController event handlers -------------------------------------

    def on_thinking_started(self, event: ThinkingStarted) -> None:
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            block.start_thinking(event.iteration)
            chat.scroll_if_pinned()

    def on_thinking_chunk(self, event: ThinkingChunk) -> None:
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            block.append_thinking(event.iteration, event.text)
            chat.scroll_if_pinned()

    def on_thinking_completed(self, event: ThinkingCompleted) -> None:
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            block.complete_thinking(event.iteration, event.full_text)

    def on_tool_started(self, event: ToolStarted) -> None:
        if event.tool_name == "todo_write":
            self._apply_todo_arguments(event.arguments)
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            block.start_tool(event.call_id, event.tool_name, event.arguments)
            chat.scroll_if_pinned()

    def on_tool_completed(self, event: ToolCompleted) -> None:
        if event.tool_name == "todo_write":
            self._sync_todos_from_agent()
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            block.complete_tool(
                event.call_id,
                event.output,
                success=event.success,
                duration_ms=event.duration_ms,
            )
            chat.scroll_if_pinned()

        if event.success and event.tool_name in {"edit_file", "write_file"}:
            added, removed = compute_diff_stats(event.output)
            path = event.output.splitlines()[0] if event.output else event.tool_name
            if "Edited " in path:
                path = path.split("Edited ", 1)[1].split(" (", 1)[0]
            elif "Written " in path and " to " in path:
                path = path.split(" to ", 1)[1]
            self._sidebar.record_file_change(path, added=added, removed=removed)

    def on_response_chunk(self, event: ResponseChunk) -> None:
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            block.append_response_chunk(event.text)
            chat.scroll_if_pinned()

    def on_response_reset(self, event: ResponseReset) -> None:
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            block.clear_response()

    def on_turn_completed(self, event: TurnCompleted) -> None:
        self._sync_todos_from_agent()
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            if event.result.final_answer:
                block.set_final_markdown(event.result.final_answer)
            model = getattr(getattr(self.agent, "provider", None), "model", "unknown")
            block.set_finish_info(model, event.duration_s, event.result.total_tokens)
            chat.scroll_if_pinned()
        self._sync_token_stats(add_completion_tokens=event.result.total_tokens)

    def on_turn_failed(self, event: TurnFailed) -> None:
        chat = self._chat_or_none
        if chat is None:
            return
        block = chat.get_turn_block(event.turn_id)
        if block is not None:
            block.set_final_markdown(f"**Error:** {event.error}")
            model = getattr(getattr(self.agent, "provider", None), "model", "unknown")
            block.set_finish_info(f"{model} [error]", event.duration_s, 0)
        else:
            chat.append_error(event.error)

    def on_status_changed(self, event: StatusChanged) -> None:
        if not self.is_running:
            return
        self._set_status(event.detail or event.state, state=event.state)

    def _apply_todo_arguments(self, arguments: dict[str, Any]) -> None:
        if self._todos.replace_from_arguments(arguments) is not None:
            self._render_todos()

    def _sync_todos_from_agent(self) -> None:
        store = getattr(self.agent, "todos", None)
        if store is None:
            return
        self._todos.replace(store.items)
        self._render_todos()

    def _render_todos(self) -> None:
        items = list(self._todos.items)
        try:
            self._todo_bar.update_todos(items)
        except Exception:
            pass
        try:
            self._sidebar.set_todos(items)
        except Exception:
            pass

    # -- commands ----------------------------------------------------------

    async def _handle_command(self, text: str) -> None:
        command, _, argument = text.partition(" ")
        command = command.lower()
        argument = argument.strip()

        if command in {"/quit", "/exit"}:
            self.exit()
            return
        if command == "/help":
            self.action_show_help()
            return
        if command in {"/commands", "/palette"}:
            self.action_open_command_palette()
            return
        if command == "/connect":
            self.action_open_connect_provider()
            return
        if command == "/focus":
            self.action_toggle_focus_mode()
            return
        if command == "/sidebar":
            self.action_toggle_sidebar()
            return
        if command == "/wait":
            await self._cmd_wait(argument)
            return
        if command == "/parallel":
            if not argument:
                self._chat.append_error("Usage: /parallel <prompt>")
                return
            self._queue.enqueue(argument, parallel=True)
            self._chat.append_system(f"Scheduled parallel prompt: {argument}")
            self._queue_tick()
            return
        if command == "/queue":
            self._cmd_queue(argument)
            return
        if command == "/clear":
            if self._busy:
                self._chat.append_error("Cannot clear context while a turn is running.")
                return
            if self.agent is not None:
                self.agent.context.clear()
                agent_todos = getattr(self.agent, "todos", None)
                if agent_todos is not None:
                    agent_todos.clear()
            self._todos.clear()
            self._render_todos()
            self._chat.clear_messages()
            self._chat.append_system("Conversation context, tasks, and chat cleared.")
            self._sync_token_stats()
            return
        if command == "/stats":
            await self._show_stats()
            return
        if command == "/tools":
            self._show_tools()
            return
        if command == "/mcp":
            self._show_mcp()
            return
        if command == "/safety":
            self._show_safety()
            return
        if command == "/todos":
            self._show_todos()
            return
        if command == "/models":
            if not argument:
                self.action_open_model_picker()
                return
            if self._busy:
                self._chat.append_error("Wait for the active turn before switching models.")
                return
            await self._switch_model(argument)
            return

        self._chat.append_error(f"Unknown command: {command}. Type /help for commands.")

    async def _cmd_wait(self, argument: str) -> None:
        parts = argument.split(maxsplit=1)
        if len(parts) < 2:
            self._chat.append_error("Usage: /wait <seconds> <prompt>")
            return
        try:
            seconds = float(parts[0])
        except ValueError:
            self._chat.append_error(f"Invalid wait duration: {parts[0]}")
            return
        prompt = parts[1].strip()
        item = self._queue.enqueue(prompt, delay_seconds=max(0.0, seconds))
        self._chat.append_system(
            f"Scheduled delayed prompt (id={item.id}) to run after {seconds:.1f}s: {prompt}"
        )

    def _cmd_queue(self, argument: str) -> None:
        sub = argument.lower().strip()
        if sub == "pause":
            self._queue.pause()
            self._chat.append_system("Queue paused.")
        elif sub == "resume":
            self._queue.resume()
            self._chat.append_system("Queue resumed.")
            self._dispatch_next_queued()
        elif sub == "run":
            count = self._queue.skip_waits()
            self._chat.append_system(f"Skipped waits for {count} queued item(s).")
            self._dispatch_next_queued()
        elif sub == "clear":
            count = self._queue.clear()
            self._chat.append_system(f"Cleared {count} queued item(s).")
        else:
            self.action_open_queue_modal()

    @work(exclusive=True, group="queue_modal")
    async def action_open_queue_modal(self) -> None:
        action = await self.push_screen_wait(
            QueueModalScreen(self._queue.snapshot(), self._queue.paused)
        )
        if action in {"pause", "resume", "run", "clear"}:
            self._cmd_queue(action)

    async def _show_stats(self) -> None:
        if self.agent is None:
            self._chat.append_error("Agent is not ready.")
            return
        try:
            stats = self.agent.get_context_stats()
            memory = await self.agent.get_memory_stats()
        except Exception as exc:
            self._chat.append_error(f"Could not read statistics: {exc}")
            return
        provider = getattr(self.agent.provider, "provider_type", None)
        model = getattr(self.agent.provider, "model", "unknown")
        provider_name = provider.value if provider is not None else "unknown"
        memory_entries = str(memory.get("total_entries", "N/A")) if memory else "Disabled"
        rows = [
            ("Provider / Model", f"{provider_name}/{model}", "bold #fab283"),
            (
                "Context Tokens",
                f"{stats['total_tokens']} / {stats['max_tokens']} ({stats['usage_ratio']:.1%})",
                "#7fd88f" if stats["usage_ratio"] < 0.8 else "#f7768e",
            ),
            ("Messages", str(stats["message_count"]), "#e4e6f0"),
            ("Vector Memory", f"{memory_entries} entries" if memory else "Disabled", "#7dcfff"),
            ("Queued Prompts", str(self._queue.pending_count), "#e4e6f0"),
        ]
        self.push_screen(InfoModalScreen("Session Statistics", rows))

    def _show_tools(self) -> None:
        if self.agent is None:
            self._chat.append_error("Agent is not ready.")
            return
        tools = self.agent.tools.get_all()
        self.push_screen(ToolsModalScreen(tools))

    def _show_todos(self) -> None:
        items = self._todos.items
        if not items:
            rows = [
                ("Tasks", "No tasks yet. Ask the agent to plan a multi-step task.", "dim")
            ]
        else:
            completed, total = self._todos.counts()
            rows = [
                (
                    "Progress",
                    f"{completed}/{total} complete",
                    "#7fd88f" if completed == total else "#e0af68",
                )
            ]
            for item in items:
                if item.status == TodoStatus.COMPLETED:
                    rows.append(("✓ Completed", item.content, "#7fd88f"))
                elif item.status == TodoStatus.IN_PROGRESS:
                    rows.append(("▸ In progress", item.display_text, "bold #e0af68"))
                else:
                    rows.append(("○ Pending", item.content, "#8c91a4"))
        self.push_screen(InfoModalScreen("Tasks", rows))

    def _show_mcp(self) -> None:
        statuses = getattr(self.agent, "mcp_statuses", []) if self.agent else []
        if not statuses:
            rows = [("MCP Servers", "No MCP servers configured.", "dim")]
        else:
            rows = []
            for status in statuses:
                if status.connected:
                    rows.append((
                        status.name,
                        f"● Connected ({status.transport}, {status.tool_count} tools)",
                        "#7fd88f",
                    ))
                else:
                    rows.append((
                        status.name,
                        f"○ Error: {redact_secrets(status.error or 'not connected')}",
                        "#f7768e",
                    ))
        self.push_screen(InfoModalScreen("MCP Servers", rows))

    def _show_safety(self) -> None:
        self.action_open_safety_modal()

    @work(exclusive=True, group="safety_modal")
    async def action_open_safety_modal(self) -> None:
        safety = getattr(self.agent, "safety", None)
        policy = getattr(safety, "policy", None)
        if policy is None:
            self.push_screen(InfoModalScreen("Safety Policy", [("Status", "Safety guard unavailable", "dim")]))
            return
        action = await self.push_screen_wait(SafetyModalScreen(policy))
        if action == "toggle_outside" and safety is not None:
            safety.policy = replace(policy, allow_outside_roots=not policy.allow_outside_roots)
            self.action_open_safety_modal()
        elif action == "toggle_shell" and safety is not None:
            new_mode = "deny_dangerous" if policy.shell_mode == "unrestricted" else "unrestricted"
            safety.policy = replace(policy, shell_mode=new_mode)
            self.action_open_safety_modal()

    async def _switch_model(
        self,
        model: str,
        *,
        provider_type: ProviderType | None = None,
    ) -> None:
        if not model:
            self._chat.append_error("Usage: /models <name>")
            return
        if self.agent is None:
            self._chat.append_error("Agent is not ready.")
            return
        from corvustui.providers.detect import detect_provider_from_model

        resolved_provider = provider_type or detect_provider_from_model(model)
        try:
            await self.agent.switch_model(resolved_provider, model)
        except Exception as exc:
            self._chat.append_error(f"Could not switch model: {exc}")
            return

        new_ptype = self.agent.provider.provider_type
        new_model = self.agent.provider.model
        self._prefs.record_recent(new_ptype, new_model)

        connected = sum(
            1 for s in getattr(self.agent, "mcp_statuses", []) if getattr(s, "connected", False)
        )
        self._status_bar.set_model_info(
            new_ptype.value,
            new_model,
            mcp_count=connected,
        )
        self._input_area.set_model_meta(new_ptype.value, new_model, self._short_cwd())
        self._sync_token_stats()
        self._set_status("Ready", state="ready")

    # -- status & actions --------------------------------------------------

    def _sync_token_stats(self, add_completion_tokens: int = 0) -> None:
        if self.agent is None:
            return
        try:
            stats = self.agent.get_context_stats()
            used = int(stats["total_tokens"])
            maximum = int(stats["max_tokens"])
        except Exception:
            used, maximum = 0, 8192

        self._status_bar.set_tokens(used, maximum, add_completion_tokens=add_completion_tokens)
        provider = getattr(self.agent.provider, "provider_type", None)
        model = getattr(self.agent.provider, "model", "unknown")
        provider_name = provider.value if provider is not None else "unknown"
        self._sidebar.set_session_info(provider_name, model, used)

    def _set_status(self, message: str, *, state: str = "ready") -> None:
        self._status_message = message
        try:
            self._status_bar.set_state(state, message)
        except Exception:
            pass

    def _status_text(self) -> str:
        """Compact summary string used for diagnostics and tests."""
        if self.agent is None:
            return self._status_message
        provider = getattr(self.agent.provider, "provider_type", None)
        model = getattr(self.agent.provider, "model", "unknown")
        provider_name = provider.value if provider is not None else "unknown"
        try:
            stats = self.agent.get_context_stats()
            tokens = f"{stats['total_tokens']}/{stats['max_tokens']}"
        except Exception:
            tokens = "?"
        connected = sum(
            1 for status in getattr(self.agent, "mcp_statuses", []) if status.connected
        )
        return f"{provider_name}/{model} | tokens {tokens} | MCP {connected} | {self._status_message}"

    def action_clear_chat(self) -> None:
        try:
            self._chat.clear_messages()
            self._chat.append_system("Chat log cleared.")
        except Exception:
            pass

    def action_toggle_focus_mode(self) -> None:
        enabled = self._chat.toggle_focus_mode()
        self._set_status("Focus mode on" if enabled else "Ready", state="ready")

    def action_toggle_sidebar(self) -> None:
        try:
            sb = self._sidebar
            if sb.has_class("hidden"):
                sb.remove_class("hidden")
            else:
                sb.add_class("hidden")
        except Exception:
            pass

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_scroll_chat_up(self) -> None:
        self._chat.page_up_chat()

    def action_scroll_chat_down(self) -> None:
        self._chat.page_down_chat()

    def action_scroll_chat_line_up(self) -> None:
        self._chat.line_up_chat()

    def action_scroll_chat_line_down(self) -> None:
        self._chat.line_down_chat()

    def action_scroll_chat_half_up(self) -> None:
        self._chat.half_page_up_chat()

    def action_scroll_chat_half_down(self) -> None:
        self._chat.half_page_down_chat()

    def action_scroll_chat_top(self) -> None:
        self._chat._pinned = False
        self._chat._scroll_pending = False
        self._chat.scroll_home(animate=False)

    def action_scroll_chat_bottom(self) -> None:
        self._chat.jump_to_bottom()

    @work(exclusive=True, group="palette")
    async def action_open_command_palette(self) -> None:
        cmd_id = await self.push_screen_wait(CommandPaletteScreen())
        if not cmd_id:
            return
        if cmd_id == "switch_model":
            self.action_open_model_picker()
        elif cmd_id == "connect_provider":
            self.action_open_connect_provider()
        elif cmd_id == "clear_chat":
            await self._handle_command("/clear")
        elif cmd_id == "toggle_focus":
            self.action_toggle_focus_mode()
        elif cmd_id == "toggle_sidebar":
            self.action_toggle_sidebar()
        elif cmd_id == "show_stats":
            await self._show_stats()
        elif cmd_id == "show_tools":
            self._show_tools()
        elif cmd_id == "show_todos":
            self._show_todos()
        elif cmd_id == "show_mcp":
            self._show_mcp()
        elif cmd_id == "show_safety":
            self._show_safety()
        elif cmd_id == "queue_status":
            self._cmd_queue("")
        elif cmd_id == "queue_pause":
            self._cmd_queue("resume" if self._queue.paused else "pause")
        elif cmd_id == "queue_run":
            self._cmd_queue("run")
        elif cmd_id == "show_help":
            self.action_show_help()
        elif cmd_id == "quit":
            self.exit()

    @work(exclusive=True, group="connect_provider")
    async def action_open_connect_provider(self, *, reopen_models: bool = False) -> None:
        current_provider = getattr(
            getattr(self.agent, "provider", None),
            "provider_type",
            ProviderType.OLLAMA,
        )
        changed = await self.push_screen_wait(ConnectProviderScreen(current_provider))
        if changed is not None:
            self._chat.append_system(f"Provider '{changed.value}' configuration updated.")
        if reopen_models or changed is not None:
            self.action_open_model_picker()

    @work(exclusive=True, group="model_picker")
    async def action_open_model_picker(self) -> None:
        if self.agent is None or not self._agent_ready:
            return
        if self._busy:
            self._chat.append_error("Wait for the active turn before switching models.")
            return
        current_provider = getattr(self.agent.provider, "provider_type", ProviderType.OLLAMA)
        current_model = getattr(self.agent.provider, "model", "unknown")

        try:
            active_models = await asyncio.wait_for(self.agent.provider.list_models(), timeout=8)
        except Exception:
            active_models = None

        entries, errors = await discover_all_models(current_provider, active_models)
        result: ModelPickerResult | None = await self.push_screen_wait(
            ModelPickerScreen(
                entries,
                current_provider,
                current_model,
                prefs=self._prefs,
                provider_errors=errors,
            )
        )
        if result is None:
            return
        if result.action == "connect":
            self.action_open_connect_provider(reopen_models=True)
            return
        if result.action == "select" and result.model:
            if result.model != current_model or (
                result.provider_type is not None and result.provider_type != current_provider
            ):
                await self._switch_model(result.model, provider_type=result.provider_type)

    def action_cancel_or_quit(self) -> None:
        if self._busy or self._turn_controller.is_running:
            cancelled = self._turn_controller.cancel_all()
            if cancelled:
                self._chat.append_system("Cancelling current generation...")
            return
        self.exit()

    async def action_quit(self) -> None:
        self.exit()
