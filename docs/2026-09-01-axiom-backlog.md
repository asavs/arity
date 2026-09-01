# Axiom backlog — what stands between here and the vision

**Date:** 2026-09-01 (revised same day, after the casting conversation and the first agent fleet)
**Companion to:** [the fleet-development position](2026-09-01-agent-fleet-development.md),
[the quiet-failure inventory](2026-09-01-quiet-failures.md), [the seam register](seam-register.md),
the 2026-08-31 codebase audit, and `.wiki/axioms.md` (which lives in the sibling
`Projects/arity` repo, not this one).

## How to read this

`TODO.md` is the near-term roadmap, organized by subsystem. This is the same territory cut a
different way: by axiom, longest horizon first. Where an item already exists in `TODO.md` it
is referenced, not restated.

- **[DECIDE]** — a question only Asa can answer. An agent answering it autonomously would be
  deciding what Arity is.
- **[BUILD]** — the answer is known; someone just has to write the naive version.
- **[SEAM]** — a join where existing software may do the job better than ours. Marked now,
  researched after 1.0.0. Do not research them yet.
- **[DONE]** — landed, with the date.

---

## Decided: the casting design

Settled in conversation 2026-09-01. Recorded here because most of A2 and A3 now depend on it.

**One engine, two questions.** Casting had grown two implementations because it was answering
two questions with different domains, not because anyone duplicated code:

- **Question A — "who is good at this?"** Ranges over *models*. Inferred from evidence,
  carries uncertainty, needs exploration to stay fresh.
- **Question B — "whose tokens should I spend?"** Ranges over *seats* — provider, account,
  quota window, warm cache, presence lock. Measured, not inferred. Arithmetic over observed
  state.

One model is reachable through several seats; one seat serves several models. They are not
interchangeable rankings of the same thing.

**Composition rule: B filters, A orders.** Seats that cannot pay are removed. Among what
survives, aptitude orders. The two are never summed — summing them requires an exchange rate
between a ten-point standing and dollars-per-million, and today's
`standing - cost*2.0 + urgency` sets that rate by accident (a seat with under an hour of quota
left gets +3.0, worth three consecutive successful trials). B may veto A; A may never veto B.

**Modes select which question orders, not how much each counts.** No weight vectors, no
coefficients to tune:

- **brokie** — economics orders; spend what is about to evaporate. Aptitude breaks ties.
- **smart** — economics filters; aptitude orders. Default.
- **chaos** — economics filters; seeded random order.

**Smart mode always carries an exploration slot.** N−1 seats by aptitude, one by
least-evidence. Not an option — without it the top seats keep winning because they are the
only ones asked, and everyone else's standing freezes at a stale value. A ranking can be
perfectly calibrated on evidence it has stopped collecting.

*Corrected during implementation:* the slot engages only at N ≥ 2. Taken literally, "N−1 by
aptitude" makes a single-candidate cast *always* exploration — which would send the
orchestrator's direct-chat turn and every peer consultation (both N=1) to the least-tried
model every time, and smart mode would never exploit at all.

**Chaos mode is not a toy.** It is what makes credit assignment possible. One verdict is
written to five keys at once (role:model, signature, harness, tools, skill), so if a model is
always paired with the same harness, "the model is good" and "the harness is good" are
literally the same number recorded twice. Randomized assignment is the only thing that
de-confounds the marginals. It is also the only unbiased evidence in the system: smart mode
can only ever observe the candidates it already believed in.

**Diversity belongs to the trial, not to casting.** Degeneracy is relative to the axis under
test — three seats all running one model is a ruined model comparison and a correct harness
comparison. Casting cannot know which trial it is serving. The caller asks for "N candidates,
distinct on model / provider / harness", and casting serves that request.

*Sharpened during implementation:* `distinct_on` can only cover **model, provider, harness** —
the three dimensions a `Seat` actually carries. Tools are not castable: `tool_runner_type`
lives on `CandidateSpec` and is assigned *after* a seat is chosen. So for the tools axis,
casting cannot serve a distinctness request even in principle, and the trial layer owns it
outright. That is a stronger version of the same conclusion, arrived at from the data model
rather than from the argument.

**Casting reports requested versus satisfied.** It does not silently pad. Today `cast()` picks
the best seat per distinct provider and then fills any remaining slots from the ranked list
regardless of provider, abandoning the diversity constraint without recording that it did.
The README already promises the honest version: "a trial can resolve fewer seats than
requested."

**A seed per trial, recorded.** Casting is currently the only step in the pipeline that cannot
be replayed. A recorded seed fixes that. Note it reproduces the *decision procedure*, not the
outcome, unless the available seat set is also recorded — `trial.started` may already carry
this.

**Not needed: recording the casting mode for comparability.** Considered and rejected.
Grading is absolute, not peer-relative: `record_verdict` has exactly one caller
(`archivist.py:161`), which audits a single candidate against hidden tests and its own claims.
The judge path writes judgement records to the journal and never touches standings, and
`score_override` is passed by nobody. So standings are comparable between models that never
met, `--arity 1` runs still produce usable evidence, and no tournament design is needed. The
calibration risk lives elsewhere — see the evidence model below.

---

## The evidence model

Where the composite score has to end up: indexable by provider, model, effort, harness,
system prompt, tools, skills, MCPs, CLIs — so that over time it is possible to say what a
model is actually good at.

- **E-1 [DONE 2026-09-01]** Standings carry an observation count. `get_observations()` mirrors
  `get_standing()`; `least_observed(keys)` picks the exploration target with ties broken on
  sorted key order. Counts increment in `_apply_delta`, the one helper both the live path and
  replay share, so a reloaded scorecard reproduces counts as well as values. A legacy record
  with no `score_delta` counts once for `role:model` and invents no derived observations —
  otherwise a cell would read 13.5 at n=0, which `least_observed` would act on.
- **E-1 (original statement)** Standings must carry an observation count, not just a value.
  `Scorecard._standings` is `dict[str, float]` today, so a cell holding 11.0 from one run and
  one holding 10.8 from fifty are indistinguishable. `(value, n)` is a small storage change
  that unlocks three things at once: confidence-aware casting, "explore the least-observed
  cell" as a well-defined instruction, and noticing when a task has stopped discriminating.
  Replay already reconstructs standings from deltas, so the count comes free.
- **E-2 [DONE 2026-09-01 — decided]** Standings are **pairwise**: model × one factor, never the
  full cross-product. The schema already works this way — `role:model`,
  `harness:<h>:<model>`, `tools:<t>:<model>`, `skill:<s>:<model>` are all pairs. The full
  `signature` is the only high-order key, and it is the only one that never accumulates
  evidence; its real job is identifying a trial arm in the record, not serving queries.
  Eight models against twenty tags is 160 cells, not 864, every trial writes several pairs at
  once, and a thin cell falls back to the model's coarse standing until it earns its own.
  Queries are shaped "is this model good at X?" and "does swapping one factor make a
  difference?", never "score this exact five-part combination."
- **E-2b [DECIDE]** The fallback threshold: how many observations must a fine cell hold before
  it outranks the coarse one? Needs E-1's counts, which now exist.
- **E-5 [BUILD]** The tag axis. Task briefs already declare `tags:` — `rate_limiter` carries
  `[python, concurrency, algorithms]`, `lru_cache` carries `[python, data-structures,
  performance]` — and nothing consumes them. Two pieces: add `tag:<t>:<model>` to the derived
  key family so the pair is recorded, and give tags a typed, enforced vocabulary under
  `definitions/tags/`, validated at task-load time, where an unknown tag is an error rather
  than a silent new category. Free-text tags rot into synonyms exactly when nobody is looking
  — `data-structures` and `algorithms` already overlap across two of the four tasks. Tags are
  the agreed finest grain: finer means routing on individual tasks, which cannot generalize
  to a task not yet written.
- **E-6 [DECIDE]** External tool identity — MCP servers and CLIs. The vision names both as
  trial axes, and neither is addressable today: a CLI is buried inside a harness string, an
  MCP inside a tool-runner alias (an alias that is not MCP at all, see A2-4). So the motivating
  question — *is this model good with the official Roblox CLI, and is there a meaningful
  difference when it uses the unofficial one?* — cannot even be **formed** against the current
  schema, let alone answered. Decide whether these are two dimensions or one "external tool"
  dimension carrying a kind. **Gated on A3-4**: adding a signature dimension invalidates
  comparability with every record written before it, so the versioning question must be
  answered first. This is the highest-value schema decision outstanding.
- **E-7 [DECIDE]** Split standings from analysis. Two different jobs have been conflated.
  *Standings* are a small, fast, incrementally-maintained pairwise cache whose only job is to
  make a casting decision quickly. *"What is this model good at"* and *"is that difference
  meaningful"* are analysis queries over the full record log, computed on demand — every
  scorecard record already carries model, role, task_id, verdict, score_delta, harness,
  tool_runner, skills, and a timestamp, and `arity standings` is an embryonic version of this.
  Accepting the split removes all pressure to precompute anything: arbitrary groupings,
  including ones nobody has thought of yet, are derived when asked. Precomputation is for the
  hot path only.
- **E-3 [DECIDE]** A task typology. "What models are good at what" needs the *what* to
  generalize. Today `task_id` is recorded but standings are not keyed by task kind, so the
  system can learn "this model is good" globally and never "good at SQL, weak at
  concurrency." With four tasks in the bank and no notion of kind, nothing transfers to a
  fifth. This is the axis with no code at all and the one that makes the composite useful.
- **E-4 [BUILD]** Task discriminating power. Because grading is absolute, a task with weak
  hidden tests inflates everyone who touches it, invisibly — there is no peer to reveal it.
  If every candidate passes `lru_cache`, that task has stopped being evidence and is just
  adding +1.0 to the whole field. Track pass rate per task and surface tasks that no longer
  separate anyone. This is the real calibration surface, not casting.

---

## The version-control governor

Proposed 2026-09-01: a staff member whose aptitude is version control, holding the repo on
behalf of every other model while they work.

**The problem it solves is timing, not partitioning.** On 2026-09-01 seven agents wrote into
one working tree across three waves. By the time anyone looked, four files carried changes
from two or three waves interleaved inside the same diff hunks, and the commit structure had
to be *reconstructed archaeologically* rather than recorded as it happened —
`scorecard._load_from_store` and `cli.py` could not be split by theme at all without
hand-editing hunks and risking an intermediate commit that does not import. The branch that
resulted is chunked by subsystem instead of by unit of work, and no intermediate commit is
test-verified. A governor committing each territory *at the moment that piece of work is
coherent* never loses the structure, because it never batches it.

**It also closes an open archivist item.** `TODO.md` wants the closing report structured
(`files: [...]`) because discrepancy detection is currently regex over prose. A diff is that
structure, and it is far better evidence of what a kernel did than parsing its prose for
filenames. The governor produces it as a byproduct of its actual job — Axiom 9's "best
evidence for what it *did*", for free.

**It gives denial sets their first job with teeth.** Roles declare denials today and
enforcement is patchy across tool-runner arms (A2-1, A2-2). Here the denial is the design:
every worker role has git denied, exactly one role has it granted.

**A9 forces it to be a separate role from the archivist.** The governor *acts* — it writes to
the repository — so it is a participant, and Axiom 9's whole point is that the impartial
account is not written by the actor. They produce related records and must stay distinct.

- **VC-1 [DECIDE]** Shared tree with enforced territories, or one worktree per agent plus a
  merge step? Territories are what today's fleets already use by hand, and they are naive and
  shippable; worktrees are more isolated but hand the governor a real merge policy. Arbitration
  — deciding merge order and resolving conflicts — is the genuinely hard part and it is a
  scheduling and authority question, not a version-control one.
- **VC-2 [DECIDE]** Does the governor gate on tests? A commit that breaks the suite is either
  refused (the governor becomes a bottleneck, and a serialization point for a parallel fleet)
  or committed and flagged (history contains known-broken states). Bisectability is the prize
  on one side; throughput on the other.
- **VC-3 [DECIDE]** Two accounts in the commit message. A governor that writes the message
  alone is describing work it did not do — the same defect as A9-3, where the dispatcher
  synthesizes a report the kernel never gave. The honest shape carries both: the agent's own
  statement of what it meant, and the governor's record of what changed, separately marked.
  Decide the format before the first one is written, because commit messages are not
  rewritable in practice.
- **VC-4 [DECIDE]** Granularity and naming: a branch per trial, per candidate, or per task?
  This determines whether a trial's arms can be diffed against each other directly, which
  would be a genuinely new evidence source — today candidate workspaces are compared by
  content hash, not by history.
- **VC-5 [BUILD]** The governor needs its own denial set, and it is the strictest in the
  system: no force-push, no history rewriting, no rebasing anything shared. A role that can
  commit is a role that can destroy the record every other axiom depends on.
- **VC-6 [SEAM]** Git plumbing (subprocess, GitPython, dulwich) and forge mechanics (`gh`,
  the GitHub API) are commodity — rent them. The owned opinion is narrower: commit at
  coherence per territory, and the two-account message.

Worth noting what this buys beyond tidiness: a fleet whose work is bisectable is partially
insured against the risk M-1 exists to address. If a decomposition introduces a subtle defect,
bisect finds it without a characterization harness having predicted it.

---

## A0 — The why

Not a work item. It is the acceptance test for every item below: *does this reduce the number
of things Asa has to hold in his head?*

## A1 — One voice, a staff, and a door to each

**Status:** The voice works. The doors do not exist. `arity chat` is hardwired to the
Secretary (`cli.py`), with no way to address a named role. `message(to="peer:X")` exists
inside a race but has never been exercised in a live run; the orchestrator's copy of that
router was broken by a keyword typo until 2026-09-01.

- **A1-1 [DECIDE]** What *is* a door? A CLI flag (`arity chat --role scout`), a transport
  address that routes by prefix, or an in-conversation handoff the voice performs on request?
- **A1-2 [BUILD]** Once decided, the naive version: bypass the resolver and cast the named
  role directly.

## A2 — The staff is organized by aptitude

**Status:** Half real. Only the sandbox arm enforces denial sets — the shell arm resolves
paths with no boundary guard, so `../` and absolute paths both escape. "Aptitude" as data
does not exist: `composer.APTITUDE_MATRIX` is passed to the constructor and then never read
by `cast()`.

- **A2-1 [DECIDE]** Which per-arm differences are deliberate experiment properties and which
  are drift? The audit's read: the AST write-check *is* the named axis and stays; the missing
  path and host checks are a hole, not an axis.
- **A2-2 [BUILD]** One `enforce_denials(role, effect)` and one `resolve_sandbox_path()` across
  all three tool runners. Changes what shell-arm candidates can do — see A3-4.
- **A2-3 [DECIDE]** What is aptitude, as data? A declared prior in the role document that the
  scorecard updates is the obvious candidate now that E-1/E-2 give standings a shape. Until
  answered, `APTITUDE_MATRIX` stays dead.
- **A2-4 [DECIDE]** The `mcp_tools` arm speaks no JSON-RPC — it is local primitives with "via
  MCP" appended. Rename to stop attributing wins to a protocol never in play, or make it
  real? Renaming breaks comparability with past signatures.

## A3 — Getting better means the casting gets better

**Status:** The design is now settled (see *Decided* above); the code is not. The shipped
front door still casts on quota alone. `CastingComposer` blends both questions into one
float and is called only from the orchestrator path.

- **A3-1 [DONE 2026-09-01]** Casting policy decided: one engine, B filters / A orders, three
  modes, seeded, exploration slot in smart mode.
- **A3-2 [DECIDE]** The magnitudes. A trial moves a standing ±1.0 from a 10.0 baseline and
  −2.5 for a discrepancy; those were chosen by feel. Nothing depends on them yet, which makes
  now the cheap moment. Revisit alongside E-1, since a count changes what a magnitude means.
- **A3-3 [BUILD]** Route the front door through the engine. Unblocked by A3-1. Deliberately
  sequenced *after* the engine lands so the behavior change to `arity run` is reviewable on
  its own.
- **A3-4 [DECIDE]** What invalidates a signature? Any change to arm semantics (A2-2, A2-4)
  silently severs comparability with every past trial. Do signatures carry a version, or does
  the record note the break?
- **A3-5 [SEAM]** The closed loop — read release papers and sentiment, discover candidate
  models, A/B them, retrain the policy with no operator. Does not exist. The wiki names it,
  with quota-reset-aware seat selection, as the part that is *yours*.

## A5 — Mine over rented

- **A5-1 [DECIDE]** State the 1.0.0 default in writing: at every seam, *mine and naive*,
  research deferred. The rule exists so it stops being re-argued; it is currently re-argued
  per seam because it was never written as a release policy.

## A6 — The front door is a phone

**Status:** Stub, now honestly labeled. `transports.py` docstrings describe what exists (an
in-process dict, an egress-only callback) with the axioms kept as stated intent.

- **A6-1 [DONE 2026-09-01]** Docstrings corrected.
- **A6-2 [DECIDE]** Is a real phone front door in 1.0.0?
- **A6-3 [SEAM]** Carrier and voice transport. Sendblue, Twilio, LiveKit, Pipecat, Asterisk.

## A7 — The voice is a role held by one model for a period

**Status:** The table is now real. `arity/cache_economics.py` holds one table sourced from
the wiki; `pulse.py` and the chat header read it, and seeded seats take their warm window
from it. The arithmetic changed substantially — Anthropic's cold-100k penalty went from
$0.345 to $0.90 — because the old private table was costing Claude 3.5 Sonnet and GPT-4o.

- **A7-1 [DONE 2026-09-01]** One table, one home, wiki-sourced. Penalties are derived from
  price and multiplier so the columns cannot drift.
- **A7-2 [DECIDE]** Who owns the kernel identity tuple — `provider + endpoint + model +
  workspace + session key + exact prefix` — and who must consult it before swapping a seat
  mid-kernel?
- **A7-3 [DONE 2026-09-01]** The sporadic-conversation rule is live as an optional input to
  question B's filter: `expected_idle_seconds`. A seat is dropped when
  `0 < warm_window_seconds < expected_idle_seconds`. No hint means no filter — nothing is
  guessed. One judgment call the wiki does not spell out: a provider assuring **no** warm
  window is *not* filtered, because it has no warm state to forfeit by going quiet, and
  excluding it would leave the sporadic case with nowhere to sit.
- **A7-4 [BUILD]** Measure real cache hit rate. Already in `TODO.md` under Races.

## A8 — Memory is tiered by distance from Asa

**Status:** Working, but tier is selected by matching role *names* against string lists while
`role.tier` and `TierLevel` sit unused — referenced only by `tests/test_roles_and_tiers.py`.

- **A8-1 [DECIDE]** Is tier a declared property of a role, or computed at the call site? Then
  wire `TierLevel`/`role.tier` or delete them.

## A9 — Two accounts of every kernel

**Status:** Half real, and the missing half is the cheap one. The archivist leg works and
feeds the scorecard. The "kernel self-report" is an f-string the *dispatcher* writes about the
kernel (`terrarium.py:641`) — the kernel is never asked. A consequence nobody intended: the
A9 fallback ("if a kernel dies without writing its report, the record says so") can never
fire, because a report is always synthesized.

- **A9-1 [DECIDE]** How do you ask a kernel for its report when the harness is a CLI you
  cannot cheaply resume? The answer differs per harness, which makes it a seam question.
- **A9-2 [BUILD]** For wire seats, the naive version: one extra turn, no tools — *what did you
  do, and what did you mean by it* — captured before the archivist writes.
- **A9-3 [BUILD]** Until A9-2 lands, rename the synthesized field so it stops claiming to be a
  self-report, and let the absent-report path actually be reachable.

## A10 — The red phone is a public address

**Status:** In-process list, now honestly labeled.

- **A10-1 [BUILD]** Non-interactive runs should leave a resolve disagreement in the inbox
  rather than silently keeping the archivist's order. Already in `TODO.md`.
- **A10-2 [DONE 2026-09-01]** Docstrings corrected.
- **A10-3 [DECIDE]** Is the public address in 1.0.0?
- **A10-4 [SEAM]** Escalation transport — email, Matrix, or a hosted inbox.

## A11 — The system has a pulse

**Status:** Orphaned, and the axiom is itself marked *proposed — strike if wrong*. The
economics now come from the real table, but nothing schedules a tick, `SchedulePulse` is an
effect nothing emits, and `Runtime` cannot execute it.

- **A11-1 [DECIDE]** In or out for 1.0.0. A pulse needs a clock that outlives a process —
  a whole new seam — and the axiom is still provisional.
- **A11-2 [BUILD]** If out: delete `pulse.py` and `SchedulePulse`, or mark them plainly
  unwired. If in: the scheduler seam comes first.

## A12 — An elegant core, and everything else plugs in

**Status:** The best work in the repo, undercut in three remaining places.

- **A12-1 [DONE 2026-09-01]** `transition()` is pure. Id generation is an injectable factory
  defaulting to the old expression; determinism is now under test.
- **A12-2 [DECIDE]** The quiet-failure policy, now with an inventory to decide against:
  **42** silent handlers across 16 files — 12 that can lose data, 16 that can hide a
  programming error, 14 benign. Two facts that shape the decision: `runtime.py:90` swallows
  the store append for *every* `StoreRecord`, so the scorecard bug fixed on 2026-09-01 was an
  instance of a systemic one; and the friction record is itself delivered through that same
  swallow, so the mechanism meant to report a dropped write is the one that drops it. There is
  no `logging` import anywhere in the package — the silence is literal.
- **A12-3 [BUILD]** A `HarnessAware` protocol, so sandbox confinement and harness attribution
  stop riding on `hasattr` probes for attributes `ModelProvider` never declares. The seam
  register found the sharper consequence: a conforming third-party provider runs unconfined
  *and* is misattributed in the trial record — false evidence, not just a broken plug.
- **A12-4 [DECIDE]** Where the seam catalog lives. `seams.py` now documents all eight and
  where the outliers sit. Moving `ContextAdapter`/`TrialJournal` in would cycle (both modules
  import `seams`); `evidence.py` imports nothing from the package, so `TrialEvaluator` could
  move. Decide whether type-locality or catalog-locality wins.

## A13 — The seam test

**Status:** A first register exists: [`docs/seam-register.md`](seam-register.md), 556 lines,
verdicts for all eight seams and 18 unresearched `[SEAM]` lines. It is harsher than expected —
`ModelProvider` and `ToolRunner` both come back "does not hold," and no out-of-process socket
exists at any seam, so the literal answer to "can an alien binary plug in" is currently *no,
everywhere*.

- **A13-1 [DONE 2026-09-01]** First draft written; needs Asa's corrections, not a rewrite.
- **A13-2 [DONE 2026-09-01]** Both defects fixed, each proven RED against the pre-fix code. The
  instance branch is now guarded with `not isinstance(..., type)` rather than reordered, so a
  built runner that happens to define `__call__` is still used rather than called. The delivery
  marker has one home, `tools.USER_DELIVERY_MARKER`, with its text frozen since it appears in
  candidate-visible output and past records. Open follow-up: the constant is importable from
  `arity.tools` but is not on the package surface or next to the `ToolRunner` Protocol an
  alien implementor would actually read.
- **A13-2 (original statement)** Two concrete defects the register found, both verified by execution:
  `isinstance(SomeRunnerClass, ToolRunner)` returns `True` because `runtime_checkable` only
  checks attribute presence on the class object, so a third-party runner *class* is assigned
  uninstantiated and dies on `TypeError`; and a kernel's final answer travels on the
  undeclared literal `"[Delivered to Asa]"`, which the dispatcher scans for — so a conforming
  alien `ToolRunner` records no output and silently loses the trial.
- **A13-3 [DONE 2026-09-01]** `README.md`'s seam list is now nine entries: `RecordReader` added
  in declaration order, and `TrialJournal` described as the concrete class it is.
- **A13-3 (original statement)** `README.md`'s seam list omits `RecordReader`, which is a real
  `@runtime_checkable` Protocol, is exported, and is the declared parameter type of the public
  `inspect_trial`/`inspect_trials`. It also lists `TrialJournal` among the Protocols; it is a
  concrete class that composes `RecordStore`, not an interface.

## "Axiom 36" — presence

**Status:** The mechanism works as of 2026-09-01 — locks persist to `~/.arity/presence.json`
with a 12-hour TTL and are re-applied after seeding. The number still does not exist.

- **X-1 [DECIDE]** Register presence in the wiki as a numbered axiom, or renumber the three
  citations (`composer.py`, `ledger.py`, `RELEASE.md`).

---

## The lab, applied to itself

- **M-1 [DECIDE]** Build a characterization harness before decomposing the megafunctions, or
  lean on the existing suite? (Position §2 and §8.)
- **M-2 [DECIDE]** Split the position document into hypotheses and taste. §2, §5 and §6 make
  checkable predictions; §4 and §7 are values claims no evidence moves.
- **M-3 [BUILD]** Once the harness can vary system prompt and effort as trial axes, run the
  hypotheses as trials: does a characterization harness reduce refactor defects (§2); do
  auditors primed for a defect class over-report it (§5); does higher effort produce diffs
  costing more to verify than they save (§6). Each needs a task with a known answer key.
- **M-4 [DECIDE]** Whether a model that helped build the harness should be a subject in its
  own trials, and what that does to the evidence. Answer before M-3.
