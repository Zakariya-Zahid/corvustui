"""CLI entry point for CorvusTUI."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from corvustui.config.settings import get_settings
from corvustui.core.agent import AgentConfig, CorvusAgent
from corvustui.mcp.client import MCPClient
from corvustui.mcp.config import load_mcp_config
from corvustui.mcp.manager import MCPManager
from corvustui.mcp.protocol import MCPConfigError, MCPError
from corvustui.mcp.transport import HTTPTransport, StdioTransport
from corvustui.providers import (
    ProviderAuthError,
    ProviderError,
    ProviderFactory,
    ProviderModelNotFoundError,
    ProviderTimeoutError,
    ProviderType,
    ProviderUnavailableError,
)
from corvustui.providers.detect import detect_provider_from_model
from corvustui.safety import (
    CLIApprover,
    DenyApprover,
    SafetyPolicy,
    redact_secrets,
)
from corvustui.tools.registry import ToolRegistry

console = Console()
settings = get_settings()


def print_banner(provider: ProviderType, model: str) -> None:
    console.print(Panel.fit(
        "[bold cyan]CorvusTUI[/bold cyan] - Interactive CLI\n"
        f"[dim]Provider: {provider.value} | Model: {model}[/dim]",
        border_style="cyan",
    ))


def _error_text(exc: BaseException) -> str:
    """Return an exception message safe to display."""

    return redact_secrets(str(exc))


def print_step(step) -> None:
    """Callback for ReAct steps."""
    if step.thought:
        console.print(
            f"[yellow]>> Thought:[/yellow] {redact_secrets(step.thought)}"
        )
    if step.action:
        console.print(f"[blue]>> Action:[/blue] {step.action}")
        if step.action_input:
            console.print(
                f"[dim]   Input: {redact_secrets(str(step.action_input))}[/dim]"
            )
    if step.observation:
        observation = redact_secrets(step.observation)
        if len(observation) > 300:
            observation = observation[:300] + "..."
        console.print(f"[green]>> Observation:[/green] {observation}")
    if step.error:
        console.print(f"[red]>> Error:[/red] {redact_secrets(step.error)}")
    console.print()


def _model_error_message(provider_type: ProviderType, exc: Exception) -> str:
    if isinstance(exc, ProviderAuthError):
        return redact_secrets(str(exc))
    if isinstance(exc, (ProviderUnavailableError, ProviderTimeoutError)):
        return redact_secrets(str(exc))
    if isinstance(exc, ProviderModelNotFoundError):
        return redact_secrets(str(exc))
    if isinstance(exc, NotImplementedError):
        return (
            f"Provider '{provider_type.value}' does not support dynamic model listing. "
            "Specify a model explicitly with --model."
        )
    if isinstance(exc, ProviderError):
        return redact_secrets(str(exc))
    return redact_secrets(f"Could not list models for {provider_type.value}: {exc}")


async def _fetch_models(provider_type: ProviderType, timeout: float = 15.0) -> list[str]:
    """Fetch a provider's live model list. Never returns a fabricated list."""
    provider = ProviderFactory.create(provider_type)
    await provider.__aenter__()
    try:
        return await asyncio.wait_for(provider.list_models(), timeout=timeout)
    finally:
        await provider.__aexit__(None, None, None)


async def _model_status(provider_type: ProviderType) -> tuple[list[str], str | None]:
    try:
        models = await _fetch_models(provider_type)
    except Exception as exc:
        return [], _model_error_message(provider_type, exc)

    if not models:
        if provider_type == ProviderType.OLLAMA:
            return [], (
                "Ollama is running but no models are installed. "
                "Install one with 'ollama pull <model>'."
            )
        return [], f"Provider '{provider_type.value}' returned an empty model list."
    return models, None


def _installed_model_name(model: str, models: list[str]) -> str | None:
    """Return the provider's spelling of ``model``, or None if absent."""
    if model in models:
        return model
    if ":" not in model and f"{model}:latest" in models:
        return f"{model}:latest"
    return None


async def _fallback_ollama_model(
    missing_model: str,
    allow_prompt: bool = True,
) -> str | None:
    """Pick an installed Ollama model when the default one is absent.

    This is still live data: the choices come from ``/api/tags``.  A single
    installed model is selected automatically; multiple models are offered in
    a prompt so the user is never silently moved to an unexpected model.
    """
    models, error = await _model_status(ProviderType.OLLAMA)
    if error:
        console.print(Panel(
            f"[red]{error}[/red]",
            title="No Ollama models available",
            border_style="red",
        ))
        return None

    if len(models) == 1:
        selected = models[0]
        console.print(
            f"[yellow]Default model '{missing_model}' is not installed. "
            f"Using the only installed model:[/yellow] [cyan]{selected}[/cyan]"
        )
        return selected

    console.print(Panel(
        f"Default model '[cyan]{missing_model}[/cyan]' is not installed.\n\n"
        "Installed models:\n" +
        "\n".join(f"  [cyan]{model}[/cyan]" for model in models),
        title="Choose an Ollama model",
        border_style="yellow",
    ))

    if not allow_prompt or not sys.stdin.isatty():
        console.print(
            "[yellow]Pass --model <name> to choose one of the installed models.[/yellow]"
        )
        return None

    try:
        return Prompt.ask("Select a model", choices=models, default=models[0])
    except (EOFError, KeyboardInterrupt):
        return None


def _build_safety(
    *,
    auto_approve: bool,
    allow_paths: tuple[str, ...],
    enable_safety: bool,
    audit_log: Path | None,
) -> tuple[SafetyPolicy, CLIApprover | DenyApprover]:
    policy = SafetyPolicy.from_settings(
        settings,
        cwd=Path.cwd(),
        allowed_paths=allow_paths,
        audit_path=audit_log,
        auto_approve=auto_approve,
        enabled=enable_safety,
    )
    if policy.auto_approve or not policy.enabled or not sys.stdin.isatty():
        approver: CLIApprover | DenyApprover = DenyApprover()
    else:
        approver = CLIApprover()
    return policy, approver


def _print_mcp_statuses(statuses) -> None:
    for status in statuses:
        if status.connected:
            console.print(
                f"[dim]MCP: {status.name} ({status.transport}, "
                f"{status.tool_count} tools)[/dim]"
            )
        else:
            error = redact_secrets(status.error or "not connected")
            console.print(Panel(
                f"[yellow]{error}[/yellow]",
                title=f"MCP server: {status.name}",
                border_style="yellow",
            ))


def _mcp_config_path(config_path: str | Path | None) -> Path:
    path = Path(config_path) if config_path else settings.mcp_servers_config
    if path is None:
        raise click.ClickException(
            "No MCP config configured. Pass --mcp-config <path> or set "
            "CORVUS_MCP_SERVERS_CONFIG."
        )
    return path


@click.group(invoke_without_command=True)
@click.option("--provider", type=click.Choice([p.value for p in ProviderType]),
              default=lambda: settings.default_provider.value, help="LLM provider")
@click.option("--model", default=None, help="Model name")
@click.option("--max-iterations", default=10, help="Max ReAct iterations")
@click.option("--rag/--no-rag", default=False, help="Enable RAG memory")
@click.option("--rag-path", type=click.Path(), default=None, help="ChromaDB path")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Auto-approve tools that modify files or run commands")
@click.option("--allow-path", "allow_paths", multiple=True, type=click.Path(),
              help="Additional filesystem root the agent may access (repeatable)")
@click.option("--no-safety", is_flag=True, default=False,
              help="Disable safety guardrails (not recommended)")
@click.option("--audit-log", type=click.Path(), default=None,
              help="Audit log path (default: ~/.corvustui/audit.jsonl)")
@click.option("--mcp-config", type=click.Path(), default=None,
              help="MCP server config (JSON or YAML)")
@click.option("--no-mcp", is_flag=True, default=False,
              help="Do not start configured MCP servers")
@click.pass_context
def cli(
    ctx,
    provider,
    model,
    max_iterations,
    rag,
    rag_path,
    yes,
    allow_paths,
    no_safety,
    audit_log,
    mcp_config,
    no_mcp,
):
    """CorvusTUI - Agentic Terminal Interface for Local and Cloud LLMs."""
    # If the user supplied only --model, infer the provider instead of
    # silently trying to load a cloud model through Ollama.
    if model and ctx.get_parameter_source("provider") != click.core.ParameterSource.COMMANDLINE:
        provider = detect_provider_from_model(model).value

    if ctx.invoked_subcommand is None:
        asyncio.run(run_interactive(
            provider=ProviderType(provider),
            model=model,
            max_iterations=max_iterations,
            enable_rag=rag,
            rag_path=Path(rag_path) if rag_path else None,
            auto_approve=yes,
            allow_paths=allow_paths,
            enable_safety=settings.safety_enabled and not no_safety,
            audit_log=Path(audit_log) if audit_log else None,
            mcp_config=Path(mcp_config) if mcp_config else None,
            enable_mcp=not no_mcp,
        ))


@cli.command()
@click.option("--provider", type=click.Choice([p.value for p in ProviderType]),
              default=None, help="Filter by provider (default: show all)")
def models(provider):
    """List models currently offered by the provider(s)."""
    if provider:
        prov_type = ProviderType(provider)

        async def _one():
            return await _model_status(prov_type)

        models_list, error = asyncio.run(_one())
        if error:
            console.print(Panel(
                f"[red]{error}[/red]",
                title=f"Models for {prov_type.value}",
                border_style="red",
            ))
            return

        console.print(Panel(
            "\n".join(f"  [cyan]{m}[/cyan]" for m in models_list),
            title=f"Models for {prov_type.value} (live)",
            border_style="cyan",
        ))
        return

    async def _all():
        return await asyncio.gather(*[_model_status(p) for p in ProviderType])

    results = asyncio.run(_all())
    table = Table(title="Available Models by Provider (live)")
    table.add_column("Provider", style="cyan")
    table.add_column("Models", style="white")
    table.add_column("Status", style="white")

    for prov_type, (models_list, error) in zip(ProviderType, results):
        if error:
            table.add_row(prov_type.value, "-", f"[red]{error}[/red]")
        else:
            preview = ", ".join(models_list[:3])
            if len(models_list) > 3:
                preview += f" (+{len(models_list) - 3} more)"
            table.add_row(prov_type.value, preview, f"[green]{len(models_list)} models[/green]")

    console.print(table)
    console.print("\n[dim]Use --provider <name> to see the full list for one provider.[/dim]")


@cli.command()
@click.option("--tasks", "tasks_path", type=click.Path(exists=True, dir_okay=False),
              default=None, help="JSON or YAML benchmark task file")
@click.option("--output", type=click.Path(), default=None,
              help="Write the JSON report to this path (use '-' for stdout)")
@click.option("--markdown", type=click.Path(), default=None,
              help="Write a Markdown report to this path (use '-' for stdout)")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Print the JSON report to stdout")
@click.option("--max-tasks", type=int, default=None,
              help="Run only the first N tasks")
@click.pass_context
def bench(ctx, tasks_path, output, markdown, as_json, max_tasks):
    """Run a benchmark and report latency, tokens, and task scores."""
    from corvustui.benchmark import (
        DEFAULT_TASKS,
        BenchmarkConfigError,
        BenchmarkRunner,
        load_tasks,
    )

    parent = ctx.parent
    provider_value = parent.params.get("provider", settings.default_provider.value)
    model = parent.params.get("model")
    max_iterations = parent.params.get("max_iterations", settings.max_iterations)
    rag_path = parent.params.get("rag_path")
    auto_approve = parent.params.get("yes", False)
    no_safety_flag = parent.params.get("no_safety", False)
    enable_safety = settings.safety_enabled and not no_safety_flag
    allow_paths = tuple(parent.params.get("allow_paths", ()))
    audit_log = parent.params.get("audit_log")
    mcp_config = parent.params.get("mcp_config")
    enable_mcp = not parent.params.get("no_mcp", False)

    try:
        tasks = load_tasks(tasks_path) if tasks_path else list(DEFAULT_TASKS)
    except BenchmarkConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    if max_tasks is not None:
        if max_tasks <= 0:
            raise click.ClickException("--max-tasks must be greater than zero.")
        tasks = tasks[:max_tasks]
    if not tasks:
        raise click.ClickException("No benchmark tasks to run.")

    policy, approver = _build_safety(
        auto_approve=auto_approve,
        allow_paths=allow_paths,
        enable_safety=enable_safety,
        audit_log=Path(audit_log) if audit_log else None,
    )
    config = AgentConfig(
        provider_type=ProviderType(provider_value),
        model=model,
        max_iterations=max_iterations,
        enable_rag=parent.params.get("rag", False),
        rag_path=Path(rag_path) if rag_path else None,
        safety_policy=policy,
        approver=approver,
        enable_safety=enable_safety,
        mcp_config=Path(mcp_config) if mcp_config else None,
        enable_mcp=enable_mcp,
    )

    async def _run():
        runner = BenchmarkRunner(config)
        return await runner.run(tasks, on_result=_print_benchmark_result)

    try:
        report = asyncio.run(_run())
    except Exception as exc:
        raise click.ClickException(_error_text(exc)) from exc

    table = Table(title="Benchmark Results")
    table.add_column("Task", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Score", style="white")
    table.add_column("Latency", style="white")
    table.add_column("Tokens", style="white")
    for result in report.results:
        table.add_row(
            result.task_id,
            result.status,
            f"{result.score:.2f}",
            f"{result.latency_ms:.1f} ms",
            str(result.total_tokens),
        )
    console.print(table)
    console.print(
        f"[bold]Passed:[/bold] {report.passed}/{report.total}  "
        f"[bold]Errors:[/bold] {report.errors}  "
        f"[bold]Average latency:[/bold] {report.average_latency_ms:.1f} ms  "
        f"[bold]Tokens:[/bold] {report.total_tokens}"
    )

    json_report = json.dumps(report.to_dict(), indent=2)
    markdown_report = report.to_markdown()
    if output:
        if output == "-":
            console.print(json_report)
        else:
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json_report + "\n", encoding="utf-8")
            console.print(f"[green]JSON report written to {output}[/green]")
    if markdown:
        if markdown == "-":
            console.print(markdown_report)
        else:
            markdown_path = Path(markdown)
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(markdown_report, encoding="utf-8")
            console.print(f"[green]Markdown report written to {markdown}[/green]")
    if as_json and not output:
        console.print(json_report)


def _print_benchmark_result(result) -> None:
    status = "PASS" if result.passed else "FAIL"
    console.print(
        f"[dim]{result.task_id}: {status} "
        f"({result.score:.2f}, {result.latency_ms:.1f} ms, "
        f"{result.total_tokens} tokens)[/dim]"
    )


@cli.command()
@click.pass_context
def tui(ctx):
    """Launch the Textual TUI."""
    if not sys.stdin.isatty():
        raise click.ClickException("The TUI requires an interactive terminal.")

    try:
        from corvustui.tui.app import CorvusTUIApp
    except ImportError as exc:
        raise click.ClickException(
            "Textual is not installed. Install it with: "
            "pip install 'corvustui[tui]'"
        ) from exc

    parent = ctx.parent
    provider_value = parent.params.get("provider", settings.default_provider.value)
    model = parent.params.get("model")
    max_iterations = parent.params.get("max_iterations", settings.max_iterations)
    rag_path = parent.params.get("rag_path")
    auto_approve = parent.params.get("yes", False)
    no_safety_flag = parent.params.get("no_safety", False)
    enable_safety = settings.safety_enabled and not no_safety_flag
    allow_paths = tuple(parent.params.get("allow_paths", ()))
    audit_log = parent.params.get("audit_log")
    mcp_config = parent.params.get("mcp_config")
    enable_mcp = not parent.params.get("no_mcp", False)

    policy, _ = _build_safety(
        auto_approve=auto_approve,
        allow_paths=allow_paths,
        enable_safety=enable_safety,
        audit_log=Path(audit_log) if audit_log else None,
    )
    config = AgentConfig(
        provider_type=ProviderType(provider_value),
        model=model,
        max_iterations=max_iterations,
        enable_rag=parent.params.get("rag", False),
        rag_path=Path(rag_path) if rag_path else None,
        safety_policy=policy,
        approver=None,
        enable_safety=enable_safety,
        mcp_config=Path(mcp_config) if mcp_config else None,
        enable_mcp=enable_mcp,
    )
    CorvusTUIApp(config).run()


async def run_interactive(
    provider: ProviderType,
    model: str | None,
    max_iterations: int,
    enable_rag: bool,
    rag_path: Path | None,
    auto_approve: bool = False,
    allow_paths: tuple[str, ...] = (),
    enable_safety: bool = True,
    audit_log: Path | None = None,
    mcp_config: Path | None = None,
    enable_mcp: bool = True,
) -> None:
    """Run interactive REPL."""
    policy, approver = _build_safety(
        auto_approve=auto_approve,
        allow_paths=allow_paths,
        enable_safety=enable_safety,
        audit_log=audit_log,
    )
    config = AgentConfig(
        provider_type=provider,
        model=model,
        max_iterations=max_iterations,
        enable_rag=enable_rag,
        rag_path=rag_path,
        safety_policy=policy,
        approver=approver,
        enable_safety=enable_safety,
        mcp_config=mcp_config,
        enable_mcp=enable_mcp,
    )

    agent = CorvusAgent(config)
    try:
        await agent.__aenter__()
    except ProviderModelNotFoundError as exc:
        # A missing default Ollama model should not prevent the interactive
        # session from starting.  An explicitly requested model is different:
        # never silently replace it.
        if model is not None or provider != ProviderType.OLLAMA:
            console.print(Panel(
                f"[red]{_error_text(exc)}[/red]",
                title="Model not available",
                border_style="red",
            ))
            console.print(
                "[dim]Run 'corvustui models --provider ollama' to list installed models, "
                "or pass --model <name>.[/dim]"
            )
            return

        selected = await _fallback_ollama_model(settings.default_model)
        if selected is None:
            return

        config.model = selected
        agent = CorvusAgent(config)
        try:
            await agent.__aenter__()
        except (ProviderAuthError, ProviderUnavailableError, ProviderTimeoutError) as retry_exc:
            console.print(Panel(
                f"[red]{_error_text(retry_exc)}[/red]",
                title="Provider error",
                border_style="red",
            ))
            return
        except MCPConfigError as retry_exc:
            console.print(Panel(
                f"[red]{_error_text(retry_exc)}[/red]",
                title="MCP configuration error",
                border_style="red",
            ))
            return
        except ProviderError as retry_exc:
            console.print(Panel(
                f"[red]{_error_text(retry_exc)}[/red]",
                title="Provider error",
                border_style="red",
            ))
            return
    except (ProviderAuthError, ProviderUnavailableError, ProviderTimeoutError) as exc:
        console.print(Panel(f"[red]{_error_text(exc)}[/red]", title="Provider error", border_style="red"))
        return
    except MCPConfigError as exc:
        console.print(Panel(
            f"[red]{_error_text(exc)}[/red]",
            title="MCP configuration error",
            border_style="red",
        ))
        return
    except ProviderError as exc:
        console.print(Panel(f"[red]{_error_text(exc)}[/red]", title="Provider error", border_style="red"))
        return

    try:
        print_banner(agent.provider.provider_type, agent.provider.model)
        _print_mcp_statuses(getattr(agent, "mcp_statuses", []))
        console.print("[dim]Type 'exit' or 'quit' to exit. Type 'help' for commands.[/dim]\n")

        while True:
            try:
                user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[yellow]Goodbye![/yellow]")
                break

            if user_input.lower() in ("exit", "quit"):
                console.print("[yellow]Goodbye![/yellow]")
                break

            if user_input.lower() == "help":
                console.print(Panel(
                    "Commands:\n"
                    "  help         - Show this help\n"
                    "  clear        - Clear conversation context\n"
                    "  stats        - Show context/memory stats\n"
                    "  tools        - List available tools\n"
                    "  mcp          - Show MCP server status\n"
                    "  safety       - Show the active safety policy\n"
                    "  models       - List live models for the current provider\n"
                    "  model <name> - Switch model (auto-detects provider)\n"
                    "  exit         - Exit CorvusTUI",
                    title="Help",
                    border_style="blue",
                ))
                continue

            if user_input.lower() == "clear":
                agent.context.clear()
                console.print("[green]Context cleared.[/green]")
                continue

            if user_input.lower() == "models":
                models_list, error = await _model_status(agent.provider.provider_type)
                if error:
                    console.print(Panel(
                        f"[red]{error}[/red]",
                        title=f"Models for {agent.provider.provider_type.value}",
                        border_style="red",
                    ))
                else:
                    console.print(Panel(
                        "\n".join(f"  [cyan]{m}[/cyan]" for m in models_list),
                        title=f"Models for {agent.provider.provider_type.value} (live)",
                        border_style="cyan",
                    ))
                continue

            if user_input.lower().startswith("model "):
                new_model = user_input[6:].strip()
                if not new_model:
                    console.print("[yellow]Usage: model <name>[/yellow]")
                    continue

                new_provider_type = detect_provider_from_model(new_model)
                try:
                    await agent.switch_model(new_provider_type, new_model)
                except Exception as exc:
                    console.print(
                        f"[red]Could not switch model:[/red] {_error_text(exc)}"
                    )
                    continue

                console.print(
                    f"[green]Switched to {agent.provider.provider_type.value} "
                    f"model: {agent.provider.model}[/green]"
                )
                continue

            if user_input.lower() == "stats":
                stats = agent.get_context_stats()
                mem_stats = await agent.get_memory_stats()
                console.print(Panel(
                    f"Tokens: {stats['total_tokens']}/{stats['max_tokens']} "
                    f"({stats['usage_ratio']:.1%})\n"
                    f"Messages: {stats['message_count']}\n"
                    f"Memory: {mem_stats.get('total_entries', 'N/A') if mem_stats else 'Disabled'} entries",
                    title="Statistics",
                    border_style="green",
                ))
                continue

            if user_input.lower() == "tools":
                tools = agent.tools.get_all()
                console.print(Panel(
                    "\n".join(
                        f"  [cyan]{t.name}[/cyan]: {redact_secrets(t.description)}"
                        for t in tools
                    ),
                    title="Available Tools",
                    border_style="blue",
                ))
                continue

            if user_input.lower() == "mcp":
                statuses = getattr(agent, "mcp_statuses", [])
                if not statuses:
                    console.print("[dim]No MCP servers configured.[/dim]")
                else:
                    lines = []
                    for status in statuses:
                        if status.connected:
                            lines.append(
                                f"  [green]{status.name}[/green] "
                                f"({status.transport}, {status.tool_count} tools)"
                            )
                        else:
                            lines.append(
                                f"  [red]{status.name}[/red]: "
                                f"{redact_secrets(status.error or 'not connected')}"
                            )
                    console.print(Panel(
                        "\n".join(lines),
                        title="MCP Servers",
                        border_style="blue",
                    ))
                continue

            if user_input.lower() == "safety":
                guard = getattr(agent, "safety", None)
                policy = getattr(guard, "policy", None)
                if policy is None:
                    console.print("[dim]Safety guard is not available.[/dim]")
                else:
                    roots = ", ".join(str(root) for root in policy.allowed_roots) or "(cwd)"
                    console.print(Panel(
                        f"Enabled: {policy.enabled}\n"
                        f"Allowed roots: {roots}\n"
                        f"Outside roots allowed: {policy.allow_outside_roots}\n"
                        f"Shell mode: {policy.shell_mode}\n"
                        f"Network: {policy.network_enabled}\n"
                        f"Approval required: {policy.require_approval}\n"
                        f"Audit log: {policy.audit_path}",
                        title="Safety Policy",
                        border_style="green",
                    ))
                continue

            console.print()
            try:
                result = await agent.run(user_input, on_step=print_step)
                console.print(Markdown(result.final_answer))
            except Exception as exc:
                console.print(f"[red]Error:[/red] {_error_text(exc)}")
    finally:
        await agent.__aexit__(None, None, None)


@cli.command()
@click.argument("prompt")
@click.option("--model", default=None)
@click.option("--max-iterations", default=None, type=int)
@click.option("--yes", "-y", is_flag=True, default=None,
              help="Auto-approve tools that modify files or run commands")
@click.option("--allow-path", "run_allow_paths", multiple=True, type=click.Path(),
              help="Additional filesystem root the agent may access (repeatable)")
@click.option("--no-safety", is_flag=True, default=None,
              help="Disable safety guardrails (not recommended)")
@click.option("--audit-log", type=click.Path(), default=None,
              help="Audit log path")
@click.option("--mcp-config", type=click.Path(), default=None,
              help="MCP server config (JSON or YAML)")
@click.option("--no-mcp", is_flag=True, default=None,
              help="Do not start configured MCP servers")
@click.pass_context
def run(
    ctx,
    prompt,
    model,
    max_iterations,
    yes,
    run_allow_paths,
    no_safety,
    audit_log,
    mcp_config,
    no_mcp,
):
    """Run a single prompt and exit."""
    parent = ctx.parent
    provider_value = parent.params.get("provider", settings.default_provider.value)
    parent_model = parent.params.get("model")
    effective_model = model or parent_model

    # An explicitly supplied provider always wins.  Otherwise, a model
    # supplied either globally (--model before the subcommand) or locally
    # (run --model) determines the provider.
    provider_explicit = (
        parent.get_parameter_source("provider")
        == click.core.ParameterSource.COMMANDLINE
    )
    model_explicit = (
        ctx.get_parameter_source("model") == click.core.ParameterSource.COMMANDLINE
        or parent.get_parameter_source("model") == click.core.ParameterSource.COMMANDLINE
    )
    if effective_model and model_explicit and not provider_explicit:
        provider_value = detect_provider_from_model(effective_model).value

    max_iterations = max_iterations or parent.params.get(
        "max_iterations", settings.max_iterations
    )
    rag_path = parent.params.get("rag_path")

    parent_yes = parent.params.get("yes", False)
    auto_approve = yes if yes is not None else parent_yes
    parent_no_safety = parent.params.get("no_safety", False)
    no_safety_flag = no_safety if no_safety is not None else parent_no_safety
    enable_safety = settings.safety_enabled and not no_safety_flag
    allow_paths = tuple(parent.params.get("allow_paths", ())) + tuple(run_allow_paths)
    effective_audit = audit_log or parent.params.get("audit_log")
    effective_mcp_config = mcp_config or parent.params.get("mcp_config")
    parent_no_mcp = parent.params.get("no_mcp", False)
    enable_mcp = not (no_mcp if no_mcp is not None else parent_no_mcp)

    asyncio.run(
        run_single(
            ProviderType(provider_value),
            effective_model,
            prompt,
            max_iterations,
            enable_rag=parent.params.get("rag", False),
            rag_path=Path(rag_path) if rag_path else None,
            auto_approve=auto_approve,
            allow_paths=allow_paths,
            enable_safety=enable_safety,
            audit_log=Path(effective_audit) if effective_audit else None,
            mcp_config=Path(effective_mcp_config) if effective_mcp_config else None,
            enable_mcp=enable_mcp,
        )
    )


async def _run_agent_once(config: AgentConfig, prompt: str) -> None:
    """Run one prompt with the given configuration."""
    async with CorvusAgent(config) as agent:
        _print_mcp_statuses(getattr(agent, "mcp_statuses", []))
        result = await agent.run(prompt, on_step=print_step)
        console.print(Markdown(result.final_answer))
        if result.status.value == "error":
            raise click.ClickException(result.final_answer)


async def run_single(
    provider: ProviderType,
    model: str | None,
    prompt: str,
    max_iterations: int,
    enable_rag: bool = False,
    rag_path: Path | None = None,
    auto_approve: bool = False,
    allow_paths: tuple[str, ...] = (),
    enable_safety: bool = True,
    audit_log: Path | None = None,
    mcp_config: Path | None = None,
    enable_mcp: bool = True,
) -> None:
    """Run a single prompt."""
    policy, approver = _build_safety(
        auto_approve=auto_approve,
        allow_paths=allow_paths,
        enable_safety=enable_safety,
        audit_log=audit_log,
    )
    config = AgentConfig(
        provider_type=provider,
        model=model,
        max_iterations=max_iterations,
        enable_rag=enable_rag,
        rag_path=rag_path,
        safety_policy=policy,
        approver=approver,
        enable_safety=enable_safety,
        mcp_config=mcp_config,
        enable_mcp=enable_mcp,
    )

    try:
        await _run_agent_once(config, prompt)
    except MCPConfigError as exc:
        raise click.ClickException(_error_text(exc)) from exc
    except ProviderModelNotFoundError as exc:
        # If no model was requested and the configured default is missing,
        # use the only installed Ollama model.  With several installed models,
        # require an explicit --model instead of guessing.
        if model is None and provider == ProviderType.OLLAMA:
            selected = await _fallback_ollama_model(
                settings.default_model,
                allow_prompt=False,
            )
            if selected is not None:
                config.model = selected
                try:
                    await _run_agent_once(config, prompt)
                    return
                except MCPConfigError as retry_exc:
                    raise click.ClickException(_error_text(retry_exc)) from retry_exc
                except ProviderError as retry_exc:
                    raise click.ClickException(_error_text(retry_exc)) from retry_exc
        raise click.ClickException(_error_text(exc)) from exc
    except ProviderError as exc:
        raise click.ClickException(_error_text(exc)) from exc


@cli.group(name="mcp")
def mcp_commands():
    """Inspect and call configured MCP servers."""


@mcp_commands.command("list")
@click.option("--config", "config_path", type=click.Path(), default=None,
              help="MCP server config (JSON or YAML)")
def mcp_list(config_path):
    """List configured MCP servers without starting them."""
    path = _mcp_config_path(config_path)
    try:
        configs = load_mcp_config(path, default_timeout=settings.mcp_timeout)
    except MCPConfigError as exc:
        raise click.ClickException(_error_text(exc)) from exc

    if not configs:
        console.print(f"[yellow]No MCP servers configured in {path}.[/yellow]")
        return

    table = Table(title=f"MCP Servers ({path})")
    table.add_column("Name", style="cyan")
    table.add_column("Transport", style="white")
    table.add_column("Target", style="white")
    table.add_column("Enabled", style="white")
    for config in configs:
        target = config.command_line if config.transport == "stdio" else (config.url or "")
        table.add_row(
            config.name,
            config.transport,
            redact_secrets(target),
            "yes" if config.enabled else "no",
        )
    console.print(table)


@mcp_commands.command("tools")
@click.option("--config", "config_path", type=click.Path(), default=None,
              help="MCP server config (JSON or YAML)")
def mcp_tools(config_path):
    """Connect to configured servers and list discovered tools."""
    path = _mcp_config_path(config_path)

    async def _run():
        configs = load_mcp_config(path, default_timeout=settings.mcp_timeout)
        registry = ToolRegistry()
        manager = MCPManager(
            configs,
            registry,
            network_enabled=settings.safety_network_enabled,
        )
        try:
            statuses = await manager.start()
            _print_mcp_statuses(statuses)
            tools = registry.get_all()
            if not tools:
                console.print("[yellow]No MCP tools discovered.[/yellow]")
                return
            table = Table(title="MCP Tools")
            table.add_column("Tool", style="cyan")
            table.add_column("Server", style="white")
            table.add_column("Description", style="white")
            for tool in tools:
                table.add_row(
                    tool.name,
                    getattr(tool, "server_name", "-"),
                    redact_secrets(tool.description),
                )
            console.print(table)
        finally:
            await manager.stop()

    try:
        asyncio.run(_run())
    except MCPConfigError as exc:
        raise click.ClickException(_error_text(exc)) from exc


@mcp_commands.command("call")
@click.argument("server")
@click.argument("tool")
@click.option("--args", "arguments_json", default="{}",
              help="JSON object of tool arguments")
@click.option("--config", "config_path", type=click.Path(), default=None,
              help="MCP server config (JSON or YAML)")
def mcp_call(server, tool, arguments_json, config_path):
    """Call one tool on one configured MCP server."""
    path = _mcp_config_path(config_path)
    try:
        configs = load_mcp_config(path, default_timeout=settings.mcp_timeout)
    except MCPConfigError as exc:
        raise click.ClickException(_error_text(exc)) from exc

    config = next((item for item in configs if item.name == server), None)
    if config is None:
        names = ", ".join(item.name for item in configs) or "(none)"
        raise click.ClickException(f"Unknown MCP server '{server}'. Configured: {names}")

    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"--args must be a JSON object: {exc}") from exc
    if not isinstance(arguments, dict):
        raise click.ClickException("--args must be a JSON object")

    if config.transport == "http":
        transport = HTTPTransport(
            config.url or "",
            headers=config.headers,
            timeout=config.timeout,
        )
    else:
        transport = StdioTransport(
            list(config.command or ()),
            cwd=config.cwd,
            env=config.env,
            inherit_env=config.inherit_env,
        )
    client = MCPClient(config.name, transport, timeout=config.timeout)

    async def _run():
        try:
            await client.connect()
            return await client.call_tool(tool, arguments)
        finally:
            await client.close()

    try:
        result = asyncio.run(_run())
    except MCPError as exc:
        raise click.ClickException(redact_secrets(str(exc))) from exc

    if result.is_error:
        raise click.ClickException(redact_secrets(result.text))
    console.print(redact_secrets(result.text))


if __name__ == "__main__":
    cli()
