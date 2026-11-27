"""Tests for the benchmark harness and CLI command."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from corvustui.benchmark import (
    BenchmarkConfigError,
    BenchmarkReport,
    BenchmarkResult,
    BenchmarkRunner,
    BenchmarkTask,
    load_tasks,
)
from corvustui.providers import ProviderType
from corvustui.react.loop import IterationStatus, ReActResult, ReActStep


class FakeContext:
    def __init__(self) -> None:
        self.clear_count = 0

    def clear(self) -> None:
        self.clear_count += 1


class FakeAgent:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(
            provider_type=ProviderType.OLLAMA,
            model="fake-model",
        )
        self.context = FakeContext()
        self.react_loop = SimpleNamespace(max_iterations=7)
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def run(self, prompt: str):
        if "boom" in prompt:
            raise RuntimeError("provider exploded")
        answer = "391" if "17 * 23" in prompt else "corvus"
        return ReActResult(
            final_answer=answer,
            steps=[ReActStep(iteration=1)],
            total_iterations=1,
            status=IterationStatus.COMPLETE,
            total_tokens=5,
        )


def test_load_tasks_json_and_yaml(tmp_path):
    json_path = tmp_path / "tasks.json"
    json_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "one", "prompt": "Say one", "expected": "one"},
                    {
                        "id": "two",
                        "prompt": "Say two",
                        "expect": ["two", "2"],
                        "metadata": {"kind": "test"},
                        "match": "regex",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    tasks = load_tasks(json_path)

    assert [task.id for task in tasks] == ["one", "two"]
    assert tasks[0].expected == ("one",)
    assert tasks[1].expected == ("two", "2")
    assert tasks[1].metadata == {"kind": "test"}
    assert tasks[1].match == "regex"

    yaml_path = tmp_path / "tasks.yaml"
    yaml_path.write_text(
        "- id: yaml\n  prompt: Say yaml\n  expected: yaml\n",
        encoding="utf-8",
    )
    yaml_tasks = load_tasks(yaml_path)

    assert yaml_tasks[0].id == "yaml"
    assert yaml_tasks[0].expected == ("yaml",)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"id": "x"}],
        [{"id": "x", "prompt": "p"}, {"id": "x", "prompt": "q"}],
        [{"id": "x", "prompt": "p", "expected": 3}],
        [{"id": "x", "prompt": "p", "max_iterations": 0}],
        [{"id": "x", "prompt": "p", "metadata": []}],
        [{"id": "x", "prompt": "p", "match": "fuzzy"}],
        [{"id": "x", "prompt": "p", "match": []}],
        [{"id": "x", "prompt": "p", "expected": "(", "match": "regex"}],
    ],
)
def test_load_tasks_rejects_invalid_payloads(tmp_path, payload):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkConfigError):
        load_tasks(path)


def test_task_scoring():
    task = BenchmarkTask("x", "prompt", expected=("alpha", "beta"))

    assert task.score("alpha and beta", True) == 1.0
    assert task.passed("alpha and beta", True)
    assert task.score("alpha only", True) == 0.5
    assert not task.passed("alpha only", True)
    assert task.score("alpha and beta", False) == 0.0
    assert BenchmarkTask("y", "prompt").score("anything", True) == 1.0

    exact = BenchmarkTask("e", "prompt", expected=("Corvus",), match="exact")
    assert exact.score("  corvus  ", True) == 1.0
    assert exact.score("Thought: corvus", True) == 0.0

    regex = BenchmarkTask("r", "prompt", expected=(r"\b391\b", r"^Answer:"), match="regex")
    assert regex.score("Answer: 391", True) == 1.0
    assert regex.score("Answer: 3910", True) == 0.5


async def test_runner_collects_results_and_clears_context():
    agent = FakeAgent()
    tasks = [
        BenchmarkTask("math", "What is 17 * 23?", expected=("391",)),
        BenchmarkTask("wrong", "Say something", expected=("missing",)),
        BenchmarkTask("error", "boom", expected=("x",)),
    ]
    seen: list[str] = []

    runner = BenchmarkRunner(agent_factory=lambda: agent)
    report = await runner.run(tasks, on_result=lambda result: seen.append(result.task_id))

    assert agent.entered and agent.exited
    assert agent.context.clear_count == 3
    assert seen == ["math", "wrong", "error"]
    assert report.total == 3
    assert report.passed == 1
    assert report.failed == 2
    assert report.errors == 1
    assert report.total_tokens == 10
    assert report.average_latency_ms > 0
    assert report.results[0].score == 1.0
    assert report.results[1].score == 0.0
    assert report.results[2].status == "error"
    assert "provider exploded" in (report.results[2].error or "")

    data = report.to_dict()
    assert data["summary"]["total"] == 3
    assert data["summary"]["passed"] == 1
    markdown = report.to_markdown()
    assert "# CorvusTUI Benchmark" in markdown
    assert "| math |" in markdown


async def test_runner_restores_task_max_iterations():
    agent = FakeAgent()
    task = BenchmarkTask("x", "prompt", max_iterations=2)

    await BenchmarkRunner(agent_factory=lambda: agent).run([task])

    assert agent.react_loop.max_iterations == 7


def test_benchmark_cli_wiring(monkeypatch, tmp_path):
    from click.testing import CliRunner

    import corvustui.benchmark as benchmark_module
    from corvustui import cli as cli_module

    class FakeRunner:
        def __init__(self, config):
            self.config = config

        async def run(self, tasks, on_result=None):
            results = [
                BenchmarkResult(
                    task_id=tasks[0].id,
                    prompt=tasks[0].prompt,
                    status="complete",
                    answer="391",
                    score=1.0,
                    passed=True,
                    latency_ms=12.5,
                    iterations=1,
                    total_tokens=5,
                )
            ]
            if on_result:
                on_result(results[0])
            return BenchmarkReport(
                provider="ollama",
                model="fake-model",
                started_at="start",
                finished_at="finish",
                results=results,
            )

    monkeypatch.setattr(benchmark_module, "BenchmarkRunner", FakeRunner)
    output = tmp_path / "nested" / "report.json"
    markdown = tmp_path / "nested" / "report.md"
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "--provider", "ollama",
            "--model", "fake-model",
            "bench",
            "--max-tasks", "1",
            "--output", str(output),
            "--markdown", str(markdown),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Benchmark Results" in result.output
    assert "Passed:" in result.output
    assert "1/1" in result.output
    assert output.exists()
    assert markdown.exists()
    assert '"total": 1' in output.read_text(encoding="utf-8")
    assert "# CorvusTUI Benchmark" in markdown.read_text(encoding="utf-8")
