# Arity release notes

The distribution and primary CLI are now `arity`. Python imports remain exclusively under
`gorkbot`, and `.gorkbot/` remains the active state/config location. Historical entries below
retain the names used when they were published.

Architecture correction: `transition` mutates `State` and emits effect descriptions; it is
I/O-free, not referentially pure. Verification, archival, and blind review are built-in trial
stages; `Observer` is a telemetry hook.

## Arity 0.4.0 — Frozen evidence, replayable trials

**Release Date:** 2026-08-30

Arity now carries an A/B trial from declared arms through frozen evidence, explicit resolution,
delivery, and read-only inspection without relying on a live workspace or provider to explain what
happened.

### What changed:

1. **Arity is the public distribution and control surface**
   - The primary package distribution and command are `arity`; `--arity N` is a positive requested
     maximum and reports how many unique candidates actually resolved.
   - The `gorkbot` Python namespace, console entry point, state directory, and environment fallbacks
     remain an explicit compatibility boundary rather than a second product identity.

2. **Evidence and resolution are explicit contracts**
   - Candidate artifacts and factual axes freeze into a content-addressed `EvidenceBundle` before
     evaluation. Evaluators rank that immutable bundle, so alternate eval systems can run later
     without rerunning candidate harnesses.
   - `Resolution` records facts winners, judge consensus, attributable human picks, or an unresolved
     outcome. Delivery validates the persisted resolution and writes the frozen artifact bytes rather
     than trusting a mutable workspace or caller-supplied report.

3. **Trial lifecycles are journaled and replayable**
   - Versioned events record trial declaration, arm completion, frozen evidence, reviews, resolution,
     and delivery through the configured JSONL or SQLite `RecordStore`.
   - Strict replay checks declared arms, phase ordering, evidence hashes, evaluator panels, resolution
     authority, and delivery binding; malformed or incomplete histories fail closed.

4. **Inspection is a read-only observer surface**
   - `arity trials`, `arity trial show`, and `arity trial replay` inspect persisted journals without
     running agents, consulting providers, repairing records, or creating a missing store.
   - The shared Python projection is intended for TUIs, GUIs, and other observers. Human and JSON
     output distinguish missing, partial future-schema, changed, and corrupt records; `show` omits
     candidate output and artifact bodies while explicit replay can expose the full local journal.

5. **The installed artifact has its own acceptance gate**
   - A clean-wheel two-arm trial verifies context isolation, immutable evidence, evaluator-driven
     resolution, frozen-byte delivery, persisted replay, JSONL/SQLite inspection behavior, and the
     installed `arity` command outside the source checkout.

6. **A deliberate public-release security boundary**
   - Arity is MIT licensed and checked on Python 3.10 and 3.14 across Linux and Windows. Release
     builds validate both source distributions and installed wheels.
   - No provider OAuth client identity or secret is bundled. Experimental native OAuth adapters
     require caller-supplied configuration, fail before opening a browser, callback server, or
     network request when it is absent, and retain the supplied configuration for later refreshes.
   - Credential-file updates use same-directory atomic replacement and POSIX owner-only mode where
     supported. Credentials remain plaintext, and model-directed tools remain outside any OS-level
     sandbox; these limits are explicit in `SECURITY.md`.

## gorkbot 0.3.0 — Trials

**Release Date:** 2026-08-29

A trial is the primitive: a small harness, agent types with system prompts, skills and tools, run in
parallel, then facts, then opinions, then you. This release makes that primitive a command, gives it
a front door, and uses it to build one of gorkbot's own organs.

### What changed:

1. **`gorkbot race` — one task, N candidates, one archivist (`gorkbot/race.py`, `gorkbot/terrarium.py`)**
   - `CandidateSpec` carries six axes: seat, harness, tool runner, skills, role, and `context` (`fresh` |
     `accounts` | `fork`; `fork` replays the parent's exact prompt prefix for a cache hit).
   - Presets vary exactly one axis at a time (`--variants models|harness|tools|skills|context`), so a result
     is attributable. A custom grammar (`model=..+harness=..+tools=..+skills=a/b+ctx=..`) composes candidates.
   - Sandboxes are per candidate; `--teardown` / `--keep`; `--mock` runs canned `good` / `slow` / `liar`
     providers against an ephemeral store so demos never touch the scorecard.
   - Three phases after the isolated build: **verify** (own tests plus a hidden suite the candidate never saw),
     **review** (`--judges`: the reviewer role reads a blind bundle, letters shuffled, and ranks with cited
     reasons; runs only when the facts tie), **conference** (`--conference N`: the candidates are woken up in
     their own sandboxes with each other's work under `peers/` and notes via `message(to="peer:B")`, then
     re-verified).

2. **`gorkbot run` — the front door (`gorkbot/race.py`, `gorkbot/cli.py`)**
   - One seat per model, fullest quota first, wire-capable first, capped by `--candidates` / `GORKBOT_CONCURRENCY`.
   - Race → review on a facts tie → if the judges split, the secretary asks you on the terminal and the answer
     is stored as a `human_pick` → **deliver**: the winner's files to `--out` (or `deliveries/<task_id>/`), or
     `answer.md` when there are none, with a one-line receipt. `--tester`, `--conference`, `--verbose`, `--json`.

3. **The archivist counts; the judge opines; you decide (`gorkbot/archivist.py`, `gorkbot/standings.py`)**
   - Trials are ordered by tiers of fact — verdict, hidden pass rate, own pass rate — and cost only inside a
     tier. Identical facts are reported as a tie. No summed score orders a trial.
   - Every candidate gets a `trial_axes` record: prompt vs. completion tokens, turns, tool calls and errors,
     test runs, LOC, test count, type-ignores, bare asserts, whether the brief's hard numbers appear in its own
     tests, fallbacks, changed files (conference), fetch reach, **false claims** and **confessions**.
   - `gorkbot standings [--by model|signature|harness]` aggregates those records — success and hidden-pass
     rates, lie and confession rates, cost, fallbacks, and judge-side facts (ranked its own model first;
     citations found true). No composite.
   - Judgements are records too: the blind bundle states what is already counted so the judge spends
     tokens on idiom and intent; each judge's cited identifiers are checked against the sandbox and printed
     beside its ranking, never scored.

4. **Types: a role plus a language (`gorkbot/definitions/types/`, `gorkbot/roles.py`)**
   - `developer:python`, `tester:python`, `reviewer:python` share one pack (skills, prompt append, verify
     commands). A task's tags pick the type; the tester and the judge in a race take the builders' type.
     `python_developer.md` is replaced by `developer.md` + `types/python.md`; `types/rust.md` is a stub.

5. **Task bank, tester, judge, secretary (`gorkbot/tasks.py`, `gorkbot/definitions/`)**
   - `gorkbot tasks`: briefs with hidden acceptance tests (`lru_cache` with a 200k-ops time budget,
     `sqlite_cache`, `rate_limiter`, `sqlite_record_store`).
   - `tester` is a test engineer again (writes acceptance tests before and apart from the implementation);
     `reviewer` is a read-only judge that must cite evidence and may say "tie"; the `secretary` shows you two
     candidates and asks when the judges disagree, and never presents a provisional winner as decided.

6. **Seats, wires, and metering (`gorkbot/ledger.py`, `gorkbot/wire.py`, `gorkbot/handlers.py`)**
   - One Antigravity seat per (account, model) with `remaining` seeded from the live quota; the backend keeps
     two quotas (Gemini; Claude+GPT-OSS together). Each seat spends its own account's token and rotates to a
     sibling account on 429 before any CLI fallback. `live_seats()` returns fullest quota first.
   - Gemini: tool declarations are sent, calls replay with thought signatures, Claude-behind-Antigravity
     carries tool ids, empty model turns are never sent; one conversion (`gemini_format.py`) for both wires.
   - Honest metering: Codex usage is read from `response.completed`; Gemini counts thought tokens; a wire
     that had to guess marks `estimated`. CLI harnesses run non-interactively, killably, inside the candidate's
     sandbox, and are filed as `cli:<harness>` when they had no wire. Fallbacks are recorded; a moved harness
     is never attributed to the wire.
   - `fetch_url` presents as a browser and falls back to a reader proxy for JS shells.

7. **Stores (`gorkbot/stores/sqlite.py`)**
   - `SqliteRecordStore` implements the `RecordStore` seam (append/query) plus `kinds()` and idempotent
     `replay_jsonl()`. `GORKBOT_STORE=sqlite` selects it; the JSONL store (now locked against interleaved
     writes) remains the default.
   - **It was built by the trial system it records:** `gorkbot run --task sqlite_record_store --tester
     --conference 1`. The tester wrote 18 hidden tests; three models built it; GPT-5.6-sol passed 24/24; one
     conference round; the delivered file credits what it borrowed from a peer. It replays this release's
     3,155 JSONL records losslessly and skips the 10 lines the pre-lock store had corrupted.

### Numbers

- 96 commits, one per fix or feature. 110 tests.
- 9 live code races and 2 live scout races on the wire across GPT-5.6-sol, Grok 4.5, Claude Opus 4.6
  (Antigravity) and Gemini 3.6 Flash; 2 front-door runs; 1 organ delivered.

### Known limits

See `TODO.md` — the issue tracker until there is a remote. The large ones: pre-flight casting against
remaining quota; actual cost per quota window vs. API-equivalent; the resolve step when judges split
(cherry-pick diffs, "keep both"); CLI harness tools bypass the role's denial set (contained by `cwd`, not
enforced); a headless browser past the reader proxy; the claude CLI as a deliberate harness.

---

# gorkbot 0.2.0 — Direct Line

**Release Date:** 2026-08-28

### What changed:

1. **Multi-account Google Antigravity & OAuth (`gorkbot/auth.py`, `gorkbot/wire.py`)**
   - Added a standard-library Python OAuth 2.0 PKCE flow on port 51121 (`gorkbot auth login google`).
   - Imports and tracks multiple Google accounts from `~/.omp/agent/agent.db` so you can use quota from different accounts.
   - Added live quota checks via `daily-cloudcode-pa.googleapis.com` in `gorkbot auth status`, showing remaining percentages for Gemini and Claude.
   - Auto-refreshes expired access tokens in the background.

2. **Simplified seat model (`gorkbot/ledger.py`, `gorkbot/wire.py`)**
   - Removed artificial seat IDs. A seat is now `(provider, model, harness)` with an optional account name.
   - Direct Python wire calls run first; if they fail or time out, execution falls back to the installed CLI tool (`omp`, `codex`, `claude`).

3. **Restructured staff roles (`gorkbot/definitions/roles/`, `gorkbot/roles.py`)**
   - Removed duplicate role definitions (`voice.md`, `builder.md`, `tester.md`).
   - Standardized on five roles: `secretary` (front desk), `scout` (search and research), `engineer` (planning), `python_developer` (coding), and `reviewer` (auditing and test execution).
   - Removed the arbitrary tier number math in favor of role-based context assembly.

4. **Unified messaging tool & runtime security (`gorkbot/tools.py`, `gorkbot/orchestrator.py`)**
   - Replaced subagent spawning functions with a single `message(to, text)` tool. Setting `to="user"` replies to the human; setting `to="<role>"` routes to a teammate.
   - Removed static `allowed_tools` lists from markdown files. Roles now inherit available tools automatically, filtered by their `denied_tools` and `denied_paths`.
   - Moved path and host access checks to the tool runner (`SandboxToolRunner`), preventing false-positive crashes when prompt text discusses files like `.env`.

5. **Terminal chat cache timer (`gorkbot/cli.py`)**
   - `python -m gorkbot chat` shows a countdown timer indicating how long the model's prompt cache stays warm before each input line.

---
### What's New in 0.1.1:
1. **Direct Wire Protocols (`gorkbot.wire`)**:
   - Direct HTTPS/SSE streaming callers for OpenAI Codex (`https://chatgpt.com/backend-api/codex/responses`) and xAI Grok (`https://api.x.ai/v1/chat/completions`).
   - Auto-discovers local OAuth subscription credentials from `~/.gorkbot/auth.json` and `~/.omp/agent/agent.db`.
   - Evaluated at flat-rate subscription cost ($0.0001/M) in the Seat Ledger.
2. **Transparent Seam Fallbacks (`FallbackModelProvider`)**:
   - Automatically intercepts wire failures (HTTP 401/429) and shifts execution seamlessly to CLI subscription harnesses (`CLIModelProvider` invoking `codex exec`, `claude -p`, or `omp task`).
3. **Telemetry Observability**:
   - Tracks cache hit ratios, tool execution success ratios, and wire fallback counts in `MetricsObserver`.
4. **Red Phone CLI Dashboard**:
   - `python -m gorkbot status`: Real-time ASCII dashboard showing active seats, wire connections, and empirical scorecard rankings.
   - `python -m gorkbot redphone`: Historical event timeline of public and private message channels.

---

# gorkbot 0.1.0 — First Pulsing Droplet

**Release Date:** 2026-08-28  
**Architecture:** Composable pure statechart agent chassis with 5 explicit seams and 7 elemental parts.

---

## The Vision & Core Premise

`gorkbot` is built on David Khourshid's pure statechart / Elm reducer formulation:

$$\text{transition}(\text{state}, \text{event}) \longrightarrow (\text{new\_state}, \text{effects})$$

By separating pure deterministic state transitions from asynchronous side-effect execution, the core control loop remains completely uncoupled from network requests, operating system calls, or third-party SDKs. Any community infrastructure (model routers, tool harnesses, vector memories, computer-use daemons, communication channels) plugs into explicit protocol seams without touching core state logic.

---

## The 5 Seam Protocols (`gorkbot.seams`)

1. **`ModelProvider`**: Universal protocol for `/chat/completions` and streaming token providers (OpenAI, Anthropic, Gemini, OpenRouter, LiteLLM, vLLM).
2. **`ToolRunner`**: Universal protocol for tool schema declaration and execution (MCP servers, local sandboxes, native Rust drivers).
3. **`RecordStore`**: Append-only persistence protocol for transcripts, trial results, and telemetry (JSONL, SQLite, LanceDB).
4. **`Transport`**: Omnichannel I/O protocol for incoming events and outgoing message delivery (CLI, Webhooks, Discord, Slack, SMS/LiveKit).
5. **`Observer`**: Passive wire-sniffing protocol for telemetry, evaluators, and impartial audit judges.

---

## The 7 Elemental Parts Shipped

### 1. Role & Persona Compiler (`gorkbot.roles`, `gorkbot.tiers`)
- **Role Registry & Archetypes:** `voice` (Tier 0), `architect` (Tier 1), `builder` (Tier 2), `reviewer` (Tier 2).
- **Denial Sets (Axioms 2 & 12):** Explicit restriction sets for denied tools, paths, network hosts, and entity names.
- **Distance-from-Asa Memory Tiers (Axiom 8):**
  - *Tier 0 (Voice):* Ingests personal biograph notes and user preferences.
  - *Tier 1 (Architect):* Ingests project architecture, roadmap, and system contracts.
  - *Tier 2 (Builder/Leaf Worker):* Strictly restricted to task instructions and local scratchpads.
- **Prompt Cache Prefix Preservation (Axiom 7):** Assembles layered prompts (System $\to$ Tools $\to$ Repo Map $\to$ History $\to$ Dynamic Tail) to maximize 90% KV cache read discounts.
- **Leak Refusal (`BriefRefusalError`):** Scans compiled briefs and raises an immediate refusal if any denied path, host, or secret leaks into the prompt.
- **Predecessor Accounts (Axiom 9):** Formats the previous kernel's self-report alongside the impartial archivist's entry across cold restarts.
- **Identity Tuples:** Computes `provider:model:session_id:hash(brief)`.

### 2. Seat Ledger & Casting Composer (`gorkbot.ledger`, `gorkbot.composer`)
- **Seat Registry:** Manages quota allowances, cycle windows, and warm cache TTLs across Gemini, NVIDIA NIM, OpenAI, OpenRouter, and Anthropic.
- **Expiring-Quota Dynamic Cost Weighting ($C_{\text{eff}}$ - Axiom 3):** Decays effective token cost to 0 as reset deadlines approach ("use it or lose it"), routing tasks and speculative rollouts to expiring seats.
- **Presence Locking (Axiom 36):** Automatically excludes any seat where a human or active session is live (`presence=True`).
- **Multi-Candidate Casting:** Ranks available seats by role aptitude and reset urgency (`dying_soonest`), supporting single or multi-seat selections.

### 3. Multi-Kernel Terrarium & Handoff Router (`gorkbot.terrarium`)
- **Structured Handoffs (`TaskRecord` - Axiom 1):** Delegation records with sender, target role, brief, evidence budget, and recursion depth bounds.
- **Terrarium Dispatcher (Axiom 3 Corollary):** Concurrently executes candidate kernels in parallel (`ThreadPoolExecutor`) for A/B trial evidence.
- **Workspace Sandboxing:** Automatically provisions isolated workspace directories (`.terrarium/{task_id}/{candidate_id}/`).
- **Token Metering:** Automatically meters tokens used and updates remaining seat balances in the ledger.

### 4. Tool Execution Engine & Sandbox (`gorkbot.tools`)
- **Sandbox Tool Runner:** Enforces strict workspace path confinement, preventing directory traversal attacks (`../` escaping).
- **AST Pre-Write Syntax Validation:** Parses Python code with `ast.parse` before writing files to prevent corrupting workspace scripts.
- **Runtime Denial Interceptor:** Actively blocks denied tools and paths during execution, returning clean security errors.
- **MCP Adapter (`McpToolAdapter`):** Translates Model Context Protocol JSON-RPC tool schemas (`inputSchema`) to OpenAI function calling format and dispatches executions.

### 5. Impartial Archivist & Evidence Scorecard (`gorkbot.scorecard`, `gorkbot.archivist`)
- **Impartial Archivist (Axiom 9):** Passive third-person auditor that verifies claimed model actions against real `tool_result` events and physical filesystem artifacts.
- **Discrepancy Detection & Penalty:** Flags hallucinations where a model claims it created or modified files that do not exist, imposing a severe -2.5 standing penalty on the scorecard.
- **Absent Report Fallback:** Explicitly records `self_report_present = False` when a kernel terminates unexpectedly.
- **Scorecard Standing Ledger:** Tracks empirical model performance by role, rewarding verified completions (+1.0) and penalizing failures (-1.0).
- **Trial Winner Selection:** Evaluates parallel candidate outcomes and selects the verified winner.

### 6. Pulse & Economic Keepalive Engine (`gorkbot.pulse`)
- **Economic Keepalive Math (Axiom 11 / Story S39):** Evaluates active sessions against cache economics:
  $$\text{Ping if } P(\text{return}) \times \text{cold\_cost} > \text{ping\_cost}$$
- **Keepalive Ping:** Sends minimal 3-token heartbeat (`"hi luv u"`) to keep warm prefixes alive when economically justified; otherwise allows kernels to die and triggers archivist auditing.
- **Expiring Quota Harvester:** Discovers seats with unused tokens nearing expiration ($T_{\text{reset}} - t \le 1\text{h}$) to trigger background tasks.

### 7. Transports & End-to-End Orchestrator (`gorkbot.transports`, `gorkbot.orchestrator`)
- **Red Phone Public Address (`RedphoneInbox` - Axiom 10):** Channel-based queue (`redphone.com/asas`, `friction`, `main`) for human DMs, public submissions, and bot-to-bot notifications.
- **Webhook Transport:** Transport adapter for webhook ingress and egress.
- **Master Orchestrator (`GorkbotOrchestrator`):** Unites all 7 parts into a single seamless loop:
  $$\text{User Ingress} \longrightarrow \text{Voice (Tier 0)} \longrightarrow \text{Delegation} \longrightarrow \text{Composer} \longrightarrow \text{Terrarium (A/B)} \longrightarrow \text{Archivist Audit} \longrightarrow \text{Scorecard Update} \longrightarrow \text{Briefing} \longrightarrow \text{Pulse}$$

---

## Verification & Test Suite

- **Unit & Integration Suite:** 34 tests passing across all 7 elemental modules (`tests/`).
- **CLI Demo:** `python -m gorkbot demo` executes a deterministic multi-turn trial with file creation, AST validation, archivist verification, and pulse checks.

---

## File Layout

```
gorkbot/
├── __init__.py         # Public API exports
├── types.py            # Event, Effect, State, Status dataclasses
├── transition.py       # Pure statechart transition reducer
├── seams.py            # ModelProvider, ToolRunner, RecordStore, Transport, Observer protocols
├── handlers.py         # Zero-dependency stdlib default handlers
├── runtime.py          # Execution chassis and effect dispatcher
├── roles.py            # Role definitions and Denial Sets (Part 1)
├── tiers.py            # Distance-from-Asa memory tiers and BriefCompiler (Part 1)
├── ledger.py           # Seat registry and quota management (Part 2)
├── composer.py         # Casting composer and aptitude router (Part 2)
├── terrarium.py        # Multi-kernel parallel dispatcher and sandboxing (Part 3)
├── tools.py            # SandboxToolRunner, AST syntax checks, MCP adapter (Part 4)
├── scorecard.py        # Scorecard standing ledger (Part 5)
├── archivist.py        # Impartial archivist evidence auditor (Part 5)
├── pulse.py            # Economic keepalive engine and quota harvester (Part 6)
├── transports.py       # RedphoneInbox and WebhookTransport (Part 7)
├── orchestrator.py     # Master end-to-end orchestrator (Part 7)
└── cli.py              # CLI entry point (demo, chat, run)
```
