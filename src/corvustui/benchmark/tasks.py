"""Benchmark task definitions and loading."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class BenchmarkConfigError(ValueError):
    """Raised when a benchmark task file is invalid."""


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    """One prompt and the expected answer used to score it.

    ``match`` controls how ``expected`` is interpreted:

    - ``contains`` (default): every expected substring must appear.
    - ``exact``: the trimmed answer must equal one expected string,
      case-insensitively.
    - ``regex``: every expected regular expression must match.
    """

    id: str
    prompt: str
    expected: tuple[str, ...] = ()
    max_iterations: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    match: str = "contains"

    def score(self, answer: str, completed: bool) -> float:
        """Return a 0..1 score for an answer."""

        if not completed:
            return 0.0
        if not self.expected:
            return 1.0

        if self.match == "exact":
            normalized = " ".join(answer.split()).casefold()
            expected_values = {" ".join(item.split()).casefold() for item in self.expected}
            return 1.0 if normalized in expected_values else 0.0

        if self.match == "regex":
            matches = sum(
                1 for pattern in self.expected if re.search(pattern, answer, re.IGNORECASE)
            )
            return matches / len(self.expected)

        lowered = answer.lower()
        matches = sum(1 for item in self.expected if item.lower() in lowered)
        return matches / len(self.expected)

    def passed(self, answer: str, completed: bool) -> bool:
        return self.score(answer, completed) >= 1.0


DEFAULT_TASKS: tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        id="arithmetic",
        prompt="What is 17 * 23? Reply with only the number.",
        expected=("391",),
        match="exact",
    ),
    BenchmarkTask(
        id="instruction-following",
        prompt="Reply with exactly the word corvus and nothing else.",
        expected=("corvus",),
        match="exact",
    ),
    BenchmarkTask(
        id="short-reasoning",
        prompt=(
            "A box has 3 red balls and 5 blue balls. "
            "How many balls are in the box? Reply with only the number."
        ),
        expected=("8",),
        match="exact",
    ),
)


def load_tasks(path: str | Path) -> list[BenchmarkTask]:
    """Load tasks from JSON or YAML.

    Accepted shapes are a top-level list or an object with a ``tasks`` list.
    """

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkConfigError(f"Could not read task file {source}: {exc}") from exc

    try:
        if source.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise BenchmarkConfigError(f"Could not parse task file {source}: {exc}") from exc

    if isinstance(payload, dict):
        payload = payload.get("tasks")
    if not isinstance(payload, list):
        raise BenchmarkConfigError(
            "Task file must contain a list, or an object with a 'tasks' list."
        )

    tasks: list[BenchmarkTask] = []
    seen: set[str] = set()
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise BenchmarkConfigError(f"Task {index} must be an object.")
        task_id = item.get("id")
        prompt = item.get("prompt")
        if not isinstance(task_id, str) or not task_id.strip():
            raise BenchmarkConfigError(f"Task {index} is missing a non-empty 'id'.")
        if task_id in seen:
            raise BenchmarkConfigError(f"Duplicate task id: {task_id}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise BenchmarkConfigError(
                f"Task {task_id!r} is missing a non-empty 'prompt'."
            )

        expected_raw = item.get("expected", item.get("expect", []))
        if isinstance(expected_raw, str):
            expected = (expected_raw,)
        elif isinstance(expected_raw, list) and all(
            isinstance(value, str) for value in expected_raw
        ):
            expected = tuple(expected_raw)
        else:
            raise BenchmarkConfigError(
                f"Task {task_id!r} has an invalid 'expected' value; "
                "use a string or a list of strings."
            )

        max_iterations = item.get("max_iterations")
        if max_iterations is not None and (
            not isinstance(max_iterations, int) or max_iterations <= 0
        ):
            raise BenchmarkConfigError(
                f"Task {task_id!r} has an invalid 'max_iterations'."
            )

        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise BenchmarkConfigError(
                f"Task {task_id!r} has an invalid 'metadata' value."
            )

        match = item.get("match", "contains")
        if not isinstance(match, str) or match not in {"contains", "exact", "regex"}:
            raise BenchmarkConfigError(
                f"Task {task_id!r} has an invalid 'match' value; "
                "use 'contains', 'exact', or 'regex'."
            )
        if match == "regex":
            for pattern in expected:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise BenchmarkConfigError(
                        f"Task {task_id!r} has an invalid regex {pattern!r}: {exc}"
                    ) from exc

        seen.add(task_id)
        tasks.append(
            BenchmarkTask(
                id=task_id,
                prompt=prompt,
                expected=expected,
                max_iterations=max_iterations,
                metadata=dict(metadata),
                match=match,
            )
        )

    if not tasks:
        raise BenchmarkConfigError("Task file contains no tasks.")
    return tasks
