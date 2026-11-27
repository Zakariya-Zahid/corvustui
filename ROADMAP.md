# CorvusTUI Roadmap: v0.2 → v1.0

> **Status:** Proposed  
> **Owner:** Zakariya Zahid  
> **Last updated:** 2026-11-27  
> **Planning horizon:** ~10 months part-time, plus buffer  
> **Starting point:** v0.1.0 (Phases 1–3 complete)

This roadmap turns the current single-session, text-only terminal agent into a
session-aware, multimodal, extensible platform. It is written for the project
lead and for team members who need to pick up a phase and ship it without
guessing at scope.

---

## 0. How to use this document

- **Project lead:** own Phase 0, architecture decisions, release gates, and the
  cross-cutting security/quality tracks. Keep the roadmap current.
- **Core contributors:** choose an epic from your area (core, storage, providers,
  TUI, memory, platform, docs). Each epic has a clear deliverable.
- **New contributors:** start with the good-first issues in §14. Do not start
  with multimodal or session storage until the interfaces in Phase 4 exist.
- **Review cadence:** update the status table monthly. Move completed work to
  the release notes, not back into this document.

Every epic follows the same contract:

| Field | Meaning |
|-------|---------|
| **Needed** | What must exist before the epic can start |
| **System impact** | What changes for users and for the architecture |
| **Key steps** | Concrete implementation sequence |
| **Acceptance** | Observable conditions that close the epic |
| **Estimate** | Person-weeks at roughly 10 focused hours per week |

Estimates are deliberately conservative. Add 20% for reviews, integration, and
the unexpected.

---

## 1. Where we are today (v0.1.0)

| Area | Current state | Main gap |
|------|---------------|----------|
| Providers | 7 providers, text + tool calls, streaming, model listing | No capability registry; no images, documents, audio, or video |
| Agent | ReAct loop, context manager, optional RAG | No persisted session; history lives in memory only |
| TUI | Streaming chat, approvals, queue, model picker, modals | No session manager, attachments, previews, or memory browser |
| Storage | Prompt history JSONL; optional ChromaDB | No session database, artifact store, migrations, or search |
| Tools | 7 built-in tools + MCP tools | No attachment-aware tools or image generation |
| Safety | Policy, approvals, redaction, audit log | No parser sandbox, attachment limits, or cloud-upload consent |
| Distribution | `uv`, `pipx`, `curl`, PyPI metadata | No CI matrix, Homebrew, Docker, standalone binary, or plugin API |
| Quality | 356 tests, Ruff, Bend proofs | No multimodal fixtures, TUI e2e, performance gates, or fuzz tests |

**Baseline size:** ~15,300 lines of Python across 47 source modules.

The current behavior is valuable and must not regress:

- Existing CLI commands, TUI shortcuts, safety defaults, and provider behavior.
- The Ollama full tool-wrapper fix and installed-model fallback.
- `pytest`, Ruff `F,E9,I001`, and `bend PROOF.bend` as release gates.
- Local-first operation: no telemetry, no required cloud account.

---

## 2. Product principles

1. **Local-first, cloud-optional.** Ollama and local files remain first-class.
   Cloud providers are explicit choices, never silent fallbacks.
2. **Safe by default.** Unsupported input is rejected with a useful message,
   not a traceback. File parsing and generated media are bounded and auditable.
3. **Capability-driven.** A model is only asked to do what it declares it can
   do. Unknown capability means “no”, with an override for advanced users.
4. **Composable, not monolithic.** Providers, storage, tools, skills, and
   presentation are interfaces with adapters. The TUI must not call provider
   APIs directly.
5. **Terminal-native with graceful fallback.** Images and video are previewed
   when the terminal supports it; otherwise the user gets paths, metadata, and
   an external-open action.
6. **Backward compatible.** Semver, migrations, deprecation windows, and a
   working `--no-save` path for users who want the old ephemeral behavior.
7. **Testable at the boundary.** Every provider, parser, store, and TUI flow has
   a deterministic fake. No network in unit tests.
8. **Small PRs, feature flags.** Large changes land behind a setting or an
   experimental extra before becoming default.

---

## 3. Target architecture (v1.0)

```text
┌──────────────────────────────────────────────────────────────┐
│ Presentation                                                 │
│  CLI (Click)          TUI (Textual)       future: serve/web  │
└───────────────┬──────────────────┬───────────────────────────┘
                │ typed events     │ commands
┌───────────────▼──────────────────▼───────────────────────────┐
│ Application services                                         │
│  AgentRuntime   SessionService   AttachmentService            │
│  SkillService   MemoryService    CapabilityService            │
└───────────────┬──────────────────┬───────────────────────────┘
                │ domain objects   │ ports
┌───────────────▼──────────────────▼───────────────────────────┐
│ Domain                                                       │
│  Message  ContentPart  Session  Turn  Attachment  ToolCall    │
│  ModelCapabilities  MemoryEntry  SkillManifest  AgentEvent    │
└───────────────┬──────────────────┬───────────────────────────┘
                │ adapters         │ adapters
┌───────────────▼──────────────────▼───────────────────────────┐
│ Infrastructure                                               │
│  Provider adapters  SQLiteSessionStore  ArtifactStore         │
│  ChromaMemory       MCP client           SafetyGuard          │
│  File parsers       Image renderers      Audit/redaction      │
└──────────────────────────────────────────────────────────────┘
```

### Core ports (interfaces)

```python
class Provider(Protocol):
    async def complete(...) -> CompletionResponse: ...
    def capabilities(self, model: str) -> ModelCapabilities: ...

class SessionStore(Protocol):
    async def create(self, session: Session) -> Session: ...
    async def load(self, session_id: str) -> Session | None: ...
    async def append(self, session_id: str, turn: Turn) -> None: ...
    async def list(self, query: SessionQuery) -> list[SessionSummary]: ...
    async def delete(self, session_id: str, purge: bool = False) -> bool: ...

class ArtifactStore(Protocol):
    async def put(self, data: bytes, *, mime: str, name: str) -> Artifact: ...
    async def path(self, artifact_id: str) -> Path: ...
    async def delete(self, artifact_id: str) -> bool: ...

class MemoryStore(Protocol):
    async def add(self, entry: MemoryEntry) -> str: ...
    async def search(self, query: str, scope: MemoryScope) -> list[MemoryHit]: ...
    async def purge(self, scope: MemoryScope) -> int: ...

class SkillRegistry(Protocol):
    def discover(self, roots: list[Path]) -> list[SkillManifest]: ...
    def resolve(self, name: str, capabilities: ModelCapabilities) -> ResolvedSkill: ...
```

### Data flow for one turn

```text
user input + attachments
  → validate paths, MIME, size
  → store artifacts (content-addressed)
  → extract text/metadata; build ContentParts
  → capability check (text/image/pdf/audio/video/tools/image-gen)
  → context builder (recent turns + summary + memories + attachments)
  → provider streaming
  → ReAct tool loop
  → persist turn, usage, artifacts, memory candidates
  → render events in TUI/CLI
```

### Storage layout

```text
~/.corvustui/
├── sessions.db          # SQLite, WAL, migrations
├── artifacts/ab/cd/...  # content-addressed files by SHA-256
├── skills/              # user-installed skills
├── memory/              # ChromaDB (optional)
├── logs/                # structured logs, redacted
└── prompt_history.jsonl # kept for backward compatibility
```

Project-local overrides live in `.corvustui/` (skills, config, sessions if the
user opts in). No project-local file is trusted automatically.

### Architecture decisions to record

Create one short ADR per decision in `docs/adr/`:

- ADR-001: SQLite as the session store, with WAL and a migration runner.
- ADR-002: `ContentPart` model and the capability registry.
- ADR-003: Content-addressed artifact store and retention policy.
- ADR-004: Plugin API via Python entry points, versioned.
- ADR-005: Typed event bus between agent and presentation.
- ADR-006: Local-first privacy, consent, and redaction boundaries.
- ADR-007: Terminal image rendering and fallback strategy.
- ADR-008: Session schema versioning and backup/restore.

---

## 4. Team and operating model

### Assumptions

- **Team:** 4–6 contributors, including the lead.
- **Availability:** 10–15 focused hours per person per week (part-time).
- **Cadence:** 2-week sprints; demo every second Friday; release every 4–8 weeks.
- **Buffer:** 20% of every phase for reviews, integration, bugs, and exams.
- **If the team is smaller:** multiply the calendar by 1.5–1.8 for 2–3 people.
- **If the team is larger/full-time:** compress to roughly 5–6 months, but keep
  the phase gates and the 20% buffer.

### Roles

| Role | Primary responsibilities | Phase focus |
|------|--------------------------|-------------|
| **Lead / architect** | ADRs, interfaces, release gates, security review, roadmap grooming | 0, 4, 7, 9 |
| **Core / storage** | Session store, agent integration, migrations, provider adapters | 4, 5, 8 |
| **Multimodal / memory** | Content parts, parsers, artifacts, skills, retrieval | 5, 6 |
| **TUI / UX** | Session manager, attachment UI, previews, accessibility | 4, 5, 6 |
| **Platform / DevOps** | CI matrix, packaging, Docker, plugins, diagnostics | 0, 7, 8 |
| **QA / docs** | Fixtures, e2e, performance tests, docs, release notes | all |

For a 4-person team, combine **Core/storage + Multimodal** and **Platform +
QA/docs**. Every epic needs a primary owner and a reviewer; no single person
owns a critical path alone.

### Lead focus vs contributor focus

**Lead (you):**
- Keep the interfaces stable and the release gates honest.
- Write ADR-001/002 before Phase 4 implementation starts.
- Review every storage, security, and provider change.
- Run the release checklist and publish PyPI/GitHub releases.
- Protect the 20% buffer; do not let a phase expand silently.
- Spend time on contributor unblocking, not just feature code.

**Contributors:**
- Pick one epic, not a whole phase.
- Keep PRs under ~400 changed lines excluding tests/docs.
- Add tests and docs in the same PR.
- Ask for an ADR before changing a public interface.
- Demo the user-visible behavior, not only the code.

### Definition of Ready (before an issue starts)

- Problem statement and user story.
- In-scope and out-of-scope bullets.
- Acceptance criteria that can be tested.
- Dependencies and owner.
- Estimate (S/M/L/XL) and target phase.
- Test plan and docs impact.
- Security/privacy impact for file, network, or provider changes.

### Definition of Done (before an epic closes)

- Code + unit tests + integration test where applicable.
- `pytest`, Ruff, and Bend gates pass.
- Docs updated: README, USER_GUIDE, roadmap status, changelog.
- Migration/backward-compatibility note for storage changes.
- Feature flag or setting if behavior is experimental.
- Demo recording or screenshots for UI changes.
- Release notes entry.
- No secrets, no network calls in unit tests, no unredacted logs.

### Labels and board

- `phase-0` … `phase-9`
- `area:core`, `area:storage`, `area:providers`, `area:tui`, `area:memory`,
  `area:skills`, `area:security`, `area:platform`, `area:docs`
- `good-first-issue`, `help-wanted`, `needs-design`, `needs-adr`, `blocked`
- `size:S`, `size:M`, `size:L`, `size:XL`
- Board columns: Backlog → Ready → In progress → Review → Done.

---

## 5. Timeline at a glance

The calendar below assumes 5 contributors at ~10–15 hours/week and starts in
December 2026. Shift the dates if the team starts later; the week numbers are
the stable part.

| Phase | Release | Duration | Example weeks | Primary outcome | Depends on |
|-------|---------|----------|---------------|-----------------|------------|
| 0 | v0.1.0 live | 1 week | Dec 7–13, 2026 | PyPI release, CI, labels, ADRs | — |
| 4 | v0.2.0 | 6 weeks | Dec 14 – Jan 24, 2027 | Persistent, multi-session workspace | Phase 0 |
| 5 | v0.3.0 | 8 weeks | Jan 25 – Mar 21, 2027 | Images, documents, audio/video, image generation | Phase 4.1–4.2 |
| 6 | v0.4.0 | 7 weeks | Mar 22 – May 9, 2027 | Skills, memory v2, context citations | Phase 5 |
| 7 | v0.5.0 | 6 weeks | May 10 – Jun 20, 2027 | Portability, plugins, distribution | Phase 4 interfaces |
| 8 | v0.6.0 | 4 weeks | Jun 21 – Jul 18, 2027 | Performance, observability, hardening | Phases 4–7 |
| 9 | v1.0.0 | 6 weeks + 4 buffer | Jul 19 – Sep 26, 2027 | Stable API, docs, ecosystem, RC | Phases 4–8 |

**Total:** ~42–44 weeks, including buffer. This is a realistic part-time
schedule, not a full-time sprint.

### Parallel tracks

Do not run the phases as a strict waterfall:

- **Weeks 1–3:** Phase 0 + Phase 4.1 (schema/store) + CI.
- **Weeks 4–6:** Phase 4.2–4.4 + multimodal research spikes (terminal image
  support, PDF extraction, video keyframes).
- **Weeks 7–14:** Phase 5 implementation; platform track starts CI matrix and
  packaging spikes.
- **Weeks 15–22:** Phase 6; platform track finishes plugin API design.
- **Weeks 23–28:** Phase 7; docs track builds the contributor guide and ADRs.
- **Weeks 29–32:** Phase 8; beta users test long sessions and attachments.
- **Weeks 33–42:** Phase 9; release candidate, bug bash, v1.0.

### Release gates

Every release must pass:

```bash
python -m pytest -q
ruff check src tests --select F,E9,I001
./bend/bin/bend PROOF.bend
uv build
uvx twine check dist/*
```

Plus a manual CLI/TUI smoke test and a migration test from the previous
release's database.

## 6. Phase 0 — Release and project hygiene

**Release:** v0.1.0 live  
**Duration:** 1 week  
**Goal:** Make the repository safe and pleasant for contributors before adding
features. This phase is mostly configuration and documentation.

### 0.1 Publish v0.1.0 and verify installs

- **Needed:** PyPI trusted publisher or API token; GitHub release permission.
- **System impact:** Users can install with `uv tool install "corvustui[all]"`
  instead of a git URL; the release process becomes repeatable.
- **Key steps:**
  1. Publish the `v0.1.0` GitHub release to trigger the trusted-publishing
     workflow.
  2. Verify `uv tool install "corvustui[all]"` and `pipx install "corvustui[all]"`.
  3. Verify the curl installer from the public repository.
  4. Record the exact commands and results in `PUBLISHING.md`.
- **Acceptance:** PyPI project page exists; a clean machine can install and run
  `corvustui --help`; `uv publish --dry-run` passes for the next version.
- **Estimate:** 0.5 week.

### 0.2 Repository hygiene

- **Needed:** GitHub admin access.
- **System impact:** Contributors know where to work and how to get reviewed.
- **Key steps:**
  1. Protect `main`: require PR, one review, CI, and conversation resolution.
  2. Add labels, milestones (`v0.2.0` … `v1.0.0`), and a project board.
  3. Add issue templates: bug, feature, provider, multimodal, docs.
  4. Add a PR template with the Definition of Done checklist.
  5. Enable Discussions for design questions and show-and-tell.
  6. Add `SECURITY.md` with a private disclosure path.
- **Acceptance:** A new contributor can open an issue and a PR without asking
  where to put it; `main` cannot be force-pushed.
- **Estimate:** 0.5 week.

### 0.3 CI and release checks

- **Needed:** GitHub Actions.
- **System impact:** Regressions are caught before merge; releases are not
  manual guesswork.
- **Key steps:**
  1. Add `.github/workflows/ci.yml` for Linux + Python 3.11 and 3.14.
  2. Run `pytest`, Ruff `F,E9,I001`, `compileall`, and the Bend gate when the
     compiler is present.
  3. Cache `uv` and pip downloads; keep the job under 10 minutes.
  4. Add a `twine check` and `uv build` job on tags.
  5. Add a nightly or weekly matrix for macOS/Windows once Phase 7 starts.
- **Acceptance:** A red CI run blocks merge; a green run is required for release.
- **Estimate:** 0.5 week.

### 0.4 Architecture decision records

- **Needed:** Agreement on the first three ADRs.
- **System impact:** Prevents the team from re-litigating storage, content, and
  plugin decisions in every PR.
- **Key steps:**
  1. Create `docs/adr/README.md` and a short template.
  2. Write ADR-001 (SQLite sessions), ADR-002 (ContentPart + capabilities), and
     ADR-003 (artifact store).
  3. Review with the team; record rejected alternatives.
- **Acceptance:** ADRs are merged before Phase 4.1/5.1 implementation.
- **Estimate:** 0.5 week.

### 0.5 Contributor onboarding

- **Needed:** This roadmap and a contributor guide.
- **System impact:** New members can make a first PR in under a day.
- **Key steps:**
  1. Add `CONTRIBUTING.md` with setup, gates, architecture map, and PR rules.
  2. Create 10–15 `good-first-issue` tickets from §14.
  3. Add a “first PR” walkthrough and a code tour.
  4. Pin a “Start here” discussion.
- **Acceptance:** A new contributor completes setup and one good-first issue
  without a private walkthrough.
- **Estimate:** 0.5 week.

---

## 7. Phase 4 — Sessions and workspace foundation

**Release:** v0.2.0 “Sessions”  
**Duration:** 6 weeks  
**Goal:** Every conversation can be saved, named, resumed, searched, exported,
and deleted. Multiple sessions coexist without leaking context.

**Why this comes first:** Session storage is the foundation for attachments,
memory, skills, and recovery. Building multimodal input before storage means
re-solving where images and transcripts live. It also directly addresses the
current “close the terminal and lose everything” behavior.

### 4.1 Session domain model and SQLite store

- **Owner:** Core/storage  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** ADR-001; no dependency on multimodal work.
- **System impact:** Replaces in-memory-only history with durable, queryable
  sessions; enables resume, search, export, and crash recovery.
- **Key steps:**
  1. Add `src/corvustui/session/models.py` with `Session`, `Turn`, `MessageRecord`,
     `SessionSummary`, `SessionQuery`, and `UsageRecord`.
  2. Add `src/corvustui/session/store.py` with a `SessionStore` protocol,
     `SQLiteSessionStore`, and `InMemorySessionStore` for tests.
  3. Create the schema in `src/corvustui/session/schema.sql`:
     sessions, turns, messages, tool_calls, artifacts, usage, and FTS5 indexes.
  4. Use WAL mode, `foreign_keys=ON`, `busy_timeout`, and a single-writer
     connection guarded by an asyncio lock.
  5. Add a migration runner keyed by `PRAGMA user_version`; back up the DB before
     migrations.
  6. Add indexes for `updated_at`, `session_id`, `role`, and FTS5 over message
     text and session titles.
  7. Add tests for create/load/append/list/search/delete, duplicate IDs, corrupt
     DB, and concurrent appends.
- **Acceptance:**
  - A session with 100 turns round-trips without losing tool calls or usage.
  - Listing 1,000 sessions returns in under 100 ms on a laptop.
  - FTS search over 10,000 messages returns in under 100 ms.
  - Deleting a session removes its messages and optionally its artifacts.
  - `InMemorySessionStore` passes the same contract tests as SQLite.
- **Risks:** SQLite writer contention; mitigate with one writer, WAL, and a
  queue. Schema churn; mitigate with ADR-001 and migration tests.

### 4.2 Session service and agent integration

- **Owner:** Core/storage  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** 4.1.
- **System impact:** The agent automatically loads prior context and saves each
  turn; users can resume exactly where they stopped.
- **Key steps:**
  1. Add `SessionService` (`create`, `load`, `resume`, `append_turn`,
     `rename`, `archive`, `delete`, `export`).
  2. Extend `AgentConfig` with `session_id`, `session_store`, `auto_save`, and
     `resume_last`.
  3. On agent start, hydrate `ContextManager` from the session’s recent turns
     plus the stored rolling summary.
  4. After each turn, persist user/assistant messages, tool calls, usage, and
     errors. Persist on failure too.
  5. Add auto-title: first prompt truncated to ~60 characters, or a small model
     summary if enabled. Never block the first turn on title generation.
  6. Add `--session`, `--continue`, and `--no-save` to the CLI and TUI config.
  7. Add tests for resume, tool-call replay, failed-turn persistence, and
     `--no-save` leaving no DB rows.
- **Acceptance:**
  - Restart the TUI, resume a session, and the model sees previous turns.
  - A crashed turn is marked `failed` and does not corrupt the session.
  - `--no-save` keeps the old ephemeral behavior.
  - Two sessions do not share context or memories unless explicitly global.
- **Risks:** Replaying assistant tool calls without matching tool results;
  persist failed turns as non-replayable and hydrate only completed turns.

### 4.3 CLI session commands

- **Owner:** Core/storage or CLI contributor  
- **Reviewer:** Lead  
- **Estimate:** 1 week
- **Needed:** 4.1–4.2.
- **System impact:** Scriptable session management; useful for automation and
  for users who prefer the CLI.
- **Key steps:**
  1. Add `corvustui sessions list [--query] [--limit] [--json]`.
  2. Add `new`, `show`, `resume`, `rename`, `archive`, `delete`, `export`, and
     `import`.
  3. Add `corvustui run --session <id>`, `--continue`, `--no-save`.
  4. Define stable exit codes and JSON output for scripts.
  5. Add tests with a temporary `CORVUS_DATA_DIR`.
- **Acceptance:** All commands work against a temp DB; `--json` output is valid
  and documented; deleting requires `--yes` or confirmation.
- **Estimate:** 1 week.

### 4.4 TUI session manager

- **Owner:** TUI/UX  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** 4.1–4.2; a fake store for UI tests.
- **System impact:** Users can see, create, switch, rename, and delete sessions
  without leaving the TUI.
- **Key steps:**
  1. Add a session picker modal (`F2`) with search, recent-first ordering, and
     keyboard navigation.
  2. Add `Ctrl+N` for a new session; `/sessions` opens the picker.
  3. Show the current session title in the top bar/status bar; show an unsaved
     indicator only when `--no-save` is active.
  4. Add rename, archive, delete (with confirmation), and “resume last” setting.
  5. Add a recent-sessions section to the sidebar; clicking resumes.
  6. Handle a deleted session gracefully: show a message, create a new session,
     never crash.
  7. Add Textual pilot tests for open/select/rename/delete/cancel.
- **Acceptance:** A user can create three sessions, switch between them, restart
  the app, and resume the correct one with no context bleed.
- **Risks:** Terminal resize and modal focus; test with narrow and wide sizes.

### 4.5 Export, import, search, retention, migrations

- **Owner:** Core/storage + docs  
- **Reviewer:** Lead  
- **Estimate:** 1 week
- **Needed:** 4.1–4.4.
- **System impact:** Portability and trust: users own their data and can move it.
- **Key steps:**
  1. Export Markdown (human-readable, with tool calls collapsed) and JSON
     (lossless).
  2. Import JSON with ID collision handling and schema validation.
  3. Add `corvustui sessions search <query>` backed by FTS5.
  4. Add retention settings: keep forever (default), keep N days, or archive.
  5. Purge artifacts when a session is hard-deleted; warn before deleting files.
  6. Document backup/restore and migration from v0.1.0 (prompt history only).
- **Acceptance:** Export → delete → import restores the session; no secrets in
  exports; artifact purge is explicit; migration from v0.1.0 is documented.
- **Estimate:** 1 week.

### 4.6 Phase 4 release gate

- **Owner:** Lead + QA/docs  
- **Estimate:** 1 week
- **Key steps:** Update README/USER_GUIDE, record a demo, run the release gates,
  test migration from v0.1.0, tag `v0.2.0`, publish to PyPI.
- **Acceptance:** `v0.2.0` installs from PyPI; session demo is in the release
  notes; no regression in existing tests.

### Phase 4 exit criteria

- [ ] SQLite store with migrations and FTS5.
- [ ] Agent autosave/resume; `--no-save` preserved.
- [ ] CLI session commands with JSON output.
- [ ] TUI session manager with create/switch/rename/delete.
- [ ] Export/import and retention.
- [ ] Tests, docs, release notes, and `v0.2.0` published.

---

## 8. Phase 5 — Multimodal input and attachments

**Release:** v0.3.0 “Multimodal”  
**Duration:** 8 weeks  
**Goal:** Users can attach images, PDFs, Office documents, audio, and video.
Capable models receive native content; incapable models get a safe, useful
fallback instead of a crash.

**Why this matters:** Text-only input forces users to copy-paste screenshots,
manually summarize PDFs, and leave the terminal for image questions. This phase
unlocks vision, document QA, meeting notes, screenshot debugging, data-file
analysis, and image generation. It also gives the memory system richer material
to retrieve.

**Important constraint:** Do not send a modality to a model that has not
declared support. The default is deny, with an explicit user override.

### 5.1 Content model and capability registry

- **Owner:** Core/architecture  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** ADR-002; Phase 4.1 (so artifacts can be persisted).
- **System impact:** Replaces `content: str` as the only message payload with a
  typed, provider-neutral content model. This is the interface every later
  multimodal feature uses.
- **Key steps:**
  1. Add `src/corvustui/domain/content.py`:
     `TextPart`, `ImagePart`, `DocumentPart`, `AudioPart`, `VideoPart`,
     `FileRefPart`, and a `ContentPart` union.
  2. Add `Message.parts: list[ContentPart] | None` while keeping `content` for
     text-only callers; `to_dict()` remains backward compatible.
  3. Add `ModelCapabilities` (`vision`, `documents`, `audio`, `video`,
     `image_generation`, `tools`, `streaming`, `max_context`, `max_file_mb`).
  4. Add a static capability matrix for known provider families/models, with
     config overrides and an “unknown = deny” default.
  5. Add provider serialization: OpenAI `image_url`, Anthropic image/document
     blocks, Gemini `inline_data`, Ollama `images` array.
  6. Add `UnsupportedModalityError` with a user-facing message and a suggested
     action (switch model, extract text, transcribe, skip file).
  7. Add serialization/round-trip tests for every part type and provider.
- **Acceptance:** A text-only message serializes exactly as before; an image
  part is rejected before the HTTP request when the model lacks vision; the
  capability override is documented and tested.
- **Risks:** Provider APIs differ and change; keep adapters isolated and add
  contract tests with recorded payloads.

### 5.2 Artifact store and ingestion pipeline

- **Owner:** Core/storage  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** 5.1; Phase 4 artifact table.
- **System impact:** Files are stored once, deduplicated, bounded, and linked to
  sessions. Large files no longer live in memory or in the chat transcript.
- **Key steps:**
  1. Add `src/corvustui/attachments/artifact_store.py` with SHA-256
     content-addressed paths (`artifacts/ab/cd/<sha>`), atomic writes, and
     metadata JSON.
  2. Add MIME detection using extension + magic bytes; never trust the
     extension alone.
  3. Add ingestion limits: per-file size (default 20 MB), per-turn count,
     per-session quota, and a configurable allowlist.
  4. Validate paths against the safety allowlist; reject traversal, symlinks
     outside roots, device files, and FIFOs.
  5. Strip EXIF/location metadata from images by default; keep a hash for audit.
  6. Add cleanup: unreferenced artifacts older than N days; explicit purge on
     session delete.
  7. Add tests for duplicate files, oversized files, spoofed MIME, traversal,
     symlinks, and corrupt metadata.
- **Acceptance:** The same file attached twice stores one blob; a 21 MB file is
  rejected before reading the whole file; a path traversal attempt is blocked;
  deleting a session can purge its artifacts.
- **Risks:** Parser and decompression bombs; enforce limits before extraction
  and run optional parsers with timeouts.

### 5.3 Image input

- **Owner:** Core/providers + TUI  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** 5.1–5.2.
- **System impact:** Screenshots, diagrams, photos, and UI mockups become
  first-class prompts. This is the highest-value multimodal feature.
- **Key steps:**
  1. Support PNG, JPEG, WebP, and GIF (first frame) with a configurable max
     dimension; downscale large images before sending.
  2. Convert to the provider format: base64 data URI for OpenAI, base64 block
     for Anthropic, `inline_data` for Gemini, `images` array for Ollama.
  3. Add a capability check and a safe-state message for text-only models.
  4. Add a `describe_image`/`image_view` path that can use OCR or a vision model
     when the primary model lacks vision.
  5. Add image fixtures (small, license-free PNG/JPEG/WebP) and payload tests.
  6. Add a “what will be sent” preview for cloud providers (size, MIME, model).
- **Acceptance:** With a vision model, “what is in this screenshot?” returns a
  correct answer and the request contains one image part. With a text-only
  model, no request is sent and the user sees a clear switch-model message.
- **Risks:** Token/cost blowups from large images; downscale and warn before
  cloud upload.

### 5.4 Documents (PDF, Office, text, data)

- **Owner:** Multimodal/core  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** 5.1–5.2.
- **System impact:** Users can ask questions about PDFs, contracts, reports,
  spreadsheets, slides, and code without leaving the terminal.
- **Key steps:**
  1. Add optional extras: `docs` (`pypdf`, `python-docx`, `openpyxl`,
     `python-pptx`) and keep the core install small.
  2. PDF: extract text per page with `pypdf`; use `pdfplumber` only when layout
     matters. Preserve page numbers for citations.
  3. DOCX: paragraphs + tables; XLSX: sheet names, headers, bounded rows;
     PPTX: slide titles + notes; CSV/TSV: headers + sampled rows.
  4. HTML/Markdown/code: read as text with encoding detection; cap bytes.
  5. Chunk extracted text for RAG with page/section metadata; store the full
     extraction as an artifact, not in the chat transcript.
  6. Scanned PDFs: detect no-text pages and offer OCR (optional Tesseract or a
     vision model) instead of silently returning empty text.
  7. Add fixtures: 1-page PDF, DOCX with a table, XLSX with two sheets, PPTX
     with notes, and a corrupt file. Test citations and limits.
- **Acceptance:** “Summarize page 2 of this PDF” cites page 2; a DOCX table is
  readable; a 10 MB XLSX is sampled, not fully loaded; a corrupt file produces a
  safe error and the session stays alive.
- **Risks:** AGPL/ licensing of PDF libraries; prefer `pypdf`/`pdfplumber` and
  document the choice. Parser CVEs; pin versions and run dependency scanning.

### 5.5 Audio and video ingestion

- **Owner:** Multimodal/core  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** 5.1–5.2; optional `media` extra.
- **System impact:** Meeting recordings, voice notes, and short screen
  recordings become usable without a separate transcription tool. Full video
  understanding is deliberately out of scope for v0.3.
- **Key steps:**
  1. Use `ffprobe` (if installed) for duration, codecs, resolution, and audio
     presence; degrade to metadata-only if `ffmpeg` is absent.
  2. Audio: transcribe with a provider transcription API (Whisper) or optional
     local `faster-whisper`; store transcript + timestamps as an artifact.
  3. Video: extract audio for transcription, plus N keyframes (default 4–8)
     using scene/interval sampling; send keyframes to a vision model only if
     the user asks a visual question.
  4. Enforce duration/size limits (e.g. 10 minutes / 200 MB default) and show a
     cost warning before cloud transcription.
  5. Add a safe state when neither transcription nor vision is available:
     return metadata and a clear explanation.
  6. Add tiny WAV/MP4 fixtures and mock the `ffmpeg` calls; do not require
     `ffmpeg` in unit tests.
  7. Document the difference between “transcript” and “visual understanding”.
- **Acceptance:** A 30-second WAV produces a transcript and a summary; a short
  MP4 produces metadata + transcript + optional keyframes; without `ffmpeg`,
  the user gets a clear install message, not a crash.
- **Risks:** Cloud transcription cost/privacy; require consent and show the
  estimated upload size. Video is expensive; cap duration aggressively.

### 5.6 TUI attachment UX and safe states

- **Owner:** TUI/UX  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** 5.2–5.5.
- **System impact:** Attachments become discoverable and manageable; errors are
  actionable instead of terminal tracebacks.
- **Key steps:**
  1. Add `/attach <path>` and a file-browser modal; support multiple attachments
     per prompt.
  2. Add attachment chips above the input with icon, name, size, type, remove,
     and “open externally”.
  3. Add progress for ingestion/transcription; keep the input responsive.
  4. Add previews: terminal image protocol when available (Kitty/iTerm2/Sixel);
     otherwise show a thumbnail path, dimensions, and an “open” action.
  5. Add safe-state banners with actions: “Switch to a vision model”, “Extract
     text instead”, “Transcribe audio”, “Remove file”.
  6. Add keyboard-only operation and a screen-reader-friendly text fallback.
  7. Add Textual pilot tests for attach, remove, oversized file, unsupported
     model, and failed parser.
- **Acceptance:** A user can attach, preview, remove, and send a file without
  touching the mouse; every failure keeps the session usable and shows a next
  action.
- **Risks:** Terminal image support varies; the fallback is the primary path,
  not an afterthought.

### 5.7 Image generation (capability-gated)

- **Owner:** Core/providers  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** 5.1–5.3; an image-generation provider or a local backend.
- **System impact:** Adds an output modality without pretending every model can
  generate images. When no capable backend is configured, the user gets a safe
  setup message and no tool call.
- **Key steps:**
  1. Add `ImageGenerationProvider` with `generate(prompt, size, style)` and a
     `capabilities()` check.
  2. Implement one cloud adapter (OpenAI Images or Gemini Imagen) and one local
     adapter spike (ComfyUI/Automatic1111) behind a flag.
  3. Register a `generate_image` tool only when a backend is configured; never
     expose a broken tool.
  4. Require approval and show estimated cost before cloud generation.
  5. Save outputs to the artifact store, link them to the session, and display
     them with the same preview/fallback as image input.
  6. Handle content-policy rejection, rate limits, timeouts, and missing API
     keys with specific messages.
  7. Add tests with a fake provider: success, no provider, policy rejection,
     timeout, and oversized output.
- **Acceptance:** “Draw a diagram of X” works when configured; without a
  backend, the message says exactly what to configure and no request is sent;
  a policy rejection is displayed as a normal message, not an exception.
- **Risks:** Cost and content policy; approval + warnings. Local backends are
  optional and must not block installation.

### 5.8 Phase 5 release gate

- **Owner:** Lead + QA/docs  
- **Estimate:** 1 week
- **Key steps:** Add `docs`/`media` extras, update install docs, record a demo
  with an image and a PDF, run the gates, test on a clean install, tag `v0.3.0`.
- **Acceptance:** `uv tool install "corvustui[all]"` includes the optional
  parsers; the demo shows image input, PDF citation, unsupported-model safe
  state, and image-generation setup message.

### Safe-state matrix

| Situation | System behavior | User-facing message |
|-----------|-----------------|---------------------|
| Model lacks vision | Reject before request; keep attachment | “This model cannot read images. Switch to a vision model, or I can extract text/OCR.” |
| Model lacks PDF support | Extract text locally; send text | “Sending extracted text with page citations.” |
| Scanned PDF, no OCR | Detect empty text; offer OCR/vision | “This PDF has no text layer. Install OCR or switch to a vision model.” |
| Model lacks audio | Transcribe first; send transcript | “Sending a transcript, not the raw audio.” |
| No transcription backend | Metadata only; no request | “Audio transcription is not configured. Install the media extra or use a provider with Whisper.” |
| Model lacks video | Transcript + keyframes only | “Video is processed as audio + keyframes, not full motion understanding.” |
| No image-generation backend | Do not register/call tool | “No image-generation model is configured. Add one under /connect or configure a local backend.” |
| File too large | Reject before reading | “File exceeds the 20 MB limit. Increase `CORVUS_ATTACHMENT_MAX_MB` or compress it.” |
| Unsupported MIME | Reject; list supported types | “Unsupported file type. Supported: images, PDF, DOCX, XLSX, PPTX, text, audio, video.” |
| Corrupt/encrypted file | Catch parser error; keep session | “Could not read this file: <reason>. The session is still active.” |
| Cloud upload | Show size/model warning; require consent | “This will upload 4.2 MB to OpenAI. Continue? [y/N]” |
| Prompt injection in document | Treat as data; do not follow instructions | “Document content is untrusted; instructions inside it are ignored.” |

### Phase 5 exit criteria

- [ ] `ContentPart` model + capability registry.
- [ ] Artifact store with limits and cleanup.
- [ ] Image input for at least Ollama, OpenAI, Anthropic, and Gemini.
- [ ] PDF/DOCX/XLSX/PPTX/text extraction with citations.
- [ ] Audio/video transcript + keyframe MVP.
- [ ] TUI attachment UX with previews and safe states.
- [ ] Image generation behind a capability check.
- [ ] Tests, docs, release notes, `v0.3.0` published.

---

## 9. Phase 6 — Skills, memory, and context

**Release:** v0.4.0 “Skills & Memory”  
**Duration:** 7 weeks  
**Goal:** Turn repeated prompts and attached knowledge into reusable skills and
durable, user-controlled memory. Make retrieval visible and trustworthy.

### 6.1 Skill system

- **Owner:** Multimodal/memory  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** Phase 5 content parts; a stable `SkillManifest` schema.
- **System impact:** Users can install a skill once and reuse it across
  sessions. Skills declare required modalities/tools/models, so the system can
  refuse safely instead of failing mid-turn.
- **Key steps:**
  1. Define a manifest (`skill.yaml` or `SKILL.md` front matter):
     name, version, description, triggers, required modalities/tools/models,
     prompt fragment, examples, permissions, and optional output schema.
  2. Add `SkillRegistry` discovery from:
     `~/.corvustui/skills`, project `.corvustui/skills`, and installed
     `corvustui.skills` entry points.
  3. Validate manifests, detect duplicates/version conflicts, and show a
     compatibility report (`corvustui skills check`).
  4. Resolve a skill per turn: inject its prompt fragment, restrict tools, and
     check capabilities. Missing requirements produce a safe message.
  5. Add `/skills` in the TUI and `corvustui skills list|show|enable|disable`.
  6. Do **not** execute arbitrary scripts from a skill by default. If scripts
     are supported later, require an explicit trust/signing step.
  7. Add tests for discovery, invalid manifests, missing models, enable/disable,
     and prompt injection resistance.
- **Acceptance:** A skill can be installed from a directory, listed, enabled,
  used in a session, and disabled; a skill requiring vision on a text-only
  model is refused with a clear message.
- **Risks:** Skill supply-chain trust; start read-only (prompts + declared
  tools), no code execution.

### 6.2 Built-in skills

- **Owner:** Multimodal/memory + docs  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** 6.1, Phase 5.
- **System impact:** Immediately useful workflows that demonstrate the skill
  system and exercise multimodal paths.
- **Key steps:**
  1. `image-view`: describe a screenshot, diagram, chart, or UI; cite regions.
  2. `document-qa`: answer questions with page/section citations.
  3. `document-summarize`: executive summary + key facts + open questions.
  4. `table-extract`: CSV/XLSX → Markdown table + column types.
  5. `code-review`: review a diff/file with a structured checklist.
  6. `screenshot-debug`: reproduce a UI bug from a screenshot + error text.
  7. `meeting-notes`: transcript → decisions, action items, owners.
  8. `image-prompt`: turn a rough idea into a safe image-generation prompt.
  9. Add fixture-driven tests and a gallery in the docs.
- **Acceptance:** Each built-in skill has a demo, a test with a fake provider,
  and a documented required capability.

### 6.3 Memory v2

- **Owner:** Multimodal/memory  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** Phase 4 sessions; current `ChromaMemory`.
- **System impact:** Memory becomes inspectable and controllable instead of an
  opaque RAG side effect. Users can see why a memory was retrieved and remove
  it.
- **Key steps:**
  1. Extend `MemoryEntry` with `scope` (global/project/session), `source`
     (session/turn/attachment), `confidence`, `created_at`, `expires_at`, and
     `tags`.
  2. Add namespaces and metadata filters; keep one collection per scope or a
     single collection with filters.
  3. Add deduplication (hash + semantic similarity), TTL/decay, pin/edit/delete,
     and `purge(scope)`.
  4. Add explicit memory commands: `/remember <fact>`, `/forget <id>`,
     `corvustui memory list|search|delete|purge`.
  5. Add optional automatic extraction after a session (facts/preferences) with
     a per-session consent toggle; default off for cloud providers.
  6. Add retrieval citations to the context and UI: “Memory 2, from session X”.
  7. Add a memory browser modal with search, provenance, edit, delete, and
     “never remember this session”.
  8. Add precision/recall tests on a small fixture set; measure retrieval
     latency.
- **Acceptance:** A fact remembered in session A is retrieved in session B with
  a citation; deleting it removes it from future retrieval; “don’t remember”
  leaves no new entries.
- **Risks:** Memory pollution and privacy; default to explicit memory, not
  automatic hoarding.

### 6.4 Context builder and citations

- **Owner:** Core/architecture  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** 6.3, Phase 5.
- **System impact:** The model gets a deliberate, token-budgeted context instead
  of an ever-growing transcript. Users can inspect what was sent.
- **Key steps:**
  1. Add a `ContextBuilder` that selects: system prompt, recent turns, rolling
     summary, retrieved memories, attachment summaries, and skill fragments.
  2. Budget tokens per source (e.g. 40% recent, 20% memory, 20% attachments,
     10% summary, 10% reserve); make it configurable.
  3. Store the rolling summary in the session DB; regenerate at a threshold.
  4. Add a context inspector (`/context` or `/stats`) showing source, tokens,
     and truncation.
  5. Add tests for budget enforcement, summary rollover, and citation
     preservation.
- **Acceptance:** A long session stays under the model limit without dropping
  the latest turn; the inspector explains every included source.
- **Risks:** Summaries losing critical details; keep the raw transcript and
  allow “show original”.

### 6.5 TUI skill/memory UX

- **Owner:** TUI/UX  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** 6.1–6.4.
- **System impact:** Skills and memory become visible product features, not
  hidden configuration.
- **Key steps:**
  1. Add `/skills`, `/memory`, and `/context` modals.
  2. Show active skill badges and “memory used” indicators on assistant turns.
  3. Add a “why this answer?” panel with citations and token usage.
  4. Add keyboard navigation and empty states (“No skills installed”).
  5. Add pilot tests for enable/disable, memory delete, and context inspector.
- **Acceptance:** A user can discover, enable, and inspect a skill without
  reading the docs; memory citations are visible and removable.

### 6.6 Phase 6 release gate

- **Owner:** Lead + QA/docs  
- **Estimate:** 1 week
- **Key steps:** Document skill authoring, memory privacy, and context budgets;
  run retrieval benchmarks; tag `v0.4.0`.
- **Acceptance:** A third-party skill can be authored from the docs in under an
  hour; memory privacy behavior is documented and tested.

### Phase 6 exit criteria

- [ ] Skill manifest, registry, enable/disable, and safe requirement checks.
- [ ] Eight built-in skills with demos and tests.
- [ ] Memory v2 with scopes, provenance, citations, TTL, and purge.
- [ ] Context builder with token budgets and an inspector.
- [ ] TUI skill/memory/context UX.
- [ ] Docs, tests, release notes, `v0.4.0` published.

---

## 10. Phase 7 — Portability, plugins, and distribution

**Release:** v0.5.0 “Portable & Extensible”  
**Duration:** 6 weeks  
**Goal:** Run well on Linux, macOS, and Windows; install without a Python
toolchain; let third parties add providers, tools, and skills without forking.

### 7.1 Platform paths, config profiles, and secrets

- **Owner:** Platform  
- **Reviewer:** Lead  
- **Estimate:** 1 week
- **Needed:** Phase 4 settings.
- **System impact:** Consistent behavior across OSes; fewer “works on my
  machine” bugs; secrets no longer live in plain `.env` by default.
- **Key steps:**
  1. Add platform-aware paths: XDG on Linux, Application Support on macOS,
     `%APPDATA%` on Windows; keep `~/.corvustui` as a fallback.
  2. Add config profiles (`default`, `work`, `local`) in TOML/YAML.
  3. Add optional OS keyring storage for API keys; fall back to `.env` with a
     warning.
  4. Add `corvustui config path|show|validate`.
  5. Test with temp home directories on all three OSes.
- **Acceptance:** The app starts with no config; profiles switch provider/model;
  keys are not printed in logs or `config show`.
- **Risks:** Keyring availability in headless containers; keep env fallback.

### 7.2 CI matrix and packaging

- **Owner:** Platform/DevOps  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** 0.3; Phase 4–6 tests.
- **System impact:** Users can install via PyPI, Homebrew, Docker, or a
  standalone binary; releases are reproducible.
- **Key steps:**
  1. CI matrix: Ubuntu/macOS/Windows × Python 3.11/3.14.
  2. Publish wheels/sdist via trusted publishing; keep `twine check`.
  3. Add a Homebrew tap/formula (or document the tap setup).
  4. Add a multi-arch Docker image (amd64/arm64) with a TTY entrypoint; document
     Ollama networking.
  5. Spike standalone binaries with PyInstaller/Nuitka; ship at least one
     platform and document limitations.
  6. Add `corvustui --version` and a build-info command.
- **Acceptance:** A clean Windows/macOS/Linux machine can install and run
  `corvustui --help`; Docker `corvustui tui` works with a mounted data dir.
- **Risks:** Native dependencies (ChromaDB/onnxruntime) in standalone builds;
  keep optional extras optional.

### 7.3 Terminal capability detection and image fallback

- **Owner:** TUI/platform  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** Phase 5 previews.
- **System impact:** Image/video previews work where possible and degrade
  cleanly everywhere else.
- **Key steps:**
  1. Detect truecolor, Unicode width, Kitty/iTerm2/Sixel support, and terminal
     size.
  2. Add a renderer abstraction: native protocol, half-block fallback, and
     “path + metadata” fallback.
  3. Add a `corvustui doctor` check for terminal capabilities.
  4. Test in tmux, SSH, Windows Terminal, and a narrow terminal.
- **Acceptance:** No crash or garbled output in an unsupported terminal; the
  user can always open the file externally.
- **Risks:** Terminal detection is heuristic; make the override explicit.

### 7.4 Plugin API

- **Owner:** Core/architecture + platform  
- **Reviewer:** Lead  
- **Estimate:** 2 weeks
- **Needed:** Stable interfaces from Phases 4–6; ADR-004.
- **System impact:** Third parties can add providers, tools, skills, and storage
  backends without editing core files.
- **Key steps:**
  1. Define entry-point groups: `corvustui.providers`, `corvustui.tools`,
     `corvustui.skills`, `corvustui.storage`.
  2. Publish a small `corvustui.plugin` module with protocols, version constants,
     and validation helpers.
  3. Load plugins in isolation; one broken plugin must not crash startup.
  4. Add `corvustui plugins list|check|disable`.
  5. Document a template repository and a “hello world” provider/tool.
  6. Add compatibility tests against the current API version.
- **Acceptance:** An external package can register a provider/tool/skill and be
  discovered; a broken plugin is reported and skipped.
- **Risks:** API instability; freeze at v1.0 and use a version handshake.

### 7.5 Diagnostics and support bundle

- **Owner:** Platform/QA  
- **Reviewer:** Lead  
- **Estimate:** 1 week
- **Needed:** Phases 4–7.
- **System impact:** Users and maintainers can diagnose installs without
  guesswork; bug reports become actionable.
- **Key steps:**
  1. `corvustui doctor`: Python, OS, terminal, config paths, DB integrity,
     provider reachability, Ollama models, optional deps, disk space.
  2. `corvustui export-debug`: redacted logs, config (no keys), versions, and
     recent errors.
  3. Add a `--debug` flag with structured logs and turn IDs.
  4. Add a troubleshooting guide keyed by doctor output.
- **Acceptance:** A failing install produces a redacted bundle that identifies
  the cause; no secrets are included.
- **Risks:** Redaction gaps; test with fake keys and tokens.

### 7.6 Phase 7 release gate

- **Owner:** Lead + QA/docs  
- **Estimate:** 1 week
- **Key steps:** Test installers on three OSes, update docs, tag `v0.5.0`.
- **Acceptance:** All supported install paths are documented and tested; plugin
  template works from a clean environment.

### Phase 7 exit criteria

- [ ] Platform-aware paths and optional keyring.
- [ ] CI matrix and at least PyPI + Docker + one native package.
- [ ] Terminal capability detection with fallbacks.
- [ ] Versioned plugin API with a template.
- [ ] `doctor` and redacted debug bundle.
- [ ] Docs, tests, release notes, `v0.5.0` published.

---

## 11. Phase 8 — Performance, reliability, and observability

**Release:** v0.6.0 “Fast & Observable”  
**Duration:** 4 weeks  
**Goal:** Keep the app responsive with long sessions, large attachments, and
slow providers. Make failures diagnosable without a debugger.

### 8.1 Chat virtualization and lazy rendering

- **Owner:** TUI/UX  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** Phase 4 sessions.
- **System impact:** A 1,000-turn session no longer mounts 1,000 widgets or
  slows scrolling.
- **Key steps:**
  1. Replace “mount every MessageBlock” with a windowed/virtualized list, or
     collapse old turns into summary blocks.
  2. Lazy-render tool outputs and code blocks; load details on expand.
  3. Cap in-memory rendered turns (e.g. 200) while keeping the full session in
     SQLite.
  4. Add scroll-position restoration and “jump to turn”.
  5. Benchmark mount time, scroll FPS, and memory with 1k/10k messages.
- **Acceptance:** Opening a 1,000-message session takes <300 ms; scrolling stays
  smooth; memory stays under 250 MB idle.
- **Risks:** Textual widget lifecycle; test with pilot and manual profiles.

### 8.2 Startup, streaming, and database performance

- **Owner:** Core/platform  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** Phases 4–7.
- **System impact:** Faster feedback and lower resource use, especially on
  laptops and remote SSH sessions.
- **Key steps:**
  1. Lazy-import optional dependencies (`chromadb`, document parsers, media
     tools) so `--help` and startup stay fast.
  2. Reuse provider HTTP connections; tune timeouts/retries; add backpressure
     for streaming.
  3. Cache token counts per message; avoid re-encoding the same text.
  4. Add DB query plans/indexes for session list/search; paginate.
  5. Measure p50/p95 startup, first token, session load, and attachment ingest.
- **Acceptance:** Startup <1.5 s without MCP; session load <200 ms for 1k
  messages; first token is provider-bound, not client-bound.
- **Risks:** Premature optimization; measure before changing.

### 8.3 Observability and debug bundle

- **Owner:** Platform/QA  
- **Reviewer:** Lead  
- **Estimate:** 1 week
- **Needed:** Phase 7.5.
- **System impact:** Bugs can be reproduced from structured logs instead of
  screenshots.
- **Key steps:**
  1. Add structured JSON logs with session/turn/provider/model/latency/tokens.
  2. Add a per-turn trace ID shared by agent, provider, tools, and TUI.
  3. Add `corvustui logs tail` and `export-debug`.
  4. Add opt-in OpenTelemetry export; default off.
  5. Add redaction tests for keys, tokens, paths, and document text.
- **Acceptance:** A failed turn has a trace with the failing provider request,
  tool call, and error class; the bundle contains no secrets.
- **Risks:** Log volume; rotate and cap.

### 8.4 Benchmark expansion and regression gates

- **Owner:** QA/docs  
- **Reviewer:** Lead  
- **Estimate:** 1 week
- **Needed:** Phase 4–7.
- **System impact:** Performance and quality regressions are caught in CI, not
  by users.
- **Key steps:**
  1. Extend `benchmarks/tasks.json` with session-resume, PDF-QA, image-QA,
     memory-retrieval, and tool-selection tasks.
  2. Add deterministic fake-provider benchmarks for CI; keep live Ollama
     benchmarks optional.
  3. Record p50/p95 latency, token usage, and pass rate; store JSON baselines.
  4. Fail CI on a >20% regression for client-side metrics.
- **Acceptance:** A PR that slows session loading or attachment ingestion by
  >20% fails the benchmark gate.
- **Risks:** Flaky live models; CI uses fakes, live runs are advisory.

### 8.5 Security hardening and fuzzing

- **Owner:** Lead + QA  
- **Reviewer:** Second maintainer  
- **Estimate:** 1 week
- **Needed:** Phases 5–7.
- **System impact:** File parsing, MCP, and provider responses are treated as
  untrusted input.
- **Key steps:**
  1. Add fuzz tests for MIME sniffing, PDF/DOCX/XLSX parsers, archive limits,
     and malformed provider JSON.
  2. Add decompression/zip-bomb limits and parser timeouts.
  3. Add path traversal and symlink tests for attachments and exports.
  4. Add prompt-injection fixtures for documents, tool outputs, and MCP
     resources; assert they are treated as data.
  5. Run `pip-audit`/dependency scanning and generate an SBOM.
- **Acceptance:** Fuzz corpus runs in CI; no crash/hang on malformed inputs;
  security-sensitive changes require two reviewers.
- **Risks:** Parser CVEs; pin and update dependencies.

### Phase 8 exit criteria

- [ ] Virtualized chat with 1k+ turns.
- [ ] Startup/session/ingest performance targets met.
- [ ] Structured logs and redacted debug bundle.
- [ ] Benchmark regression gate.
- [ ] Fuzz/security suite and SBOM.
- [ ] Docs, tests, release notes, `v0.6.0` published.

---

## 12. Phase 9 — Ecosystem and v1.0

**Release:** v1.0.0  
**Duration:** 6 weeks + 4 weeks buffer  
**Goal:** Freeze the public interfaces, make the project teachable, and ship a
stable release that contributors can build on.

### 9.1 API freeze and deprecation policy

- **Owner:** Lead  
- **Reviewer:** Core team  
- **Estimate:** 1 week
- **Needed:** Phases 4–8.
- **System impact:** Plugin authors and downstream users can rely on stable
  interfaces.
- **Key steps:**
  1. Declare stable: provider protocol, tool protocol, skill manifest, session
     store, plugin entry points, CLI JSON output.
  2. Add a compatibility policy: deprecate for two minor releases; document
     migrations.
  3. Add `corvustui.api_version` and a plugin handshake.
  4. Run a breaking-change audit; remove or hide experimental APIs.
- **Acceptance:** A plugin built against v1.0 loads on the next patch/minor
  without changes; deprecations are documented.

### 9.2 Documentation site and tutorials

- **Owner:** QA/docs + lead  
- **Reviewer:** Core team  
- **Estimate:** 2 weeks
- **Needed:** Phase 7 docs.
- **System impact:** New users can learn from tasks, not source code; support
  load drops.
- **Key steps:**
  1. Build a docs site (MkDocs Material or similar) from `docs/`.
  2. Tutorials: first session, attach a PDF, use a skill, install a plugin,
     local Ollama, cloud provider, memory privacy.
  3. Reference: CLI, config, provider capability matrix, skill manifest, plugin
     API, troubleshooting.
  4. Add screenshots/GIFs and a “5-minute quick start”.
  5. Add docs CI for broken links and code-block syntax.
- **Acceptance:** A new user completes the quick start and one multimodal
  tutorial without reading source code.

### 9.3 Examples and templates

- **Owner:** Core team + community  
- **Reviewer:** Lead  
- **Estimate:** 1.5 weeks
- **Needed:** 9.1–9.2.
- **System impact:** Demonstrates extension points and seeds the ecosystem.
- **Key steps:**
  1. `examples/plugins/hello-provider` and `hello-tool`.
  2. `examples/skills/document-qa`, `image-view`, `code-review`.
  3. A cookiecutter/copier template for a plugin or skill.
  4. A “good first plugin” guide and a compatibility test.
- **Acceptance:** Each example installs and runs from a clean environment; CI
  tests at least one example.

### 9.4 Community beta and bug bash

- **Owner:** Lead + QA/docs  
- **Estimate:** 2 weeks
- **Needed:** v0.6.0 + docs.
- **System impact:** Real users find the integration bugs that unit tests miss.
- **Key steps:**
  1. Recruit 5–10 beta users (classmates, local devs, Ollama users).
  2. Provide a feedback form and a redacted debug bundle command.
  3. Run a two-week bug bash; label and triage every report.
  4. Fix release blockers; defer non-blockers to v1.1.
  5. Publish a beta release (`v1.0.0-rc.1`) and migration notes.
- **Acceptance:** No open P0/P1 bugs; all beta blockers closed or explicitly
  deferred; crash-free session rate >99% in the beta.

### 9.5 Release candidate and v1.0.0

- **Owner:** Lead  
- **Estimate:** 1.5 weeks + buffer
- **Key steps:** Freeze, run the full matrix, test upgrades from v0.1–v0.6,
  publish v1.0.0, write release notes, record a demo, announce.
- **Acceptance:** v1.0.0 installs from PyPI/Homebrew/Docker; upgrade path works;
  docs and examples are live; security review signed off.

### Phase 9 exit criteria

- [ ] Stable public API + deprecation policy.
- [ ] Docs site and tutorials.
- [ ] Plugin/skill examples and template.
- [ ] Beta bug bash with no release blockers.
- [ ] v1.0.0 published with migration guide.

---

## 13. Cross-cutting workstreams

These run in every phase; assign an owner even if the work is small.

| Workstream | What “good” looks like | Owner |
|------------|------------------------|-------|
| Security & privacy | Threat model, redaction, consent, dependency scanning, fuzz tests | Lead |
| Testing & QA | Unit/integration/e2e, provider fixtures, TUI pilot, performance gates | QA/docs |
| Documentation | README, user guide, ADRs, tutorials, changelog, release notes | QA/docs |
| Performance | Startup, first token, session load, memory, attachment ingest | Core/platform |
| Accessibility & UX | Keyboard-only, narrow terminals, color-independent status, fallbacks | TUI/UX |
| Release engineering | Semver, tags, PyPI, Homebrew, Docker, migration tests | Platform |
| Community | Issues, discussions, good-first issues, reviews, recognition | Lead |

### Security and privacy rules

- No secrets in logs, exports, debug bundles, or error messages.
- Cloud uploads require explicit consent and show size/model/provider.
- Attachment parsers are bounded by size, time, and recursion limits.
- Document/tool/MCP content is untrusted data; never follow instructions found
  inside it.
- Destructive tools still require approval; generated files are linked to the
  session and auditable.
- New dependencies need a license check and a pinned minimum version.

### Testing rules

- No network in unit tests; use `httpx.MockTransport` or fakes.
- Every provider change adds a payload/response fixture test.
- Every parser change adds a valid, corrupt, and oversized fixture.
- Every TUI feature adds a pilot test and a manual narrow-terminal check.
- Every storage change adds a migration test from the previous release.
- New code targets 80% line coverage; overall coverage is reported, not gamed.

---

## 14. Good-first issues

These are scoped for a new contributor with a reviewer. Each should be a
standalone issue with the Definition of Ready. Do not label an issue
`good-first-issue` if it touches storage, security, or provider payloads.

### Documentation and developer experience

1. **Add a `docs/` index** — link README, user guide, phase report, roadmap,
   and ADRs; add a docs check for broken relative links.
2. **Document the provider capability matrix** — table of model families and
   supported modalities, with the “unknown = deny” rule.
3. **Write a troubleshooting page** — Ollama not running, model missing, TUI
   colors broken, MCP server fails, ChromaDB download blocked.
4. **Add a “first 30 minutes” tutorial** — install, run, connect a provider,
   run a tool, enable RAG, quit and resume.
5. **Improve `--help` text** — add examples to every CLI command; verify no
   line exceeds 100 columns.
6. **Add architecture diagrams** — one for the agent loop, one for the provider
   layer, one for the planned session/attachment flow.

### Testing and fixtures

7. **Add a fake provider test helper** — reusable streaming/tool-call fake in
   `tests/fakes/`; migrate two existing tests to use it.
8. **Add a TUI pilot smoke test** — start the app, submit “hello”, assert the
   assistant block appears and no exception is raised.
9. **Add a narrow-terminal snapshot test** — 80×24 and 60×20; assert no
   overflow and the input remains usable.
10. **Add a corrupt-config test** — invalid `.env`, malformed MCP JSON, bad
    model name; assert a friendly error and non-zero exit.
11. **Add a provider timeout test** — simulated slow stream; assert cancellation
    and a user-facing message.
12. **Add a redaction test** — API keys, bearer tokens, and paths in errors and
    logs are masked.

### Small features

13. **Add `/version`** — show version, Python, platform, and provider/model.
14. **Add `corvustui models --json`** — stable machine-readable output.
15. **Add a `--no-color` flag** — plain output for CI and screen readers.
16. **Add a “copy last answer” action** — TUI binding and CLI flag where
    clipboard support exists; degrade with a message.
17. **Add prompt-history search** — `/history <query>` over the existing JSONL.
18. **Add a session-title fallback** — derive a readable title from the first
    prompt without a model call.
19. **Add an “open data directory” command** — platform-aware, prints the path
    when no opener exists.
20. **Add a startup timing line** — behind `--debug`, report import/startup
    time.

### Refactors with guardrails

21. **Extract provider retry logic** — shared helper for 429/5xx with jitter;
    no behavior change, tests first.
22. **Split `cli.py`** — move session commands into `cli_sessions.py` once
    Phase 4 lands; keep the entry point unchanged.
23. **Type the TUI event payloads** — replace `Any` in event dataclasses with
    concrete types; no runtime change.
24. **Add `py.typed`** — mark the package typed and fix the first batch of
    mypy errors in one module.

---

## 15. Dependencies, critical path, and sequencing

```text
Phase 0 (release/CI/ADRs)
   │
   ├──► Phase 4.1 schema/store ──► 4.2 agent integration ──► 4.4 TUI sessions
   │                                  │                        │
   │                                  └──► 4.3 CLI sessions    │
   │                                                           │
   └──► Phase 5.1 content model ◄──────────────────────────────┘
              │
              ├──► 5.2 artifact store ──► 5.3 images ──► 5.6 TUI attachments
              │                        ├──► 5.4 documents
              │                        └──► 5.5 audio/video
              │
              └──► 5.7 image generation (after 5.3)
                        │
Phase 6.1 skills ◄────────┘
   ├──► 6.2 built-in skills
   ├──► 6.3 memory v2 ──► 6.4 context builder ──► 6.5 TUI skill/memory UX
   │
Phase 7.1 paths/secrets ──► 7.2 CI/packaging ──► 7.3 terminal fallbacks
   └──► 7.4 plugins ──► 7.5 doctor ──► 7.6 release
                                          │
Phase 8.1 virtualization ◄────────────────┘
   ├──► 8.2 performance
   ├──► 8.3 observability
   ├──► 8.4 benchmarks
   └──► 8.5 security/fuzzing
              │
Phase 9 API freeze ──► docs ──► examples ──► beta ──► v1.0.0
```

### Critical path

`Phase 0 → 4.1 → 4.2 → 5.1 → 5.2 → 5.3/5.4 → 6.3 → 6.4 → 8.1 → 9.5`

Everything else can slip a week without moving the v1.0 date. Protect the
critical path:

- Do not start Phase 5 before 4.1 and 4.2 are merged.
- Do not start Phase 6 memory work before sessions exist; memory without
  provenance is not reviewable.
- Do not start Phase 7 plugins before the Phase 6 interfaces are stable.
- Do not start Phase 9 docs before the feature set is frozen.

### Parallelization rules

- One owner per epic; a second person reviews.
- Two epics may run in parallel only if they touch different directories or
  one is behind a feature flag.
- Storage schema changes merge alone; no feature PR may include a migration.
- Provider adapters can be developed in parallel with TUI work against the
  same fake payload fixtures.

---

## 16. Risk register

| Risk | Likelihood | Impact | Mitigation | Owner |
|------|-----------|--------|------------|-------|
| Scope creep in multimodal | High | High | Cap v0.3 at images + documents + transcript MVP; defer full video understanding | Lead |
| Provider API drift | Medium | High | Adapter isolation, recorded fixtures, capability registry, contract tests | Providers |
| Attachment parser CVE | Medium | High | Pin versions, fuzz, size/time limits, optional extras | Security |
| SQLite corruption / data loss | Low | High | WAL, backups before migration, integrity check, export command | Storage |
| Privacy leak to cloud | Medium | High | Consent prompt, size/model display, redaction, local-first default | Lead |
| TUI performance regression | Medium | Medium | Virtualization, benchmarks, 1k-message fixture | TUI |
| Team availability drops (exams) | High | Medium | 20% buffer, one-epic-per-person, explicit “pause” policy | Lead |
| Plugin API instability | Medium | Medium | Freeze at v1.0, version handshake, experimental label | Architecture |
| Dependency bloat | Medium | Medium | Optional extras, lazy imports, install-size budget | Platform |
| Burnout / bus factor | Medium | High | Pair on critical paths, document decisions, rotate release owner | Lead |
| PyPI/name dispute | Low | High | Publish early, keep the trusted publisher configured | Lead |
| Terminal image fragmentation | High | Low | Fallback-first design, `doctor`, external open | TUI |

### When a phase slips

1. Cut scope, not quality: move epics to the next release and say so in the
   status table.
2. Never skip the release gates or the migration test.
3. If the critical path slips more than two weeks, re-baseline the timeline
   publicly rather than silently.
4. Keep a “known limits” section in the release notes, as Phase 3 did.

---

## 17. Success metrics

Measure outcomes, not activity. Baselines come from v0.1.0 and the Phase 8
benchmarks.

### Product

| Metric | v0.1.0 | v1.0 target |
|--------|--------|-------------|
| Sessions resumed without loss | 0% (unsupported) | >99% |
| Crash-free turns | Not measured | >99.5% |
| Unsupported-input crashes | Possible traceback | 0 |
| Time to first useful answer (local) | Provider-bound | No client regression |
| Attachments supported | 0 | Images, PDF, DOCX, XLSX, PPTX, text, audio, video (transcript) |
| Built-in skills | 0 | ≥8 |
| Install paths | uv/pipx/curl | + PyPI, Docker, Homebrew, one binary |

### Engineering

| Metric | Target |
|--------|--------|
| Test count | ≥700 by v1.0 |
| Line coverage (new code) | ≥80% |
| Ruff violations | 0 |
| Startup time (no MCP) | <1.5 s |
| Session load (1k messages) | <200 ms |
| Chat mount (1k messages) | <300 ms |
| CI duration | <10 min |
| Open P0/P1 bugs at release | 0 |
| Flaky tests | <1% |

### Community

| Metric | Target by v1.0 |
|--------|----------------|
| External contributors with merged PRs | ≥5 |
| Good-first issues closed by newcomers | ≥10 |
| Third-party plugins/skills | ≥3 |
| Beta users | 5–10 |
| Time to first review | <72 h |

Review metrics monthly; do not optimize a metric that harms safety or clarity.

---

## 18. Explicitly out of scope (through v1.0)

Saying no is part of a realistic roadmap. These are deliberate deferrals, not
oversights:

- **Full video understanding** (temporal reasoning, long recordings). v0.3
  provides transcript + keyframes only.
- **Real-time audio conversation** (microphone streaming, barge-in). Text and
  file input first.
- **Multi-user collaboration, shared sessions, and server mode.** The
  architecture leaves room for a `serve` adapter, but v1.0 is single-user.
- **Mobile or web UI.** Terminal-first.
- **Model training or fine-tuning.** CorvusTUI orchestrates models; it does not
  train them.
- **Arbitrary skill code execution.** Skills are prompts + declared tools until
  a signing/trust model exists.
- **Telemetry by default.** Opt-in diagnostics only.
- **Windows-native terminal image protocols** beyond a safe fallback.
- **A hosted cloud service.** Local-first; users bring providers.

Revisit these after v1.0 with user evidence, not enthusiasm.

---

## 19. Tracking and governance

### Status table (update monthly)

| Phase | Release | Status | Owner | Start | Target | Notes |
|-------|---------|--------|-------|-------|--------|-------|
| 0 | v0.1.0 | In progress | Lead | 2026-12-07 | 2026-12-13 | PyPI release pending |
| 4 | v0.2.0 | Planned | TBD | 2026-12-14 | 2027-01-24 | Sessions |
| 5 | v0.3.0 | Planned | TBD | 2027-01-25 | 2027-03-21 | Multimodal |
| 6 | v0.4.0 | Planned | TBD | 2027-03-22 | 2027-05-09 | Skills/memory |
| 7 | v0.5.0 | Planned | TBD | 2027-05-10 | 2027-06-20 | Portability/plugins |
| 8 | v0.6.0 | Planned | TBD | 2027-06-21 | 2027-07-18 | Performance |
| 9 | v1.0.0 | Planned | Lead | 2027-07-19 | 2027-09-26 | Stable |

Status values: `Planned`, `Ready`, `In progress`, `Blocked`, `In review`,
`Done`, `Deferred`.

### Monthly review agenda (45 minutes)

1. Status table and critical path (10 min).
2. Blocked issues and decisions needed (10 min).
3. Metrics and regressions (10 min).
4. Security/privacy review of the month’s changes (5 min).
5. Next month’s epics and owners (10 min).

### Decision log

Record every irreversible or cross-team decision in `docs/adr/` and link it from
the relevant epic. If a decision changes, supersede the ADR; do not rewrite
history.

### First 30 / 60 / 90 days

**Days 1–30**
- Publish v0.1.0 to PyPI and verify installs.
- Merge ADR-001/002/003 and CI.
- Land the session schema/store behind a flag.
- Create the good-first issues and the project board.

**Days 31–60**
- Agent autosave/resume and CLI session commands.
- TUI session picker.
- Artifact store spike and image payload fixtures.
- First external contributor PR merged.

**Days 61–90**
- `v0.2.0` released with sessions.
- Image input end-to-end on Ollama + one cloud provider.
- PDF extraction with citations.
- Multimodal demo recorded and docs updated.

---

## 20. Open questions for the team

Resolve these in Phase 0/4; do not let them block early implementation.

1. **Default session behavior:** autosave every turn, or prompt on first exit?
   Recommendation: autosave with `--no-save` as the escape hatch.
2. **Project-local sessions:** allow `.corvustui/sessions.db` per repository, or
   keep all sessions global with a project tag? Recommendation: global store,
   project tag, opt-in local store.
3. **Cloud consent:** per-file, per-session, or per-provider? Recommendation:
   per-session with a “remember for this provider” toggle.
4. **Memory default:** explicit `/remember` only, or automatic extraction?
   Recommendation: explicit by default, automatic opt-in.
5. **Attachment retention:** keep originals, or keep extracted text only?
   Recommendation: keep originals until session purge; document disk usage.
6. **Image generation scope:** cloud only at first, or a local backend too?
   Recommendation: one cloud adapter + a flagged local spike.
7. **Plugin distribution:** entry points only, or a curated registry?
   Recommendation: entry points first; no central registry before v1.0.
8. **Minimum Python:** keep 3.11, or move to 3.12+? Recommendation: keep 3.11
   until the CI matrix is stable, then decide at v1.0.
9. **Release cadence:** every 4 weeks or 8 weeks? Recommendation: 6–8 weeks,
   with patch releases as needed.
10. **Team size:** 4 or 6? Recommendation: plan for 4; add contributors to
    non-critical epics only.

---

## Appendix A: Phase-to-code map

| Phase | Primary new modules | Existing modules touched |
|-------|---------------------|--------------------------|
| 4 | `session/models.py`, `session/store.py`, `session/service.py` | `core/agent.py`, `react/loop.py`, `cli.py`, `tui/app.py` |
| 5 | `domain/content.py`, `attachments/*`, `providers/capabilities.py` | `providers/*`, `tui/widgets/input_area.py`, `memory/vector.py` |
| 6 | `skills/*`, `memory/*`, `context/builder.py` | `core/agent.py`, `tui/app.py`, `cli.py` |
| 7 | `platform/*`, `plugins/*`, `diagnostics/*` | `config/settings.py`, `cli.py`, packaging |
| 8 | `observability/*`, benchmarks, fuzz corpus | `tui/widgets/chat_view.py`, providers, stores |
| 9 | `docs/`, `examples/` | README, USER_GUIDE, PUBLISHING |

## Appendix B: Definition of Done checklist (copy into PRs)

```markdown
- [ ] Problem and approach described
- [ ] Tests added (unit; integration if applicable)
- [ ] `pytest -q` passes
- [ ] `ruff check src tests --select F,E9,I001` passes
- [ ] Bend proofs pass (or not applicable)
- [ ] Docs updated (README/USER_GUIDE/roadmap status)
- [ ] Migration/backward-compatibility note (storage changes)
- [ ] Feature flag or setting (experimental behavior)
- [ ] No secrets, no network in unit tests
- [ ] Screenshot/demo for UI changes
- [ ] Release notes entry
```

## Appendix C: Release checklist

```markdown
- [ ] All phase exit criteria checked
- [ ] Full test suite + Ruff + Bend
- [ ] Migration test from the previous release
- [ ] Clean install test (uv, pipx, Docker if supported)
- [ ] TUI smoke test in 80×24 and 120×40
- [ ] `uv build` and `uvx twine check dist/*`
- [ ] Version bumped in `pyproject.toml` and `__init__.py`
- [ ] CHANGELOG and release notes written
- [ ] Tag `vX.Y.Z`, push, publish GitHub release
- [ ] Verify PyPI page and install from PyPI
- [ ] Announce with known limits
```

---

*This roadmap is a living document. Update the status table, not the history.
When reality changes, change the plan and say why.*
