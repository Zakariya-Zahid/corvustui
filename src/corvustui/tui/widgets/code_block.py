"""Syntax-highlighted code block widget with line numbers and truncation."""

from __future__ import annotations

from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual.widgets import Static

EXTENSION_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".rs": "rust",
    ".go": "go",
    ".css": "css",
    ".tcss": "css",
    ".html": "html",
    ".sql": "sql",
    ".bend": "python",
}


def detect_language(path: str | None, fallback: str = "text") -> str:
    if not path:
        return fallback
    suffix = Path(path).suffix.lower()
    return EXTENSION_LANG_MAP.get(suffix, fallback)


class CodeBlock(Static):
    """Syntax-highlighted code snippet with optional line-limit truncation."""

    def __init__(
        self,
        code: str,
        *,
        language: str = "text",
        line_numbers: bool = True,
        max_lines: int | None = 10,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._code = code
        self._language = language
        self._line_numbers = line_numbers
        self._max_lines = max_lines
        self._expanded = max_lines is None

    def on_mount(self) -> None:
        self._render_code()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._render_code()

    @property
    def total_lines(self) -> int:
        return len(self._code.splitlines())

    def _render_code(self) -> None:
        lines = self._code.splitlines()
        shown_code = self._code
        truncated_count = 0
        if not self._expanded and self._max_lines is not None and len(lines) > self._max_lines:
            shown_code = "\n".join(lines[: self._max_lines])
            truncated_count = len(lines) - self._max_lines

        try:
            syntax = Syntax(
                shown_code,
                self._language,
                theme="monokai",
                line_numbers=self._line_numbers,
                word_wrap=True,
                background_color="#1a1b22",
            )
            self.update(syntax)
        except Exception:
            text = Text(shown_code)
            if truncated_count:
                text.append(f"\n... ({truncated_count} more lines)", style="dim")
            self.update(text)
