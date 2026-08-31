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

- With an interactive terminal, `j`/`k` or arrow keys move, Enter expands, `r`
  retries immediately, `?` shows help, and `q` quits.
- With redirected output or a terminal that cannot be controlled safely, the command
  prints one ANSI-free catalog snapshot and exits. It never hangs in a pipeline.
- `NO_COLOR` disables color. `--ascii` replaces non-ASCII glyphs and borders.
  `--no-motion` keeps the spiral still. These modes preserve all words and states.
- Machine consumers continue to use `arity trials --json` and
  `arity trial show ID --json`. The first `watch` release does not introduce another
  JSON schema or change their version-1 envelopes and exit codes.

## Compact wireframe

```text
 arity watch                    jsonl | 4 trials | read 12:04:09
             . o * @ * o .      journal update

 > trial 8f31  started    valid        agents 1/3
     Agent A   completed
     Agent B   pending *
     Agent C   pending *
   trial 6ca0  delivered  valid        agents 2/2
   trial 103d  evidenced  partial      agents 2/2
   trial 771e  unknown    corrupt      details unavailable

 selected: trial 8f31
 evidence -   reviews -   resolution -   delivery -
 * pending means no completion is recorded; activity is unknown

 [j/k] select  [enter] expand  [r] retry  [?] help  [q] quit
```

The layout collapses to a single stacked list on narrow terminals. The spiral is
decoration plus an observed-journal-change cue, not a progress indicator.

## Truth and privacy rules

### Neutral labels

The TUI derives `Agent A`, `Agent B`, ... from the stable `arm_ordinal` declared by
`trial.started` (continuing `Z`, `AA`, `AB`, ... for larger trials). Legacy scalar
arms use their declaration order. The mapping lives only in the view model.

The default TUI never renders persisted `name`, `model`, `provider`, `signature`,
`harness`, `tool_runner`, `skills`, `context`, evaluator identity, or raw candidate
ID. Any of those can reveal an experimental axis. Candidate IDs are used internally
only to map a completion or resolution back to its neutral agent label. There is no
identity-reveal toggle in the first release.

Task names may appear. Ad-hoc trials are labeled by a shortened, terminal-safe trial
ID rather than by their brief in the list. The detail pane may show the existing
content-safe summary, but never full replay, candidate output, artifact bodies, raw
review text, or credentials. Users who deliberately need those local records already
have `arity trial replay ID --json` and its documented sensitivity warning.

All persisted strings pass through the same control-character and bidirectional-mark
escaping rule used by the current ANSI-free inspection renderer. Color, glyph shape,
animation, and cursor position are never the only carriers of meaning.

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
| agent `pending` | No `arm.completed` event exists for that arm. Activity is unknown. |
| agent completion status | Display the terminal-safe recorded value; do not reinterpret it as live state. |

Today `run_race` appends `trial.started` before dispatch, but appends all initial
`arm.completed` events only after the dispatcher returns. There is no durable
`arm.started`, model-turn, heartbeat, or process-liveness event. The first TUI must
therefore never relabel `pending` as `queued`, `working`, `thinking`, or `running`, and
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
  -> inspection_overview
  -> neutral WatchViewModel
  -> terminal renderer
```

`inspection_overview`, currently shared by `trial show --json` inside
`gorkbot.inspection_cli`, is the content-safe detail boundary. Before implementing the
TUI, move or wrap it as an intentionally shared projection without changing its JSON
shape. The TUI must consume Python data, not scrape human CLI output and not launch an
`arity --json` subprocess.

A refresh opens and closes a reader every time so JSONL receives its strict file
snapshot and SQLite receives its bounded private snapshot. The watcher never holds a
writable `RecordStore` and never uses the live runtime `Observer` protocol. A pure,
injectable clock controls read timestamps and the short visual pulse; it cannot alter
journal-derived state.

The view model fingerprints only content-safe projected fields to decide whether a
journal update was observed. Polling itself does not animate the spiral. Selection is
stable by full trial ID, not row number, when the catalog reorders.

### Exceptional snapshots

- **Missing store:** render `No persisted trials.` and keep watching without creating
  `.gorkbot/`.
- **Empty store:** render the same empty state.
- **Requested trial missing:** report the stable `trial_not_found` condition; an
  interactive catalog may remain usable.
- **Future event or nested schema:** label the trial `partial`, show its best-known
  lifecycle and verified-prefix agents, and show the safe issue code. Never trust later
  known-looking events across the unsupported boundary.
- **Logically corrupt trial:** retain its catalog row, label it `corrupt / unknown`,
  suppress the untrusted agent tree, and show only content-safe diagnostics.
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

The first implementation should preserve Arity's zero mandatory runtime dependencies.
A richer renderer can later live behind an optional extra, provided the plain and
non-interactive fallbacks remain complete.

## Acceptance criteria

- Opening, refreshing, selecting, and quitting never invokes a provider, tool runner,
  runtime, writable store, workspace, delivery path, or authentication path.
- Watching a missing store leaves the filesystem byte-for-byte absent at that path.
- Golden snapshots cover every lifecycle value and all three integrity values on JSONL
  and SQLite fixtures.
- No rendered TUI snapshot contains the fixture's model, provider, signature, harness,
  tool runner, context, evaluator ID, candidate ID, output, or artifact body.
- `pending` is always accompanied by `no completion recorded` or `activity unknown`;
  no view says running or shows a percentage without a future activity contract.
- Future-schema, corrupt, orphan-record, physical-store, and changing-store fixtures
  render the exceptional states above without a traceback or untrusted nested data.
- Control characters and bidirectional markers cannot control the terminal. Very long
  and very narrow content truncates predictably without changing semantic labels.
- Reduced-motion tests produce no timer-driven frame change. ASCII and no-color golden
  snapshots contain no non-ASCII or ANSI bytes and remain understandable.
- A journal-change pulse occurs only after the safe snapshot fingerprint changes; an
  unchanged `started` trial becomes and remains visually still.
- Existing `trials`, `trial show`, and `trial replay` human output, version-1 JSON
  shapes, and semantic exit-code tests remain unchanged.
- The pure view model and fallbacks pass the supported Python 3.10/3.14 Linux and
  Windows CI matrix without a real terminal.

## Staged path

1. Extract and freeze the shared content-safe overview projection; add blindness and
   terminal-safety view-model tests.
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
