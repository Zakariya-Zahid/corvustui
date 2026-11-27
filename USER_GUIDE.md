# CorvusTUI User Guide

**CorvusTUI** is an agentic terminal interface for local and cloud LLMs. It lets you run prompts, call tools, inspect and invoke MCP servers, benchmark models, and manage persistent long-term memory — all from the terminal.

This guide covers everything from your first prompt to advanced automation. It assumes you are running from the project root with the virtualenv activated:

```bash
cd /home/zak/Main/FYP/corvustui
source corvusenv/bin/activate
```

---

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Your First Prompt](#your-first-prompt)
4. [The CLI Modes](#the-cli-modes)
   - [`corvustui` — interactive REPL](#corvustui-interactive-repl)
   - [`corvustui run` — single prompt](#corvustui-run-single-prompt)
   - [`corvustui tui` — Textual TUI](#corvustui-tui-textual-tui)
   - [`corvustui models` — live model listing](#corvustui-models-live-model-listing)
   - [`corvustui mcp` — inspect and call MCP servers](#corvustui-mcp-inspect-and-call-mcp-servers)
   - [`corvustui bench` — benchmarking](#corvustui-bench-benchmarking)
5. [Providers and Models](#providers-and-models)
6. [Tools and Tool Use](#tools-and-tool-use)
7. [Safety Guardrails](#safety-guardrails)
8. [RAG / Vector Memory](#rag-vector-memory)
9. [Benchmarking Workflows](#benchmarking-workflows)
10. [Environment Variables Reference](#environment-variables-reference)
11. [Tips and Facts](#tips-and-facts)
12. [Troubleshooting](#troubleshooting)
13. [Exit Codes and Automation](#exit-codes-and-automation)

---

## Installation

CorvusTUI ships as a Python package with optional extras:

| Extra | Purpose |
|-------|---------|
| (core) | CLI + all seven LLM providers + MCP + safety + benchmarks |
| `rag` | Persistent ChromaDB memory |
| `tui` | Textual chat interface |
| `dev` | Tests + lint |
| `all` | TUI + RAG + web search |

#### Recommended: `uv`

```bash
# All extras
uv tool install "corvustui[all] @ git+https://github.com/OWNER/corvustui.git"

# TUI only (lighter)
uv tool install "corvustui[tui] @ git+https://github.com/OWNER/corvustui.git"
```

#### curl installer

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/corvustui/main/install.sh | bash

# TUI only
curl -fsSL https://raw.githubusercontent.com/OWNER/corvustui/main/install.sh | CORVUS_EXTRAS=tui bash
```

The installer prefers `uv tool install`, then `pipx`, then `pip --user`.

#### pipx or pip

```bash
pipx install "corvustui[all] @ git+https://github.com/OWNER/corvustui.git"
pip install --user "corvustui[all] @ git+https://github.com/OWNER/corvustui.git"
```

#### Development checkout

```bash
git clone https://github.com/OWNER/corvustui.git
cd corvustui
./install.sh                    # local, all extras
# or
uv tool install --editable ".[all]"
# or
pip install -e ".[all]"
```

Replace `OWNER` with your GitHub username. The `all` extra includes ChromaDB;
its embedding model downloads on first use.

Upgrade or remove a uv install:

```bash
uv tool install --force "corvustui[all] @ git+https://github.com/OWNER/corvustui.git"
uv tool uninstall corvustui
```

Verify the install:

```bash
corvustui --help
```

---

## Configuration

Copy the example env file and edit it:

```bash
cp .env.example .env
# edit .env with your API keys, default model, etc.
```

CorvusTUI reads `.env` automatically via `pydantic-settings`. Provider-specific defaults prevent a local Ollama tag from being silently sent to a cloud API.

---

## Your First Prompt

### Interactive REPL (default)

```bash
corvustui
```

You are dropped into a Rich/Click REPL. Type a question and press Enter:

```
You  What is 17 * 23?
💭 Thought: ...
🔧 ...
391
```

Type `/help` for commands, `/quit` to exit.

### Single prompt (non-interactive, scriptable)

```bash
corvustui --provider ollama --model qwen3.5:0.8b \
  --max-iterations 4 --no-mcp --yes \
  run "Use the shell tool to run printf 'hello from corvus'"
```

- `--yes` auto-approves side-effecting tools (shell, file writes, MCP calls).
- Without `--yes`, non-interactive runs deny side-effecting tools by default.
- `--no-mcp` skips starting configured MCP servers (faster startup when you don't need them).

---

## The CLI Modes

### `corvustui` — interactive REPL

```bash
corvustui
corvustui --provider ollama --model qwen3.5:0.8b
corvustui --rag --rag-path ./memory
corvustui --mcp-config ./mcp.json --yes
```

The REPL accepts normal prompts plus slash commands:

| Command | Meaning |
|---------|---------|
| `/help` | Show available commands |
| `/clear` | Clear conversation context |
| `/stats` | Show context tokens and memory statistics |
| `/tools` | List available tools |
| `/mcp` | Show MCP server status |
| `/safety` | Show the active safety policy |
| `/models` | List live models for the current provider |
| `/model <name>` | Switch model (provider auto-detected) |
| `/quit` | Exit |

REPL-specific behaviors:
- Side-effecting tools prompt for approval interactively.
- RAG memories are injected per turn and successful interactions are stored.
- Context is summarized automatically when it approaches the limit.

### `corvustui run` — single prompt

Best for CI, scripts, and one-off tasks:

```bash
# Simple question
corvustui run "Explain quantum computing in one sentence"

# With a specific model and more iterations
corvustui --provider ollama --model qwen3.5:0.8b --max-iterations 6 \
  run "Write a Python script that lists files in the current directory"

# Auto-approve tools (useful in trusted CI)
corvustui --yes run "Use shell to run pwd"

# With RAG memory
corvustui --rag --rag-path ./project-memory run "What did we decide about the API design last week?"
```

### `corvustui tui` — Textual TUI

```bash
pip install -e ".[tui]"
corvustui tui
corvustui --provider ollama --model qwen3.5:0.8b --no-mcp tui
```

The TUI provides:
- Scrolling chat log with user/assistant/system messages
- **Live streaming preview** — shows provider tokens as they arrive
- Status bar: provider/model, token usage, MCP count, state
- Modal approval dialogs for side-effecting tools (fails closed if the modal can't show)
- Slash commands: `/help`, `/clear`, `/stats`, `/tools`, `/mcp`, `/safety`, `/models`, `/model <name>`, `/quit`
- Footer bindings: `^l` clear chat, `^p` palette, `^c` quit

**Model fallback:** If the configured default Ollama model is not installed:
- Exactly one model installed → auto-selected with a notice.
- Multiple models installed → the first installed model is used with a notice; switch with `/models` or Ctrl+O. The TUI stays usable instead of failing at startup.
- An explicitly requested `--model` is never silently replaced.

### `corvustui models` — live model listing

```bash
corvustui models                       # default provider
corvustui models --provider ollama
corvustui models --provider openai
```

Returns live names from the provider, never a canned list. If Ollama is unreachable or the model is missing, you get an actionable error (e.g. "Start it with `ollama serve`" or "Run `ollama pull <model>`").

### `corvustui mcp` — inspect and call MCP servers

CorvusTUI includes an in-tree JSON-RPC MCP client (stdio + Streamable HTTP). No `mcp` extra required.

```bash
# List configured servers
corvustui mcp list --config ./mcp.json

# Show discovered tools per server
corvustui mcp tools --config ./mcp.json

# Call a tool
corvustui mcp call fixture echo --args '{"text":"live"}' --config ./mcp.json
```

Example `mcp.json` (Claude Desktop-style):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/projects"]
    },
    "remote": {
      "url": "https://example.test/mcp",
      "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
    }
  }
}
```

- `${VAR}` references are expanded from the environment; an unset variable is an error, never an empty string.
- `inherit_env: false` limits the child environment to safe base variables plus the server's explicit `env` — provider API keys are not inherited.
- MCP tools are always approval-gated. A failing server is reported without preventing other servers or the LLM session from starting.

### `corvustui bench` — benchmarking

```bash
# Built-in tasks, human-readable table
corvustui --provider ollama --model qwen3.5:0.8b bench

# Custom tasks, JSON + Markdown reports
corvustui --provider ollama --model qwen3.5:0.8b bench \
  --tasks ./benchmarks/tasks.json \
  --output ./benchmarks/results/run-1.json \
  --markdown ./benchmarks/results/run-1.md

# Run only the first two tasks
corvustui bench --tasks ./benchmarks/tasks.json --max-tasks 2

# Print JSON to stdout
corvustui bench --tasks ./benchmarks/tasks.json --json --max-tasks 1
```

Task file format (JSON or YAML):

```json
{
  "tasks": [
    {
      "id": "arithmetic",
      "prompt": "What is 17 * 23? Reply with only the number.",
      "expected": "391",
      "match": "exact"
    },
    {
      "id": "style",
      "prompt": "Reply with exactly the word corvus.",
      "expected": "corvus",
      "match": "exact"
    },
    {
      "id": "regex-task",
      "prompt": "Name a corvid.",
      "expected": ["crow", "raven", "jay"],
      "match": "regex"
    }
  ]
}
```

Scoring modes:
- `contains` (default): every expected substring must appear.
- `exact`: trimmed answer must equal an expected value (case-insensitive).
- `regex`: every expected pattern must match.

Each result records status, score, latency (ms), iterations, tokens, answer, and redacted error. Conversation context is cleared between tasks. RAG memory is **not** cleared automatically when `--rag` is enabled — use `--no-rag` or a disposable `--rag-path` for isolated runs.

A recorded live baseline is in `benchmarks/results/ollama-qwen3.5-0.8b.md`. It is reported as-is: 1/2 tasks passed with `qwen3.5:0.8b`.

---

## Providers and Models

Seven providers are supported:

| Provider | Notes |
|----------|-------|
| `ollama` | Local models via Ollama (`ollama serve`) |
| `openai` | OpenAI-compatible APIs |
| `anthropic` | Anthropic Claude |
| `groq` | Groq-hosted models |
| `gemini` | Google Gemini |
| `openrouter` | OpenRouter |
| `grok` | xAI Grok |

Select with `--provider` and `--model`:

```bash
corvustui --provider ollama --model qwen3.5:0.8b
corvustui --provider openai --model gpt-4o
corvustui --provider anthropic --model claude-3-5-sonnet-20241022
```

**Ollama model rules:**
- If the default model is missing and exactly one model is installed, it is used automatically (with a notice).
- With multiple models installed, the interactive CLI asks you to choose; the TUI starts with the first installed model and lets you switch with `/models` or Ctrl+O.
- Explicit model names are never silently replaced.

**Streaming:** `CorvusAgent.run_streaming()` requests a real provider stream and forwards each content fragment to `on_chunk`. The TUI uses this for its live preview. The CLI `run` and REPL paths currently use non-streaming turns.

---

## Tools and Tool Use

Built-in tools:

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write content to file |
| `edit_file` | Replace text in a file and return a diff |
| `list_files` | List directory contents |
| `shell` | Execute shell commands |
| `web_search` | Search the web via Tavily (requires key) |
| `duckduckgo_search` | Search via DuckDuckGo with backend fallback (no key) |

Tools that mutate files or run commands (`write_file`, `edit_file`, `shell`, and every MCP tool) require approval. Read-only built-ins run without a prompt unless the path or network policy rejects them.

**Parameter aliases:** `file_path`, `output_file`, `value`, `directory`, `sh`, `search` are accepted where applicable. Numeric parameters sent as strings are coerced. Empty file writes are valid; empty `old_string` edits are rejected.

Example agentic run:

```bash
corvustui --yes run "Read README.md, then write a one-sentence summary to summary.txt"
```

---

## Safety Guardrails

Safety is enforced in the ReAct loop **before** a tool executes. Denied calls never reach the tool.

### Filesystem scope

Reads/writes are limited to the current directory plus `CORVUS_SAFETY_ALLOWED_PATHS` / `--allow-path`. Symlink escapes are resolved and rejected. `.env`, SSH/AWS/GnuPG credentials, private keys, and similar sensitive paths are denied even when outside roots are allowed.

### Shell policy

Catastrophic patterns are blocked:
- `rm -rf /`, `rm -rf .`, `sudo`, disk formatting
- `curl | sh`, fork bombs
- Namespace/mount escapes
- Every command in a `&&` / `;` / pipe / newline chain is checked, including wrappers like `env`, `timeout`, `xargs`, `busybox`
- Nested `sh -c`, `bash -lc`, `eval`, `find -exec`, `env -S`, `$(...)`, backticks are inspected
- Recursive removal of `.`, `..`, `*`, `$HOME`, `$PWD`, `{}`, or `xargs` stdin targets is blocked
- Paths in commands, option values, redirections, and `cd`/`pushd` targets are expanded and checked against allowed roots

### Network policy

`web_search`, `duckduckgo_search`, HTTP MCP servers, and network shell commands can be disabled with `CORVUS_SAFETY_NETWORK_ENABLED=false`. A stdio MCP server is a trusted child process and can make its own network calls — that is why shell and MCP tools remain approval-gated.

### Approval

Side-effecting tools prompt in the REPL and TUI. Non-interactive `run` and `bench` deny them unless `--yes` is supplied.

### Redaction

Provider keys, bearer tokens, private keys, JWTs, and `api_key=...`-style assignments are redacted from tool observations, approval prompts, and audit entries.

### Audit

Every decision is appended as JSONL (default `~/.corvustui/audit.jsonl`, created owner-readable `0600`) with tool, redacted arguments, rule, outcome, duration, and a bounded output preview.

```bash
# Custom audit path
corvustui --audit-log ./audit.jsonl run "..."

# Disable safety (isolated environments only — not recommended)
corvustui --no-safety run "..."
```

This is defence-in-depth, not a kernel sandbox. Code run by interpreters (`python -c`, scripts), here-documents, and stdio MCP servers is not fully inspectable.

---

## RAG / Vector Memory

Enable long-term memory with `--rag`:

```bash
# In-memory (no extra dependencies, lost on exit)
corvustui --rag

# Persistent ChromaDB collection
pip install -e ".[rag]"
corvustui --rag --rag-path ./memory
```

- Retrieved memories are injected per turn as extra system context.
- Successful interactions are stored after completion.
- `CORVUS_RAG_TOP_K` controls how many memories are retrieved (default 5).
- `CORVUS_RAG_SIMILARITY_THRESHOLD` controls the minimum similarity (default 0.7).
- `CORVUS_CHROMADB_PATH` sets the persistent collection path.
- `CORVUS_EMBEDDING_MODEL` sets the embedding model (default `all-MiniLM-L6-v2`).
- ChromaDB downloads its default embedding model on first use unless a custom embedder is injected via `ChromaMemory(..., embedder=...)`.

RAG memory is injected per turn and does not mutate the stored conversation context. It is not cleared between benchmark tasks when `--rag` is enabled — use `--no-rag` or a disposable `--rag-path` for isolated runs.

---

## Benchmarking Workflows

### Quick smoke test (built-in tasks)

```bash
corvustui --provider ollama --model qwen3.5:0.8b \
  --max-iterations 3 bench --max-tasks 2
```

### Full run with reports

```bash
corvustui --provider ollama --model qwen3.5:0.8b bench \
  --tasks ./benchmarks/tasks.json \
  --output ./benchmarks/results/ollama-qwen3.5-0.8b.json \
  --markdown ./benchmarks/results/ollama-qwen3.5-0.8b.md
```

### CI automation

```bash
# Fail CI if any task errors
corvustui bench --tasks ./benchmarks/tasks.json --json > report.json
# then parse report.json and check summary.errors == 0
```

Each result records: task_id, prompt, status, score, passed, latency_ms, iterations, total_tokens, answer (redacted), error (redacted).

---

## Environment Variables Reference

| Variable | Meaning |
|----------|---------|
| `CORVUS_DEFAULT_PROVIDER` | Default LLM provider |
| `CORVUS_DEFAULT_MODEL` | Default Ollama model (default: `qwen3.5:0.8b`) |
| `CORVUS_OPENAI_API_KEY` | OpenAI API key |
| `CORVUS_ANTHROPIC_API_KEY` | Anthropic API key |
| `CORVUS_GROQ_API_KEY` | Groq API key |
| `CORVUS_GEMINI_API_KEY` | Google Gemini API key |
| `CORVUS_OPENROUTER_API_KEY` | OpenRouter API key |
| `CORVUS_GROK_API_KEY` | xAI Grok API key |
| `CORVUS_TAVILY_API_KEY` | Tavily web search API key |
| `CORVUS_MCP_SERVERS_CONFIG` | Default MCP server config path |
| `CORVUS_MCP_ENABLED` | Set to `false` to disable MCP startup |
| `CORVUS_MCP_TIMEOUT` | Default per-server request timeout (seconds) |
| `CORVUS_CHROMADB_PATH` | Persistent ChromaDB collection path |
| `CORVUS_EMBEDDING_MODEL` | ChromaDB embedding model (default `all-MiniLM-L6-v2`) |
| `CORVUS_RAG_TOP_K` | Memories retrieved per turn (default `5`) |
| `CORVUS_RAG_SIMILARITY_THRESHOLD` | Minimum similarity (default `0.7`) |
| `CORVUS_SAFETY_ENABLED` | Set to `false` to disable guardrails |
| `CORVUS_SAFETY_ALLOWED_PATHS` | JSON list of extra filesystem roots |
| `CORVUS_SAFETY_SHELL_MODE` | `deny_dangerous`, `allowlist`, or `unrestricted` |
| `CORVUS_SAFETY_NETWORK_ENABLED` | Set to `false` to block network tools |
| `CORVUS_SAFETY_REQUIRE_APPROVAL` | Set to `false` to skip approval prompts |
| `CORVUS_SAFETY_AUTO_APPROVE` | Set to `true` to approve side effects |
| `CORVUS_SAFETY_AUDIT_PATH` | JSONL audit log path |

See `.env.example` for all options.

---

## Tips and Facts

### Tips

1. **Start small:** `corvustui run "What is 2 + 2?"` verifies your provider is reachable before attempting tool use.
2. **Use `--no-mcp`** when you don't need MCP servers — faster startup, no subprocesses.
3. **Use `--yes` in CI** only in trusted, isolated environments. In shared CI, deny side effects and catch failures explicitly.
4. **Inspect before calling:** `corvustui mcp list` and `mcp tools` show what a server exposes without starting an LLM session.
5. **Exact-match scoring:** Use `match: "exact"` in benchmark tasks when you need the model to output only a specific value (e.g. a number). A correct number wrapped in extra prose scores 0.0 with exact matching.
6. **TUI model switching:** If the default Ollama model is missing and several are installed, the TUI picks the first one; press Ctrl+O or use `/models` to switch.
7. **Audit log:** Check `~/.corvustui/audit.jsonl` (or your `CORVUS_SAFETY_AUDIT_PATH`) to review what was approved, denied, and redacted.
8. **Context window:** Long conversations are summarized automatically when approaching the token limit. Use `/clear` to start fresh.

### Facts

| Item | Detail |
|------|--------|
| Providers | 7: Ollama, OpenAI, Anthropic, Groq, Gemini, OpenRouter, Grok |
| Built-in tools | 7: read_file, write_file, edit_file, list_files, shell, web_search, duckduckgo_search |
| MCP transports | stdio + Streamable HTTP |
| Default audit path | `~/.corvustui/audit.jsonl` (mode `0600`) |
| Default RAG top_k | 5 |
| Default RAG threshold | 0.7 |
| Max observation size | 12,000 characters per tool result |
| Tests | 356 passing |
| Lint gate | `ruff check src tests --select F,E9,I001` — clean |
| Proof gate | `./bend/bin/bend PROOF.bend` — ALL PROOFS CHECK |
| Default interface | Rich/Click CLI (`corvustui` REPL); TUI is optional (`corvustui tui`) |
| Streaming | Provider → ReAct loop → `on_chunk` fragments → TUI live preview; CLI `run`/REPL use non-streaming turns |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| "Ollama is not reachable" | Ollama not running | `ollama serve` |
| "no models are installed" | No Ollama models | `ollama pull <model>` |
| "model not found; Available models: ..." | Wrong model name | Use one of the listed names, or `--model <name>` |
| "Tool 'shell' was not approved" | Side effect denied in non-interactive mode | Add `--yes`, or use the interactive REPL/TUI |
| "ChromaDB dependencies not installed" | Missing `rag` extra | `pip install -e ".[rag]"` |
| "Textual is not installed" | Missing `tui` extra | `pip install -e ".[tui]"` |
| "The TUI requires an interactive terminal" | Piped/non-TTY stdin | Run `corvustui tui` directly in a terminal |
| Variable unset in MCP server | `inherit_env: false` | Add the variable to the server's explicit `env` in config |

### Live checks you can run

```bash
# Provider reachable + model listed
corvustui models --provider ollama

# CLI reaches a live model
corvustui --provider ollama --model qwen3.5:0.8b --max-iterations 3 \
  --no-mcp --yes run "Reply with exactly the word corvus"

# TUI launches
TERM=xterm-256color corvustui --provider ollama --model qwen3.5:0.8b --no-mcp tui
# then type /quit

# Benchmark runs
corvustui --provider ollama --model qwen3.5:0.8b bench --max-tasks 1
```

---

## Exit Codes and Automation

- `0` — success
- Non-zero — error (Click exception, provider failure, safety denial in non-interactive mode, etc.)

Example shell guard:

```bash
if corvustui --provider ollama --model qwen3.5:0.8b --no-mcp --yes \
   run "..."; then
  echo "Agent completed successfully"
else
  echo "Agent run failed" >&2
  exit 1
fi
```

---

## Verifying the Installation

From a development checkout with the `dev` extra installed:

```bash
# Full test suite
python -m pytest -q
# 356 passed with the optional Bend compiler present
# 354 passed, 2 skipped on a clean clone

# Lint gate
ruff check src tests --select F,E9,I001
# All checks passed

# Formal proof gate (optional; needs a local bend/ checkout)
./bend/bin/bend PROOF.bend
# ALL PROOFS CHECK

# Compile check
python -m compileall -q src tests
# ok
```

The Bend compiler is not committed. When `bend/bin/bend` is missing, the two
proof tests skip automatically and the rest of the suite still runs.

Happy agenting! 🦫