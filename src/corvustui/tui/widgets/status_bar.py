"""Single-line OpenCode/Claude-inspired StatusBar widget."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from corvustui.tui.widgets.token_meter import TokenMeter, estimate_cost_usd


class StatusBar(Widget):
    """Bottom status bar: ^? help | Context meter | Cost | State | Model."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state: str = "connecting"
        self._detail: str = "Starting..."
        self._provider: str = "unknown"
        self._model: str = "unknown"
        self._mcp_count: int = 0
        self._session_cost: float = 0.0
        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0

    def compose(self) -> ComposeResult:
        yield Static(" ^? help ", classes="status-help", markup=False)
        yield TokenMeter(id="status-token-meter", classes="status-context")
        yield Static("Cost: $0.000", id="status-cost", classes="status-context", markup=False)
        yield Static("⚪ Starting...", id="status-state", classes="status-state", markup=False)
        yield Static(" ⌬ unknown ", id="status-model", classes="status-model", markup=False)

    def set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        self._detail = detail or state.capitalize()
        self._refresh_state()

    def set_model_info(self, provider: str, model: str, mcp_count: int = 0) -> None:
        self._provider = provider
        self._model = model
        self._mcp_count = mcp_count
        try:
            self.query_one("#status-model", Static).update(
                f" ⌬ {provider}/{model} "
            )
        except Exception:
            pass
        self._refresh_state()

    def set_tokens(self, used: int, maximum: int, add_completion_tokens: int = 0) -> None:
        try:
            self.query_one("#status-token-meter", TokenMeter).set_usage(used, maximum)
        except Exception:
            pass

        if add_completion_tokens > 0:
            self._completion_tokens += add_completion_tokens
            self._prompt_tokens = max(self._prompt_tokens, used)
            self._session_cost = estimate_cost_usd(
                self._model, self._prompt_tokens, self._completion_tokens
            )
        try:
            self.query_one("#status-cost", Static).update(
                f"Cost: ${self._session_cost:.4f}" if self._session_cost > 0 else "Cost: $0.00"
            )
        except Exception:
            pass

    def _refresh_state(self) -> None:
        try:
            state_widget = self.query_one("#status-state", Static)
        except Exception:
            return

        badge_map = {
            "ready": ("🟢", "#7fd88f"),
            "thinking": ("🟡", "#f1fa8c"),
            "tool": ("🔵", "#7dcfff"),
            "streaming": ("🔵", "#5c9cf5"),
            "waiting": ("⏳", "#bb9af7"),
            "error": ("🔴", "#f7768e"),
            "connecting": ("⚪", "#8c91a4"),
        }
        icon, color = badge_map.get(self._state, ("⚪", "#8c91a4"))
        t = Text()
        t.append(f"{icon} {self._detail}", style=color)
        if self._mcp_count > 0:
            t.append(f"  │ MCP: {self._mcp_count}", style="dim")
        state_widget.update(t)
