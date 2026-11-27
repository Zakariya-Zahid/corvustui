"""Unified diff viewer widget with green/red line styling and diffstats."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


def compute_diff_stats(diff_text: str) -> tuple[int, int]:
    """Return (added_lines, removed_lines) from a unified diff string."""
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


class DiffView(Static):
    """Renders unified diffs with colored additions/deletions and hunk headers."""

    def __init__(self, diff_text: str, *, max_lines: int | None = 14, **kwargs) -> None:
        super().__init__(**kwargs)
        self._diff_text = diff_text
        self._max_lines = max_lines
        self._expanded = max_lines is None

    def on_mount(self) -> None:
        self._render_diff()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._render_diff()

    def _render_diff(self) -> None:
        lines = self._diff_text.splitlines()
        if not self._expanded and self._max_lines is not None and len(lines) > self._max_lines:
            lines = lines[: self._max_lines]

        rendered = Text()
        for idx, line in enumerate(lines):
            if idx > 0:
                rendered.append("\n")
            if line.startswith("+++") or line.startswith("---"):
                rendered.append(line, style="bold #8c91a4")
            elif line.startswith("@@"):
                rendered.append(line, style="bold #7dcfff")
            elif line.startswith("+"):
                rendered.append(line, style="#85e89d on #1e3a29")
            elif line.startswith("-"):
                rendered.append(line, style="#f97583 on #3d1d24")
            else:
                rendered.append(line, style="#8c91a4")

        self.update(rendered)
