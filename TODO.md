# TODO

Open items, roughly in the order they matter. Use the repository's GitHub issue tracker for
external reports; this file remains the maintainer's near-term roadmap.

Longer horizon, cut by axiom rather than by subsystem:
[docs/2026-09-01-axiom-backlog.md](docs/2026-09-01-axiom-backlog.md). Items there are tagged
`[DECIDE]` (blocked on a judgment only the maintainer can make), `[BUILD]`, or `[SEAM]`
(a join where existing software may win; researched after 1.0.0, not before).

## Cost and casting
- [x] **Pre-flight casting.** Estimate a task's cost from `trial_axes` history and skip seats whose
      remaining quota cannot cover it, instead of attempting and rotating at zero usage.
- [ ] **Actual cost per trial.** `actual_cost_usd = quota_fraction_consumed × window_price_usd`, comparable
      to `api_equiv_usd = tokens × list_price`. Needs `Seat.window_price_usd` (Antigravity plan, ChatGPT
      Plus/Pro, SuperGrok — being fetched by a scout race) and a quota snapshot before/after each candidate.
- [ ] Codex and xAI quota: look for usage / rate-limit headers on the wire; until then "unmeasured", never guessed.
- [ ] Tie-break inside a correctness tier by actual cost when measured, API-equivalent otherwise; show both.

## Front door
- [x] `arity run "<brief>"` goes through the trial pipeline (explicit `--arity`, `ARITY`, then legacy `ARITY_CONCURRENCY`,
      review on tie, conference off) and then **delivers**: winning sandbox copied to `--out` or
      `deliveries/<task_id>/`, or the closing output printed when there are no files. One-line receipt.
- [x] Secretary asks Asa on a review disagreement (`human_pick`, recorded as `human_pick` records).

## Observability
- [x] Read-only `arity trials`, `arity trial show`, and `arity trial replay` commands share a versioned
      Python projection, strict JSONL/SQLite readers, semantic exit codes, and forward-schema boundaries.
- [ ] Build the first agent-graph TUI as a pure consumer of the inspection API. Keep execution controls out
      of the first pass so the observer contract can settle before it becomes a control plane.

## Resolve (what happens after facts tie)
- [ ] Refine the choices when models cannot settle on one implementation: present Asa the judges' cherry-picks as
      concrete diffs to apply to the winner, offer conference as the merge path, and make "keep both" a valid
      answer. Today: winner by cost tie-break, cherry-picks printed, human_pick only on a terminal.
- [x] Non-interactive runs leave a judge-split question in the redphone inbox instead of silently
      keeping the archivist's order.

## Harnesses
- [ ] The claude CLI is a real harness (own tools, own system prompt). Race it as cli:claude on purpose, with a
      brief adapted to it, rather than as a wire stand-in. Out of scope until the wire seats are exhausted.

## Races
- [ ] A brief that asks a write-denied role (scout) to write a file is a task-design error; the race
      should refuse or warn at resolve time (role.can_use_tool('write_file') vs brief mentions 'write').
- [x] Archivist discrepancy detection uses structured closing reports (`files: [...]`) so claims are exact;
      prose regexes remain only as a compatibility fallback for older reports.
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
- [x] Existing test suite writes into the real `.arity/records` in places; `test_terrarium.py` was the
      only one, and now passes a temp store. An audit of every `default_record_store()` caller found no
      other write-side offender. Registry-discovery tests in `test_race_runner.py` still *read* the user's
      real `~/.arity` overrides — same non-hermeticity, read side, still open.
