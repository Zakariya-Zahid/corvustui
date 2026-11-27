"""Benchmark runner and report formatting."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from corvustui.benchmark.tasks import BenchmarkTask
from corvustui.core.agent import AgentConfig, CorvusAgent
from corvustui.react.loop import IterationStatus
from corvustui.safety.redact import redact_secrets


@dataclass(slots=True)
class BenchmarkResult:
    """Result for one benchmark task."""

    task_id: str
    prompt: str
    status: str
    answer: str
    score: float
    passed: bool
    latency_ms: float
    iterations: int
    total_tokens: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "status": self.status,
            "answer": self.answer,
            "score": round(self.score, 4),
            "passed": self.passed,
            "latency_ms": round(self.latency_ms, 2),
            "iterations": self.iterations,
            "total_tokens": self.total_tokens,
            "error": self.error,
        }


@dataclass(slots=True)
class BenchmarkReport:
    """Aggregate benchmark results."""

    provider: str
    model: str
    started_at: str
    finished_at: str
    results: list[BenchmarkResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def errors(self) -> int:
        return sum(1 for result in self.results if result.status == "error")

    @property
    def average_latency_ms(self) -> float:
        if not self.results:
            return 0.0
        return sum(result.latency_ms for result in self.results) / len(self.results)

    @property
    def total_tokens(self) -> int:
        return sum(result.total_tokens for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "errors": self.errors,
                "average_latency_ms": round(self.average_latency_ms, 2),
                "total_tokens": self.total_tokens,
            },
            "results": [result.to_dict() for result in self.results],
        }

    def to_markdown(self) -> str:
        lines = [
            "# CorvusTUI Benchmark",
            "",
            f"- Provider: `{self.provider}`",
            f"- Model: `{self.model}`",
            f"- Started: {self.started_at}",
            f"- Finished: {self.finished_at}",
            f"- Passed: {self.passed}/{self.total}",
            f"- Errors: {self.errors}",
            f"- Average latency: {self.average_latency_ms:.1f} ms",
            f"- Total tokens: {self.total_tokens}",
            "",
            "| Task | Status | Score | Latency (ms) | Tokens |",
            "|------|--------|-------|--------------|--------|",
        ]
        for result in self.results:
            task_id = result.task_id.replace("|", "\\|")
            lines.append(
                f"| {task_id} | {result.status} | {result.score:.2f} | "
                f"{result.latency_ms:.1f} | {result.total_tokens} |"
            )
        return "\n".join(lines) + "\n"


class BenchmarkRunner:
    """Run benchmark tasks against one agent instance."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        agent_factory: Callable[[], Any] | None = None,
    ):
        self.config = config
        self.agent_factory = agent_factory

    async def run(
        self,
        tasks: list[BenchmarkTask],
        on_result: Callable[[BenchmarkResult], None] | None = None,
    ) -> BenchmarkReport:
        agent = (
            self.agent_factory()
            if self.agent_factory is not None
            else CorvusAgent(self.config or AgentConfig())
        )
        owns_agent = True

        started_at = datetime.now(UTC).isoformat()
        if owns_agent:
            await agent.__aenter__()

        provider = getattr(getattr(agent, "provider", None), "provider_type", None)
        model = getattr(getattr(agent, "provider", None), "model", "unknown")
        report = BenchmarkReport(
            provider=provider.value if provider is not None else "unknown",
            model=model,
            started_at=started_at,
            finished_at=started_at,
        )

        try:
            for task in tasks:
                result = await self._run_task(agent, task)
                report.results.append(result)
                if on_result is not None:
                    on_result(result)
        finally:
            if owns_agent:
                await agent.__aexit__(None, None, None)
            report.finished_at = datetime.now(UTC).isoformat()

        return report

    async def _run_task(self, agent: Any, task: BenchmarkTask) -> BenchmarkResult:
        if hasattr(agent, "context"):
            agent.context.clear()

        loop = getattr(agent, "react_loop", None)
        previous_iterations = getattr(loop, "max_iterations", None)
        if task.max_iterations is not None and loop is not None:
            loop.max_iterations = task.max_iterations

        started = time.perf_counter()
        try:
            result = await agent.run(task.prompt)
            latency_ms = (time.perf_counter() - started) * 1000
            answer = result.final_answer or ""
            completed = result.status == IterationStatus.COMPLETE
            score = task.score(answer, completed)
            error = None
            if result.errors:
                error = redact_secrets("; ".join(result.errors))
            return BenchmarkResult(
                task_id=task.id,
                prompt=task.prompt,
                status=result.status.value,
                answer=redact_secrets(answer),
                score=score,
                passed=task.passed(answer, completed),
                latency_ms=latency_ms,
                iterations=result.total_iterations,
                total_tokens=result.total_tokens,
                error=error,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return BenchmarkResult(
                task_id=task.id,
                prompt=task.prompt,
                status="error",
                answer="",
                score=0.0,
                passed=False,
                latency_ms=latency_ms,
                iterations=0,
                total_tokens=0,
                error=redact_secrets(str(exc)),
            )
        finally:
            if task.max_iterations is not None and loop is not None:
                loop.max_iterations = previous_iterations
