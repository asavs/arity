# TODO

Open items, roughly in the order they matter. Use the repository's GitHub issue tracker for
external reports; this file remains the maintainer's near-term roadmap.

## Arity clean-break follow-through
- [x] Complete the no-user rename now: make `arity` the only Python namespace and
      command, remove former environment and harness aliases, and rename active
      project state to `.arity/`.  The acceptance gate is zero former-name
      occurrences in active source, tests, package metadata, and current docs.
- [ ] Decide and perform the maintainer-only local state migration. Prefer
      reauthentication; if preserving the existing credential is necessary,
      discover only metadata, copy it to `.arity/` without printing it, verify
      restrictive permissions and byte identity, and retain the source until the
      renamed CLI has authenticated successfully. Never commit either file.
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

## Messaging
- [x] Make team membership organizational metadata rather than an ACL. Through the default sandbox
      path, any personality can address any exact registered personality; delegated recipients retain
      their own prompt, skills, runner, and denial set, and undeliverable messages are recorded as errors.
- [ ] **Next release gate:** lift `message` into a role-bound control-plane wrapper around every
      tool-runner axis so sandbox, MCP, shell, and custom-runner races all preserve the same addressing
      and recipient authority. Carry trusted recipient provenance, the requester's exact work root,
      inherited record exclusions, and frozen-evidence taint across every hop. Reserve the `message`
      schema, add correlated delivery records, and reject non-forkable runner instances explicitly.
      Until then, communication and Scout delegation are guaranteed only on the default sandbox path
      and must not be claimed as invariant across `--variants tools`.
- [ ] Remove or bind the dormant `HandoffRequested` / `SpawnHandoff` event path to that same control
      plane; it currently reuses the sender's runner and is not a second valid delegation mechanism.

## Observability
- [x] Read-only `arity trials`, `arity trial show`, and `arity trial replay` commands share a versioned
      Python projection, strict JSONL/SQLite readers, semantic exit codes, and forward-schema boundaries.
- [x] `arity watch` adds a blind-safe, bounded, one-shot projection and fixed ASCII
      renderer without contacting providers, tools, runtimes, or credential stores.
- [x] Stage 3: add explicit `arity watch --follow` polling, stable selection, keyboard
      control, last-good-snapshot errors, terminal cleanup, and the bounded
      journal-change spiral.  Keep ordinary `arity watch` deterministic and one-shot.
- [x] Define an attributed observation envelope so mechanical checks, optional LLM
      interpretations, and human judgments can examine equivalent blinded evidence,
      retain disagreements, and feed later analytics.  Mechanical facts remain
      distinct from model hypotheses and human decisions.
- [ ] Build the analytics consumer that compares those independent observations;
      `watch` currently shows only bounded counts and never invokes a new Judge.
- [x] Persist every candidate-provider attempt crossing `JournaledModelProvider` at
      the runtime boundary: request-start time, outcome, cache-read/write and token
      counts, and documented retention policy. Invalid results retain a safe failed
      attempt. `watch` only consumes these records; it never probes or prewarms a
      provider.
- [ ] Extend normalized request telemetry to Test Engineer and Judge model calls; those
      call sites do not yet cross the candidate-provider recording boundary.
- [ ] Add explicit context-reset events for compaction, model switches, and other
      prefix changes so later cache analytics can separate comparison epochs.
- [x] Add user-facing cache heat with `exact`, `conservative`, and `off` policies.
      Exact evaluates each activity with its recorded policy hint; conservative uses
      the shortest usable recorded window and earliest current-arm deadline; off
      omits cache projection so the timer cannot become an A/B identity fingerprint.
      Show documented reuse eligibility and prior-activity certainty, never
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
- [x] Ship the bounded Scout gate as a packaged-role contract: substantive-research delegation with
      the frozen-Judge exception; host-issued trusted-role provenance; separate project and exact
      requester-work roots; a sealed application-level runner; and a positive completed-history
      projection with scope-local HMAC references. This is not OS isolation or secret scanning.
- [x] Validate public DNS answers and every redirect before requesting it, bound fetch bodies and
      returned text plus search query/result count, refuse unsafe project or work roots, and fail
      closed for every trusted research personality on non-sandbox runners, CLI/OMP harnesses,
      and provider graphs with CLI-owned fallback tools, even when no active scope was issued.
- [x] Keep query-bearing target URLs out of the third-party reader proxy and restrict their
      redirects to the same normalized origin; direct failure returns only a canned error.
- [ ] Add an explicit trust-confirmation and provenance UI for project/home role definitions. Until
      then they remain executable local configuration but cannot acquire privileged Scout research.
- [ ] Pin a validated public address to the outbound connection (with correct TLS hostname handling)
      or move fetching behind an equivalent broker. The current validation/connect split retains a
      DNS-rebinding race; also decide whether the queryless third-party reader fallback should
      require consent.
- [ ] Add byte ceilings to web-search provider response bodies and rendered snippets; the current
      query-length and result-count limits are not response-size limits.
- [ ] Decide whether cross-session analytics require authenticated record provenance. The current
      reader and lifecycle checks validate internal consistency, not who wrote a self-consistent row.
- [ ] Add a headless browser in an isolated browser process or tiny VM as the escalation past
      `fetch_url`'s reader fallback, with explicit roots, outbound destinations, download handling,
      and no ambient browser cookies. Concrete trigger:
      https://grok.com/plans is a JS shell directly and Cloudflare 403s the reader proxy. Pricing pages for
      Google/OpenAI/Anthropic read fine without it; do not build for one page yet.
- [ ] Scout citations: check URLs in the closing report against the fetch_url log (fetched vs. merely named).

## Judge
- [ ] Citation check is code-shaped (backticked identifiers). For prose/scout output check cited URLs
      against the fetch_url tool log instead - the scout race scored 0/0 citations.
- [ ] Citation checker attributes comparative sentences ("C, unlike A's `move_to_end`") to the wrong letter
      (~90% precision). Reported, never scored — improve the attributor or leave it.
- [ ] Explore explicit evidence-reading skills for non-code judging once a non-code race exists. Keep the
      Judge personality itself untyped and limited to frozen evidence unless that mandate is deliberately changed.

## Kernel
- [ ] xAI wire read timeout (60s) tripped four times in one conference round on long tool-heavy turns; the
      grok CLI fallback then took 807s. Raise/stream the wire timeout before touching the CLI.
- [x] Direct Tier-0 chat never calls a CLI-owned provider node: reject a bare CLI or unsafe primary
      chain; when only a wrapper's fallback or alternate graph is unsafe, use its clean wire primary
      and discard those branches before `.call()`.
- [ ] Explicit race/candidate CLI harnesses (claude/codex/omp) for ordinary non-research roles still
      bring their own tools, which bypass the role's denial set. Setting `cwd` selects their starting
      directory but does not confine them; run such a leaf as an OS identity or in a VM/container
      that cannot see the repository.
- [ ] Provider replay compatibility is its own fragility axis (Gemini thought signatures, Claude tool ids and
      empty parts). Consider a per-provider replay test that round-trips one tool call.
- [ ] Existing test suite writes into the real `.arity/records` in places (`test_terrarium.py` etc.);
      point them at temp stores like the race tests do.
