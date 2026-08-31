# Plain `arity` lobby experience contract

Status: implemented terminal baseline on `feat/arity-lobby`. Rich visual styling and
an agent graph remain proposals, not runtime claims.

## Verified inputs

The lobby renders only the blind-safe `WatchViewModel` projected from persisted
journal records. It may show neutral trial and agent labels, bounded structural
counts, recorded lifecycle and integrity, observation-source counts, and a cache
eligibility deadline supported by normalized usage records.

It does not receive model or provider identities, prompts, candidate output,
artifact bodies, credential material, private trial IDs, or source paths. It cannot
truthfully report that an agent is currently working or that an entry resides in a
provider cache.

## Recommendation

Use plain `arity` as the quiet observer lobby. Keep explicit commands stable:
`arity watch` remains a deterministic one-shot snapshot,
`arity watch --follow` remains the explicit live-observer spelling, and
`arity demo` remains the only demo entry.

## Journey and hierarchy

1. In a supported terminal, plain `arity` opens the existing query-only follow
   controller. Outside a terminal it prints the exact one-shot result and exits.
2. An empty journal stays observable without creating a store. When the first
   non-empty snapshot arrives, the first displayed trial is selected and expanded
   once. Later refreshes retain that private selection and never steal focus.
3. The frame reads top to bottom: `arity` and snapshot metadata; an optional
   journal-change cue; the trial catalog; nested detail for the expanded trial;
   then keyboard help.
4. Trial and agent identity stays neutral. Expanded detail is visually subordinate
   to its selected trial instead of appearing as a detached second panel.

## State, interaction, and copy contract

| Situation | Presentation |
| --- | --- |
| Missing or empty store | `No persisted trials.`; keep polling only in a supported terminal |
| First read fails | `snapshot unavailable` plus a canned error code |
| Later read fails | retain `last good snapshot` plus a canned error code |
| Journal changes | show one bounded update cue; never imply task progress |
| Cache evidence exists | `respond by HH:MM:SS`, prior-activity confidence, and `eligibility only` |
| Selection | `j`/`k` or arrows; identity remains controller-private |
| Expansion | Enter toggles persisted structural detail only |
| Refresh/help/exit | `r`, `?`, and `q`; none may mutate runtime work |

The first line says `arity` only for the plain lobby. Explicit follow output retains
the `arity watch` title so existing command contracts remain recognizable.

## Accessibility and resilience

- Every interaction has a keyboard spelling and the collapsed list remains useful.
- `--ascii`, `--no-motion`, and `NO_COLOR` are independent presentation controls.
- Motion is decoration triggered by observed journal change, never a progress bar.
- Every line is width-bounded; error text is canned; raw identifiers never render.
- Terminal setup and cleanup use the existing managed follow session. Unsupported
  terminals fall back before changing terminal state.

## Acceptance criteria

- Plain `arity` and `python -m arity` are finite, byte-stable, read-only commands
  outside a terminal and create neither Arity nor demo state.
- Plain `arity` selects and expands the first available trial once, while explicit
  `arity watch --follow` does not auto-select.
- Plain lobby detail is nested beneath the selected row and contains no detached
  `selected:` section; explicit follow retains its prior renderer contract.
- The installed wheel preserves all default, explicit-watch, demo, and resolution
  behavior proven by the acceptance harness.

## Unknowns and deferred schema requests

Defer agent topology, observation bodies, live working/idle state, progress,
controls that steer work, chat, mouse interaction, remote-computer placement, and
model identity. A future graph, GUI, or simulated world must first name the durable
journal events that support each visible state; until then those elements belong in
clearly labeled non-production prototypes only.
