"""Tests for context window management."""

from __future__ import annotations

import pytest

from corvustui.context.manager import ContextManager
from corvustui.providers import MessageRole
from tests.fakes import FakeProvider, text_response


def build_context(provider, max_tokens=40, reserved_tokens=0) -> ContextManager:
    return ContextManager(
        provider=provider,
        max_tokens=max_tokens,
        reserved_tokens=reserved_tokens,
    )


def test_get_history_keeps_summary_but_omits_main_system_prompt():
    context = build_context(FakeProvider([]))
    context.add_system("main prompt")
    context._summary = "older facts"
    context.add_user("recent")

    history = context.get_history()

    assert [message.role for message in history] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]
    assert "Previous conversation summary" in (history[0].content or "")
    assert "older facts" in (history[0].content or "")
    assert history[1].content == "recent"


def test_context_stats_track_token_usage():
    provider = FakeProvider([])
    context = build_context(provider, max_tokens=100, reserved_tokens=20)
    context.add_user("hello world")

    stats = context.get_stats()

    assert stats.total_tokens == provider.count_messages_tokens(context.get_all_messages())
    assert stats.max_tokens == 100
    assert stats.reserved_tokens == 20
    assert stats.available_tokens == 80
    assert stats.message_count == 1
    assert 0 < stats.usage_ratio < 1


def test_force_slide_window_keeps_recent_messages():
    context = build_context(FakeProvider([]))
    for index in range(6):
        context.add_user(f"message {index}")

    context.force_slide_window(2)

    assert [message.content for message in context.messages] == [
        "message 4",
        "message 5",
    ]


@pytest.mark.asyncio
async def test_summarization_replaces_old_messages_with_summary():
    provider = FakeProvider([text_response("summary of earlier turns")])
    context = build_context(provider, max_tokens=10, reserved_tokens=0)
    for index in range(6):
        context.add_user(f"a fairly long message number {index}")

    assert context.needs_summarization() is True
    changed = await context.summarize_if_needed()

    assert changed is True
    assert context.summary == "summary of earlier turns"
    assert len(context.messages) == 4
    assert context.messages[-1].content == "a fairly long message number 5"


@pytest.mark.asyncio
async def test_summarization_failure_falls_back_to_sliding_window():
    provider = FakeProvider([], fail_with=RuntimeError("summarizer down"))
    context = build_context(provider, max_tokens=10, reserved_tokens=0)
    for index in range(6):
        context.add_user(f"a fairly long message number {index}")

    changed = await context.summarize_if_needed()

    assert changed is False
    assert context.summary is None
    assert len(context.messages) == 4


@pytest.mark.asyncio
async def test_summarization_is_skipped_below_threshold():
    provider = FakeProvider([])
    context = build_context(provider, max_tokens=10_000, reserved_tokens=0)
    context.add_user("short")

    assert context.needs_summarization() is False
    assert await context.summarize_if_needed() is False
    assert len(context.messages) == 1
