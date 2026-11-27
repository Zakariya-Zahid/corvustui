"""Token meter and formatting helpers matching Claude Code & OpenCode."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


def format_tokens(count: int) -> str:
    """Format token count cleanly (e.g. 850, 3.2K, 1.1M)."""
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        val = count / 1000.0
        return f"{val:.1f}K" if val < 100 else f"{int(val)}K"
    return f"{count / 1_000_000:.1f}M"


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Best-effort session cost estimation based on common model pricing."""
    m = model.lower()
    if "ollama" in m or (":" in m and not m.startswith(("gpt", "claude", "gemini"))):
        return 0.0
    if "opus" in m:
        in_rate, out_rate = 15.0 / 1_000_000, 75.0 / 1_000_000
    elif "sonnet" in m or "claude-3" in m:
        in_rate, out_rate = 3.0 / 1_000_000, 15.0 / 1_000_000
    elif "haiku" in m or "flash" in m or "mini" in m:
        in_rate, out_rate = 0.25 / 1_000_000, 1.25 / 1_000_000
    elif "gpt-4o" in m or "gpt-4" in m:
        in_rate, out_rate = 2.5 / 1_000_000, 10.0 / 1_000_000
    else:
        return 0.0
    return prompt_tokens * in_rate + completion_tokens * out_rate


class TokenMeter(Static):
    """Crisp visual context gauge: `▰▰▱▱▱▱▱▱ 2.1K/8.2K (26%)`."""

    def __init__(self, used: int = 0, maximum: int = 8192, **kwargs) -> None:
        super().__init__(**kwargs)
        self.used = used
        self.maximum = max(1, maximum)

    def on_mount(self) -> None:
        self._render_meter()

    def set_usage(self, used: int, maximum: int) -> None:
        self.used = max(0, used)
        self.maximum = max(1, maximum)
        self._render_meter()

    def _render_meter(self) -> None:
        ratio = min(1.0, self.used / self.maximum)
        pct = int(ratio * 100)
        width = 8
        filled = round(ratio * width)

        if pct >= 90:
            color = "#f7768e"
            prefix = "⚠ "
        elif pct >= 80:
            color = "#e0af68"
            prefix = "⚠ "
        elif pct >= 55:
            color = "#f1fa8c"
            prefix = ""
        else:
            color = "#7fd88f"
            prefix = ""

        renderable = Text()
        renderable.append(f"{prefix}Ctx ", style="#6b7085")
        if filled > 0:
            renderable.append("━" * filled, style=f"bold {color}")
        if width - filled > 0:
            renderable.append("─" * (width - filled), style="#323548")
        renderable.append(
            f" {format_tokens(self.used)}/{format_tokens(self.maximum)} ({pct}%)",
            style=color if pct >= 80 else "#c8ccd9",
        )
        self.update(renderable)
