# TODO

Open items, roughly in the order they matter. Use the repository's GitHub issue tracker for
external reports; this file remains the maintainer's near-term roadmap.

## Arity clean break (before Stage 3)
- [ ] Complete the no-user rename now: make `arity` the only Python namespace and
      command, remove former environment and harness aliases, and rename active
      project state to `.arity/`.  The acceptance gate is zero former-name
      occurrences in active source, tests, package metadata, and current docs.
- [ ] Protect local credentials during the state rename.  Discover only file
      metadata, copy the credential file to `.arity/` without printing it, verify
      restrictive permissions and byte identity, and retain the source until the
      renamed CLI has authenticated successfully.  Never commit either file.
- [ ] Keep history action separate from the code rename.  Reachable public commit
      subjects are already clean; decide explicitly whether removing the former name
      from historical trees is worth a destructive history rewrite.

## Cost and casting
- [ ] **Pre-flight casting.** Estimate a task's cost from `trial_axes` history for the signature and skip
      seats whose remaining quota cannot cover it, instead of attempting and rotating at zero usage.
- [ ] **Actual cost per trial.** `actual_cost_usd = quota_fraction_consumed × window_price_usd`, comparable
      to `api_equiv_usd = tokens × list_price`. Needs `Seat.window_price_usd` (Antigravity plan, ChatGPT
      Plus/Pro, SuperGrok — being fetched by a scout race) and a quota snapshot before/after each candidate.
- [ ] Codex and xAI quota: look for usage / rate-limit headers on the wire; until then "unmeasured", never guessed.
- [ ] Tie-break inside a correctness tier by actual cost when measured, API-equivalent otherwise; show both.

## Front door
- [x] `arity run "<brief>"` goes through the trial pipeline (explicit `--arity`, then `ARITY`, then the command default,
      review on tie, conference off) and then **delivers**: winning sandbox copied to `--out` or
      `deliveries/<task_id>/`, or the closing output printed when there are no files. One-line receipt.
- [x] Secretary asks Asa on a review disagreement (`human_pick`, recorded as `human_pick` records).

## Observability
- [x] Read-only `arity trials`, `arity trial show`, and `arity trial replay` commands share a versioned
      Python projection, strict JSONL/SQLite readers, semantic exit codes, and forward-schema boundaries.
- [x] `arity watch` adds a blind-safe, bounded, one-shot projection and fixed ASCII
      renderer without contacting providers, tools, runtimes, or credential stores.
- [ ] Stage 3: add explicit `arity watch --follow` polling, stable selection, keyboard
      control, last-good-snapshot errors, terminal cleanup, and the bounded
      journal-change spiral.  Keep ordinary `arity watch` deterministic and one-shot.
- [ ] Define an attributed observation envelope so mechanical checks, optional LLM
      interpretations, and human judgments can examine equivalent blinded evidence,
      retain disagreements, and feed later analytics.  Mechanical facts remain
      distinct from model hypotheses and human decisions.
- [ ] Persist per-request cache telemetry at the runtime boundary: request-start
      time, cache-read/write and prompt-token counts, documented retention policy,
      and context-reset events such as compaction or model switches.  `watch` only
      consumes these records; it never probes or prewarms a provider.
- [ ] Add user-facing cache heat with `exact`, `conservative`, and `off` policies.
      Exact uses the observed provider policy, conservative uses the shortest
      configured response window, and off prevents the timer from becoming an A/B
      identity fingerprint.  Show documented reuse eligibility and certainty, never
      claim direct knowledge of provider cache residency.

## Resolve (what happens after facts tie)
- [ ] Refine the choices when models cannot settle on one implementation: present Asa the judges' cherry-picks as
      concrete diffs to apply to the winner, offer conference as the merge path, and make "keep both" a valid
      answer. Today: winner by cost tie-break, cherry-picks printed, human_pick only on a terminal.
- [ ] Non-interactive runs should leave the question in the redphone inbox instead of silently keeping the
      archivist's order.

## Harnesses
- [ ] The claude CLI is a real harness (own tools, own system prompt). Race it as cli:claude on purpose, with a
      brief adapted to it, rather than as a wire stand-in. Out of scope until the wire seats are exhausted.

## Races
- [ ] A brief that asks a write-denied role (scout) to write a file is a task-design error; the race
      should refuse or warn at resolve time (role.can_use_tool('write_file') vs brief mentions 'write').
- [ ] Archivist discrepancy detection is regex over prose; make the closing report structured
      (files: [...]) so claims are exact.
- [ ] First non-code race (scout, secretary) — verify facts-tie → review → human holds up without pytest.
- [ ] `types/rust.md` verify commands are untested (`cargo test`, hidden tests under `tests/`).
- [ ] Conference: nobody has used `message(to="peer:X")` yet in a live run; watch whether it matters.
- [ ] Conference cost is ~99% context replay per turn; measure with a provider that reports cache hits.

## Scout
- [ ] Headless browser (or a tiny VM) as the escalation past fetch_url's reader fallback. Concrete trigger:
      https://grok.com/plans is a JS shell directly and Cloudflare 403s the reader proxy. Pricing pages for
      Google/OpenAI/Anthropic read fine without it; do not build for one page yet.
- [ ] Scout citations: check URLs in the closing report against the fetch_url log (fetched vs. merely named).

## Judge
- [ ] Citation check is code-shaped (backticked identifiers). For prose/scout output check cited URLs
      against the fetch_url tool log instead - the scout race scored 0/0 citations.
- [ ] Citation checker attributes comparative sentences ("C, unlike A's `move_to_end`") to the wrong letter
      (~90% precision). Reported, never scored — improve the attributor or leave it.
- [ ] Judge with `type` skills for non-code domains (`reviewer:design`?) once a non-code race exists.

## Kernel
- [ ] xAI wire read timeout (60s) tripped four times in one conference round on long tool-heavy turns; the
      grok CLI fallback then took 807s. Raise/stream the wire timeout before touching the CLI.
- [ ] CLI harnesses (claude/codex/omp) bring their own tools, which bypass the role's denial set. cwd now
      confines them to the sandbox (a live run wrote into the repo root before this), but that is containment,
      not enforcement - the wiki's answer is a leaf user that cannot see the repo.
- [ ] Provider replay compatibility is its own fragility axis (Gemini thought signatures, Claude tool ids and
      empty parts). Consider a per-provider replay test that round-trips one tool call.
- [ ] Existing test suite writes into the real `.arity/records` in places (`test_terrarium.py` etc.);
      point them at temp stores like the race tests do.
