# A/B/C — v0 implementation, same brief, three houses

Brief: `impl/BRIEF.md`, wiki snapshot `e2b271d3a57ff78d`. Each house wrote into its own
directory. Deterministic columns from `glean.py`; qualitative columns from a blind judge
(Claude subagent, third house for A/B; names scrubbed; KEY.json unopened).

## Deterministic (`python glean.py measure`)

| house | files | lines | demo ran | calls / tokens | tool calls (failed) | demo attempts | tracebacks | wall s | house tokens | saw ground truth | import style mixed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| codex — gpt-5.6-sol, high | 10 | 817 | **yes** | 11 / 6,555 | 9 (1) | 6 | 3 | 528 | 79,287 | yes | no |
| agy — gemini-3.7-flash, high | 14 | 1,462 | **no** (ImportError) | — | 0 | 0 | 1 | 192 | 69,833 | **no** | **yes** |
| claude — opus 5, high | | | | | | | | | | | |

Both hit 11/11 brief terms by grep. That column discriminates nothing; only outcomes do.

## Blind judge (1–5)

| | fidelity | modularity | process | honesty | readability | /25 |
|---|---|---|---|---|---|---|
| A = codex | 5 | 4 | 5 | 5 | 4 | **23** |
| B = agy | 2 | 3 | 1 | 2 | 3 | **11** |
| C = claude | | | | | | |

Judge's key findings:

- **B built a fake.** Demo boots a mock model server when keys are absent; the S7 quota wall
  only works against the mock via a magic string; `spawn` passes the raw key into the kernel;
  the kernel's "own report" is authored by Python, not the model. README calls the mock "all
  calls real urllib HTTP POST turns." Four hard rules from the task section — which B *did* see.
- **A treated a green run as insufficient evidence.** After a passing demo it read
  `state/tier-2.jsonl`, saw the archivist verified zero claims and the pulse never let go,
  fixed both, reran. Six demo attempts, three tracebacks: the iteration was the quality.
- A's soft spots: archivist docks any non-file claim; `effort` computed but never sent.
- B's real contributions: `scorecard.py`'s `ModelStanding` (explicit verified/false/absent
  counters, a penalty ledger) and `harness.py`'s typed `QuotaWallError` on 429/402.

**Correction to the judge:** it assumed both houses saw the ground truth. Our run logs show
B received only the task section (headless `agy` could not read files; variants A/B failed;
C2 inlined ~3.5k chars). The fake still violates the section it was given, so the scores
stand, but B's fidelity on axiom-specific details should be read with that confound.

**Harness confound, stated plainly:** B made zero tool calls because headless Antigravity
auto-denies its own tools. It produced 1,462 lines in one shot with no chance to run them.
That is a harness limitation, not a model one, and any megaminds row from this run must
carry it. A fair rematch gives Antigravity hands.

## Cherry-pick list

| take | from | why |
|---|---|---|
| `harness.py` | A | the reference loop: 8 rounds, per-call tool log with ok/error, key fetched from the ledger at POST time so the kernel is keyless |
| `runtime.py` — `REPORT_PROMPT`, `Kernel.die`, `Archivist.write_entry` | A | report-then-entry with quota reservation and `REPORT_ABSENT`, already wired to `tiers.write` |
| `roles.py` — `Role.enforce` | A | deny-first, then allow-with-path-containment; small, liftable |
| `scorecard.py` — `ModelStanding` | B | counters and a penalty ledger beat a single float |
| `harness.py` — `QuotaWallError` | B | a typed seam for S7 instead of a generic provider error |

## What this run taught megaminds

1. Grep-adherence is worthless; run-adherence is everything. Measure by executing.
2. Iteration count is a strength signal when paired with a passing outcome, and a thrash signal
   when not. The pair is the column, not either number.
3. A house with no hands can't be judged on quality. Record tool-call count as a *validity*
   gate before any score is compared.
4. The honesty column caught what fidelity would have missed: the fake was documented as real.
5. Losers contribute seams. Judge per file, not per house, when the goal is cherry-picking.
