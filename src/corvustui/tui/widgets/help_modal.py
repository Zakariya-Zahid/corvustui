"""Help modal overlay displaying commands, queue syntax, and keybindings."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class HelpScreen(ModalScreen[None]):
    """Interactive help dialog triggered by Ctrl+? or /help."""

    CSS = """
    HelpScreen {
        align: center middle;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        help_text = (
            "Slash Commands:\n"
            "  /commands              Open Commands Palette (Ctrl+P)\n"
            "  /connect               Connect cloud/local provider & API keys\n"
            "  /models [name]         Open model selector or switch model\n"
            "  /wait <sec> <prompt>   Schedule a prompt after waiting N seconds\n"
            "  /parallel <prompt>     Queue a prompt for parallel execution\n"
            "  /queue [pause|resume|run|clear]  Manage queued prompts\n"
            "  /focus                 Toggle compact focus mode (hide thinking)\n"
            "  /sidebar               Toggle right workspace sidebar\n"
            "  /stats                 Show token usage and memory stats\n"
            "  /tools /mcp /safety    Inspect tools, MCP servers, and guardrails\n"
            "  /todos                 Show the agent's task list\n"
            "  /clear                 Clear chat and context\n"
            "  /quit                  Exit CorvusTUI\n\n"
            "Keyboard Shortcuts:\n"
            "  Ctrl+P                 Open Commands Palette\n"
            "  Ctrl+O                 Open Select Model modal (local & cloud)\n"
            "  Ctrl+A (in model box)  Connect a provider / API key\n"
            "  Enter                  Submit prompt (queues automatically if busy)\n"
            "  Shift+Enter / \\+Enter  Insert newline\n"
            "  Up / Down              Navigate prompt history or / commands\n"
            "  Tab                    Autocomplete / command\n"
            "  Click Thinking/Tool    Expand or collapse streaming blocks\n"
            "  Ctrl+F                 Toggle focus mode\n"
            "  Ctrl+B                 Toggle sidebar\n"
            "  Ctrl+L                 Clear chat log\n"
            "  PageUp / PageDown      Scroll chat history (or Ctrl+U / Ctrl+D)\n"
            "  Ctrl+Home / Ctrl+End   Jump to top / bottom of chat\n"
            "  Ctrl+C                 Cancel active generation (or quit when idle)\n"
        )
        with Container(id="help-dialog"):
            yield Label("CorvusTUI Help & Shortcuts", markup=False)
            yield Static(help_text, markup=False)
            yield Button("Close (Esc)", id="help-close", variant="primary")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
