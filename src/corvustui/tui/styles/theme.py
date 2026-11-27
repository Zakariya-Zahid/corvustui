"""Theme definitions and color palettes inspired by Claude Code and OpenCode."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TUITheme:
    """Color palette and visual tokens."""

    name: str
    bg: str
    surface: str
    surface_hover: str
    border_normal: str
    border_focused: str
    border_dim: str

    primary: str           # Accent / Assistant border (gold/orange or amber)
    secondary: str         # User border / accents (blue or cyan)
    accent: str            # General highlights (purple/violet)
    
    text: str
    text_muted: str
    text_dim: str

    success: str           # Green for completed tools, diff additions
    warning: str           # Amber for active thinking/waiting
    error: str             # Red for errors, diff deletions
    info: str              # Cyan for informational badges

    diff_add_bg: str
    diff_add_fg: str
    diff_del_bg: str
    diff_del_fg: str


# OpenCode default dark theme palette (refined for Textual)
OPENCODE_DARK = TUITheme(
    name="opencode",
    bg="#1e1e24",
    surface="#262730",
    surface_hover="#2f313d",
    border_normal="#3b3d4d",
    border_focused="#fab283",
    border_dim="#2d2f3b",
    primary="#fab283",        # Warm peach/orange
    secondary="#5c9cf5",      # Crisp sky blue
    accent="#bb9af7",         # Lavender
    text="#e2e4ed",
    text_muted="#8c91a4",
    text_dim="#5c6070",
    success="#7fd88f",        # Pastel green
    warning="#f1fa8c",        # Soft amber
    error="#f7768e",          # Coral red
    info="#7dcfff",           # Cyan
    diff_add_bg="#1e3a29",
    diff_add_fg="#85e89d",
    diff_del_bg="#3d1d24",
    diff_del_fg="#f97583",
)

# Tokyo Night theme
TOKYO_NIGHT = TUITheme(
    name="tokyonight",
    bg="#1a1b26",
    surface="#24283b",
    surface_hover="#2f354f",
    border_normal="#3b4261",
    border_focused="#7aa2f7",
    border_dim="#292e42",
    primary="#7aa2f7",
    secondary="#7dcfff",
    accent="#bb9af7",
    text="#c0caf5",
    text_muted="#565f89",
    text_dim="#414868",
    success="#9ece6a",
    warning="#e0af68",
    error="#f7768e",
    info="#7dcfff",
    diff_add_bg="#1f3833",
    diff_add_fg="#9ece6a",
    diff_del_bg="#3b242e",
    diff_del_fg="#f7768e",
)

# Claude Code inspired theme
CLAUDE_DARK = TUITheme(
    name="claude",
    bg="#18181b",
    surface="#27272a",
    surface_hover="#3f3f46",
    border_normal="#3f3f46",
    border_focused="#d97706",
    border_dim="#27272a",
    primary="#f59e0b",        # Anthropic warm amber
    secondary="#38bdf8",      # Sky blue
    accent="#a855f7",         # Purple
    text="#f4f4f5",
    text_muted="#a1a1aa",
    text_dim="#71717a",
    success="#22c55e",
    warning="#eab308",
    error="#ef4444",
    info="#06b6d4",
    diff_add_bg="#14532d",
    diff_add_fg="#86efac",
    diff_del_bg="#7f1d1d",
    diff_del_fg="#fca5a5",
)

THEMES: dict[str, TUITheme] = {
    "opencode": OPENCODE_DARK,
    "tokyonight": TOKYO_NIGHT,
    "claude": CLAUDE_DARK,
}

CURRENT_THEME = OPENCODE_DARK
