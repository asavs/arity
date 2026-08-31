# Observer TUI

Status: Stage 1 blind-safe view model and Stage 2 one-shot command implemented on
this branch; Stage 3 interactive TUI deferred.

`arity watch` is a read-only local view of what Arity's persisted trial journal can
prove. It is an observer, never a participant: opening it must not run an agent,
contact a provider, execute a tool, attach to a runtime, repair a record, or create a
missing store.

Here "observer" describes the user interface. It does not mean the live runtime
`Observer` hook, and it does not mean `TrialEvaluator`. Runtime observers may collect
telemetry while work executes; evaluators may form decisions from frozen evidence;
`watch` only projects records that have already been persisted.

## Approved Stage 3 decisions (2026-08-31)

- Ordinary `arity watch` remains the deterministic one-shot command. Live terminal
  behavior is entered explicitly with `arity watch --follow`.
- The live view remains a presentation client, not a control plane. It may select,
  expand, retry a read, or quit, but it cannot start, stop, steer, repair, evaluate,
  or pre-warm work.
- Arity will define an attributed observation envelope for three independent lenses:
  mechanical checks, optional LLM interpretations, and human judgments. They may
  examine equivalent blinded evidence and later be compared for analytics, but none
  silently overwrites another. `watch` may display their persisted, blind-safe
  projections; it does not run them.
- Cache telemetry follows one direction: provider response -> normalized versioned
  usage event -> trial journal -> inspection projection -> watch. It does not flow
  from an in-process metrics hook directly into the UI.
- Cache heat is user-facing and supports `exact`, `conservative`, and `off` policies.
  `exact` uses the recorded provider policy; `conservative` uses the shortest
  configured response window; `off` avoids turning a provider-specific timer into an
  A/B identity fingerprint. The display reports a documented reuse window and its
  certainty, never direct knowledge of provider cache residency.

## Current Stage 2 slice

The current command has one useful center: a nested list of trials and their neutral
agent labels. An explicitly selected trial opens a compact
evidence/review/resolution summary. It reads the same configured JSONL or SQLite
store as `arity trials`, takes exactly one read-only snapshot, renders it, and exits.

The command shape is deliberately small:

```text
arity watch [trial-id] [--ascii] [--no-motion]
```

- Every invocation emits one fixed printable-ASCII, ANSI-free snapshot and exits. It
  behaves the same in a terminal and a pipeline.
- It never reads stdin, inspects TTY state or terminal size, polls, sleeps, animates,
  emits color, retries, or retains a last-good snapshot.
- `--ascii` and `--no-motion` are accepted future-compatibility promises. They are
  deliberately inert in Stage 2 and every flag combination produces identical
  output.
- Machine consumers continue to use `arity trials --json` and
  `arity trial show ID --json`. Stage 2 does not introduce another
  JSON schema or change their version-1 envelopes and exit codes.

Interactive selection, repeated refresh, retry, motion, Unicode glyphs, color and
`NO_COLOR`, terminal-width-aware layout, and terminal cleanup all belong to Stage 3.
They are design goals below, not descriptions of the current command.

## Current snapshot

```text
arity watch | jsonl | 2 trials | read 12:04:09
  Trial 1 | started | valid | completions 0/2
> Trial 2 | evidenced | partial | completions 1/2
    Agent A | completion recorded
    Agent B | no completion recorded
    issue unsupported_event
      The trial contains an event type this version does not understand.
selected: Trial 2
  evidence 1 | reviews 0 | resolutions 0 | delivery no
```

The renderer uses a fixed line-oriented hierarchy and lets the surrounding terminal
wrap naturally. It does not query terminal width. If a finite read timestamp is
outside the host platform's local-time range, the header uses the fixed unknown value
`read ??:??:??` instead of inventing a time.

## Deferred Stage 3 wireframe

```text
 arity watch                    jsonl | 4 trials | read 12:04:09
             . o * @ * o .      journal update

 > Trial 1     started    valid        completions 1/3
     Agent A   completion recorded
     Agent B   no completion recorded *
     Agent C   no completion recorded *
   Trial 2     delivered  valid        completions 2/2
   Trial 3     evidenced  partial      completions 2/2
   Trial 4     unknown    corrupt      details unavailable

 selected: Trial 1
 evidence 0   reviews 0   resolution no   delivery no
 * no completion record exists; activity is unknown

 [j/k] select  [enter] expand  [r] retry  [?] help  [q] quit
```

Stage 3 may collapse this layout to a single stacked list on narrow terminals. Its
spiral remains decoration plus an observed-journal-change cue, not a progress
indicator. Interactive mode may then use `j`/`k` or arrow keys to move, Enter to
expand, `r` to retry, `?` for help, and `q` to quit, but none of those controls exist
in Stage 2.

## Truth and privacy rules

### The blind-safe view model

`WatchViewModel` is the only blind-safe boundary. It is a dedicated, positive
allowlist built from inspection data; neither `TrialInspection`, `TrialSummary`, nor
`inspection_overview` is safe to render directly. The overview remains useful source
data for valid trials, but contains experimental identities and paths that the view
model must discard.

The allowlist contains only finite structural values: neutral trial and agent labels,
the closed integrity and lifecycle enums, the closed whole-catalog integrity
aggregate, completion-recorded and bounded selection-state booleans, bounded counts
of verified-prefix arms/evidence/reviews/resolutions, delivery presence, allowlisted
issue codes with canned text, local read time, and store backend (`jsonl` or `sqlite`).
It never carries `task_name`, brief, role, raw trial/arm/candidate/evaluator/resolution
or evidence IDs, names, signatures, model, provider, harness, tool runner, skills,
context, raw completion/review status, output, artifact or delivery file paths, raw
issue messages, or credentials. There is no identity-reveal toggle in the first
release.

Trials receive in-memory neutral labels (`Trial 1`, `Trial 2`, ...). The controller owns
a session-scoped map from full trial ID to neutral label: it assigns the first snapshot
in display order, gives each newly observed ID the next monotonically increasing label,
and never recycles or reassigns a label during that watch session. Re-sorting moves the
existing label with its trial. Raw trial IDs are kept only as map keys and controller
selection state so an exact requested ID can be found; they do not enter the view
model. Arms from the verified declaration are sorted by ordinal, then labeled by their
bounded list position (`Agent A`, `Agent B`, ...), not by the numeric ordinal itself. A
negative or enormous ordinal can affect only its sorted position: it never controls
allocation, indentation, label width, or character count. Legacy scalar arms retain
declaration order. Rendering is capped at 256 trials and 256 arms per trial with only a
structural `more omitted` flag.

The projector computes `catalog_integrity` across the complete projected catalog
before applying the 256-row display cap. It is an exact plain string from the closed
set `valid`, `partial`, and `corrupt`, so an offscreen degraded row cannot make a
displayed snapshot claim a weaker exit severity. `selected_trial_omitted` is a
bounded, exact boolean used only when the requested trial exists beyond that cap. In
that state no uncapped neutral number for the selection enters the view model: the
renderer emits only
`selected: omitted trial | details unavailable`.

All fixed labels are supplied by the renderer, not persisted text. If a future
allowlisted field permits persisted text, it must pass the current control-character
and bidirectional-mark escaping rule; `--ascii` additionally escapes each non-ASCII
code point. Color, glyph shape, animation, and cursor position are never the only
carriers of meaning. Users who deliberately need raw local records already have
`arity trial replay ID --json` and its documented sensitivity warning.

### Lifecycle is not liveness

The two current dimensions remain separate:

| Journal value | TUI meaning |
| --- | --- |
| integrity `valid` | The installed version validated the whole journal. |
| integrity `unsupported` | `partial`; show only the best-known verified prefix. |
| integrity `corrupt` | `corrupt`; lifecycle is `unknown` and the agent tree is suppressed. |
| status `started` | A start event exists. It does **not** mean queued, running, or alive. |
| status `evidenced` | Candidate evidence was frozen. |
| status `unresolved` | A recorded resolution has no winner. |
| status `resolved` | A winner was recorded, but delivery is not recorded. |
| status `delivered` | Delivery was recorded. |
| agent `no completion recorded` | No verified-prefix `arm.completed` exists for that arm. Activity is unknown. |
| agent `completion recorded` | A verified-prefix completion exists. Its raw status remains hidden. |

Today `run_race` appends `trial.started` before dispatch, but appends all initial
`arm.completed` events only after the dispatcher returns. There is no durable
`arm.started`, model-turn, heartbeat, or process-liveness event. The first TUI must
therefore never relabel an absent completion as `queued`, `working`, `thinking`, or `running`, and
must not derive progress from elapsed time, event count, completed-arm ratio, token
count, or lifecycle phase.

### Deferred Stage 3 spiral and future cache heat

Stage 2 has no spiral or update cue. In Stage 3, a small, fixed-density Vogel-style
mark may pulse briefly after a successful snapshot is observably different from the
preceding snapshot. Its adjacent text says `journal update`; it settles when no new
journal data is observed. It does not grow from empty to full, spin continuously
merely because a trial is `started`, or use the existing `SpiralSpinners` labels that
claim agents are thinking. Reduced-motion mode swaps the pulse for a static mark and
an updated read timestamp. ASCII mode uses `.o*@` only.

Continuous inference animation is gated on a future, versioned durable activity
contract such as arm/model-turn start and finish events. Until that exists, Arity can
show only the short pulse caused by journal changes, including the start event that
precedes inference.

There is no fire, warmth, hit-rate, or cache bar in Stage 2. `MetricsObserver` has
in-process aggregate cache counters, but they are neither a per-arm durable journal
contract nor part of the inspection projection. Stage 3 first persists normalized
per-request request-start time, prompt/cache-read/cache-write token counts, retention
policy, and context-reset events. A time-derived flame may then visualize the recorded
reuse window with explicit `confirmed`, `estimated`, `elapsed`, `unknown`, and
`unsupported` states. Elapsed means the documented window passed; it does not prove
that a provider evicted the entry. A cache read or write on a later request refreshes
the recorded window. Compaction, a model switch, or another prefix-reset boundary
starts a new comparison epoch.

## Data and snapshot path

Each Stage 2 invocation uses the existing read-only seams exactly once:

```text
configured_store_spec
  -> open_record_reader (one query-only reader)
  -> inspect_trials (one complete catalog)
  -> close reader
  -> injected clock
  -> WatchProjector
  -> project the complete catalog and aggregate catalog_integrity
  -> select at most 256 display rows and bound offscreen selection to a boolean
  -> allowlisted WatchViewModel (the blind-safe boundary)
  -> fixed ASCII renderer
  -> exit
```

`inspection_overview`, currently used by `trial show --json` inside
`arity.inspection_cli`, is not the watch command's content, privacy, or blindness
boundary, and Stage 2 does not call or render it. The command consumes the complete
`TrialCatalog` as Python data through `WatchProjector`; it never scrapes human CLI
output or launches an `arity --json` subprocess.

A Stage 2 invocation opens and closes one reader so JSONL receives its strict file
snapshot and SQLite receives its bounded private snapshot. The clock is read once
after the reader closes. The command never holds a writable `RecordStore` and never
uses the live runtime `Observer` protocol. Its pure, injectable clock records only
the read time; Stage 2 has no visual pulse.

For a valid inspection, the view model may derive structural fields from its validated
replay. For an unsupported inspection, it derives them only from
`inspection.replay`, which is the verified prefix; when that replay is absent it emits
only a neutral trial label, `partial`, and an allowlisted safe issue. For corruption it
emits only a neutral trial label, `corrupt / unknown`, and an allowlisted safe issue.
It never reads `TrialInspection.events` or raw-summary timestamps/counts for an
unsupported or corrupt view.

Sorting and fingerprinting operate on the allowlisted view model. Valid trials may use
their validated replay timestamp; unsupported trials may use only the last finite
timestamp in the verified replay prefix. Trials without a trusted timestamp use an
opaque internal trial-identity tie-break that is never rendered. No post-boundary raw
event, timestamp, physical event count, payload, or summary value may influence row
order or the fingerprint. The fingerprint includes the closed catalog aggregate, so
a safe severity change beyond the display cap remains observable without exposing
the hidden row or source count. Stage 2 uses an exact requested full trial ID only to
select within its one snapshot. Stage 3 will retain selection by that internal
identity, never by row number; polling itself will not animate the spiral.

### Exceptional snapshots

- **Missing store:** render `No persisted trials.`, exit `0`, and do not create
  `.arity/`.
- **Empty store:** render the same empty state.
- **Requested trial missing:** emit only `arity: trial_not_found` on stderr, without
  echoing the raw ID, and exit `3`.
- **Requested trial beyond the display cap:** retain the complete catalog's severity,
  emit `selected: omitted trial | details unavailable`, and never render its raw ID,
  uncapped neutral number, detail, or source rank.
- **Unrepresentable local read time:** render `??:??:??`; do not substitute midnight
  or another invented time.
- **Future event or nested schema:** label the trial `partial`, derive lifecycle and
  agent structure only from `inspection.replay`, and show an allowlisted issue code
  with canned text. With no verified replay, show no lifecycle or agent detail. Never
  trust later known-looking events across the unsupported boundary.
- **Logically corrupt trial:** retain its catalog row, label it `corrupt / unknown`,
  suppress all raw summary and agent data, and show only an allowlisted issue code with
  canned text.
- **Physical corruption:** emit no snapshot, report only
  `arity: record_store_corrupt` on stderr, and exit `5`.
- **Store changed during read:** emit no snapshot, report
  `arity: record_store_changed` on stderr, and exit `1`. Do not call it corruption or
  repair it.
- **Other read failure:** emit no snapshot, report only
  `arity: record_read_error` on stderr, and exit `1`.
- **Output write, short-write, or flush failure:** return operational exit `1`
  without a traceback or an attempted unsafe diagnostic containing the exception.

Stage 3 may retain a last successful snapshot, show a store-error banner, and retry
after a change or read failure. Stage 2 never keeps state or retries.

For one-shot/non-interactive output, semantic results retain the established meanings:
success/empty `0`, operational read failure `1`, missing selected trial `3`, partial
projection `4`, and corruption `5`. Argument parsing remains `2`. A future Stage 3
interactive user quit after the screen opens will return `0`; machine automation
belongs on the existing JSON commands.

Typed physical read failures take precedence because no trustworthy catalog exists.
After a successful catalog read, a missing requested trial returns `3`; otherwise a
`corrupt` whole-catalog aggregate returns `5`, a `partial` aggregate returns `4`, and
a `valid` or empty aggregate returns `0`. That aggregate is computed before the
256-row cap, so the `5 > 4 > 0` precedence includes offscreen projected trials and
catalog issues.

## Implementation seams

Stage 2 keeps the feature in three small layers:

1. **Snapshot source/controller:** `load_watch_model` resolves one `StoreSpec`, opens
   one query-only reader, inspects one complete `TrialCatalog`, closes the reader,
   reads its injected clock once, and projects the result. `run_watch_command` maps
   typed and logical outcomes to fixed stdout/stderr text and semantic exit codes.
   Neither function owns runtime/provider/tool objects.
2. **Pure view model:** neutral-label mapping, safe text, selection, capability flags,
   the pre-cap `catalog_integrity` aggregate, the bounded
   `selected_trial_omitted` boolean, and the exact state mapping above. It has no
   terminal or filesystem access.
3. **Pure renderer:** `render_watch_snapshot` accepts only an exact `WatchViewModel`
   and returns one canonical printable-ASCII string ending in one LF. It uses
   `??:??:??` when the platform cannot represent the local read time and has no
   terminal, clock, or filesystem access.

The thin CLI parser accepts the optional exact trial ID plus `--ascii` and
`--no-motion`, then dispatches once. Stage 2 has no terminal capability or cleanup
context because it never changes terminal state.

For the default process stdout and stderr, `run_watch_command` encodes the already
validated frame or canned error as strict ASCII and writes it through the stream's
binary buffer. This bypasses platform newline translation, so Windows and POSIX
receive the same byte-exact LF-only output. Explicitly injected text streams remain
the embedding and test seam; they receive the same canonical strings and are flushed.
A write, incomplete write, encoding, or flush failure is contained and returns `1`
without printing a traceback or echoing the exception.

### Deferred Stage 3 controller

A Stage 3 controller may add terminal sizing, key input, refresh scheduling, color,
motion, and frame drawing. It must own terminal state through one cleanup context and
enter interactive mode only after stdin and stdout both pass TTY and platform
capability checks. On every return path, setup failure, renderer exception, EOF,
`KeyboardInterrupt`, Ctrl-C, supported platform termination signal, and ordinary
`q`, it must restore the original POSIX termios or Windows console mode, leave the
alternate screen, and make the cursor visible before propagating the exit. A partial
setup unwinds only the changes that succeeded. Capability failure selects the
one-shot fallback instead of leaving a half-configured console.

The implementation preserves Arity's zero mandatory runtime dependencies. A richer
Stage 3 renderer can live behind an optional extra, provided the fixed one-shot
fallback remains complete.

## Acceptance criteria

### Implemented Stage 1 and Stage 2 criteria

- One invocation never reads terminal input or capabilities and never invokes a
  provider, tool runner, runtime, writable store, workspace, delivery path, or
  authentication path.
- Watching a missing store leaves the filesystem byte-for-byte absent at that path.
- Golden renderer snapshots cover every lifecycle value and all three integrity
  values. Query-only integration fixtures cover both JSONL and SQLite.
- One adversarial fixture places the unique marker `BLIND_LEAK_SENTINEL` in every
  free-form or identity field, including event/idempotency values, task name, brief,
  role, every raw ID/name/signature/axis/status, issue/evaluator fields, output,
  artifact path/body, and delivery file path. The marker and its encoded fragments
  never occur in `WatchViewModel` or rendered output.
- Agent state is only `completion recorded` or `no completion recorded`; no view says
  running or shows a percentage without a future activity contract.
- Future-schema, corrupt, orphan-record, physical-store, and changing-store fixtures
  exercise the exceptional states above without a traceback or untrusted nested data.
  Adversarial future events after the boundary change no label, ordering key,
  fingerprint, trusted timestamp, or count.
- Negative and enormous arm ordinals produce bounded position-based labels without
  large allocation, padding, Unicode lookup, or a renderer error. The 256-item caps
  report only `more omitted`, never an unbounded source count.
- The closed `catalog_integrity` field aggregates every projected row before the
  256-row cap and preserves `corrupt > partial > valid` exit severity even when the
  degraded row is offscreen.
- An offscreen exact selection sets only the bounded `selected_trial_omitted` boolean
  and renders `selected: omitted trial | details unavailable`; no `Trial 257`, raw
  identity, uncapped source count, or rank reaches the model or output.
- Every Stage 2 snapshot is printable ASCII and ANSI-free, ends in exactly one LF,
  and is identical across `--ascii` and `--no-motion` combinations. Default CLI
  stdout/stderr are asserted as byte-exact LF-only output on Windows-like and POSIX
  streams; explicit injected text streams remain supported.
- Finite read times outside the platform's representable local-time range render
  exactly `??:??:??`.
- Output write, incomplete-write, and flush fixtures return `1` without a traceback
  or exception-text leak for both default process streams and injected text streams.
- Exactly one complete catalog is read through one query-only reader; the reader
  closes before the clock is read. No trial is selected implicitly.
- Missing selection and typed physical failures emit only fixed safe stderr codes.
  Logical partial and corrupt catalogs remain visible on stdout, while the pre-cap
  aggregate drives semantic exit codes `4` and `5`.
- Existing `trials`, `trial show`, and `trial replay` human output, version-1 JSON
  shapes, and semantic exit-code tests remain unchanged.

The implemented contract lives in `tests/test_watch_view_model.py` and
`tests/test_watch_cli.py`. Current CI gates it as follows:

- Ubuntu/Python 3.10 runs the full source suite, including the view-model and one-shot
  command adversarial, golden, capability, and query-only reader tests.
- Ubuntu/Python 3.14 keeps build and Twine validation, installs the
  wheel, then runs the one-shot `arity watch --ascii --no-motion` acceptance outside
  the source checkout. Acceptance checks exact empty-state and missing-selection
  stdout/stderr bytes and rejects CRLF translation.
- Windows/Python 3.14 runs `acceptance/verify_installed.py`, including the installed
  one-shot command outside the source checkout with the same byte-exact LF-only
  assertions.

### Deferred Stage 3 criteria

- A refresh fixture changes trusted timestamps and lifecycle values, reorders
  existing rows, and inserts a new trial. Every previously observed full ID and the
  selected trial retain their original neutral label; the new ID receives the next
  label and no retired label is reused during the session.
- Control characters and bidirectional markers cannot control the terminal. Very long
  and very narrow content truncates predictably without changing semantic labels.
- Reduced-motion tests produce no timer-driven frame change. ASCII snapshots escape
  non-ASCII persisted input. Separate no-color snapshots contain no SGR sequences
  while retaining textual state; Unicode, color, width, and terminal cursor control
  remain independently testable capabilities.
- Interactive tests cover every stdin/stdout TTY combination, partial setup failure,
  render failure, EOF, Ctrl-C, supported signals, and normal quit, asserting exact
  restoration of console/raw mode and cursor state.
- A journal-change pulse occurs only after the safe snapshot fingerprint changes; an
  unchanged `started` trial becomes and remains visually still.
- Cache-heat tests cover confirmed reads and writes, unknown telemetry, elapsed
  documented windows, context-reset boundaries, and all three display policies. The
  `off` policy exposes no provider-specific duration; `watch` never sends a provider
  request to affect the result it displays.
- These Stage 3 behaviors will receive their own terminal/controller tests. They are
  not requirements of `tests/test_watch_cli.py`.

## Staged path

1. **Implemented:** build the dedicated allowlisted `WatchViewModel`; treat existing
   inspection and overview objects only as untrusted source data and add adversarial
   blindness tests.
2. **Implemented on this branch:** add a fixed printable-ASCII, ANSI-free,
   non-interactive `arity watch` snapshot that performs no terminal capability work.
3. **Approved next:** add polling, stable selection, keyboard control, retries,
   last-good-snapshot errors, optional Unicode/color with `NO_COLOR`,
   terminal-width-aware layout and cleanup, and the bounded journal-change spiral
   pulse. Add cache heat only through the durable normalized usage path and the
   explicit display policies above.
4. Only after a separately reviewed journal schema records truthful arm activity may
   the TUI show active inference.

## Non-goals

This slice does not run or control agents, reveal model identities, edit trials, stream
full replay, inspect workspaces, display prompt/cache estimates, invent active states,
or replace the current JSON CLI. Agent graphs, arbitrary topology, remote/multi-host
watching, a GUI, Minecraft homes, and a SimCity-like agent world are delightful later
clients of the same observer contract, not requirements for the first TUI.
