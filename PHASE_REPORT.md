# CorvusTUI Phase Audit

Date: 2026-11-26

This report compares the README's phase claims with the code that is actually
present in the repository. "Complete" means the feature exists, has tests, and
was exercised against a live or controlled provider. "Partial" means useful
code exists but a required part is missing. "Claimed only" means the README or
architecture diagram mentions it but there is no implementation.

## Verdict

| Phase | README claim | Verdict | Evidence |
|-------|--------------|---------|----------|
| **Phase 1**: Provider abstraction + ReAct loop | Checked | **Complete (hardened)** | Seven providers, dynamic model listing/validation, ReAct loop, tool system, context manager, tests, live Ollama + shell-tool run |
| **Phase 2**: MCP protocol + Safety guardrails | Unchecked, but feature list says "MCP Protocol Ready" | **Complete** | Built-in JSON-RPC MCP client (stdio + Streamable HTTP), config loading, tool discovery/invocation, filesystem/shell/network policy, approvals, redaction, JSONL audit log, CLI commands, tests, and Phase 2 Bend laws |
| **Phase 3**: Textual TUI + ChromaDB RAG + Benchmarking | Checked | **Complete (with documented limits)** | Textual TUI with modal approvals and live streaming preview, real agent-level streaming, ChromaDB persistence + default embeddings verified at runtime, benchmark harness with JSON/Markdown reports and a recorded Ollama baseline, 356 tests, Bend gate |

## Phase 1 - Complete

### Providers

- `OllamaProvider`, `OpenAIProvider`, `AnthropicProvider`, `GroqProvider`,
  `GeminiProvider`, `OpenRouterProvider`, and `GrokProvider` are implemented.
- `list_models()` returns live names for every provider. It never returns a
  canned/sample list.
- `validate_model()` fails fast with actionable errors:
  - Ollama unreachable -> "Start it with `ollama serve`".
  - Ollama reachable with no models -> "no models are installed; `ollama pull`".
  - Ollama model missing -> installed-model list plus `ollama pull` hint.
  - Cloud provider without a key -> explicit environment-variable hint.
- Ollama bare names now match the installed `:latest` tag.
- If the configured default Ollama model is missing, interactive and
  single-run startup use the only installed model (with a notice). With
  multiple installed models, the REPL prompts or `run` requires `--model`,
  while the TUI starts with the first installed model and offers `/models`;
  explicit model names are never silently replaced.
- The default Ollama model is `qwen3.5:0.8b`; the uninstalled
  `llama3.1:8b` default was removed from settings and `.env.example`.
- Ollama chat requests send the full OpenAI-compatible tool wrapper
  (`{"type": "function", "function": {...}}`) and drop malformed definitions.
  Sending only the inner function body caused HTTP 500
  `Failed to parse tools: Unsupported tool type` on the first prompt.
- Provider-specific defaults prevent a local Ollama tag from being sent to a
  cloud API.
- Streaming tool-call fragments are aggregated; Anthropic/Gemini message
  conversion and turn merging are covered by tests.

### ReAct loop

- Per-run state reset (`steps`, `total_tokens`, `tool_calls`).
- Native multi-tool responses execute every call, in order, with matching
  `tool_call_id` values.
- Text-based ReAct observations are sent as `USER` messages, not as invalid
  `TOOL` messages without an assistant tool call.
- Malformed `Action: tool({...})` and `TOOL_CALL: tool({...})` forms are parsed.
- Single-quoted JSON, trailing commas, and surrounding prose are repaired.
- Unknown tools are rejected without execution and list the available tools.
- Tool exceptions, invalid arguments, and timeouts become error observations.
- Observations are bounded at 12,000 characters.
- `max_iterations`, provider errors, and rate limits terminate with an explicit
  status.

### Tools and search

- Built-in tools: `read_file`, `write_file`, `edit_file`, `list_files`,
  `shell`, `web_search` (Tavily), and `duckduckgo_search`.
- Parameter aliases: `file_path`, `output_file`, `value`, `directory`, `sh`,
  and `search`.
- Empty file writes are valid; empty `old_string` edits are rejected.
- Reads and command output are bounded with accurate truncation metadata.
- Numeric tool parameters sent as strings are coerced.
- DuckDuckGo tries `duckduckgo`, `bing`, then `brave`; a total failure reports
  every backend error instead of pretending there were no results.
- Tavily errors are actionable, including missing key and 401 responses.

### Context and memory

- `ContextManager` counts tokens, reports usage, slides the window, and
  summarizes old turns.
- `get_history()` excludes the main system prompt (the ReAct loop owns it) but
  includes the rolling summary.
- Summarization failure falls back to a sliding window.
- RAG memories are injected per run without mutating stored context.

### Formal specification

- `LAWS.bend` contains 32 laws: 12 Phase 1 laws covering model-list honesty,
  aliases, search fallback, multi-tool observations, truncation, state reset,
  and unknown-tool dispatch, plus 20 Phase 2 laws covering MCP exposure,
  safety precedence, path/network/shell policy, redaction, audit, and MCP
  result status.
- `PROOF.bend` proves all 32.
- The test suite includes a negative control: a tampered law produces
  `SOME PROOFS FAIL`, so the gate is not vacuous.

## Phase 2 - Complete

### MCP client and tool bridge

- `src/corvustui/mcp/protocol.py` implements JSON-RPC 2.0 request, response,
  notification, error, and validation types.
- `StdioTransport` launches the configured command with `create_subprocess_exec`
  (no shell), speaks newline-delimited JSON, matches responses by id, captures
  bounded stderr for diagnostics, answers `ping`, rejects unknown server
  requests, and terminates the process group on close. `inherit_env: false`
  limits the child environment to safe base variables plus the server's
  explicit `env`, keeping provider keys out of untrusted servers.
- `HTTPTransport` implements Streamable HTTP: JSON and SSE responses, session
  id propagation, protocol-version headers, timeouts, and actionable HTTP
  errors.
- `MCPClient` performs the `initialize` handshake, sends
  `notifications/initialized`, lists tools, calls tools, supports `ping`, and
  validates result shapes. It advertises no optional client capabilities
  (roots, sampling, elicitation) because those are not implemented. MCP
  `isError` results become failed `ToolResult`s.
- `MCPServerConfig` / `load_mcp_config` load Claude Desktop-style
  `mcpServers` mappings from JSON or YAML, plus a list form. They validate
  command/url exclusivity, transport, timeout, duplicate names, and `${VAR}`
  expansion in commands, args, cwd, env, URLs, and headers. An unset variable
  is an error, never an empty string. `CORVUS_MCP_TIMEOUT` supplies the
  default when a server omits `timeout`.
- `MCPManager` starts servers independently, reports partial failures, and
  registers discovered tools as `mcp_<server>_<tool>` with collision handling
  and a 64-character bound. Server failure does not prevent other servers or
  the LLM session from starting.
- `MCPTool` maps text/image/audio/resource content blocks, marks MCP tools as
  approval-required, and returns MCP errors as tool errors.
- CLI: `--mcp-config`, `--no-mcp`, `mcp list`, `mcp tools`, `mcp call`, plus
  REPL `mcp` and `tools` commands. Startup reports connected servers and
  per-server errors.

### Safety guardrails

- `SafetyPolicy` defines allowed filesystem roots, sensitive-path denial,
  shell mode (`deny_dangerous`, `allowlist`, `unrestricted`), network access,
  approval requirements, redaction, and the audit path.
- `SafetyGuard.authorize()` runs before every tool execution. Denied calls
  never reach `tool.execute`.
- Filesystem scope: relative and absolute paths are resolved (including
  symlinks), checked against allowed roots, and rejected if sensitive. `.env`,
  SSH/AWS/GnuPG/Kubernetes credentials, private keys, `/etc/shadow`, and
  similar paths are denied even when outside roots are allowed.
- Shell policy: catastrophic patterns (`rm -rf /`, `rm -rf .`, `sudo`, `mkfs`,
  `dd of=/dev/...`, `curl | sh`, fork bombs, `chroot`/`unshare`/`nsenter`/
  `mount` escapes), network commands when networking is disabled, and path
  arguments/redirections outside allowed roots are denied. Commands are
  tokenised with shell punctuation and every segment of a `&&`/`;`/pipe/
  newline chain is checked, including `env`, `timeout`, `nice`, `stdbuf`,
  `xargs`, `busybox`, and `toybox` wrappers. Commands nested in `sh -c`,
  `bash -lc`, `eval`, `find -exec`, `env -S`, `$(...)`, and backticks are
  expanded and checked. Recursive removal of `.`, `..`, `*`, `$HOME`,
  `$PWD`, `{}`, or targets supplied on `xargs` stdin is blocked even through
  those wrappers, while `rm -rf ./build` remains allowed. Paths embedded in
  options (`-O/tmp/x`), environment variables (`$HOME/.ssh`), and `cd`/`pushd`
  targets are expanded and checked. Allowlist mode requires every command in
  a chain to match and rejects command substitution.
- Network policy: built-in search tools, network shell commands, and HTTP MCP
  servers are blocked when networking is disabled.
- Approval: `write_file`, `edit_file`, `shell`, and every MCP tool require
  approval. The REPL prompts; non-interactive `run` denies unless `--yes` is
  supplied. `--yes` and `CORVUS_SAFETY_AUTO_APPROVE` opt into auto-approval.
- Redaction: provider keys, bearer tokens, private-key blocks, JWTs, cloud
  access keys, and strong `key=value` assignments are redacted from
  observations, approval prompts, and audit entries.
- Audit: every decision (allowed, denied, approved, failed, timed out) is
  appended as JSONL with tool, redacted arguments, rule, outcome, duration,
  and a bounded output preview. The log is created owner-readable only (and an
  existing log is tightened to `0600`), and audit write failures do not break
  execution.
- CLI: `--yes`, `--allow-path`, `--no-safety`, `--audit-log`; the REPL
  `safety` command shows the active policy.

### Phase 2 tests and formal specification

- `tests/test_safety.py` covers path scope, symlink escape, sensitive files,
  option/env/cd path escapes, shell chains, wrapper commands, nested shells,
  `$(...)`/backtick substitutions, destructive `rm` targets (including `$PWD`
  and `xargs` stdin), privileged commands, allowlist mode, network policy,
  approval outcomes, redaction, audit events/permissions, and ReAct integration
  (deny, approve, redact, tool exceptions, safety-check failure).
- `tests/test_mcp.py` covers JSON-RPC validation, JSON/YAML config, env
  expansion, default timeout, duplicate names, environment filtering,
  capabilities, name sanitisation, real stdio subprocess handshake/tool calls,
  timeouts, crashes, HTTP/SSE, partial server failure, redacted startup errors,
  tool errors, and agent integration including custom-prompt preservation.
- `LAWS.bend` now contains 32 laws: the 12 Phase 1 laws plus 20 Phase 2 laws
  for MCP exposure, deny precedence, approval, path scope, network policy,
  redaction, audit recording, shell policy, and MCP result status.
- `PROOF.bend` proves all 32; the negative control still produces
  `SOME PROOFS FAIL`.

Known Phase 2 limits (documented, not hidden):

- MCP tools are supported; resources, prompts, sampling, roots, OAuth, and
  `tools/list_changed` re-discovery are not.
- Shell path inspection is static and defence-in-depth, not a kernel sandbox.
  Code run by interpreters (`python -c`, scripts), here-documents, and stdio
  MCP servers is not fully inspectable; shell and MCP tools remain
  approval-gated.
- The network policy blocks HTTP MCP servers and recognised network shell
  commands, but a stdio MCP server is a trusted child process and can make its
  own network calls.
- Audit logs are append-only JSONL with no rotation, signing, or retention
  policy.
- Approval is terminal-only; the Textual TUI adds a modal dialog, but there is
  no remote or non-terminal approval channel.

## Phase 3 - Complete (with documented limits)

### RAG

- `VectorMemory`, `InMemoryVectorMemory`, and `ChromaMemory` exist.
- The in-memory backend works without optional dependencies and is covered by
  metadata-filter, delete, and clear tests.
- `ChromaMemory` lazily creates a persistent ChromaDB client and configures
  new collections with cosine distance. With no injected embedder it uses
  ChromaDB's built-in embedding function, so the `rag` extra only needs
  `chromadb` (the previous `sentence-transformers` requirement was removed).
- Runtime verification used `chromadb 1.5.9`: a memory was added, searched,
  and read back after reopening the persistent client. A unit test also uses a
  real `PersistentClient` to prove persistence, alongside fake-client tests for
  add/search/threshold/filter/delete/clear/error paths.
- RAG injection now honors `CORVUS_RAG_TOP_K` and
  `CORVUS_RAG_SIMILARITY_THRESHOLD` (with per-agent overrides) instead of a
  hard-coded `top_k=3`.
- Limits: ChromaDB downloads its default embedding model on first use unless a
  custom embedder is injected; there is no collection migration/versioning.

### TUI

- `src/corvustui/tui/` contains `CorvusTUIApp`, `ApprovalScreen`, and
  `TUIApprover`.
- The app has a scrolling chat log, prompt input, status bar
  (provider/model, token usage, MCP count, state), footer, and a live streaming
  preview. It supports `/help`, `/clear`, `/stats`, `/tools`, `/mcp`,
  `/safety`, `/models`, `/model <name>`, and `/quit`.
- Side-effecting tools use a modal approval screen. If the modal cannot be
  shown, approval fails closed.
- The TUI preserves the Ollama fallback: a missing default is replaced with
  the only installed model, or the first of several, with a notice. The agent
  still becomes ready, and `/models` or Ctrl+O switches models.
- Startup clears the chat before entering the agent, and message blocks ignore
  a removed body during mount, so fallback notices no longer race the mount.
- Tests use Textual's headless `run_test()`/`Pilot`: startup, a full turn,
  slash commands, model switching, approval/denial, unknown commands, and the
  non-interactive-terminal CLI guard.
- A live launch was exercised through a pseudo-terminal: the status bar showed
  `ollama/qwen3.5:0.8b | tokens 3/8192 | MCP 0 | Ready`, and `/quit` exited 0.
- Limits: the CLI remains the default interface and still uses non-streaming
  turns. A live TUI turn was recorded through a pseudo-terminal (prompt,
  streamed turn, and quit), while turn behavior is also covered headlessly
  with deterministic fake agents.

### Benchmarking

- `src/corvustui/benchmark/` contains task loading, scoring, the runner, and
  JSON/Markdown report generation.
- `corvustui bench` supports built-in or JSON/YAML task files, `--max-tasks`,
  `--output`, `--markdown`, and `--json`. Each result records status, score,
  latency, iterations, tokens, answer, and redacted error.
- Scoring supports `contains`, `exact`, and `regex`; exact matching prevents a
  correct substring inside incorrect prose from passing.
- Tests cover valid/invalid task files, scoring modes, context resets,
  `max_iterations` restoration, runner aggregation, report formatting, and CLI
  wiring.
- A live baseline was recorded with `qwen3.5:0.8b`:
  `benchmarks/results/ollama-qwen3.5-0.8b.{json,md,log}`. It passed 1/2 tasks
  (average 17,591 ms, 2,610 tokens). Arithmetic failed exact matching because
  the model answered `The answer is 391`; instruction-following passed with
  `corvus`. The result is reported as-is.
- Limits: this is a two-task smoke baseline, not a broad model comparison, and
  it does not yet measure tool-selection success or repeated-run variance.
  RAG memory is not reset between tasks when `--rag` is enabled; use
  `--no-rag` or a disposable `--rag-path` for isolated tasks. Tool-using tasks
  need `--yes` or an approval-capable interface.

### Streaming

- Provider streams are now consumed by the ReAct loop, accumulated into a
  completion response, and forwarded fragment-by-fragment through
  `on_chunk`. `CorvusAgent.run_streaming()` uses this path and still returns
  the completed `ReActResult`.
- The TUI uses `run_streaming()` and shows a transient live preview before
  rendering the final answer.
- Live check with `qwen3.5:0.8b`: 2 chunks, first chunk at 16.36 s, completed
  at 16.38 s, answer `corvus`.
- Limits: the CLI `run`/REPL paths still use non-streaming turns; intermediate
  ReAct text may be included in the stream on tool-calling iterations.

## Verification

Run from the repository root:

```bash
./corvusenv/bin/python -m pytest -q
# 356 passed

./bend/bin/bend PROOF.bend
# ALL PROOFS CHECK
```

Live checks performed:

```bash
./corvusenv/bin/corvustui models --provider ollama
# sadiq-bd/llama3.2-1b-uncensored:latest
# maternion/minicpm-v4.7-heretic:1b
# qwen3.5:0.8b

CORVUS_OLLAMA_BASE_URL=http://127.0.0.1:9 ./corvusenv/bin/corvustui models --provider ollama
# Ollama is not reachable ... Start it with 'ollama serve'

./corvusenv/bin/corvustui --provider ollama --model qwen3.5:0.8b \
  --max-iterations 4 --no-mcp run "Use the shell tool to run printf 'cli-live-ok' ..."
# Observation: shell: Safety blocked: Tool 'shell' was not approved.

./corvusenv/bin/corvustui --provider ollama --model qwen3.5:0.8b \
  --max-iterations 3 --no-mcp --yes run "Use the shell tool to run printf 'cli-live-ok' ..."
# Observation: shell: cli-live-ok
# (an earlier identical run also reached the final answer "cli-live-ok";
#  small local models can repeat a tool call until max-iterations is hit)

./corvusenv/bin/corvustui mcp list --config ./mcp.json
# fixture  stdio  /usr/bin/python ...  yes

./corvusenv/bin/corvustui mcp tools --config ./mcp.json
# MCP: fixture (stdio, 7 tools)
# mcp_fixture_echo, mcp_fixture_add, ...

./corvusenv/bin/corvustui mcp call fixture echo \
  --args '{"text":"live"}' --config ./mcp.json
# echo:live

./corvusenv/bin/corvustui mcp call fixture secret --config ./mcp.json
# api_key=[REDACTED]

./corvusenv/bin/corvustui mcp call slow slow \
  --args '{"seconds":1}' --config ./mcp-timeout.json
# Error: [slow] MCP server did not answer 'tools/call' within 0.2s.
```

Phase 3 live checks:

```bash
# Textual TUI through a pseudo-terminal, then /quit
TERM=xterm-256color expect -c 'spawn ./corvusenv/bin/corvustui \
  --provider ollama --model qwen3.5:0.8b tui; expect "Ready"; send "/quit\r"; ...'
# status: ollama/qwen3.5:0.8b | tokens 3/8192 | MCP 0 | Ready
# exit: 0

# Real ChromaDB persistence and default embedding
./corvusenv/bin/python - <<'PY'
from corvustui.memory.vector import ChromaMemory
...
PY
# stats: {'total_entries': 1, 'collection_name': 'runtime_check', ...}
# persisted_after_reopen: 1
# result: 'The corvus is a genus of birds including crows and ravens.'

# Live benchmark (2 of the built-in tasks)
./corvusenv/bin/corvustui --provider ollama --model qwen3.5:0.8b \
  --max-iterations 3 bench --max-tasks 2 \
  --output benchmarks/results/ollama-qwen3.5-0.8b.json \
  --markdown benchmarks/results/ollama-qwen3.5-0.8b.md
# Passed: 1/2  Errors: 0  Average latency: 17591.0 ms  Tokens: 2610
# arithmetic: "The answer is 391" -> exact-match failure
# instruction-following: "corvus" -> pass

# Real provider streaming through CorvusAgent
./corvusenv/bin/python - <<'PY'
result = await agent.run_streaming("Reply with exactly the word corvus ...", on_chunk)
PY
# status: complete
# chunks: 2
# first_chunk_ms: 16361.1
# elapsed_ms: 16378.9
# answer: 'corvus'
```

Post-review Ollama/TUI fixes verified live:

```bash
# Ollama accepts the full OpenAI tool wrapper (no HTTP 500)
./corvusenv/bin/python - <<'PY'
provider = OllamaProvider(model="qwen3.5:0.8b", ...)
await provider.complete(..., tools=registry.get_definitions())
PY
# HTTP_OK content='' tool_calls=None finish=length usage=1137

# The model selected in the reported failure, through the interactive CLI
./corvusenv/bin/corvustui --provider ollama \
  --model maternion/minicpm-v4.7-heretic:1b --max-iterations 1 --no-mcp
# You: Reply with just the word ok. Do not use tools.
# ok
# exit 0

# TUI with a missing default and multiple installed models
CORVUS_DEFAULT_MODEL=missing:1b TERM=xterm-256color script -qec \
  "timeout 8 ./corvusenv/bin/corvustui --provider ollama tui" /dev/null
# Default model 'missing:1b' is not installed. Using ...
# no Traceback / NoMatches / HTTP 500; startup clean

# Live TUI turn through a pseudo-terminal: prompt -> streamed "ok" -> quit
# no Traceback / NoMatches / HTTP 500
```

A direct guard harness also confirmed:

```text
non-interactive shell: allowed=False rule=approval_denied
"timeout -s KILL 5 sh -c 'curl https://example.com'": allowed=False rule=network_disabled
'echo `curl https://example.com`': allowed=False rule=network_disabled
'echo "$(curl https://example.com)"': allowed=False rule=network_disabled
'busybox sh -c "curl https://example.com"': allowed=False rule=network_disabled
'cd -P /': allowed=False rule=outside_allowed_root
'rm -rf $PWD': allowed=False rule=shell_denylist
'find . | xargs -0 rm -rf': allowed=False rule=shell_denylist
'xargs rm -rf .': allowed=False rule=shell_denylist
'cat /etc/passwd': allowed=False rule=outside_allowed_root
approved execution: success=True output='live-ok'
audit events: 24
audit mode: 0o600
```

With `inherit_env: false`, a parent-only variable was `<unset>` in the MCP
server while the configured `env` value was visible; `mcp list` redacted a
secret embedded in a command argument.

Static checks:

```bash
./corvusenv/bin/ruff check src tests --select F,E9,I001
# All checks passed

./corvusenv/bin/python -m compileall -q src tests
# ok
```

The remaining default-Ruff findings are style choices (broad exception
handling in tool boundaries and mutable class-level JSON schemas), not
correctness failures. `mypy src/corvustui` is not yet clean (47 findings,
mostly provider response unions, MCP transport assignment, and
`Tool.execute` override signatures); it is not part of the passing gate and
should be addressed before claiming strict type safety.

## Recommended next steps

1. Add CI that runs `pytest`, Ruff, and the Bend proof gate on every change.
2. Wire real token streaming through the CLI `run`/REPL paths (the agent and
   TUI already stream).
3. Expand the benchmark set with tool-selection, multi-turn, repeated-run
   variance, and additional local/cloud models.
4. Add ChromaDB collection migration/versioning and an offline embedding
   option for deployments that cannot download the default model.
5. Extend MCP coverage to resources/prompts, OAuth for HTTP servers, and
   `tools/list_changed` re-discovery.
6. Add audit-log rotation/retention and a non-terminal approval channel.
7. Address the remaining `mypy` findings before claiming strict type safety.
