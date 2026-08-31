# Observer TUI

Status: proposed first slice; documentation only.

`arity watch` is a read-only local view of what Arity's persisted trial journal can
prove. It is an observer, never a participant: opening it must not run an agent,
contact a provider, execute a tool, attach to a runtime, repair a record, or create a
missing store.

## First release

The first release has one useful center: a nested list of trials and their neutral
agent labels. A selected trial opens a compact evidence/review/resolution summary.
It reads the same configured JSONL or SQLite store as `arity trials` and refreshes by
taking new read-only snapshots.

The command shape is deliberately small:

```text
arity watch [trial-id] [--ascii] [--no-motion]
```

- Interactive mode starts only when both stdin and stdout are controllable TTYs.
  `j`/`k` or arrow keys move, Enter expands, `r` retries immediately, `?` shows help,
  and `q` quits.
- If either stream is redirected, terminal capability setup fails, or the terminal
  cannot be controlled safely, the command prints one ANSI-free snapshot and exits.
  It never hangs in a pipeline.
- `NO_COLOR` disables color. `--ascii` uses only ASCII UI glyphs and escapes every
  non-ASCII character in persisted text. `--no-motion` keeps the spiral still. These
  modes preserve all words and states.
- Machine consumers continue to use `arity trials --json` and
  `arity trial show ID --json`. The first `watch` release does not introduce another
  JSON schema or change their version-1 envelopes and exit codes.

## Compact wireframe

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

The layout collapses to a single stacked list on narrow terminals. The spiral is
decoration plus an observed-journal-change cue, not a progress indicator.

## Truth and privacy rules

### The blind-safe view model

`WatchViewModel` is the only blind-safe boundary. It is a dedicated, positive
allowlist built from inspection data; neither `TrialInspection`, `TrialSummary`, nor
`inspection_overview` is safe to render directly. The overview remains useful source
data for valid trials, but contains experimental identities and paths that the view
model must discard.

The allowlist contains only finite structural values: neutral trial and agent labels,
the closed integrity and lifecycle enums, completion-recorded booleans, bounded counts
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

### Spiral and cache heat

A small, fixed-density Vogel-style mark can pulse briefly after a successful snapshot
is observably different from the preceding snapshot. Its adjacent text says `journal
update`; it settles when no new journal data is observed. It does not grow from empty
to full, spin continuously merely because a trial is `started`, or use the existing
`SpiralSpinners` labels that claim agents are thinking. Reduced-motion mode swaps the
pulse for a static mark and an updated read timestamp. ASCII mode uses `.o*@` only.

Continuous inference animation is gated on a future, versioned durable activity
contract such as arm/model-turn start and finish events. Until that exists, Arity can
show only the short pulse caused by journal changes, including the start event that
precedes inference.

There is no fire, warmth, hit-rate, or cache bar in this release. `MetricsObserver`
has in-process aggregate cache counters, but they are neither a per-arm durable journal
contract nor part of the inspection projection. A future meter requires persisted,
provider-attributed cache-read and prompt-token measurements with explicit unknown and
unsupported states. Estimates based on time or context mode do not qualify.

## Data and refresh path

Each refresh uses existing read-only seams:

```text
configured_store_spec
  -> open_record_reader (new reader for this refresh)
  -> inspect_trials
  -> selected TrialInspection
  -> inspection/replay source data
  -> allowlisted WatchViewModel (the blind-safe boundary)
  -> terminal renderer
```

`inspection_overview`, currently used by `trial show --json` inside
`gorkbot.inspection_cli`, may be shared as source data without changing its JSON shape.
It is not the TUI's content, privacy, or blindness boundary. The TUI must construct the
strict allowlist above, consume Python data rather than scrape human CLI output, and
never launch an `arity --json` subprocess.

A refresh opens and closes a reader every time so JSONL receives its strict file
snapshot and SQLite receives its bounded private snapshot. The watcher never holds a
writable `RecordStore` and never uses the live runtime `Observer` protocol. A pure,
injectable clock controls read timestamps and the short visual pulse; it cannot alter
journal-derived state.

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
order, animation, or the fingerprint. Polling itself does not animate the spiral.
Selection remains stable by internal full trial ID, never by row number.

### Exceptional snapshots

- **Missing store:** render `No persisted trials.` and keep watching without creating
  `.gorkbot/`.
- **Empty store:** render the same empty state.
- **Requested trial missing:** report `trial_not_found` without echoing the raw ID; an
  interactive catalog may remain usable.
- **Future event or nested schema:** label the trial `partial`, derive lifecycle and
  agent structure only from `inspection.replay`, and show an allowlisted issue code
  with canned text. With no verified replay, show no lifecycle or agent detail. Never
  trust later known-looking events across the unsupported boundary.
- **Logically corrupt trial:** retain its catalog row, label it `corrupt / unknown`,
  suppress all raw summary and agent data, and show only an allowlisted issue code with
  canned text.
- **Physical corruption:** keep the last successful snapshot visible with a prominent
  store-error banner. If no successful snapshot exists, show only the error state.
- **Store changed during read:** treat `RecordChanged` as transient, retain the last
  successful snapshot, and retry. Do not call it corruption and do not repair it.
- **Other read failure:** retain the last successful snapshot, show the stable error
  code and last successful read time, and wait for `r` or the next refresh.

For one-shot/non-interactive output, semantic results retain the established meanings:
success/empty `0`, operational read failure `1`, missing selected trial `3`, partial
projection `4`, and corruption `5`. Argument parsing remains `2`. An interactive user
quit after the screen opened returns `0`; machine automation belongs on the existing
JSON commands.

## Implementation seams

Keep the feature in three small layers:

1. **Snapshot source:** one function returning either a `TrialCatalog` or the existing
   typed read error. It owns no runtime/provider/tool objects.
2. **Pure view model:** neutral-label mapping, safe text, selection, capability flags,
   and the exact state mapping above. It has no terminal or filesystem access.
3. **Renderer/controller:** terminal sizing, key input, refresh scheduling, and frame
   drawing. Terminal capabilities and clock are injected so tests do not sleep or need
   a real console.

The controller owns terminal state through one cleanup context. It enters interactive
mode only after stdin and stdout both pass TTY and platform capability checks. On every
return path, setup failure, renderer exception, EOF, `KeyboardInterrupt`, Ctrl-C,
supported platform termination signal, and ordinary `q`, it restores the original
POSIX termios or Windows console mode, leaves the alternate screen, and makes the
cursor visible before propagating the exit. A partial setup unwinds only the changes
that succeeded. Capability failure selects the one-shot fallback instead of leaving a
half-configured console.

The first implementation should preserve Arity's zero mandatory runtime dependencies.
A richer renderer can later live behind an optional extra, provided the plain and
non-interactive fallbacks remain complete.

## Acceptance criteria

- Opening, refreshing, selecting, and quitting never invokes a provider, tool runner,
  runtime, writable store, workspace, delivery path, or authentication path.
- Watching a missing store leaves the filesystem byte-for-byte absent at that path.
- Golden snapshots cover every lifecycle value and all three integrity values on JSONL
  and SQLite fixtures.
- One adversarial fixture places the unique marker `BLIND_LEAK_SENTINEL` in every
  free-form or identity field, including event/idempotency values, task name, brief,
  role, every raw ID/name/signature/axis/status, issue/evaluator fields, output,
  artifact path/body, and delivery file path. The marker and its encoded fragments
  never occur in `WatchViewModel` or rendered output.
- Agent state is only `completion recorded` or `no completion recorded`; no view says
  running or shows a percentage without a future activity contract.
- Future-schema, corrupt, orphan-record, physical-store, and changing-store fixtures
  render the exceptional states above without a traceback or untrusted nested data.
  Adversarial future events after the boundary change no label, ordering key,
  fingerprint, pulse, trusted timestamp, or count.
- Negative and enormous arm ordinals produce bounded position-based labels without
  large allocation, padding, Unicode lookup, or a renderer error. The 256-item caps
  report only `more omitted`, never an unbounded source count.
- A refresh fixture changes trusted timestamps and lifecycle values, reorders existing
  rows, and inserts a new trial. Every previously observed full ID and the selected
  trial retain their original neutral label; the new ID receives the next label and no
  retired label is reused during the session.
- Control characters and bidirectional markers cannot control the terminal. Very long
  and very narrow content truncates predictably without changing semantic labels.
- Reduced-motion tests produce no timer-driven frame change. ASCII golden snapshots
  contain no non-ASCII output and escape non-ASCII persisted input. Separate no-color
  snapshots contain no SGR color sequences while retaining textual state; Unicode and
  terminal cursor control remain independently testable capabilities.
- Non-interactive snapshots are ANSI-free. Tests cover every stdin/stdout TTY
  combination, partial setup failure, render failure, EOF, Ctrl-C, supported signals,
  and normal quit, asserting exact restoration of console/raw mode and cursor state.
- A journal-change pulse occurs only after the safe snapshot fingerprint changes; an
  unchanged `started` trial becomes and remains visually still.
- Existing `trials`, `trial show`, and `trial replay` human output, version-1 JSON
  shapes, and semantic exit-code tests remain unchanged.

Before shipping, CI must add these exact gates; current Windows wheel acceptance does
not yet cover the TUI:

- Ubuntu/Python 3.10 runs the full source suite, including new
  `tests/test_watch_view_model.py` and `tests/test_watch_terminal.py` adversarial,
  golden, capability, and cleanup tests.
- Ubuntu/Python 3.14 keeps build and Twine validation and additionally installs the
  wheel, then runs the one-shot `arity watch --ascii --no-motion` acceptance outside
  the source checkout.
- Windows/Python 3.14 runs the injected Windows console restoration cases from
  `tests/test_watch_terminal.py`, then extends `acceptance/verify_installed.py` to run
  the installed one-shot `arity watch --ascii --no-motion` outside the source checkout.

## Staged path

1. Build the dedicated allowlisted `WatchViewModel`; treat existing inspection and
   overview objects only as untrusted source data and add the adversarial blindness
   tests.
2. Add a one-snapshot `arity watch` renderer with ASCII, no-color, narrow-terminal, and
   non-interactive behavior.
3. Add polling, stable selection, keyboard control, last-good-snapshot errors, and the
   bounded journal-change spiral pulse.
4. Only after a separately reviewed journal schema records truthful arm activity may
   the TUI show active inference. Only after durable cache measurements may it show
   cache heat.

## Non-goals

This slice does not run or control agents, reveal model identities, edit trials, stream
full replay, inspect workspaces, display prompt/cache estimates, invent active states,
or replace the current JSON CLI. Agent graphs, arbitrary topology, remote/multi-host
watching, a GUI, Minecraft homes, and a SimCity-like agent world are delightful later
clients of the same observer contract, not requirements for the first TUI.
