# TODO

Open items, roughly in the order they matter. No remote yet, so this file is the issue tracker.

## Cost and casting
- [ ] **Pre-flight casting.** Estimate a task's cost from `trial_axes` history for the signature and skip
      seats whose remaining quota cannot cover it, instead of attempting and rotating at zero usage.
- [ ] **Actual cost per trial.** `actual_cost_usd = quota_fraction_consumed × window_price_usd`, comparable
      to `api_equiv_usd = tokens × list_price`. Needs `Seat.window_price_usd` (Antigravity plan, ChatGPT
      Plus/Pro, SuperGrok — being fetched by a scout race) and a quota snapshot before/after each candidate.
- [ ] Codex and xAI quota: look for usage / rate-limit headers on the wire; until then "unmeasured", never guessed.
- [ ] Tie-break inside a correctness tier by actual cost when measured, API-equivalent otherwise; show both.

## Front door
- [ ] `arity run "<brief>"` goes through the race pipeline (models preset capped by `ARITY_CONCURRENCY`,
      review on tie, conference off) and then **delivers**: winning sandbox copied to `--out` or
      `deliveries/<task_id>/`, or the closing output printed when there are no files. One-line receipt.
- [ ] Secretary asks Asa on a review disagreement (definition written; code not).

## Races
- [ ] A brief that asks a write-denied role (scout) to write a file is a task-design error; the race
      should refuse or warn at resolve time (role.can_use_tool('write_file') vs brief mentions 'write').
- [ ] Archivist discrepancy detection is regex over prose; make the closing report structured
      (files: [...]) so claims are exact.
- [ ] First non-code race (scout, secretary) — verify facts-tie → review → human holds up without pytest.
- [ ] `types/rust.md` verify commands are untested (`cargo test`, hidden tests under `tests/`).
- [ ] Conference: nobody has used `message(to="peer:X")` yet in a live run; watch whether it matters.
- [ ] Conference cost is ~99% context replay per turn; measure with a provider that reports cache hits.

## Judge
- [ ] Citation check is code-shaped (backticked identifiers). For prose/scout output check cited URLs
      against the fetch_url tool log instead - the scout race scored 0/0 citations.
- [ ] Citation checker attributes comparative sentences ("C, unlike A's `move_to_end`") to the wrong letter
      (~90% precision). Reported, never scored — improve the attributor or leave it.
- [ ] Judge with `type` skills for non-code domains (`reviewer:design`?) once a non-code race exists.

## Kernel
- [ ] Provider replay compatibility is its own fragility axis (Gemini thought signatures, Claude tool ids and
      empty parts). Consider a per-provider replay test that round-trips one tool call.
- [ ] Existing test suite writes into the real `.arity/records` in places (`test_terrarium.py` etc.);
      point them at temp stores like the race tests do.
