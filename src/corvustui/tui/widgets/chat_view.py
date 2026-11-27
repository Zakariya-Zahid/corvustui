"""Scrollable ChatView container with responsive wheel/scrollbar/keyboard scrolling."""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.scrollbar import ScrollDown, ScrollTo, ScrollUp

from corvustui.tui.widgets.message_block import MessageBlock, MessageRole


class ChatView(VerticalScroll):
    """Scrollable conversation stream with responsive scrollbar and smart auto-scroll."""

    BINDINGS = [
        Binding("up", "scroll_line_up", "Scroll Up", show=False),
        Binding("down", "scroll_line_down", "Scroll Down", show=False),
        Binding("pageup", "page_up", "Page Up", show=False),
        Binding("pagedown", "page_down", "Page Down", show=False),
        Binding("home", "scroll_top", "Top", show=False),
        Binding("end", "scroll_bottom", "Bottom", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._focus_mode: bool = False
        self._turn_blocks: dict[str, MessageBlock] = {}
        self._pinned: bool = True
        self._scroll_pending: bool = False
        self._programmatic_scroll: bool = False

    @property
    def focus_mode(self) -> bool:
        return self._focus_mode

    def toggle_focus_mode(self) -> bool:
        self._focus_mode = not self._focus_mode
        for child in self.query(MessageBlock):
            child.set_focus_mode(self._focus_mode)
        return self._focus_mode

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._pinned = False
        self._scroll_pending = False
        if self.allow_vertical_scroll:
            self.scroll_relative(y=-3, animate=False, immediate=True)
            event.prevent_default()
            event.stop()

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self.allow_vertical_scroll:
            self.scroll_relative(y=3, animate=False, immediate=True)
            if self.max_scroll_y <= 0 or self.scroll_y >= self.max_scroll_y - 0.5:
                self._pinned = True
            event.prevent_default()
            event.stop()

    def _on_scroll_up(self, event: ScrollUp) -> None:
        if self.allow_vertical_scroll:
            self.page_up_chat()
            event.prevent_default()
            event.stop()

    def _on_scroll_down(self, event: ScrollDown) -> None:
        if self.allow_vertical_scroll:
            self.page_down_chat()
            event.prevent_default()
            event.stop()

    def _on_scroll_to(self, message: ScrollTo) -> None:
        if self._allow_scroll:
            if message.y is not None and message.y < self.scroll_y:
                self._pinned = False
                self._scroll_pending = False
            self.scroll_to(message.x, message.y, animate=False, immediate=True)
            if self.max_scroll_y > 0 and self.scroll_y >= self.max_scroll_y - 0.5:
                self._pinned = True
            message.prevent_default()
            message.stop()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self._programmatic_scroll:
            return
        if new_value < old_value - 0.05:
            self._pinned = False
            self._scroll_pending = False
        elif self.max_scroll_y > 0 and new_value >= self.max_scroll_y - 0.5:
            self._pinned = True

    def scroll_if_pinned(self) -> None:
        if not self._pinned or self.is_vertical_scrollbar_grabbed:
            return
        if self._scroll_pending:
            return
        self._scroll_pending = True
        self.call_after_refresh(self._flush_scroll_end)

    def _flush_scroll_end(self) -> None:
        self._scroll_pending = False
        if not self._pinned or self.is_vertical_scrollbar_grabbed or not self.is_mounted:
            return
        self._programmatic_scroll = True
        try:
            self.scroll_end(animate=False, immediate=True)
        finally:
            self._programmatic_scroll = False

    def action_scroll_line_up(self) -> None:
        self.line_up_chat()

    def action_scroll_line_down(self) -> None:
        self.line_down_chat()

    def action_page_up(self) -> None:
        self.page_up_chat()

    def action_page_down(self) -> None:
        self.page_down_chat()

    def action_scroll_top(self) -> None:
        self._pinned = False
        self._scroll_pending = False
        self.scroll_home(animate=False, immediate=True)

    def action_scroll_bottom(self) -> None:
        self.jump_to_bottom()

    def line_up_chat(self, lines: int = 3) -> None:
        self._pinned = False
        self._scroll_pending = False
        self.scroll_relative(y=-lines, animate=False, immediate=True)

    def line_down_chat(self, lines: int = 3) -> None:
        self.scroll_relative(y=lines, animate=False, immediate=True)
        if self.max_scroll_y <= 0 or self.scroll_y >= self.max_scroll_y - 0.5:
            self._pinned = True

    def jump_to_bottom(self) -> None:
        self._pinned = True
        self._scroll_pending = False
        self.scroll_end(animate=False)

    def page_up_chat(self) -> None:
        self._pinned = False
        self._scroll_pending = False
        step = max(1, self.scrollable_content_region.height or self.size.height or 10)
        self.scroll_relative(y=-step, animate=False, immediate=True)

    def page_down_chat(self) -> None:
        step = max(1, self.scrollable_content_region.height or self.size.height or 10)
        self.scroll_relative(y=step, animate=False, immediate=True)
        if self.max_scroll_y <= 0 or self.scroll_y >= self.max_scroll_y - 0.5:
            self._pinned = True

    def half_page_up_chat(self) -> None:
        step = max(3, self.size.height // 2)
        self.line_up_chat(step)

    def half_page_down_chat(self) -> None:
        step = max(3, self.size.height // 2)
        self.line_down_chat(step)

    def append_user(self, text: str) -> MessageBlock:
        self._pinned = True
        block = MessageBlock(MessageRole.USER, initial_text=text)
        self.mount(block)
        self.scroll_if_pinned()
        return block

    def append_system(self, text: str) -> MessageBlock:
        block = MessageBlock(MessageRole.SYSTEM, initial_text=text)
        self.mount(block)
        self.scroll_if_pinned()
        return block

    def append_error(self, text: str) -> MessageBlock:
        block = MessageBlock(MessageRole.ERROR, initial_text=text)
        self.mount(block)
        self._pinned = True
        self.scroll_if_pinned()
        return block

    def start_assistant_turn(self, turn_id: str, model_label: str) -> MessageBlock:
        block = MessageBlock(
            MessageRole.ASSISTANT,
            sender_label=f"◈ {model_label}",
            turn_id=turn_id,
        )
        self._turn_blocks[turn_id] = block
        self.mount(block)
        block.set_focus_mode(self._focus_mode)
        self.scroll_if_pinned()
        return block

    def get_turn_block(self, turn_id: str) -> MessageBlock | None:
        return self._turn_blocks.get(turn_id)

    def clear_messages(self) -> None:
        self._turn_blocks.clear()
        self._pinned = True
        self._scroll_pending = False
        self.remove_children()
