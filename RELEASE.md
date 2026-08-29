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

6. **`gorkbot race` — single-axis A/B/C trials with an impartial judge (`gorkbot/race.py`, `gorkbot/terrarium.py`, `gorkbot/archivist.py`)**
   - `CandidateSpec` now carries six axes: seat, harness, tool runner, skills, role, and `context` (`fresh` | `accounts` | `fork`). `fork` replays the parent's exact prompt prefix for a cache hit.
   - Presets vary exactly one axis at a time (`--variants models|harness|tools|skills|context`); a custom grammar (`model=..+harness=..+tools=..+skills=a/b+ctx=..`) composes candidates by hand.
   - Candidates are drawn from the authenticated `SeatLedger`; sandbox directories are slug-safe on Windows.
   - The archivist ignores verification side-effects (`__pycache__`, `.pytest_cache`, `.hidden_tests`), reports ties instead of crowning duration jitter, and weights hidden tests above a candidate's own.
   - `--mock` runs canned `good` / `slow` / `liar` providers against an ephemeral store, so demos never touch the real scorecard. `--teardown` / `--keep` control sandbox lifetime.

7. **Task bank and the tester role (`gorkbot/tasks.py`, `gorkbot/definitions/tasks/`, `gorkbot/definitions/roles/tester.md`)**
   - `gorkbot tasks` lists briefs with hidden acceptance tests (`lru_cache`, `sqlite_cache`, `rate_limiter`); `gorkbot race --task <name>` grades every candidate against tests it never saw, including a time budget where the brief says "fast".
   - `tester` is a real role again (test engineer, not a reviewer alias). `--tester` has it author the hidden suite before the builders run.

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
