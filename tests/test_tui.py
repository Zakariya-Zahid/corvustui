"""Tests for the consumer-grade Textual TUI, widgets, queue, and approvals."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult
from textual.widgets import Static

from corvustui.core.agent import AgentConfig
from corvustui.providers import ProviderModelNotFoundError, ProviderType
from corvustui.react.loop import IterationStatus, ReActEvent, ReActResult, ReActStep
from corvustui.safety import ApprovalRequest
from corvustui.tui import CorvusTUIApp
from corvustui.tui.approval import ApprovalScreen, TUIApprover
from corvustui.tui.controllers import (
    MessageQueue,
    save_provider_credentials,
)
from corvustui.tui.widgets import (
    ChatView,
    CommandPaletteScreen,
    ConnectProviderScreen,
    InfoModalScreen,
    InputArea,
    MessageBlock,
    MessageRole,
    ModelPickerScreen,
    QueueBar,
    QueueModalScreen,
    SafetyModalScreen,
    Sidebar,
    ThinkingBlock,
    TodoBar,
    ToolBlock,
    ToolsModalScreen,
)
from corvustui.tui.widgets.input_area import PromptTextArea


class FakeProvider:
    def __init__(self) -> None:
        self.provider_type = ProviderType.OLLAMA
        self.model = "fake-model"
        self.models = ["fake-model", "other:1b"]

    async def list_models(self) -> list[str]:
        return list(self.models)


class FakeTools:
    def get_all(self):
        return [SimpleNamespace(name="echo", description="Echo text")]


class FakeContext:
    def __init__(self) -> None:
        self.clear_count = 0

    def clear(self) -> None:
        self.clear_count += 1


class FakeAgent:
    def __init__(self, *, delay: float = 0.0, emit_events: bool = True) -> None:
        self.provider = FakeProvider()
        self.tools = FakeTools()
        self.context = FakeContext()
        self.safety = SimpleNamespace(
            policy=SimpleNamespace(
                enabled=True,
                allowed_roots=(),
                allow_outside_roots=False,
                shell_mode="deny_dangerous",
                network_enabled=False,
                require_approval=True,
                audit_path=None,
            )
        )
        self.mcp_statuses: list = []
        self.entered = False
        self.exited = False
        self.prompts: list[str] = []
        self.switched: list[tuple[ProviderType, str]] = []
        self.streamed_chunks = 0
        self.delay = delay
        self.emit_events = emit_events

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def run(self, text: str, on_step=None):
        self.prompts.append(text)
        step = ReActStep(iteration=1, thought="thinking about " + text, observation="Task complete")
        if on_step:
            on_step(step)
        return ReActResult(
            final_answer=f"answer:{text}",
            steps=[step],
            total_iterations=1,
            status=IterationStatus.COMPLETE,
            total_tokens=12,
        )

    async def run_streaming(self, text: str, on_chunk, on_step=None, on_event=None):
        self.prompts.append(text)
        if self.delay > 0:
            await asyncio.sleep(self.delay)

        if self.emit_events and on_event is not None:
            on_event(ReActEvent("thinking_start", iteration=1))
            on_event(ReActEvent("thinking_chunk", iteration=1, data={"text": "Analyzing prompt... "}))
            on_event(ReActEvent("thinking_chunk", iteration=1, data={"text": "Done reasoning."}))
            on_event(ReActEvent("thinking_end", iteration=1, data={"text": "Analyzing prompt... Done reasoning."}))
            on_event(ReActEvent("tool_start", iteration=1, data={
                "call_id": "call_1",
                "tool": "read_file",
                "arguments": {"path": "app.py"},
            }))
            on_event(ReActEvent("tool_end", iteration=1, data={
                "call_id": "call_1",
                "tool": "read_file",
                "output": "print('hello world')",
                "success": True,
                "duration_ms": 25.0,
            }))

        answer = f"answer:{text}"
        for index in range(0, len(answer), 4):
            self.streamed_chunks += 1
            frag = answer[index:index + 4]
            on_chunk(frag)
            if self.emit_events and on_event is not None:
                on_event(ReActEvent("response_chunk", iteration=1, data={"text": frag}))

        step = ReActStep(iteration=1, thought="Analyzing prompt... Done reasoning.", observation="Task complete")
        if on_step:
            on_step(step)
        return ReActResult(
            final_answer=answer,
            steps=[step],
            total_iterations=1,
            status=IterationStatus.COMPLETE,
            total_tokens=12,
        )

    async def switch_model(self, provider_type: ProviderType, model: str) -> None:
        self.switched.append((provider_type, model))
        self.provider.provider_type = provider_type
        self.provider.model = model

    def get_context_stats(self) -> dict:
        return {
            "total_tokens": 300,
            "max_tokens": 1000,
            "usage_ratio": 0.3,
            "message_count": 2,
        }

    async def get_memory_stats(self) -> dict:
        return {"total_entries": 0}


async def _wait_for(pilot, predicate, timeout: float = 3.0) -> None:
    elapsed = 0.0
    while not predicate():
        if elapsed >= timeout:
            raise AssertionError("Timed out waiting for TUI state")
        await pilot.pause(0.02)
        elapsed += 0.02


async def test_tui_starts_and_renders_thinking_and_tool_blocks():
    agent = FakeAgent()
    app = CorvusTUIApp(agent=agent)

    async with app.run_test(size=(110, 34)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)
        assert agent.entered
        assert "fake-model" in app._status_text()

        ta = app.query_one("#prompt-input", PromptTextArea)
        ta.focus()
        await pilot.press(*"hello")
        await pilot.press("enter")
        await _wait_for(pilot, lambda: not app._busy and agent.prompts)
        await pilot.pause(0.05)

        assert agent.prompts == ["hello"]
        assert agent.streamed_chunks > 1

        chat = app.query_one("#chat-view", ChatView)
        user_blocks = [b for b in chat.query(MessageBlock) if b.role == MessageRole.USER]
        assistant_blocks = [b for b in chat.query(MessageBlock) if b.role == MessageRole.ASSISTANT]
        assert len(user_blocks) == 1
        assert len(assistant_blocks) == 1

        thinking = chat.query(ThinkingBlock)
        assert len(thinking) == 1
        tb = thinking.first()
        assert tb.expanded is False
        tb.toggle()
        await pilot.pause(0.02)
        assert tb.expanded is True
        assert "Analyzing prompt" in tb._buffer

        tools = chat.query(ToolBlock)
        assert len(tools) == 1
        tlb = tools.first()
        assert tlb.tool_name == "read_file"
        assert tlb.state == "success"

    assert agent.exited


async def test_tui_queues_messages_while_busy_and_auto_continues():
    agent = FakeAgent(delay=1.2)
    app = CorvusTUIApp(agent=agent)

    async with app.run_test(size=(110, 34)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)

        ta = app.query_one("#prompt-input", PromptTextArea)
        ta.focus()
        app.query_one("#input-area", InputArea).value = "first"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._busy)

        # Submit second prompt while first is still running -> should queue!
        app.query_one("#input-area", InputArea).value = "second"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._queue.pending_count == 1)
        assert not app.query_one("#queue-bar", QueueBar).has_class("hidden")

        # Wait for both turns to finish via automatic continuation
        await _wait_for(pilot, lambda: len(agent.prompts) == 2 and not app._busy, timeout=4.0)
        assert agent.prompts == ["first", "second"]
        assert app._queue.pending_count == 0


async def test_tui_wait_and_parallel_commands():
    agent = FakeAgent(delay=0.05)
    app = CorvusTUIApp(agent=agent)

    async with app.run_test(size=(110, 34)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)

        app.query_one("#input-area", InputArea).value = "/wait 0.4 delayed-task"
        app.query_one("#prompt-input", PromptTextArea).focus()
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._queue.pending_count == 1)
        assert app._queue.pending_count == 1

        await _wait_for(pilot, lambda: "delayed-task" in agent.prompts and not app._busy, timeout=3.0)
        assert "delayed-task" in agent.prompts

        # Parallel execution command
        app.query_one("#input-area", InputArea).value = "/parallel par-task"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: "par-task" in agent.prompts, timeout=3.0)


async def test_tui_commands_update_agent_and_ui_state():
    agent = FakeAgent()
    app = CorvusTUIApp(agent=agent)

    async with app.run_test(size=(110, 34)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)
        ta = app.query_one("#prompt-input", PromptTextArea)
        ta.focus()

        expected_modals = [
            ("/stats", InfoModalScreen),
            ("/tools", ToolsModalScreen),
            ("/mcp", InfoModalScreen),
            ("/safety", SafetyModalScreen),
            ("/queue", QueueModalScreen),
            ("/commands", CommandPaletteScreen),
            ("/models", ModelPickerScreen),
        ]
        for command, modal_cls in expected_modals:
            app.query_one("#input-area", InputArea).value = command
            await pilot.press("enter")
            await _wait_for(pilot, lambda cls=modal_cls: isinstance(app.screen, cls), timeout=3.0)
            await pilot.press("escape")
            await pilot.pause(0.03)

        for command in ["/focus", "/sidebar"]:
            app.query_one("#input-area", InputArea).value = command
            await pilot.press("enter")
            await pilot.pause(0.04)

        # Sidebar toggled hidden, toggle back
        assert app.query_one("#sidebar", Sidebar).has_class("hidden") is True
        app.query_one("#input-area", InputArea).value = "/sidebar"
        await pilot.press("enter")
        await pilot.pause(0.04)
        assert app.query_one("#sidebar", Sidebar).has_class("hidden") is False

        # Startup & focus toggle must NOT spam chat messages
        assert len(app.query_one("#chat-view", ChatView).query(MessageBlock)) == 0

        # /model was removed; /models <name> switches directly
        app.query_one("#input-area", InputArea).value = "/models other:1b"
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert agent.switched == [(ProviderType.OLLAMA, "other:1b")]
        assert "other:1b" in app._status_text()

        app.query_one("#input-area", InputArea).value = "/clear"
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert agent.context.clear_count == 1


async def test_approval_screen_approves_session_and_denies():
    app = CorvusTUIApp(agent=FakeAgent())

    async with app.run_test(size=(100, 30)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)
        request = ApprovalRequest("shell", {"command": "echo hi"})

        results: list[bool] = []
        app.push_screen(ApprovalScreen(request), results.append)
        await pilot.pause()
        await pilot.click("#approve")
        await pilot.pause()
        assert results == [True]

        session_tools: set[str] = set()
        sess_results: list[bool] = []
        app.push_screen(
            ApprovalScreen(request, on_allow_session=session_tools.add),
            sess_results.append,
        )
        await pilot.pause()
        await pilot.click("#allow-session")
        await pilot.pause()
        assert sess_results == [True]
        assert "shell" in session_tools

        denied: list[bool] = []
        app.push_screen(ApprovalScreen(request), denied.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert denied == [False]


async def test_tui_approver_fails_closed_outside_worker():
    app = CorvusTUIApp(agent=FakeAgent())
    approver = TUIApprover(app)

    assert await approver.approve(ApprovalRequest("shell", {"command": "echo hi"})) is False


def test_message_queue_wait_pause_resume():
    q = MessageQueue()
    item1 = q.enqueue("first", delay_seconds=10.0)
    assert item1.status == "waiting"
    assert q.pop_ready() is None

    q.pause()
    assert q.paused is True
    q.skip_waits()
    assert q.paused is False
    popped = q.pop_ready()
    assert popped is not None and popped.text == "first"


async def test_tui_rejects_unknown_command():
    agent = FakeAgent()
    app = CorvusTUIApp(agent=agent)

    async with app.run_test(size=(100, 30)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)
        app.query_one("#prompt-input", PromptTextArea).focus()
        app.query_one("#input-area", InputArea).value = "/nope"
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert agent.prompts == []


class FallbackProvider:
    provider_type = ProviderType.OLLAMA

    def __init__(
        self,
        model: str,
        *,
        fail: bool = False,
        models: list[str] | None = None,
    ) -> None:
        self.model = model
        self.fail = fail
        self._models = models or ["only:1b"]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def validate_model(self) -> None:
        if self.fail:
            raise ProviderModelNotFoundError("missing", ProviderType.OLLAMA)

    async def list_models(self) -> list[str]:
        return list(self._models)

    def count_message_tokens(self, message) -> int:
        return 1

    def count_messages_tokens(self, messages) -> int:
        return len(messages)


async def test_tui_ollama_fallback_rebuilds_agent(monkeypatch):
    from corvustui.core import agent as agent_module

    providers = [
        FallbackProvider("missing", fail=True),
        FallbackProvider("only:1b"),
    ]

    def fake_create(provider_type, model=None, **kwargs):
        return providers.pop(0)

    monkeypatch.setattr(agent_module.ProviderFactory, "create", fake_create)
    app = CorvusTUIApp(
        AgentConfig(
            provider_type=ProviderType.OLLAMA,
            model=None,
            enable_mcp=False,
        )
    )

    async def fallback(self):
        return "only:1b"

    app._fallback_ollama_model = fallback.__get__(app, CorvusTUIApp)

    async with app.run_test(size=(100, 30)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)
        assert app.config.model == "only:1b"
        assert app.agent.provider.model == "only:1b"
        assert providers == []


async def test_tui_ollama_fallback_uses_first_model_when_several_installed(monkeypatch):
    from corvustui.core import agent as agent_module

    providers = [
        FallbackProvider("missing", fail=True),
        FallbackProvider(
            "unused",
            models=["maternion/minicpm-v4.7-heretic:1b", "qwen3.5:0.8b"],
        ),
        FallbackProvider("maternion/minicpm-v4.7-heretic:1b"),
    ]

    def fake_create(provider_type, model=None, **kwargs):
        return providers.pop(0)

    monkeypatch.setattr(agent_module.ProviderFactory, "create", fake_create)
    app = CorvusTUIApp(
        AgentConfig(
            provider_type=ProviderType.OLLAMA,
            model=None,
            enable_mcp=False,
        )
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)
        assert app.config.model == "maternion/minicpm-v4.7-heretic:1b"
        assert app.agent.provider.model == "maternion/minicpm-v4.7-heretic:1b"
        assert providers == []


def test_injected_agent_is_reused():
    agent = FakeAgent()
    app = CorvusTUIApp(agent=agent)

    assert app._make_agent() is agent


def test_tui_cli_requires_interactive_terminal(monkeypatch):
    from click.testing import CliRunner

    from corvustui import cli as cli_module

    monkeypatch.setattr(cli_module.sys.stdin, "isatty", lambda: False)
    result = CliRunner().invoke(cli_module.cli, ["tui"])

    assert result.exit_code != 0
    assert "interactive terminal" in result.output


async def test_slash_autocomplete_command_palette_model_picker_and_connect(tmp_path, monkeypatch):
    agent = FakeAgent()
    app = CorvusTUIApp(agent=agent)

    # Test credential persistence to an isolated temp .env
    env_file = tmp_path / ".env"
    save_provider_credentials(ProviderType.OPENAI, api_key="sk-test-12345678", env_path=env_file)
    assert "CORVUS_OPENAI_API_KEY=sk-test-12345678" in env_file.read_text(encoding="utf-8")
    # Clean up key from settings/env right after assertion so discover doesn't hit real OpenAI
    save_provider_credentials(ProviderType.OPENAI, api_key="", env_path=env_file)
    assert "CORVUS_OPENAI_API_KEY" not in env_file.read_text(encoding="utf-8")

    async with app.run_test(size=(110, 34)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)

        # 1. Typing "/" opens interactive autocomplete popup and /stats opens InfoModalScreen
        ta = app.query_one("#prompt-input", PromptTextArea)
        ta.focus()
        app.query_one("#input-area", InputArea).value = "/st"
        await pilot.pause(0.05)
        assert app.query_one("#input-area", InputArea).is_autocomplete_open() is True
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, InfoModalScreen), timeout=3.0)
        assert ta.text == ""
        await pilot.press("escape")
        await pilot.pause(0.05)

        # 2. Ctrl+P opens CommandPaletteScreen
        await pilot.press("ctrl+p")
        await pilot.pause(0.05)
        assert isinstance(app.screen, CommandPaletteScreen)
        await pilot.press("escape")
        await pilot.pause(0.05)

        # 3. Ctrl+O opens ModelPickerScreen and switches model
        await pilot.press("ctrl+o")
        await _wait_for(pilot, lambda: isinstance(app.screen, ModelPickerScreen), timeout=3.0)
        await pilot.press("down")
        await pilot.press("enter")
        await _wait_for(pilot, lambda: len(agent.switched) == 1, timeout=3.0)
        assert agent.switched == [(ProviderType.OLLAMA, "other:1b")]

        # 4. /connect opens ConnectProviderScreen
        app.query_one("#input-area", InputArea).value = "/connect"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ConnectProviderScreen), timeout=3.0)
        await pilot.press("escape")
        await pilot.pause(0.05)


async def test_chat_view_scrolling_and_sudo_approval_ui():
    agent = FakeAgent()
    app = CorvusTUIApp(agent=agent)

    async with app.run_test(size=(110, 24)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)
        chat = app.query_one("#chat-view", ChatView)
        for i in range(25):
            chat.append_user(f"Prompt {i}: " + ("word " * 12))
        await pilot.pause(0.1)

        assert chat.max_scroll_y > 10
        bottom_y = chat.scroll_y
        assert chat._pinned is True

        # PageUp from focused input must scroll chat up and unpin!
        ta = app.query_one("#prompt-input", PromptTextArea)
        ta.focus()
        await pilot.press("pageup")
        await pilot.pause(0.05)
        assert chat.scroll_y < bottom_y
        assert chat._pinned is False

        # Streaming while unpinned must NOT yank scrollbar back to bottom
        scrolled_y = chat.scroll_y
        chat.scroll_if_pinned()
        await pilot.pause(0.05)
        assert chat.scroll_y == scrolled_y

        # Ctrl+End jumps back to bottom and re-pins
        await pilot.press("ctrl+end")
        await pilot.pause(0.05)
        assert chat.scroll_y == chat.max_scroll_y
        assert chat._pinned is True

        # Sudo confirmation modal classifies elevated access and disables session auto-allow
        sudo_req = ApprovalRequest("shell", {"command": "sudo apt update"})
        sudo_screen = ApprovalScreen(sudo_req)
        results: list[bool] = []
        app.push_screen(sudo_screen, results.append)
        await pilot.pause(0.05)
        assert sudo_screen._is_elevated is True
        assert len(sudo_screen.query("#allow-session")) == 0
        await pilot.press("y")
        await pilot.pause(0.05)
        assert results == [True]

        # Scrollbar must sit flush on the right edge, sync thumb position, and respond to track clicks, wheel & keys
        from textual import events

        sb = chat.vertical_scrollbar
        assert sb.region.right == chat.region.right
        assert sb.region.width == 2
        assert sb.position == pytest.approx(chat.scroll_y, abs=0.25)

        await pilot.mouse_down(app.screen, offset=(sb.region.x, sb.region.y + 1))
        await pilot.mouse_up(app.screen, offset=(sb.region.x, sb.region.y + 1))
        await pilot.pause(0.05)
        after_track_y = chat.scroll_y
        assert after_track_y < bottom_y
        assert chat._pinned is False
        assert sb.position == pytest.approx(after_track_y, abs=0.25)

        chat.post_message(events.MouseScrollUp(chat, 10, 5, 0, 0, 0, False, False, False))
        await pilot.pause(0.05)
        assert chat.scroll_y < after_track_y
        assert sb.position == pytest.approx(chat.scroll_y, abs=0.25)

        after_wheel_y = chat.scroll_y
        await pilot.press("ctrl+down")
        await pilot.pause(0.05)
        assert chat.scroll_y > after_wheel_y

        await pilot.press("ctrl+home")
        await pilot.pause(0.05)
        assert chat.scroll_y == 0
        assert sb.position == 0


async def test_outside_root_escalation_prompts_and_protects_sensitive_paths(tmp_path):
    from corvustui.safety import CallbackApprover, SafetyGuard, SafetyPolicy
    from corvustui.tools.builtin import ShellTool, WriteFileTool

    root = tmp_path / "workspace"
    outside = tmp_path / "outside_dir"
    root.mkdir()
    outside.mkdir()

    policy = SafetyPolicy(allowed_roots=(root,), audit_path=tmp_path / "audit.jsonl")
    seen_requests: list[ApprovalRequest] = []

    async def approve_cb(req: ApprovalRequest) -> bool:
        seen_requests.append(req)
        return True

    guard = SafetyGuard(policy, approver=CallbackApprover(approve_cb), prompt_outside_roots=True)

    # 1. Writing outside workspace prompts the user and succeeds when approved
    decision = await guard.authorize(WriteFileTool(), {"path": str(outside / "app.js"), "content": "x"})
    assert decision.allowed is True
    assert decision.rule == "outside_root_approved"
    assert len(seen_requests) == 1

    # 2. Shell command creating folder outside workspace also prompts and succeeds when approved
    decision = await guard.authorize(ShellTool(), {"command": f"mkdir -p {outside / 'sub'}"})
    assert decision.allowed is True
    assert decision.rule == "outside_root_approved"

    # 3. Heredoc shell command outside workspace names the real target path, not '<'
    seen_requests.clear()
    heredoc_cmd = f"cat << 'EOF' > {outside / 'note.txt'}\nhello\nEOF"
    decision = await guard.authorize(ShellTool(), {"command": heredoc_cmd})
    assert decision.allowed is True
    assert decision.rule == "outside_root_approved"
    assert len(seen_requests) == 1
    assert str(outside / "note.txt") in seen_requests[0].reason

    # 4. Sensitive path (.env) outside workspace is STILL unconditionally blocked without prompting
    seen_requests.clear()
    decision = await guard.authorize(WriteFileTool(), {"path": str(outside / ".env"), "content": "SECRET=1"})
    assert decision.allowed is False
    assert decision.rule == "sensitive_path"
    assert seen_requests == []


async def test_tui_todo_bar_sidebar_and_modal_track_tasks():
    agent = FakeAgent()
    app = CorvusTUIApp(agent=agent)

    async with app.run_test(size=(110, 34)) as pilot:
        await _wait_for(pilot, lambda: app._agent_ready)

        app._apply_todo_arguments(
            {
                "todos": [
                    {"content": "Inspect code", "status": "completed"},
                    {
                        "content": "Implement fix",
                        "status": "in_progress",
                        "activeForm": "Implementing fix",
                    },
                    {"content": "Run tests", "status": "pending"},
                ]
            }
        )
        await pilot.pause(0.05)

        bar = app.query_one("#todo-bar", TodoBar)
        assert bar.display is True
        sidebar_tasks = app.query_one("#sb-todos", Static)
        assert "1/3 complete" in str(sidebar_tasks.render())

        app.query_one("#input-area", InputArea).value = "/todos"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, InfoModalScreen))
        await pilot.press("escape")
        await pilot.pause(0.03)

        app.query_one("#input-area", InputArea).value = "/clear"
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert bar.display is False
        assert app._todos.items == []


class _MessageBlockApp(App[None]):
    def compose(self) -> ComposeResult:
        yield MessageBlock(MessageRole.ASSISTANT, turn_id="turn-1")


async def test_message_block_clears_intermediate_text_and_cleans_final_answer():
    app = _MessageBlockApp()

    async with app.run_test(size=(80, 24)) as pilot:
        block = app.query_one(MessageBlock)
        response = block.query_one("#msg-response", Static)

        block.append_response_chunk("intermediate ReAct text")
        await pilot.pause(0.1)
        assert block._response_buffer == "intermediate ReAct text"
        assert response.display is True

        block.clear_response()
        await pilot.pause(0.01)
        assert block._response_buffer == ""
        assert response.display is False

        block.set_final_markdown("Thought: internal\nFinal Answer: **done**")
        await pilot.pause(0.01)
        assert block._response_buffer == "**done**"
        assert response.display is True
