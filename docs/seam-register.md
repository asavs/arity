# Seam register

**Date:** 2026-09-01
**Answers:** `A13-1` in [the axiom backlog](2026-09-01-axiom-backlog.md).
**Source of the questions:** Axiom 13 in `.wiki/axioms.md` (sibling `Projects/arity` repo).

## How to read this

Axiom 13 says a chunk is not *done* until it answers three questions. This is the first pass at
answering them for every seam Arity has, from the code as it stands on 2026-09-01. It is a
draft written to be corrected, not a status report. Where a seam does not hold, it says so.

The three questions, restated:

1. **Male join — replaceability.** Could this be ripped out tomorrow and replaced by an
   external tool without the rest of the system noticing?
2. **Female joins — pluggability.** What explicit sockets does it expose? What would an alien
   binary have to implement, exactly?
3. **Boundary of intent.** What is the specific unbuilt opinion we own here, versus commodity
   execution someone else will do better?

**[SEAM]** lines name candidate replacements. Per the 1.0.0 plan they are *marked now,
researched later*. Nothing on a [SEAM] line has been evaluated.

**On the citations.** Line numbers were read on 2026-09-01 while several files were being
edited concurrently, and some had already shifted before this document was saved. The **symbol
name is the anchor**; treat the line number as a hint. Every claim below was verified against
the code, not against another document.

Verdicts used below:

- **Holds** — an outside implementation of the declared protocol works, and the rest does not notice.
- **Holds with a leak** — works, but something the protocol never declares is load-bearing.
- **Does not hold** — a conforming outside implementation silently misbehaves or crashes.
- **Half a seam** — only one direction of the join exists.

---

## Summary

| Seam | Declared in | Verdict |
|---|---|---|
| `ModelProvider` | `arity/seams.py` | **Does not hold** — confinement and harness attribution ride on undeclared attributes |
| `ToolRunner` | `arity/seams.py` | **Does not hold** — a third-party *class* is never instantiated; the delivery contract is undeclared |
| `RecordStore` / `RecordReader` | `arity/seams.py` | **Holds with a leak** — two built-ins disagree on filter equality; journal locking probes undeclared attributes; write failures are swallowed |
| `Transport` | `arity/seams.py` | **Half a seam** — egress only. There is no ingress seam at all |
| `Observer` | `arity/seams.py` | **Holds, carries no weight** — every callback is wrapped in `except Exception: pass`, and the archivist that should use it does not |
| `ContextAdapter` | `arity/terrarium.py:131` | **Holds** — the cleanest seam in the repo. Not in `seams.py` |
| `TrialEvaluator` | `arity/evidence.py:587` | **Holds in-process; no socket** — the CLI cannot name one |
| `TrialJournal` | `arity/trial_events.py:455` | **Holds for one process; does not hold across processes** — its own docstring says so |

Two facts apply to every row and are not repeated in each section:

- **There is no out-of-process socket anywhere.** No entry points, no plugin discovery, no
  config key that names a class, no subprocess or RPC boundary at any seam. Every graft is a
  Python object passed to a constructor. The literal answer to "can an alien *binary* plug in
  cleanly with zero glue" is **no, at every seam** — an alien *Python object* can. The one
  exception is `ARITY_STORE` / `ARITY_STORE` (`arity/tools.py:453`,
  `arity/record_readers.py:80`), which selects between the two built-in record backends and
  cannot name a third.
- **The spine is not injectable.** Axiom 13's stack audit lists the pure statechart as one of
  the five seams, with the join `transition(state, event) -> (new_state, effects)`. Arity's
  reducer is genuinely pure and genuinely the only place state changes, but `Runtime` imports
  it at module level and calls it directly (`arity/runtime.py:20,71`). It cannot be replaced
  without editing `Runtime`.
  **[SEAM]** XState v5, LangGraph Pregel/checkpointers, Erlang OTP / Rust Ractor.

---

## 1. `ModelProvider`

**Declared:** `call(effect: CallModel) -> ModelCompleted | ModelFailed`. One method.
**Implemented by:** `OpenAIModelProvider`, `GeminiModelProvider`, `CLIModelProvider`,
`OMPModelProvider` (`arity/handlers.py`); `AntigravityWireProvider`, `CodexWireProvider`,
`GrokWireProvider`, `FallbackModelProvider` (`arity/wire.py`).

### Male join — **does not hold**

A provider that implements the Protocol exactly and nothing more runs **unconfined** and is
**misattributed in the evidence record**. Three undeclared attributes are load-bearing:

- **`cwd`** — `arity/terrarium.py:557-559` walks `(model_provider, model_provider.fallback)`
  and sets `.cwd` on anything that has it. This is the only thing that keeps a CLI harness
  inside the candidate sandbox. A conforming third-party provider has no `cwd`, gets no
  confinement, and acts in the coordinator's own working directory — the Arity repo. The code
  already says so: `TODO(kernel): a CLI's own tools still bypass the role's denial set;
  sandboxing by cwd is containment, not enforcement.`
- **`fallback` / `fallback_count`** — `arity/terrarium.py:618-621` reads these to append
  `->cli` to the recorded harness name when a wire fell back mid-run. A third-party provider
  that internally reroutes to a different model or harness reports `fallbacks: 0`, and the
  trial record attributes the result to the harness the signature *claims*. That is not a
  plumbing gap; it is an evidence-integrity failure, in the subsystem whose entire purpose is
  attributable evidence.
- **`primary`** — `arity/terrarium.py:551` distinguishes "a bare CLI" from "a wire with a
  fallback" by `hasattr(model_provider, "primary")`, to decide whether to file the trial under
  `cli:<harness>` or under `wire`.

Also undeclared and read elsewhere: `harness`, `model`, `account_key`
(`arity/wire.py:450-459`, `arity/terrarium.py:552`).

This is `A12-3` in the backlog: a `HarnessAware` protocol. Until it exists, the honest
statement is that Arity has *two* model seams — the declared one, and an undeclared one that
decides confinement and attribution.

### Female joins — pluggability

One method to implement, and it must return the frozen dataclasses from `arity/types.py`,
not duck-typed equivalents (`Runtime` re-queues the return value as an `Event` and
`transition` dispatches on `isinstance`). `runtime_checkable` means `isinstance(x,
ModelProvider)` only checks that a `call` attribute exists — it will accept a class object as
readily as an instance.

Sockets that exist: `Runtime(model_provider=...)` (`arity/runtime.py:46`),
`CandidateSpec.custom_model_provider` and `CandidateSpec.harness`
(`arity/terrarium.py:160,166`), `TerrariumDispatcher(model_factory=...)`
(`arity/terrarium.py:383`), `ArityOrchestrator(model_factory=...)`
(`arity/orchestrator.py:71`).

Socket that is missing: the orchestrator's direct-chat path reaches into
`self.terrarium._model_factory` (`arity/orchestrator.py:184`) — a private attribute of
another object — rather than holding its own.

### Boundary of intent

Almost everything currently in this seam is commodity we happen to own. `wire.py` and
`auth.py` together are ~83 KB of OAuth device flow, token refresh, request framing, and
per-provider payload translation. None of it is an opinion.

The opinion Axiom 3 names — **quota-reset-aware seat selection** ($C_{\text{eff}} \to 0$
before an expiring seat's deadline) and **the closed loop** (discover models from papers and
sentiment, A/B them on real tasks, retrain the policy with no operator) — is not in this seam.
`FallbackModelProvider` rotates seats on a 429, which is reactive error handling, not deadline
weighting. `CastingComposer` implements evidence-based casting and the shipped front door
never calls it (`A3-1`, `A3-3`).

**Honest reading:** this seam is where our opinion is *supposed* to live, and today it holds
none of it. Everything in it is rentable.

- **[SEAM]** LiteLLM (load balancing, circuit breakers), OpenRouter Auto, RouteLLM, Not
  Diamond, vLLM Semantic Router, CLIProxyAPI (seat pooling).
- **[SEAM]** OAuth device-flow + token refresh (`arity/auth.py`): any maintained OAuth
  client library.
- **[SEAM]** Per-provider payload translation (`arity/gemini_format.py`): LiteLLM's
  translation layer covers this.

---

## 2. `ToolRunner`

**Declared:** `execute(effect: ExecuteTool) -> ToolCompleted` and
`get_schemas() -> list[dict]`.
**Implemented by:** `LocalToolRunner` (`arity/handlers.py:400`), `SandboxToolRunner`
(`arity/tools.py:44`), `McpToolAdapter` (`arity/tools.py:563`).

### Male join — **does not hold**

Two independent defects, both verified.

**A third-party `ToolRunner` class is never instantiated.** `CandidateSpec.tool_runner_type`
explicitly accepts `type[ToolRunner]` (`arity/terrarium.py:161`). The dispatch chain checks
`isinstance(spec.tool_runner_type, ToolRunner)` *before* the `callable(...)` branch
(`arity/terrarium.py:510` vs `:512`). Because `ToolRunner` is `runtime_checkable`, that
`isinstance` only asks whether the object has `execute` and `get_schemas` — and a *class*
object has both. So a custom class takes the instance branch, is assigned as-is, and the first
call fails:

```
>>> isinstance(MyRunner, ToolRunner)      # the class, not an instance
True
>>> MyRunner.get_schemas()
TypeError: MyRunner.get_schemas() missing 1 required positional argument: 'self'
```

The two built-in classes escape this only because they are special-cased by identity on lines
504-508. Nothing else can be passed as a class.

**The kernel's final answer travels on an undeclared string prefix.** When a kernel ends its
turn by calling `message(to="user")`, `SandboxToolRunner` returns the literal
`"[Delivered to Asa]: <text>"` (`arity/tools.py:424`), and the dispatcher recovers the
candidate's output by scanning tool messages for that prefix and splitting on `"]: "`
(`arity/terrarium.py:648-653`). The comment there notes Claude in particular delivers its
answer this way and then stops with empty content. An alien `ToolRunner` that satisfies the
Protocol has no way to know this string exists, so a Claude candidate running on it records
**no output** and loses its trial.

**Also:** denial-set enforcement is a property of one implementation, not of the seam.
`SandboxToolRunner` checks role, path, host, and command (`arity/tools.py:96-139`).
`LocalToolRunner` checks nothing — `(self.workspace_root / path).resolve()`
(`arity/handlers.py:460,487`) escapes on `../` or an absolute path, and `run_command` is
`shell=True` with no filter (`arity/handlers.py:506`). The MCP arm checks
`role.can_use_tool` only. This is backlog `A2-2`; it is repeated here because it means "swap
the tool runner" also silently swaps the security model.

### Female joins — pluggability

Two methods, plus the undeclared prefix contract above. Schema format is OpenAI
function-calling JSON (`{"type": "function", "function": {...}}`), which `GeminiModelProvider`
translates on the way out. `McpToolAdapter(mcp_client_callable=...)` is a real socket for an
external tool executor and is the best-shaped one in the repo — it just has nothing real
plugged into it.

**`create_mcp_tool_runner` speaks no MCP.** It is an in-process Python closure over local
primitives that appends `"via MCP"` to its output strings (`arity/tools.py:618-726`). No
JSON-RPC, no stdio, no server. Trials attributing wins to an `mcp_tools` arm are attributing
them to local Python. This is backlog `A2-4`.

### Boundary of intent

The opinion is not the sandbox — it is that **the tool runner is a comparable trial axis**:
`normalize_tool_runner` gives it a canonical name that enters the trial signature
(`arity/terrarium.py:103`, `:203`), so "did MCP or native tools do better on this task for
this model" is a question with a recorded answer. Nobody else is asking it that way.

Everything under it is commodity, and ours is weak: `cwd` is containment, not isolation; there
is no container, VM, or user boundary anywhere in the repo (verified: no Docker, Firecracker,
or podman reference in `arity/`). The README's claim of "Docker/WSL sandboxes"
(`README.md:53`) describes an intention, not code.

- **[SEAM]** MCP: the official SDK, as a real JSON-RPC client over stdio/Streamable HTTP.
- **[SEAM]** Isolation: Firecracker microVMs, Docker/WSL, or a plain unprivileged OS user that
  cannot see the repo.
- **[SEAM]** Computer use: Anthropic Computer Use API, UI-TARS, `uiautomation-rs`.
- **[SEAM]** Code-editing primitives: Pi/omp (`hashline` anchors, Tree-sitter AST mutations,
  ConPTY supervision).

---

## 3. `RecordStore` / `RecordReader`

**Declared:** `RecordReader.query(kind, **filters) -> list[dict]`;
`RecordStore` adds `append(effect: StoreRecord) -> None`.
**Implemented by:** `JsonlRecordStore` (`arity/handlers.py:550`), `SqliteRecordStore`
(`arity/stores/sqlite.py:11`), and the query-only `JsonlRecordReader` /
`SqliteRecordReader` (`arity/record_readers.py`).

### Male join — **holds with a leak**

The split into a query-only reader with its own trust boundary is good and deliberate — the
reader refuses to turn malformed persistence into a shorter, apparently-valid log. Three
leaks:

- **The two halves disagree on what a filter means.** `JsonlRecordStore.query` compares with
  Python `==` (`arity/handlers.py:581`), so `1` matches `True`. `JsonlRecordReader` uses
  `_strict_equal`, which compares types first (`arity/record_readers.py:102-124`). Same
  Protocol, two semantics, and nothing tells a third-party store which one the journal
  requires. Latent rather than live today, because `TrialJournal` filters only on a string
  `trial_id`.
- **Journal locking probes undeclared attributes.** `_journal_lock`
  (`arity/trial_events.py:47-57`) keys the shared in-process lock off `store.path` or
  `store.root` — neither declared by the Protocol — and falls back to `object:{id(store)}`.
  A conforming third-party store gets the fallback, so two journals wrapping two handles on
  the *same* backing store do not share a lock, and sequence allocation races.
- **Write failures are silent.** `Runtime.step` wraps `self.store.append(effect)` in
  `except Exception: pass` (`arity/runtime.py:86-90`). A third-party store that fails to
  persist loses records and generates no friction record. This is the bad direction of "the
  rest doesn't notice." Same for `Transport.emit` (`:93-97`). Backlog `A12-2`.

Minor: `hasattr(self.store, "query")` (`arity/terrarium.py:644`) and
`hasattr(self.store, "append")` (`arity/transports.py:56,90`) guard methods the Protocol
already requires — harmless, but they signal that callers do not trust the seam.

### Female joins — pluggability

Two methods, both taking frozen dataclasses. Records must round-trip through JSON. `kind` is a
free string; the kinds actually in use are `message`, `model_turn`, `tool_result`, `friction`,
`pulse`, `terrarium_trial`, `redphone_message`, `trial_event`, `scorecard`. That set is not
declared anywhere — a store that wanted to shard or index by kind would have to discover it by
reading the code.

`ARITY_STORE` / `ARITY_STORE` selects `jsonl` or `sqlite` and nothing else. There is no way
to name a third backend from configuration.

### Boundary of intent

The storage engine is commodity and should be. The opinion is one layer up, in what gets
stored: the **frozen evidence bundle** with per-artifact content hashes
(`arity/evidence.py:147-485`), and a **`Resolution` that is attributable to a named
evaluator panel** and records `expected_evaluator_ids` alongside the ones that actually
reported (`arity/evidence.py:614`). That refusal to quietly resolve on a partial panel is
genuinely ours and is not what memory frameworks are built for.

Memory in the Letta/Zep sense — tiered working/recall/archival, temporal knowledge graphs,
vector recall — does not exist here at all, and when it is wanted it should be rented.

- **[SEAM]** Memory: Letta/MemGPT (3-tier), Zep/Graphiti (temporal graph, `valid_from`/
  `valid_to`), GraphRAG, LanceDB.
- **[SEAM]** Event store with cross-process sequence allocation (see `TrialJournal` below):
  Postgres, EventStoreDB, Temporal.

---

## 4. `Transport`

**Declared:** `emit(effect: EmitMessage) -> None`. One method.
**Implemented by:** `ConsoleTransport` (`arity/handlers.py:592`), `WebhookTransport`
(`arity/transports.py:97`), `_NullTransport` (`arity/terrarium.py:47`).

### Male join — **half a seam**

Replacing egress is easy and nothing notices, which is the correct answer to question 1. But
the seam is only half there.

Axiom 13's own join description is: *"Transports ingest raw wire protocols and emit
`Event::UserMessage` / `Event::Interrupt`; receive `Effect::EmitMessage`."* Arity implements
the receive half only. **There is no ingress seam.** `RedphoneInbox`
(`arity/transports.py:29`) is a concrete class holding an in-process dict, is not a Protocol,
implements nothing in `seams.py`, and is called directly by the orchestrator
(`arity/orchestrator.py:113`). Input arrives by calling
`ArityOrchestrator.handle_message(str)` from Python. Nothing can inject an `Interrupt` at all —
the event type exists and the reducer handles it (`arity/transition.py:291`), but no seam
can produce one.

`WebhookTransport`'s default callback is `pass` (`arity/transports.py:108`). The module
docstring claims "SMS, voice, webhooks"; the code is a list and a no-op. Backlog `A6-1`,
`A10-2`.

### Female joins — pluggability

One method for egress. For ingress: nothing to implement, because there is nothing to
implement against.

If ingress is built, the shape is already implied by the types — a transport should hand the
runtime an `Event` (`UserMessage` or `Interrupt`) and receive `EmitMessage`. That is a
straightforward protocol to write and it does not exist.

### Boundary of intent

**This is commodity, end to end, and we should rent all of it.** Carrier wire, WebRTC,
VAD/barge-in, Discord rate-limit buckets, iMessage — none of it contains an Arity opinion. The
only opinion in the neighborhood is Axiom 10's *public address* (a red phone anyone can post
to, triaged by bots, escalated by email), and that is a product decision about routing and
triage, not about transport.

- **[SEAM]** Voice/telephony: LiveKit Agents, Pipecat, Cartesia streaming, Asterisk
  AudioSocket.
- **[SEAM]** Messaging: Sendblue (iMessage), Twilio, Matrix SDK, Discord (Serenity/Hikari).
- **[SEAM]** Desktop: Tauri v2 daemon.

---

## 5. `Observer`

**Declared:** `on_event(state, event)` and `on_effect(state, effect)`, both returning `None`.
**Implemented by:** `MetricsObserver` (`arity/handlers.py:608`). That is the only
implementation in the repo.

### Male join — **holds, but carries no weight**

Nothing depends on an observer, so anything can replace it and nothing notices — technically a
pass, practically a sign the seam is not doing work. Two things to be honest about:

- **Every callback is swallowed.** Both loops wrap the call in `except Exception: pass`
  (`arity/runtime.py:64-68`, `:74-79`). A third-party observer that throws on every event is
  indistinguishable from one that works. Telemetry that can fail silently is telemetry you
  cannot trust to be complete.
- **The archivist does not use this seam.** `arity/archivist.py:17` imports `Observer` and
  never references it again. `ImpartialArchivist` audits by reading the candidate's workspace
  from disk and querying the record store (`arity/archivist.py:60-95`). Axiom 13's stack
  audit describes the Observer join as exactly this: *"passively sniffs events and effects on
  the wire, letting the impartial archivist audit claimed model changes against real tool logs
  without invading the agent loop."* That is the design. It is not the implementation.

Note also that a `SpawnHandoff` child runtime is constructed with the parent's observer list
(`arity/runtime.py:131`), so one `MetricsObserver` accumulates parent and child tokens into
one number with no way to separate them.

### Female joins — pluggability

Two methods. Observers see effects *before* execution but cannot veto or modify them — the
list returned by `transition` is dispatched regardless. That is a defensible design (the
observer is passive by intent) but it should be stated, because it rules out using this seam
for policy enforcement.

### Boundary of intent

`MetricsObserver` is a worse OpenTelemetry. Token counting, cache-hit ratio, and tool-success
ratio are standard GenAI telemetry with a published semantic convention.

The opinion is Axiom 9's **impartial archivist** — a second, non-self-interested account of
what a kernel did, cross-checked against artifacts, that is *also* the fallback when the kernel
dies without writing its own report. Nobody else builds that, because nobody else treats a
model's own account of its work as one of two accounts. But note `A9`: the "kernel self-report"
is currently an f-string the dispatcher writes about the kernel
(`arity/terrarium.py:641`), so the two-account structure has one real leg.

- **[SEAM]** OpenTelemetry GenAI semantic conventions, Langfuse, Arize Phoenix (tracing,
  token/cost accounting, Bradley-Terry eval judges).

---

## 6. `ContextAdapter`

**Declared:** `adapter_id: str` and `apply(envelope: ContextEnvelope) -> ContextEnvelope`
(`arity/terrarium.py:131`). Not in `seams.py`; not `runtime_checkable`.

### Male join — **holds**

This is the best-behaved seam in the repo, and the reason is that its contract is enforced at
both ends rather than assumed:

- `CandidateSpec.__post_init__` rejects an adapter without a non-empty `adapter_id`
  (`arity/terrarium.py:172`).
- The dispatcher type-checks the return value: `if not isinstance(adapted, ContextEnvelope):
  raise TypeError` (`arity/terrarium.py:591`).
- `ContextEnvelope` is a frozen dataclass with a tuple of messages, so an adapter cannot
  mutate the caller's state by accident.
- `adapter_id` enters the trial signature as `ctx_adapter=<id>`
  (`arity/terrarium.py:208`), so an adapter's effect on outcomes is recorded and comparable.

An external compaction strategy can be dropped in and the rest genuinely does not notice.

### Female joins — pluggability

One attribute, one method, one frozen input type, one frozen output type. This is the concrete
answer to give anyone asking what an Arity plug looks like.

Gaps, both small: it is named in the `seams.py` docstring catalog but not defined or
re-exported there, so `from arity.seams import ContextAdapter` still fails (backlog
`A12-4`); it is not `runtime_checkable`, so it cannot be validated by `isinstance` the way the
others can; and like everything else it must be a Python object — there is no way to name an
adapter from the CLI or config.

### Boundary of intent

The opinion is not any particular compaction algorithm — it is that **compaction and memory
policy are a trial axis, not a setting**. `context` (`fresh` / `accounts` / `fork`) and
`context_adapter` both land in the signature, which makes "did this compaction strategy help
this model on this task" an empirical question with a stored answer. That framing is ours.

Any specific adapter — summarize-the-middle, recursive summary, tiered recall, vector
retrieval — is commodity and should be borrowed.

- **[SEAM]** Letta/MemGPT tiering, LangChain/LlamaIndex compaction chains, provider-native
  context editing.

---

## 7. `TrialEvaluator`

**Declared:** `evaluator_id: str` and `evaluate(bundle: EvidenceBundle) -> Evaluation`
(`arity/evidence.py:587`). Not in `seams.py`.

### Male join — **holds in-process**

`evaluate_bundle` (`arity/evidence.py:596-603`) enforces the contract properly: the return
must be an `Evaluation`, it must validate against the bundle it claims to judge, and its
`evaluator_id` must match the evaluator that produced it. An evaluator that lies about its own
identity is rejected rather than recorded.

Failures are handled honestly rather than swallowed: an evaluator that raises is recorded as a
`review.recorded` event with `status: failed` and the exception text
(`arity/race.py:853-863`), and a declared judge that never reported is recorded as
`status: missing` (`arity/race.py:876-886`). This is the one place in the repo where a seam
failing is treated as evidence instead of noise.

### Female joins — **no socket outside Python**

`RaceConfig.evaluators` (`arity/race.py:88`) is reachable only from the Python API —
`run_front_door(evaluators=[...])` (`arity/race.py:1432`). The CLI never constructs one;
`arity/inspection_cli.py` reads `evaluator_id` only to display it. So the only evaluators
that can run from the shipped front door are the built-in LLM judges named by `--judges`.

An external eval service — the obvious thing to plug in here — cannot be plugged in without
writing Python.

### Boundary of intent

LLM-as-judge is commodity and the ecosystem is ahead of us. The opinion is the surrounding
discipline: an evaluation is **bound to one content-hashed evidence bundle**, carries its
evaluator's identity, and a resolution names both the panel it *expected* and the panel that
*reported*, so a partial panel produces an unresolved trial instead of a quiet winner
(`arity/evidence.py:713`, `arity/race.py:890`). That is refusal-to-attribute as a
first-class behavior, and it is worth keeping when the judging itself is rented.

- **[SEAM]** Langfuse / Phoenix judges, promptfoo, DeepEval, Inspect.

---

## 8. `TrialJournal`

**Declared:** a concrete class, not a Protocol (`arity/trial_events.py:455`). It *consumes*
the `RecordStore` seam and exposes `append(event_type, payload, idempotency_key=...)` and
`replay()`.

### Male join — **holds for one process; does not hold across processes**

The class documents its own limit: *"Journal instances sharing a local store path coordinate
sequence allocation in-process. Cross-process orchestration requires a RecordStore with
transactional sequence allocation."* That is exactly right and exactly the constraint.

Sequence allocation is guarded by a module-level dict of `threading.RLock`s keyed by
`(store_identity, trial_id)` (`arity/trial_events.py:35-57`), where store identity is
`store.path` or `store.root` — undeclared attributes, as noted under `RecordStore`. Two
coordinator processes writing the same trial will allocate the same sequence number, and
replay will then refuse the log: `_coalesce_events` raises on conflicting events at one
sequence and on gaps (`arity/trial_events.py:527-552`). Failing loudly at read time is the
right choice; it does not make the write path safe.

Replay itself is strict in a way worth preserving: exactly one `trial.started` at sequence 1,
no gaps, unique arm ids and ordinals, unique idempotency keys (`arity/trial_events.py:555+`).

### Female joins — pluggability

The journal is not itself pluggable — it is a concrete class with a hardcoded event
vocabulary (`trial.started`, `arm.completed`, `evidence.frozen`, `review.recorded`,
`resolution.recorded`, `delivery.completed`; `arity/trial_events.py:26-33`). What it needs
*from* a plug is one thing the `RecordStore` seam does not offer: **atomic sequence
allocation**. If that were declared — an optional `allocate_sequence(kind, key) -> int`, or a
compare-and-set append — a Postgres or EventStoreDB backend would make cross-process
orchestration work with no other change. `SqliteRecordStore` already has the unique index that
would support it (`arity/stores/sqlite.py:36-41`).

### Boundary of intent

Durable event logs, checkpointers, and deterministic replay are commodity, and mature.

The opinion is **what** is on the log: a trial reconstructible from events alone, without
consulting workspaces, providers, or current policy — so that a past decision can be re-read
under the rules that were in force when it was made, and a tampered or truncated log is
refused rather than summarized. Given that Arity exists to produce evidence about models, an
append-only record that will not quietly reinterpret itself is close to the whole product.

- **[SEAM]** LangGraph checkpointers, XState persisted snapshots, Temporal, EventStoreDB,
  Postgres.

---

## Seams the axioms imply that do not exist

- **Ingress.** See `Transport` above. Nothing can deliver a `UserMessage` or an `Interrupt`
  into a running session from outside Python.
- **Clock / scheduler.** `SchedulePulse` is in the `Effect` union and imported by the reducer
  (`arity/types.py:149`, `arity/transition.py:27`), but nothing emits it and `Runtime.step`
  has no branch that executes it (`arity/runtime.py:84-147`) — it would be silently dropped.
  `PulseEngine` is an advisory object the orchestrator polls (`arity/orchestrator.py:204`).
  Axiom 11 needs a clock that outlives a process; that is a seam, and it is unstarted.
  Backlog `A11-1`, `A11-2`.
  **[SEAM]** systemd timers / cron, APScheduler, Temporal.
- **Harness awareness.** The undeclared second model seam described in section 1. Backlog
  `A12-3`.

## What to correct first

If only one line in this document gets red-penned, it should be the verdicts. In rough order
of how much a wrong answer costs:

1. `ModelProvider` — the `cwd` / `fallback_count` leak is the one that puts *false evidence*
   in the record rather than merely breaking a plug.
2. `ToolRunner` — the `isinstance`-on-a-class defect is a two-line fix (reorder the branches,
   or test `isinstance(x, type)` first) and it is the difference between "third parties can
   plug in" being true and being a claim.
3. `Transport` — decide whether ingress is in 1.0.0. If not, the docstrings that claim SMS and
   voice should be scoped down now.
4. Everything else can wait for the post-1.0.0 [SEAM] research.
