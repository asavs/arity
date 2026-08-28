# arity 0.1.1 — Marrow & Wire

**Release Date:** 2026-08-28  
**Highlights:** Direct Codex & Grok subscription wire protocols, local OAuth token discovery, transparent CLI harness fallbacks, and real-time Red Phone CLI dashboard.

### What's New in 0.1.1:
1. **Direct Wire Protocols (`arity.wire`)**:
   - Direct HTTPS/SSE streaming callers for OpenAI Codex (`https://chatgpt.com/backend-api/codex/responses`) and xAI Grok (`https://api.x.ai/v1/chat/completions`).
   - Auto-discovers local OAuth subscription credentials from `~/.arity/auth.json` and `~/.omp/agent/agent.db`.
   - Evaluated at flat-rate subscription cost ($0.0001/M) in the Seat Ledger.
2. **Transparent Seam Fallbacks (`FallbackModelProvider`)**:
   - Automatically intercepts wire failures (HTTP 401/429) and shifts execution seamlessly to CLI subscription harnesses (`CLIModelProvider` invoking `codex exec`, `claude -p`, or `omp task`).
3. **Telemetry Observability**:
   - Tracks cache hit ratios, tool execution success ratios, and wire fallback counts in `MetricsObserver`.
4. **Red Phone CLI Dashboard**:
   - `python -m arity status`: Real-time ASCII dashboard showing active seats, wire connections, and empirical scorecard rankings.
   - `python -m arity redphone`: Historical event timeline of public and private message channels.

---

# arity 0.1.0 — First Pulsing Droplet

**Release Date:** 2026-08-28  
**Architecture:** Composable pure statechart agent chassis with 5 explicit seams and 7 elemental parts.

---

## The Vision & Core Premise

`arity` is built on David Khourshid's pure statechart / Elm reducer formulation:

$$\text{transition}(\text{state}, \text{event}) \longrightarrow (\text{new\_state}, \text{effects})$$

By separating pure deterministic state transitions from asynchronous side-effect execution, the core control loop remains completely uncoupled from network requests, operating system calls, or third-party SDKs. Any community infrastructure (model routers, tool harnesses, vector memories, computer-use daemons, communication channels) plugs into explicit protocol seams without touching core state logic.

---

## The 5 Seam Protocols (`arity.seams`)

1. **`ModelProvider`**: Universal protocol for `/chat/completions` and streaming token providers (OpenAI, Anthropic, Gemini, OpenRouter, LiteLLM, vLLM).
2. **`ToolRunner`**: Universal protocol for tool schema declaration and execution (MCP servers, local sandboxes, native Rust drivers).
3. **`RecordStore`**: Append-only persistence protocol for transcripts, trial results, and telemetry (JSONL, SQLite, LanceDB).
4. **`Transport`**: Omnichannel I/O protocol for incoming events and outgoing message delivery (CLI, Webhooks, Discord, Slack, SMS/LiveKit).
5. **`Observer`**: Passive wire-sniffing protocol for telemetry, evaluators, and impartial audit judges.

---

## The 7 Elemental Parts Shipped

### 1. Role & Persona Compiler (`arity.roles`, `arity.tiers`)
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

### 2. Seat Ledger & Casting Composer (`arity.ledger`, `arity.composer`)
- **Seat Registry:** Manages quota allowances, cycle windows, and warm cache TTLs across Gemini, NVIDIA NIM, OpenAI, OpenRouter, and Anthropic.
- **Expiring-Quota Dynamic Cost Weighting ($C_{\text{eff}}$ - Axiom 3):** Decays effective token cost to 0 as reset deadlines approach ("use it or lose it"), routing tasks and speculative rollouts to expiring seats.
- **Presence Locking (Axiom 36):** Automatically excludes any seat where a human or active session is live (`presence=True`).
- **Multi-Candidate Casting:** Ranks available seats by role aptitude and reset urgency (`dying_soonest`), supporting single or multi-seat selections.

### 3. Multi-Kernel Terrarium & Handoff Router (`arity.terrarium`)
- **Structured Handoffs (`TaskRecord` - Axiom 1):** Delegation records with sender, target role, brief, evidence budget, and recursion depth bounds.
- **Terrarium Dispatcher (Axiom 3 Corollary):** Concurrently executes candidate kernels in parallel (`ThreadPoolExecutor`) for A/B trial evidence.
- **Workspace Sandboxing:** Automatically provisions isolated workspace directories (`.terrarium/{task_id}/{candidate_id}/`).
- **Token Metering:** Automatically meters tokens used and updates remaining seat balances in the ledger.

### 4. Tool Execution Engine & Sandbox (`arity.tools`)
- **Sandbox Tool Runner:** Enforces strict workspace path confinement, preventing directory traversal attacks (`../` escaping).
- **AST Pre-Write Syntax Validation:** Parses Python code with `ast.parse` before writing files to prevent corrupting workspace scripts.
- **Runtime Denial Interceptor:** Actively blocks denied tools and paths during execution, returning clean security errors.
- **MCP Adapter (`McpToolAdapter`):** Translates Model Context Protocol JSON-RPC tool schemas (`inputSchema`) to OpenAI function calling format and dispatches executions.

### 5. Impartial Archivist & Evidence Scorecard (`arity.scorecard`, `arity.archivist`)
- **Impartial Archivist (Axiom 9):** Passive third-person auditor that verifies claimed model actions against real `tool_result` events and physical filesystem artifacts.
- **Discrepancy Detection & Penalty:** Flags hallucinations where a model claims it created or modified files that do not exist, imposing a severe -2.5 standing penalty on the scorecard.
- **Absent Report Fallback:** Explicitly records `self_report_present = False` when a kernel terminates unexpectedly.
- **Scorecard Standing Ledger:** Tracks empirical model performance by role, rewarding verified completions (+1.0) and penalizing failures (-1.0).
- **Trial Winner Selection:** Evaluates parallel candidate outcomes and selects the verified winner.

### 6. Pulse & Economic Keepalive Engine (`arity.pulse`)
- **Economic Keepalive Math (Axiom 11 / Story S39):** Evaluates active sessions against cache economics:
  $$\text{Ping if } P(\text{return}) \times \text{cold\_cost} > \text{ping\_cost}$$
- **Keepalive Ping:** Sends minimal 3-token heartbeat (`"hi luv u"`) to keep warm prefixes alive when economically justified; otherwise allows kernels to die and triggers archivist auditing.
- **Expiring Quota Harvester:** Discovers seats with unused tokens nearing expiration ($T_{\text{reset}} - t \le 1\text{h}$) to trigger background tasks.

### 7. Transports & End-to-End Orchestrator (`arity.transports`, `arity.orchestrator`)
- **Red Phone Public Address (`RedphoneInbox` - Axiom 10):** Channel-based queue (`redphone.com/asas`, `friction`, `main`) for human DMs, public submissions, and bot-to-bot notifications.
- **Webhook Transport:** Transport adapter for webhook ingress and egress.
- **Master Orchestrator (`ArityOrchestrator`):** Unites all 7 parts into a single seamless loop:
  $$\text{User Ingress} \longrightarrow \text{Voice (Tier 0)} \longrightarrow \text{Delegation} \longrightarrow \text{Composer} \longrightarrow \text{Terrarium (A/B)} \longrightarrow \text{Archivist Audit} \longrightarrow \text{Scorecard Update} \longrightarrow \text{Briefing} \longrightarrow \text{Pulse}$$

---

## Verification & Test Suite

- **Unit & Integration Suite:** 34 tests passing across all 7 elemental modules (`tests/`).
- **CLI Demo:** `python -m arity demo` executes a deterministic multi-turn trial with file creation, AST validation, archivist verification, and pulse checks.

---

## File Layout

```
arity/
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
