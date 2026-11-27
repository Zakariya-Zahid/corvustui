"""OpenCode-inspired InputArea with interactive `/` command popup, history, and model meta."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from corvustui.tui.controllers.history import PromptHistory

# (command, description, needs_arg)
SLASH_COMMANDS: list[tuple[str, str, bool]] = [
    ("/commands", "Open commands palette (Ctrl+P)", False),
    ("/connect", "Connect cloud/local provider & API keys", False),
    ("/models", "Open model selector across connected providers", False),
    ("/wait", "Schedule delayed prompt (/wait <sec> <prompt>)", True),
    ("/parallel", "Run prompt in parallel (/parallel <prompt>)", True),
    ("/queue", "Open queue manager modal (/queue [pause|resume|run|clear])", False),
    ("/focus", "Toggle focus mode (hide/show thinking)", False),
    ("/sidebar", "Toggle workspace sidebar", False),
    ("/clear", "Clear conversation context & chat", False),
    ("/stats", "Open context, token & memory stats modal", False),
    ("/tools", "Open searchable tools modal", False),
    ("/mcp", "Open MCP server status modal", False),
    ("/safety", "Open active safety policy modal", False),
    ("/help", "Open commands & shortcuts modal", False),
    ("/quit", "Exit CorvusTUI", False),
]

_NEEDS_ARG: dict[str, bool] = {cmd: needs for cmd, _, needs in SLASH_COMMANDS}


class PromptSubmitted(Message):
    """Emitted when the user submits a prompt via Enter or Ctrl+S."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class PromptTextArea(TextArea):
    """Inner TextArea supporting Enter-to-submit, Shift+Enter / \\+Enter newline, and history."""

    def __init__(self, history: PromptHistory, **kwargs) -> None:
        super().__init__(**kwargs)
        self.prompt_history = history
        self.show_line_numbers = False

    def _parent_input_area(self) -> InputArea | None:
        node = self.parent
        while node is not None:
            if isinstance(node, InputArea):
                return node
            node = node.parent
        return None

    async def _on_key(self, event: events.Key) -> None:
        area = self._parent_input_area()
        popup_open = area is not None and area.is_autocomplete_open()

        if event.key in (
            "pageup",
            "pagedown",
            "ctrl+up",
            "ctrl+down",
            "ctrl+u",
            "ctrl+d",
            "ctrl+home",
            "ctrl+end",
        ):
            # Let app-level chat scroll bindings handle viewport scrolling
            return

        if event.key in ("shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return

        if popup_open and area is not None:
            if event.key == "down":
                event.prevent_default()
                event.stop()
                area.move_autocomplete(1)
                return
            if event.key == "up":
                event.prevent_default()
                event.stop()
                area.move_autocomplete(-1)
                return
            if event.key == "tab":
                event.prevent_default()
                event.stop()
                area.apply_autocomplete(submit=False)
                return
            if event.key == "enter":
                event.prevent_default()
                event.stop()
                area.apply_autocomplete(submit=True)
                return
            if event.key == "escape":
                event.prevent_default()
                event.stop()
                area.hide_autocomplete()
                return

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            current = self.text
            if current.endswith("\\"):
                self.text = current[:-1] + "\n"
                self.move_cursor((len(self.text.splitlines()) - 1, 0))
                return
            cleaned = current.strip()
            if cleaned:
                self.prompt_history.add(cleaned)
                self.text = ""
                self.post_message(PromptSubmitted(cleaned))
            return

        if event.key == "ctrl+s":
            event.prevent_default()
            event.stop()
            cleaned = self.text.strip()
            if cleaned:
                self.prompt_history.add(cleaned)
                self.text = ""
                self.post_message(PromptSubmitted(cleaned))
            return

        if event.key == "up" and self.cursor_location[0] == 0:
            prev = self.prompt_history.previous(self.text)
            if prev is not None:
                event.prevent_default()
                event.stop()
                self.text = prev
                self.move_cursor((0, len(prev)))
            return

        if event.key == "down":
            line_count = max(1, len(self.text.splitlines()))
            if self.cursor_location[0] >= line_count - 1:
                nxt = self.prompt_history.next()
                if nxt is not None:
                    event.prevent_default()
                    event.stop()
                    self.text = nxt
                    last_line = nxt.splitlines()[-1] if nxt else ""
                    self.move_cursor((max(0, len(nxt.splitlines()) - 1), len(last_line)))
                return

        if event.key == "escape" and self.text:
            event.prevent_default()
            event.stop()
            self.text = ""
            self.prompt_history.reset_navigation()
            return

        await super()._on_key(event)


class InputArea(Vertical):
    """OpenCode-style thick left-bordered prompt box with interactive `/` command popup."""

    def __init__(self, history: PromptHistory | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.history = history or PromptHistory()
        self._busy = False
        self._provider = "ollama"
        self._model = "unknown"

    def compose(self) -> ComposeResult:
        yield OptionList(id="autocomplete-popup", classes="opencode-list hidden")
        with Vertical(id="input-card"):
            with Horizontal(id="input-row"):
                yield Static("❯", id="prompt-gutter", markup=False)
                yield PromptTextArea(
                    self.history,
                    id="prompt-input",
                    placeholder="Ask anything... (/ commands · Shift+Enter newline)",
                )
            yield Static("", id="input-meta", markup=False)
        with Horizontal(id="input-hint-row"):
            yield Static("", id="input-cwd", markup=False)
            yield Static(
                "ctrl+p commands   ctrl+o models   /connect providers",
                id="input-hint",
                markup=False,
            )

    def on_mount(self) -> None:
        self._refresh_meta()
        self._update_autocomplete("")

    def focus_input(self) -> None:
        try:
            self.query_one("#prompt-input", PromptTextArea).focus()
        except Exception:
            pass

    @property
    def value(self) -> str:
        try:
            return self.query_one("#prompt-input", PromptTextArea).text
        except Exception:
            return ""

    @value.setter
    def value(self, text: str) -> None:
        try:
            ta = self.query_one("#prompt-input", PromptTextArea)
            ta.text = text
        except Exception:
            pass

    def set_model_meta(self, provider: str, model: str, cwd_short: str = "") -> None:
        self._provider = provider
        self._model = model
        if cwd_short:
            try:
                self.query_one("#input-cwd", Static).update(cwd_short)
            except Exception:
                pass
        self._refresh_meta()

    def set_busy(self, busy: bool, queue_count: int = 0) -> None:
        self._busy = busy
        try:
            gutter = self.query_one("#prompt-gutter", Static)
            if busy:
                gutter.update("⏳" if queue_count else "⚡")
            else:
                gutter.update("❯")
        except Exception:
            pass
        self._refresh_meta(queue_count)

    def _refresh_meta(self, queue_count: int = 0) -> None:
        try:
            meta = self.query_one("#input-meta", Static)
        except Exception:
            return
        line = Text()
        if self._busy:
            line.append("Working ", style="bold #e0af68")
            line.append(f"· {self._model} ", style="#e4e6f0")
            line.append(f"{self._provider}", style="dim")
            if queue_count:
                line.append(f"  ·  {queue_count} queued (Enter to queue more)", style="#7dcfff")
            else:
                line.append("  ·  Enter to queue next prompt · ^C cancel", style="dim")
        else:
            line.append("Corvus ", style="bold #5c9cf5")
            line.append(f"· {self._model} ", style="#e4e6f0")
            line.append(self._provider, style="dim")
        meta.update(line)

    # -- Interactive / Command Autocomplete Popup --------------------------

    def is_autocomplete_open(self) -> bool:
        try:
            popup = self.query_one("#autocomplete-popup", OptionList)
            return not popup.has_class("hidden") and popup.option_count > 0
        except Exception:
            return False

    def hide_autocomplete(self) -> None:
        try:
            self.query_one("#autocomplete-popup", OptionList).add_class("hidden")
        except Exception:
            pass

    def move_autocomplete(self, delta: int) -> None:
        try:
            popup = self.query_one("#autocomplete-popup", OptionList)
            if delta > 0:
                popup.action_cursor_down()
            else:
                popup.action_cursor_up()
        except Exception:
            pass

    def apply_autocomplete(self, *, submit: bool) -> None:
        try:
            popup = self.query_one("#autocomplete-popup", OptionList)
            ta = self.query_one("#prompt-input", PromptTextArea)
        except Exception:
            return

        if popup.highlighted is None:
            return
        opt = popup.get_option_at_index(popup.highlighted)
        cmd = opt.id
        if not cmd:
            return

        popup.add_class("hidden")
        needs_arg = _NEEDS_ARG.get(cmd, False)
        if submit and not needs_arg:
            self.history.add(cmd)
            ta.text = ""
            self.post_message(PromptSubmitted(cmd))
        else:
            ta.text = f"{cmd} "
            ta.move_cursor((0, len(ta.text)))
            ta.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "autocomplete-popup" and event.option.id:
            self.apply_autocomplete(submit=True)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._update_autocomplete(event.text_area.text)

    def _update_autocomplete(self, text: str) -> None:
        try:
            popup = self.query_one("#autocomplete-popup", OptionList)
        except Exception:
            return

        stripped = text.lstrip()
        if stripped.startswith("/") and "\n" not in stripped and " " not in stripped:
            matches = [
                (cmd, desc)
                for cmd, desc, _ in SLASH_COMMANDS
                if cmd.startswith(stripped.lower())
            ]
            if matches:
                popup.clear_options()
                for cmd, desc in matches:
                    row = Text()
                    row.append(f"{cmd:<18}", style="bold")
                    row.append(desc, style="dim")
                    popup.add_option(Option(row, id=cmd))
                popup.highlighted = 0
                popup.remove_class("hidden")
                return

        popup.add_class("hidden")
