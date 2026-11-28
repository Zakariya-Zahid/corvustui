"""Text cleanup and Markdown normalization shared by the CLI and TUI."""

from __future__ import annotations

import re

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)
_FINAL_ANSWER_RE = re.compile(
    r"(?:^|\n)\s*(?:Final\s+Answer|Answer)\s*:\s*",
    re.IGNORECASE,
)
_THOUGHT_PREFIX_RE = re.compile(r"^\s*Thought\s*:\s*", re.IGNORECASE)
_ROLE_PREFIX_RE = re.compile(
    r"^\s*(?:Assistant|CorvusTUI|Corvus)\s*:\s*",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s*(.*?)\s*#*\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_TABLE_RE = re.compile(r"^\s*\|")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")


def clean_final_answer(text: str) -> str:
    """Remove ReAct scaffolding from a model's final answer.

    The ReAct prompt asks for ``Thought`` / ``Final Answer`` sections.  Smaller
    models often stream the whole scaffold, so keep only the human-facing part.
    """
    if not text:
        return ""

    cleaned = _THINK_BLOCK_RE.sub("", text)

    final_match = None
    for match in _FINAL_ANSWER_RE.finditer(cleaned):
        final_match = match
    if final_match is not None:
        cleaned = cleaned[final_match.end():]
    else:
        cleaned = _THOUGHT_PREFIX_RE.sub("", cleaned, count=1)

    cleaned = _ROLE_PREFIX_RE.sub("", cleaned, count=1)
    return cleaned.strip()


def _ensure_blank(lines: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")


def normalize_markdown(text: str) -> str:
    """Give headings, lists, tables, and code fences consistent breathing room.

    Rich's Markdown renderer puts every block on the next line but does not add
    blank lines between them.  Models also frequently emit headings directly
    after a paragraph.  Normalizing the source makes the rendered answer easier
    to scan and keeps fenced code blocks intact.
    """
    if not text:
        return ""

    source = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    in_fence = False
    fence_marker = ""
    prev_kind = "start"

    for raw in source.split("\n"):
        line = raw.rstrip()

        if in_fence:
            out.append(line)
            if line.lstrip().startswith(fence_marker):
                in_fence = False
                prev_kind = "fence"
            continue

        if not line.strip():
            if out and out[-1] != "":
                out.append("")
            prev_kind = "blank"
            continue

        fence_match = _FENCE_RE.match(line)
        if fence_match:
            _ensure_blank(out)
            out.append(line)
            in_fence = True
            fence_marker = fence_match.group(1)
            prev_kind = "fence"
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            hashes, content = heading_match.group(1), heading_match.group(2).strip()
            if content:
                line = f"{hashes} {content}"
            _ensure_blank(out)
            out.append(line)
            out.append("")
            prev_kind = "heading"
            continue

        if _HR_RE.match(line):
            _ensure_blank(out)
            out.append(line)
            out.append("")
            prev_kind = "hr"
            continue

        if _LIST_RE.match(line):
            if prev_kind not in {"list", "blank", "start"}:
                _ensure_blank(out)
            out.append(line)
            prev_kind = "list"
            continue

        if _QUOTE_RE.match(line):
            if prev_kind not in {"quote", "blank", "start"}:
                _ensure_blank(out)
            out.append(line)
            prev_kind = "quote"
            continue

        if _TABLE_RE.match(line):
            if prev_kind not in {"table", "blank", "start"}:
                _ensure_blank(out)
            out.append(line)
            prev_kind = "table"
            continue

        # Indented continuation of a list item, not a new paragraph.
        if prev_kind == "list" and raw.startswith(("  ", "\t")):
            out.append(line)
            continue

        if prev_kind in {"list", "quote", "table", "fence", "heading", "hr"}:
            _ensure_blank(out)
        out.append(line)
        prev_kind = "paragraph"

    while out and out[-1] == "":
        out.pop()

    return "\n".join(out)


__all__ = ["clean_final_answer", "normalize_markdown"]
