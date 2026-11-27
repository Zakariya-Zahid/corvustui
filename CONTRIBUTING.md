# Contributing to CorvusTUI

Thanks for helping. CorvusTUI is a local-first agentic terminal app: a Rich/Click
CLI, a Textual TUI, a provider layer for local and cloud models, an MCP client,
and a small safety/approval system.

This guide covers setup, the architecture you need to know, the quality gates,
and how to get a PR merged. The [roadmap](ROADMAP.md) explains where the
project is going; the [user guide](USER_GUIDE.md) explains how it behaves today.

## Before you start

- Read the [Code of Conduct](CODE_OF_CONDUCT.md). Be decent; assume good faith.
- Check open issues and the [roadmap](ROADMAP.md) before starting large work.
- For anything touching storage, provider payloads, security, or public
  interfaces, open an issue or draft ADR first. We would rather discuss a
  design than reject a finished PR.
- Do not open a PR for a feature that is explicitly out of scope through v1.0
  without agreement from a maintainer.

Good first issues are labeled `good-first-issue`. The roadmap's §14 lists
candidate tasks that are intentionally small and low-risk.

## Development setup

### Requirements

- Python 3.11 or newer (the CI matrix targets 3.11 and 3.14)
- Git
- Ollama for local end-to-end testing (optional; unit tests use fakes)
- `ffmpeg` only if you work on audio/video features (optional)

### Clone and install

```bash
git clone git@github.com:Zakariya-Zahid/corvustui.git
cd corvustui

# With uv (recommended)
uv venv
uv pip install -e ".[all,dev]"

# Or with stdlib venv
python -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

Use a lighter install if ChromaDB is inconvenient:

```bash
uv pip install -e ".[tui,dev]"
```

The `all` extra pulls ChromaDB, whose embedding model downloads on first use.
That download is not needed for the test suite.

### Configuration

```bash
cp .env.example .env
```

The default provider is Ollama with `qwen3.5:0.8b`. Unit tests never require a
live model or an API key. For manual testing:

```bash
ollama serve
ollama pull qwen3.5:0.8b
corvustui tui
```

Never commit `.env`, API keys, tokens, session databases, or audit logs. If you
need a fixture that looks like a secret, use an obviously fake value and add it
to the redaction tests.

## Architecture in one page

```text
CLI (cli.py) ─┐
              ├─► CorvusAgent (core/agent.py)
TUI (tui/)  ──┘        │
                       ├─► ReActLoop (react/loop.py)
                       ├─► ContextManager (context/manager.py)
                       ├─► Provider (providers/)
                       ├─► ToolRegistry (tools/)
                       ├─► MCPManager (mcp/)
                       ├─► SafetyGuard (safety/)
                       └─► VectorMemory (memory/)
```

- **`core/agent.py`** owns lifecycle, provider creation, tool registration, RAG
  injection, and the public `run`/`run_streaming` API.
- **`react/loop.py`** implements reasoning, tool calls, repair, iteration limits,
  and emits `ReActEvent`s. Keep provider-specific logic out of this loop.
- **`providers/base.py`** defines `Message`, `ToolCall`, `ToolDefinition`,
  `CompletionResponse`, and the `BaseProvider` contract. Every provider adapter
  translates to and from this shared model.
- **`providers/ollama.py`** uses Ollama's `/api/chat`; the full OpenAI-style
  tool wrapper is intentional. Do not “simplify” it without a live test.
- **`safety/`** is fail-closed: path scope, shell policy, network policy,
  approvals, redaction, and audit logging. Never bypass it in a tool.
- **`tui/`** is presentation only. It must not call provider APIs directly; it
  talks to the agent through events and controller methods.
- **`mcp/`** is an in-tree JSON-RPC client with stdio and Streamable HTTP
  transports. MCP tools are exposed through the same `Tool` interface.

If you add a new layer, prefer an interface plus an adapter over a special case
in `CorvusAgent`. The roadmap's target architecture (§3) shows the direction:
domain objects, application services, and infrastructure adapters.

## Running the checks

Run these before every PR. They are the same gates used for releases.

```bash
# Tests
python -m pytest -q

# Focused tests while developing
python -m pytest tests/test_agent.py -q
python -m pytest -q -k "ollama and tool"

# Lint (project gate)
ruff check src tests --select F,E9,I001

# Optional but appreciated: full Ruff and type checks
ruff check src tests
mypy src/corvustui --ignore-missing-imports

# Packaging sanity
python -m compileall -q src
uv build
uvx twine check dist/*
```

### Bend proofs

`bend/` is a development-only compiler checkout and is not committed. If it is
present, run:

```bash
./bend/bin/bend PROOF.bend
```

You should see `ALL PROOFS CHECK`. If `bend/` is absent, the proof tests skip;
that is expected in a fresh clone and in CI unless the compiler is installed.

### Test expectations

- No network in unit tests. Use `httpx.MockTransport`, the fakes in
  `tests/fakes.py`, or a local fake server.
- Every provider change needs a request/response fixture test. Do not assert
  only that “it did not crash”.
- Every bug fix needs a regression test that fails before the fix.
- TUI changes need a Textual pilot test where practical, plus a manual check at
  80×24 and 120×40.
- Storage changes need a migration test from the previous schema.
- New code should be covered; do not lower coverage to make a PR pass.
- Keep tests deterministic: no sleeps for timing, no reliance on model output,
  no ordering assumptions between independent async tasks.

Run a single test with verbose output when debugging:

```bash
python -m pytest tests/test_react.py::test_name -vv
```

## Coding standards

- Python 3.11+, `from __future__ import annotations` in new modules.
- Formatting and lint: Ruff, line length 100. The merge gate is
  `F,E9,I001`; keep the rest clean when you touch a file.
- Type hints on public functions and new dataclasses. `Any` is acceptable at
  provider boundaries, not in domain models.
- Prefer `dataclass(slots=True)` for value objects, `Protocol` for ports, and
  small functions over deep inheritance.
- Async by default in I/O paths. Never block the event loop with file or
  network I/O; use `asyncio.to_thread` for blocking parsers.
- Raise specific exceptions (`ProviderError`, `SafetyError`, etc.) and translate
  them at the CLI/TUI boundary into a message the user can act on.
- Do not log secrets, full prompts, document text, or file contents at INFO.
- Keep user-facing errors short, specific, and free of tracebacks. Include a
  next action (“switch model”, “install the docs extra”, “check Ollama”).
- Preserve backward compatibility for CLI flags, config keys, and public
  imports unless the PR explicitly announces a deprecation.

### Naming and layout

```text
src/corvustui/<area>/<module>.py
tests/test_<area>.py
```

- One responsibility per module; if a file passes ~600 lines, consider splitting
  it in a separate refactor PR.
- New optional dependencies go in `pyproject.toml` under a named extra and are
  imported lazily.
- New settings go in `config/settings.py` with a `CORVUS_` env prefix, a safe
  default, and documentation in `.env.example`.

### Commits and PRs

Use short imperative commit subjects:

```text
Add session store schema and migrations
Fix Ollama tool-call payload for multi-turn calls
Document attachment size limits
```

PRs should be small and reviewable:

- Aim for under ~400 changed lines excluding tests and docs.
- One logical change per PR. Split refactors from behavior changes.
- Include a short “what/why/how to test” description.
- Link the issue (`Closes #123`) and the roadmap phase/epic.
- Add screenshots or a terminal recording for TUI changes.
- Update README, USER_GUIDE, `.env.example`, and the roadmap status when the
  user-visible behavior changes.
- Fill in the Definition of Done checklist from the roadmap appendix.

A maintainer will review for correctness, safety, tests, and consistency. We may
ask for a smaller PR or a design note; that is normal, not a rejection.

## Where to contribute

| Area | Good entry point | Notes |
|------|------------------|-------|
| Docs | README, USER_GUIDE, troubleshooting | High value, low risk |
| Tests | Fakes, provider fixtures, TUI pilot | Makes everything else safer |
| Providers | New adapter or capability metadata | Needs fixture tests |
| TUI | Modals, keyboard flow, narrow terminals | Needs pilot tests |
| Tools | Small, safe built-ins | Must use `SafetyGuard` |
| MCP | Transports, discovery, error handling | Needs protocol fixtures |
| Sessions | Phase 4 roadmap work | Wait for ADR-001 |
| Multimodal | Phase 5 roadmap work | Wait for ADR-002 |

If you are unsure, open a discussion or comment on the issue. Asking is cheaper
than rewriting.

## Reporting bugs

Include:

- CorvusTUI version (`corvustui --version` once available; otherwise the commit)
- Python version, OS, terminal, and whether you are in tmux/SSH
- Provider and model (never include the API key)
- Exact command or TUI steps
- Expected vs actual behavior
- The smallest redacted log or traceback that shows the failure

For security issues, do not open a public issue. Follow
[SECURITY.md](SECURITY.md) and use GitHub's private vulnerability reporting.

## License

By contributing, you agree that your contributions are licensed under the MIT
License (see [LICENSE](LICENSE)).
