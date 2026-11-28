"""Tests for final-answer cleanup and Markdown spacing normalization."""

from __future__ import annotations

from corvustui.formatting import clean_final_answer, normalize_markdown


def test_clean_final_answer_keeps_only_the_human_facing_part():
    raw = (
        "Thought: I should answer directly.\n"
        "Final Answer: **Hello**\n\n"
        "- one\n"
        "- two"
    )
    assert clean_final_answer(raw) == "**Hello**\n\n- one\n- two"


def test_clean_final_answer_removes_thinking_blocks():
    raw = "Before <thi" "nk>secret</thi" "nk> and after."
    assert clean_final_answer(raw) == "Before  and after."


def test_clean_final_answer_strips_role_and_thought_prefixes():
    assert clean_final_answer("Assistant: hi there") == "hi there"
    assert clean_final_answer("Thought: plain answer") == "plain answer"
    assert clean_final_answer("") == ""


def test_normalize_markdown_adds_spacing_around_headings():
    assert normalize_markdown("Intro\n# Title\nBody") == "Intro\n\n# Title\n\nBody"


def test_normalize_markdown_does_not_touch_fenced_code():
    text = "Before\n```python\n# not a heading\nprint('x')\n```\nAfter"
    result = normalize_markdown(text)

    assert "```python\n# not a heading\nprint('x')\n```" in result
    assert result == "Before\n\n```python\n# not a heading\nprint('x')\n```\n\nAfter"


def test_normalize_markdown_groups_list_items_and_separates_paragraphs():
    assert normalize_markdown("- one\n- two\nAfter") == "- one\n- two\n\nAfter"


def test_normalize_markdown_is_idempotent():
    text = "# Title\n\n- a\n- b\n\n```py\nx = 1\n```\n\nDone"
    once = normalize_markdown(text)
    assert normalize_markdown(once) == once


def test_normalize_markdown_handles_empty_and_blank_input():
    assert normalize_markdown("") == ""
    assert normalize_markdown("\n\n") == ""
