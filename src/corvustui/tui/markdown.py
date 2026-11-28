"""Rich Markdown rendering tuned for the CorvusTUI chat canvas."""

from __future__ import annotations

from rich.markdown import Heading, Markdown, MarkdownContext
from rich.text import Text

from corvustui.formatting import normalize_markdown

HEADING_STYLES = {
    "h1": "bold #fab283",
    "h2": "bold #7dcfff",
    "h3": "bold #bb9af7",
    "h4": "bold #9ece6a",
    "h5": "bold #c8ccd9",
    "h6": "bold #8c91a4",
}


class CorvusHeading(Heading):
    """Left-aligned, color-coded headings (Rich centers h1 by default)."""

    LEVEL_ALIGN = {tag: "left" for tag in HEADING_STYLES}

    def on_enter(self, context: MarkdownContext) -> None:
        self.text = Text()
        context.enter_style(HEADING_STYLES.get(self.tag, "bold #e4e6f0"))


class CorvusMarkdown(Markdown):
    """Markdown renderer with CorvusTUI heading styles."""

    elements = {**Markdown.elements, "heading_open": CorvusHeading}


def render_markdown(text: str, *, style: str = "#e4e6f0") -> CorvusMarkdown:
    """Normalize spacing and return a renderable Markdown document."""
    return CorvusMarkdown(normalize_markdown(text), style=style)


__all__ = ["CorvusHeading", "CorvusMarkdown", "render_markdown"]
