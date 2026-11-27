"""MessageBlock widget with compact editorial styling, embedded thinking/tools, and live Markdown."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

from corvustui.safety import redact_secrets
from corvustui.tui.widgets.thinking_block import ThinkingBlock
from corvustui.tui.widgets.tool_block import ToolBlock


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    ERROR = "error"


class MessageBlock(Widget):
    """Compact conversation block containing header, thinking/tools, response, and finish badge."""

    def __init__(
        self,
        role: MessageRole,
        *,
        sender_label: str | None = None,
        initial_text: str = "",
        turn_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.role = role
        self.turn_id = turn_id
        self._sender_label = sender_label or self._default_label(role)
        self._timestamp = datetime.now().strftime("%H:%M:%S")
        self._initial_text = initial_text
        self._response_buffer: str = initial_text if role == MessageRole.ASSISTANT else ""
        self._thinking_blocks: dict[int, ThinkingBlock] = {}
        self._tool_blocks: dict[str, ToolBlock] = {}
        self._pending_mounts: list[Widget] = []
        self._footer_text: str | None = None
        self._compact_focus: bool = False
        self._render_scheduled: bool = False

    @staticmethod
    def _default_label(role: MessageRole) -> str:
        return {
            MessageRole.USER: "› You",
            MessageRole.ASSISTANT: "◈ Assistant",
            MessageRole.SYSTEM: "System",
            MessageRole.ERROR: "✖ Error",
        }[role]

    def compose(self) -> ComposeResult:
        if self.role != MessageRole.SYSTEM:
            with Horizontal(classes="msg-header"):
                yield Static(
                    self._sender_label,
                    classes=f"msg-sender msg-sender-{self.role.value}",
                    markup=False,
                )
                yield Static(self._timestamp, classes="msg-timestamp", markup=False)

        yield Vertical(classes="msg-body", id="msg-body")
        yield Static("", id="msg-response", classes="msg-markdown", markup=False)
        yield Static("", classes="msg-footer", id="msg-footer", markup=False)

    def on_mount(self) -> None:
        self.add_class(f"role-{self.role.value}")

        # The block may be removed between compose and mount (for example when
        # startup clears the chat), in which case its composed body is already
        # gone.  Treat that as a no-op instead of crashing the TUI.
        try:
            body = self.query_one("#msg-body", Vertical)
        except Exception:
            return
        if self._pending_mounts:
            for child in self._pending_mounts:
                body.mount(child)
            self._pending_mounts.clear()

        if self._initial_text and self.role != MessageRole.ASSISTANT:
            body.display = True
            if self.role == MessageRole.SYSTEM:
                body.mount(Static(Text(redact_secrets(self._initial_text), style="dim"), classes="msg-markdown", markup=False))
            elif self.role == MessageRole.ERROR:
                body.mount(Static(Text(redact_secrets(self._initial_text), style="bold #f7768e"), classes="msg-markdown", markup=False))
            else:
                body.mount(Static(Text(redact_secrets(self._initial_text)), classes="msg-markdown", markup=False))

        self._refresh_body_visibility()
        self._refresh_response()
        self._refresh_footer()

    def _refresh_body_visibility(self) -> None:
        if not self.is_mounted:
            return
        try:
            body = self.query_one("#msg-body", Vertical)
            has_content = bool(self._thinking_blocks or self._tool_blocks or (self._initial_text and self.role != MessageRole.ASSISTANT))
            body.display = has_content
        except Exception:
            pass

    def _mount_in_body(self, widget: Widget) -> None:
        if not self.is_mounted:
            self._pending_mounts.append(widget)
            return
        try:
            body = self.query_one("#msg-body", Vertical)
            body.display = True
            body.mount(widget)
        except Exception:
            self._pending_mounts.append(widget)

    # -- Thinking integration ----------------------------------------------

    def start_thinking(self, iteration: int, *, expanded: bool = False) -> ThinkingBlock:
        if iteration in self._thinking_blocks:
            return self._thinking_blocks[iteration]
        block = ThinkingBlock(iteration=iteration, expanded=expanded)
        block.display = not self._compact_focus
        self._thinking_blocks[iteration] = block
        self._mount_in_body(block)
        return block

    def append_thinking(self, iteration: int, text: str) -> None:
        block = self._thinking_blocks.get(iteration) or self.start_thinking(iteration)
        block.append_chunk(text)

    def complete_thinking(self, iteration: int, full_text: str | None = None) -> None:
        block = self._thinking_blocks.get(iteration)
        if block is not None:
            block.complete(full_text)

    # -- Tool call integration ---------------------------------------------

    def start_tool(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolBlock:
        if call_id in self._tool_blocks:
            return self._tool_blocks[call_id]
        block = ToolBlock(call_id=call_id, tool_name=tool_name, arguments=arguments)
        self._tool_blocks[call_id] = block
        self._mount_in_body(block)
        return block

    def complete_tool(
        self,
        call_id: str,
        output: str,
        *,
        success: bool,
        duration_ms: float = 0.0,
    ) -> None:
        block = self._tool_blocks.get(call_id)
        if block is not None:
            block.complete(output, success=success, duration_ms=duration_ms)

    # -- Streaming & final Markdown response -------------------------------

    def append_response_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self._response_buffer += chunk
        if not self.is_mounted or self._render_scheduled:
            return
        self._render_scheduled = True
        self.set_timer(0.06, self._flush_response_render)

    def _flush_response_render(self) -> None:
        self._render_scheduled = False
        self._refresh_response()

    def set_final_markdown(self, text: str) -> None:
        self._response_buffer = text
        self._render_scheduled = False
        self._refresh_response()

    def _refresh_response(self) -> None:
        if not self.is_mounted:
            return
        try:
            widget = self.query_one("#msg-response", Static)
        except Exception:
            return
        if not self._response_buffer:
            widget.display = False
            return
        widget.display = True
        safe = redact_secrets(self._response_buffer)
        try:
            widget.update(Markdown(safe))
        except Exception:
            widget.update(Text(safe))

    def set_finish_info(self, model: str, duration_s: float, tokens: int = 0) -> None:
        """Display OpenCode-style compact finish info line below assistant message."""
        info = f"✓ {model} · {duration_s:.2f}s"
        if tokens > 0:
            info += f" · {tokens} tok"
        self._footer_text = info
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        if not self.is_mounted:
            return
        try:
            footer = self.query_one("#msg-footer", Static)
        except Exception:
            return
        if not self._footer_text:
            footer.display = False
            return
        footer.update(Text(self._footer_text, style="#565a6e"))
        footer.display = True

    def set_focus_mode(self, compact: bool) -> None:
        """Toggle Claude Code-style /focus mode (hides thinking blocks)."""
        self._compact_focus = compact
        for block in self._thinking_blocks.values():
            block.display = not compact
