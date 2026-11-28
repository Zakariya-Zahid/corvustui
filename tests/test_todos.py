"""Tests for the shared todo list model and the todo_write tool."""

from __future__ import annotations

from corvustui.todos import (
    MAX_TODOS,
    TodoStatus,
    TodoStore,
    normalize_status,
)
from corvustui.tools.builtin import TodoWriteTool


def test_replace_from_arguments_accepts_status_aliases_and_active_form():
    store = TodoStore()
    items = store.replace_from_arguments(
        {
            "todos": [
                {"content": "Read code", "status": "done"},
                {"content": "Write fix", "status": "active", "activeForm": "Writing fix"},
                {"content": "Run tests", "status": "todo"},
            ]
        }
    )

    assert items is not None
    assert [item.status for item in items] == [
        TodoStatus.COMPLETED,
        TodoStatus.IN_PROGRESS,
        TodoStatus.PENDING,
    ]
    assert items[1].display_text == "Writing fix"
    assert store.counts() == (1, 3)


def test_replace_from_arguments_accepts_items_and_tasks_keys():
    store = TodoStore()

    assert store.replace_from_arguments(
        {"items": [{"content": "A", "status": "pending"}]}
    ) is not None
    assert [item.content for item in store.items] == ["A"]

    assert store.replace_from_arguments(
        {"tasks": [{"content": "B", "status": "pending"}]}
    ) is not None
    assert [item.content for item in store.items] == ["B"]


def test_invalid_payload_preserves_the_previous_plan():
    store = TodoStore()
    store.replace_from_arguments({"todos": [{"content": "keep", "status": "pending"}]})

    assert store.replace_from_arguments({"todos": [{"content": "", "status": "pending"}]}) is None
    assert store.replace_from_arguments({"todos": [{"content": "x", "status": "bogus"}]}) is None
    assert store.replace_from_arguments({"todos": "not-a-list"}) is None
    assert [item.content for item in store.items] == ["keep"]


def test_replace_from_arguments_rejects_oversized_lists():
    store = TodoStore()
    too_many = [
        {"content": f"task {index}", "status": "pending"} for index in range(MAX_TODOS + 1)
    ]
    assert store.replace_from_arguments({"todos": too_many}) is None
    assert store.items == []


def test_current_and_completion_helpers():
    store = TodoStore()
    store.replace_from_arguments(
        {
            "todos": [
                {"content": "first", "status": "completed"},
                {"content": "second", "status": "in_progress"},
                {"content": "third", "status": "pending"},
            ]
        }
    )

    assert store.current() is not None
    assert store.current().content == "second"
    assert store.is_complete is False

    store.replace_from_arguments({"todos": [{"content": "done", "status": "completed"}]})
    assert store.is_complete is True
    assert store.current() is None


def test_render_plain_lists_every_task():
    store = TodoStore()
    store.replace_from_arguments(
        {
            "todos": [
                {"content": "done task", "status": "completed"},
                {"content": "active task", "status": "in_progress"},
                {"content": "later task", "status": "pending"},
            ]
        }
    )

    rendered = store.render_plain()
    assert "Tasks 1/3 complete" in rendered
    assert "[x] done task" in rendered
    assert "[>] active task" in rendered
    assert "[ ] later task" in rendered


def test_normalize_status_handles_unknown_values():
    assert normalize_status("DONE") == TodoStatus.COMPLETED
    assert normalize_status(" in-progress ") == TodoStatus.IN_PROGRESS
    assert normalize_status("nonsense") is None
    assert normalize_status(None) is None


async def test_todo_write_tool_updates_store_and_rejects_bad_payloads():
    store = TodoStore()
    tool = TodoWriteTool(store)

    result = await tool.execute(
        todos=[
            {"content": "Inspect code", "status": "completed"},
            {"content": "Implement fix", "status": "in_progress"},
        ]
    )

    assert result.success is True
    assert result.output is not None
    assert "Tasks 1/2 complete" in result.output
    assert len(store.items) == 2

    invalid = await tool.execute(todos=[{"content": "", "status": "pending"}])

    assert invalid.success is False
    assert len(store.items) == 2


async def test_todo_write_tool_accepts_items_alias():
    store = TodoStore()
    tool = TodoWriteTool(store)

    result = await tool.execute(items=[{"content": "Only task", "status": "pending"}])

    assert result.success is True
    assert [item.content for item in store.items] == ["Only task"]
