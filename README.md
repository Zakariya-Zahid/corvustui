# CorvusTUI

**CorvusTUI: An Agentic Terminal Interface for Local and Cloud LLMs**

A high-performance, open-source terminal interface and agentic execution
engine that unifies local LLMs (via Ollama) and cloud APIs (OpenAI, Anthropic,
Gemini, Groq, OpenRouter, Grok) in one terminal environment. It ships a
Rich/Click CLI and an interactive Textual TUI.

## Features

- **Multi-Provider Support**: Seamless routing across Ollama, OpenAI, Anthropic, Groq, Gemini, OpenRouter, and Grok
- **ReAct Execution Loop**: Custom Reasoning + Acting loop with error recovery and auto-repair
- **Context Management**: Token counting, sliding window, and automatic summarization
- **Tool System**: Built-in tools (file ops, shell, web search) + extensible registry
- **MCP Client**: Built-in JSON-RPC client with stdio and Streamable HTTP transports, tool discovery, and tool invocation
- **Safety Guardrails**: Filesystem scope, shell deny/allow policy, network policy, approval prompts, secret redaction, and a JSONL audit log
- **Vector Memory (RAG)**: ChromaDB-backed long-term memory with persistent collections and an in-memory fallback (optional `rag` extra)
- **Textual TUI**: Interactive chat log, live streaming preview, slash commands, status bar, and modal tool approvals
- **Benchmarking**: Repeatable task files with latency, token, iteration, and pass/fail reporting (JSON + Markdown)

## Architecture

```
┌────────────────────────────────────────────────────────
│          CLI (Rich/Click) + Textual TUI                │
└───────────────────────────┬────────────────────────────
                            ▼
┌────────────────────────────────────────────────────────
│          ReAct Agent Loop & Context Pruner             │
└───────────────────────────┼───────────────────────────┘
     │                      │                      │
     ▼                      ▼                      ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Provider   │    │  MCP Client  │    │ Local Vector │
│    Router    │    │  & Safety    │    │  DB Memory   │
│(Ollama/Cloud)│    │   Guardrails │    │ (optional)   │
└──────────────┘    └──────────────┘    └──────────────┘
```

## Quick Start

### Installation

#### Recommended: `uv` (isolated tool install)

```bash
# All extras: TUI + RAG + web search
uv tool install "corvustui[all] @ git+https://github.com/Zakariya-Zahid/corvustui.git"

# Smaller TUI-only install
uv tool install "corvustui[tui] @ git+https://github.com/Zakariya-Zahid/corvustui.git"
```

#### From PyPI (once the first release is published)

```bash
uv tool install "corvustui[all]"
# or
pipx install "corvustui[all]"
```

#### curl installer

```bash
curl -fsSL https://raw.githubusercontent.com/Zakariya-Zahid/corvustui/main/install.sh | bash

# TUI only
curl -fsSL https://raw.githubusercontent.com/Zakariya-Zahid/corvustui/main/install.sh | CORVUS_EXTRAS=tui bash
```

The installer prefers `uv tool install`, then `pipx`, then `pip --user`.

#### pipx or pip

```bash
pipx install "corvustui[all] @ git+https://github.com/Zakariya-Zahid/corvustui.git"
pip install --user "corvustui[all] @ git+https://github.com/Zakariya-Zahid/corvustui.git"
```

#### From a local checkout (development)

```bash
git clone https://github.com/Zakariya-Zahid/corvustui.git
cd corvustui
./install.sh                    # local, all extras
# or
uv tool install --editable ".[all]"
# or
pip install -e ".[all]"
```

> The `all` extra pulls ChromaDB; its embedding model downloads on first use.
> Use `tui` for a lighter install. The curl installer needs the repository to
> be public (or a token for private repositories).

#### Upgrade and uninstall

```bash
# Re-run the install with --force to upgrade a git install
uv tool install --force "corvustui[all] @ git+https://github.com/Zakariya-Zahid/corvustui.git"

# Remove it
uv tool uninstall corvustui
```

### Configuration

```bash
# Copy example config
cp .env.example .env

# Edit with your API keys
# At minimum, ensure Ollama is running for local models
```

### Running

```bash
# Interactive CLI mode (default)
corvustui

# Interactive Textual TUI
corvustui tui

# With specific provider/model
corvustui --provider openai --model gpt-4o
corvustui --provider ollama --model qwen3.5:0.8b tui

# Single prompt
corvustui run "Explain quantum computing"

# Enable RAG memory
corvustui --rag --rag-path ./memory

# Run the built-in benchmark tasks against a live model
corvustui --provider ollama --model qwen3.5:0.8b bench \
  --output benchmarks/results/latest.json \
  --markdown benchmarks/results/latest.md

# Start configured MCP servers and auto-approve side-effecting tools
corvustui --mcp-config ./mcp.json --yes

# Add another filesystem root and a custom audit log
corvustui --allow-path ~/projects --audit-log ./audit.jsonl

# Inspect MCP servers/tools without starting an LLM session
corvustui mcp list --config ./mcp.json
corvustui mcp tools --config ./mcp.json
corvustui mcp call filesystem read_file --args '{"path": "README.md"}' --config ./mcp.json
```

If the configured default Ollama model is not installed, the CLI and TUI use
the only installed model automatically and print a notice. When several models
are installed, the interactive CLI asks you to choose and the TUI starts with
the first installed model (switch with `/models` or Ctrl+O). An explicit model
name is never silently replaced.

## Project Structure

```
corvustui/
├── src/corvustui/
│   ├── config/          # Settings & configuration
│   ├── core/            # Main agent orchestration
│   ├── providers/       # LLM provider implementations
│   │   ├── base.py      # Abstract base classes
│   │   ├── ollama.py    # Ollama local provider
│   │   ├── openai_compat.py  # OpenAI, Groq
│   │   ├── anthropic.py # Anthropic provider
│   │   └── gemini.py    # Google Gemini provider
│   ├── react/           # ReAct execution loop
│   ├── context/         # Context window management
│   ├── memory/          # Vector memory (ChromaDB + in-memory)
│   ├── tools/           # Tool system
│   │   ├── base.py      # Tool base classes
│   │   ├── registry.py  # Tool registry
│   │   └── builtin.py   # Built-in tools
│   ├── mcp/             # JSON-RPC client, stdio/HTTP transports, tool bridge
│   ├── safety/          # Policy, approvals, redaction, audit log
│   ├── tui/             # Textual app, streaming preview, approval modal
│   ├── benchmark/       # Task loading, runner, JSON/Markdown reports
│   └── cli.py           # CLI entry point
├── benchmarks/          # Sample tasks and recorded live results
├── tests/
├── install.sh           # uv/pipx/pip installer for local and remote installs
├── pyproject.toml
└── .env.example
```

The bundled Bend compiler checkout (`bend/`) and the local virtualenv
(`corvusenv/`) are development-only and are excluded from git.

## Usage Examples

### Basic Agent Usage

```python
import asyncio
from corvustui import CorvusAgent, AgentConfig, ProviderType

async def main():
    config = AgentConfig(
        provider_type=ProviderType.OLLAMA,
        model="qwen3.5:0.8b",
        max_iterations=10,
        enable_rag=True,
        rag_path="./memory",
    )

    async with CorvusAgent(config) as agent:
        # Register custom tool
        async def my_tool(query: str) -> str:
            return f"Custom result for: {query}"
        
        agent.register_function_tool(
            "custom_search",
            "Search custom knowledge base",
            my_tool,
            {"query": {"type": "string"}},
        )

        # Run query
        result = await agent.run("What is the capital of France?")
        print(result.final_answer)

asyncio.run(main())
```

### Step Callbacks

```python
async with CorvusAgent(config) as agent:
    def on_step(step):
        if step.thought:
            print(f"💭 {step.thought}")
        if step.action:
            print(f"🔧 {step.action}")

    result = await agent.run("Analyze this codebase", on_step=on_step)
```

### Streaming

`CorvusAgent.run_streaming()` requests a real provider stream and forwards each
content fragment to `on_chunk`. The completed `ReActResult` is still returned.
The TUI uses this for its live preview; the CLI `run` and REPL paths currently
use non-streaming turns.

```python
async with CorvusAgent(config) as agent:
    def on_chunk(text):
        print(text, end="", flush=True)

    result = await agent.run_streaming("Summarize this repository", on_chunk)
    print(result.final_answer)
```

> Streaming may include intermediate ReAct text on iterations that call tools,
> not only the final answer.

## Textual TUI

```bash
pip install -e ".[tui]"
corvustui tui
```

The TUI provides a scrolling chat log, a live streaming preview, a status bar
(provider/model, context tokens, connected MCP servers), and modal approval
dialogs for side-effecting tools. It supports `/help`, `/clear`, `/stats`,
`/tools`, `/mcp`, `/safety`, `/models`, `/model <name>`, and `/quit`. If the
configured default Ollama model is missing and several models are installed,
the TUI starts with the first installed model and lets you switch with
`/models` or Ctrl+O. Approvals fail closed if the modal cannot be shown.

## Benchmarking

```bash
# Built-in tasks, human-readable table + JSON/Markdown reports
corvustui --provider ollama --model qwen3.5:0.8b bench \
  --output benchmarks/results/latest.json \
  --markdown benchmarks/results/latest.md

# Custom tasks (JSON or YAML), first two only
corvustui bench --tasks ./benchmarks/tasks.json --max-tasks 2
```

Each task records status, score, latency, iterations, and tokens. `match` can be
`contains` (default), `exact`, or `regex`; an `exact` task only passes when the
trimmed answer equals the expected value, so a correct number embedded in extra
prose does not silently pass.

```json
{
  "tasks": [
    {"id": "math", "prompt": "What is 17 * 23? Reply with only the number.",
     "expected": "391", "match": "exact"},
    {"id": "style", "prompt": "Reply with exactly the word corvus.",
     "expected": "corvus", "match": "exact"}
  ]
}
```

A recorded live baseline is in
[`benchmarks/results/ollama-qwen3.5-0.8b.md`](benchmarks/results/ollama-qwen3.5-0.8b.md).
It is reported as-is: 1/2 tasks passed. The arithmetic answer was correct but
included extra prose, so the exact-match arithmetic check failed.

Benchmarks clear the conversation context between tasks. RAG memory is not
cleared automatically when `--rag` is enabled, so use `--no-rag` or a
disposable `--rag-path` for isolated runs. Tool-using tasks need `--yes` or an
approval-capable interface.

## RAG Memory

```bash
# In-memory vector memory (no extra dependencies)
corvustui --rag

# Persistent ChromaDB collection
pip install -e ".[rag]"
corvustui --rag --rag-path ./memory
```

`ChromaMemory` uses ChromaDB's built-in embedding function by default; the
model is downloaded on first use. A custom embedder can be injected through
`ChromaMemory(..., embedder=...)` for offline or deterministic deployments.
`CORVUS_RAG_TOP_K` and `CORVUS_RAG_SIMILARITY_THRESHOLD` control retrieval.
Retrieved memories are injected per turn and successful interactions are
stored after completion.

## Built-in Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write content to file |
| `edit_file` | Replace text in a file and return a diff |
| `list_files` | List directory contents |
| `shell` | Execute shell commands |
| `web_search` | Search the web via the Tavily API (requires a key) |
| `duckduckgo_search` | Search the web via DuckDuckGo with backend fallback (no key) |

Tools that can mutate files or execute commands (`write_file`, `edit_file`,
`shell`, and every MCP tool) require approval. Read-only built-ins run without
a prompt unless the path or network policy rejects them.

## MCP Servers

CorvusTUI includes an in-tree MCP client, so no `mcp` extra is required. The
config format is compatible with Claude Desktop-style `mcpServers` mappings;
JSON and YAML are supported.

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

- `command`/`args`/`cwd`/`env` start a stdio server.
- `inherit_env: false` limits the child environment to common safe variables
  (`PATH`, `HOME`, locale, terminal) plus the server's explicit `env`; provider
  API keys are not inherited.
- `url`/`headers` use Streamable HTTP (JSON or SSE responses).
- `${VAR}` references are expanded from the environment; an unset variable is
  an error, never an empty string.
- Discovered tools are registered as `mcp_<server>_<tool>` and are always
  approval-gated. Tool descriptions from a server are untrusted input; review
  a server before enabling it.
- A failing server is reported without preventing other servers or the LLM
  session from starting.

Use `corvustui mcp list`, `corvustui mcp tools`, and `corvustui mcp call` to
inspect or test servers. The REPL also accepts `mcp` and `tools`; the TUI
accepts `/mcp` and `/tools`.

## Safety Guardrails

Safety is enforced in the ReAct loop before a tool executes:

- **Filesystem scope**: reads/writes are limited to the current directory plus
  `CORVUS_SAFETY_ALLOWED_PATHS` / `--allow-path`. Symlink escapes are resolved
  and rejected. `.env`, SSH/AWS/GnuPG credentials, private keys, and similar
  sensitive paths are denied even when outside roots are allowed.
- **Shell policy**: catastrophic patterns (`rm -rf /`, `rm -rf .`, `sudo`,
  disk formatting, `curl | sh`, fork bombs, namespace/mount escapes) are
  blocked. Every command in a `&&`/`;`/pipe/newline chain is checked,
  including wrapper commands such as `env`, `timeout`, `xargs`, and
  `busybox`. Commands nested in `sh -c`, `bash -lc`, `eval`, `find -exec`,
  `env -S`, `$(...)`, and backticks are inspected too. Recursive removal of
  `.`, `..`, `*`, `$HOME`, `$PWD`, `{}`, or targets supplied on `xargs` stdin
  is blocked, while a named subdirectory such as `./build` remains allowed.
  Paths in commands, option values, redirections, and `cd`/`pushd` targets are
  expanded and checked against the same roots. An optional allowlist mode
  permits only named commands.
- **Network policy**: `web_search`, `duckduckgo_search`, HTTP MCP servers, and
  network shell commands can be disabled with
  `CORVUS_SAFETY_NETWORK_ENABLED=false`.
- **Approval**: side-effecting tools prompt in the REPL and the Textual TUI.
  Non-interactive `run` and `bench` deny them unless `--yes` is supplied. A
  denied call never reaches the tool.
- **Redaction**: provider keys, bearer tokens, private keys, JWTs, and
  `api_key=...`-style assignments are redacted from tool observations,
  approval prompts, and audit entries.
- **Audit**: every decision is appended as JSONL (default
  `~/.corvustui/audit.jsonl`, created owner-readable only) with tool, redacted
  arguments, rule, outcome, duration, and a bounded output preview.

This is defence-in-depth, not a kernel sandbox. Code run by interpreters
(`python -c`, scripts), here-documents, and stdio MCP servers is not fully
inspectable; that is why shell and MCP tools are approval-gated. The network
policy also cannot stop a stdio MCP server from making its own network calls.
Use `--no-safety` only in an isolated environment.

## Environment Variables

See `.env.example` for all configuration options.

Key variables:
- `CORVUS_DEFAULT_PROVIDER`: Default LLM provider
- `CORVUS_DEFAULT_MODEL`: Default Ollama model (default: `qwen3.5:0.8b`)
- `CORVUS_OPENAI_API_KEY`: OpenAI API key
- `CORVUS_ANTHROPIC_API_KEY`: Anthropic API key
- `CORVUS_GROQ_API_KEY`: Groq API key
- `CORVUS_GEMINI_API_KEY`: Google Gemini API key
- `CORVUS_OPENROUTER_API_KEY`: OpenRouter API key
- `CORVUS_GROK_API_KEY`: xAI Grok API key
- `CORVUS_TAVILY_API_KEY`: Tavily web search API key
- `CORVUS_MCP_SERVERS_CONFIG`: Default MCP server config path
- `CORVUS_MCP_ENABLED`: Set to `false` to disable MCP startup
- `CORVUS_MCP_TIMEOUT`: Default per-server request timeout in seconds
- `CORVUS_CHROMADB_PATH`: Persistent ChromaDB collection path
- `CORVUS_EMBEDDING_MODEL`: Embedding model for ChromaDB (default `all-MiniLM-L6-v2`)
- `CORVUS_RAG_TOP_K`: Number of memories retrieved per turn (default `5`)
- `CORVUS_RAG_SIMILARITY_THRESHOLD`: Minimum similarity for retrieved memories (default `0.7`)
- `CORVUS_SAFETY_ENABLED`: Set to `false` to disable guardrails
- `CORVUS_SAFETY_ALLOWED_PATHS`: JSON list of extra filesystem roots
- `CORVUS_SAFETY_SHELL_MODE`: `deny_dangerous`, `allowlist`, or `unrestricted`
- `CORVUS_SAFETY_NETWORK_ENABLED`: Set to `false` to block network tools
- `CORVUS_SAFETY_REQUIRE_APPROVAL`: Set to `false` to skip approval prompts
- `CORVUS_SAFETY_AUTO_APPROVE`: Set to `true` to approve side effects
- `CORVUS_SAFETY_AUDIT_PATH`: JSONL audit log path

Provider-specific defaults (`CORVUS_OPENAI_DEFAULT_MODEL`, etc.) are used when
`--model` is omitted, so a local Ollama tag is never sent to a cloud API.

## Formal Laws (Bend)

The audited behaviours are specified in `LAWS.bend` and proved in
`PROOF.bend`. The laws cover model-list honesty, tool-parameter aliases,
search-backend fallback, multi-tool observations, observation bounds,
per-run state reset, unknown-tool rejection, MCP tool honesty, safety
decision precedence, filesystem/network/shell policy, redaction, and audit
recording.

The Bend compiler is an optional development tool and is not committed (the
95 MB checkout lives in `bend/`). The Python suite runs the proof gate when
`bend/bin/bend` exists and skips those two tests otherwise.

```bash
# With a local Bend checkout (must print ALL PROOFS CHECK)
./bend/bin/bend PROOF.bend

# Run the Python test suite, including the Bend gate when available
pytest
```

## Development

```bash
# Install dev dependencies (Textual is included for TUI tests)
pip install -e ".[dev]"

# Include ChromaDB for the real-persistence test
pip install -e ".[dev,rag]"

# Run tests
pytest

# Lint
ruff check src/
mypy src/
```

See [PUBLISHING.md](PUBLISHING.md) for the PyPI release checklist.

## Roadmap (FYP Phases)

- [x] **Phase 1**: Provider abstraction + ReAct loop (Module 1)
  - Complete: seven providers, live model listing/validation, hardened ReAct
    loop, built-in tools, context management, tests, Bend proof gate.
- [x] **Phase 2**: MCP protocol + Safety guardrails (Module 2)
  - Complete: in-tree JSON-RPC MCP client (stdio + Streamable HTTP), config
    loading, tool discovery/invocation, approval-gated MCP tools, filesystem
    scope, shell/network policy, secret redaction, JSONL audit log, CLI
    commands, and Phase 2 Bend laws.
- [x] **Phase 3**: Textual TUI + ChromaDB RAG + Benchmarking (Module 3)
  - Complete: Textual TUI with modal approvals, slash commands, status bar,
    and live streaming preview; real provider streaming through
    `CorvusAgent.run_streaming()`; ChromaDB persistence and default embeddings
    verified at runtime; benchmark harness with JSON/Markdown reports and a
    recorded Ollama baseline; 356 tests and the Bend proof gate pass.
  - Limits: the CLI still uses non-streaming turns; the recorded benchmark is
    a small 2-task smoke run, not a broad model comparison; ChromaDB's default
    embedding model is downloaded on first use.

See [PHASE_REPORT.md](PHASE_REPORT.md) for the evidence and remaining gaps.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

Inspired by Anthropic's Claude Code architecture, adapted for local-first, multi-provider agentic workflows.