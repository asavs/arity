# RELAY.md — agy2 rematch log

Relay for a rematch of the arity A/B/C implementation brief against agy (Antigravity CLI,
gemini-3.7-flash, effort high). This relay only transcribed and executed; it never wrote or
fixed implementation code.

## Brief delivery (3 parts, all acked)

| Turn | Sent (chars) | Response | Usage total_tokens | Wall (s) |
|---|---|---|---|---|
| turn-1 (`--new-project`, Part 1/3) | 25,069 | "ok 1/3 received" | 21,506 | 2.22 |
| turn-2 (`--continue`, Part 2/3) | 25,011 | "ok 2/3 received" | 30,880 | 18.36 |
| turn-3 (`--continue`, Part 3/3) | 17,681 | "ok 3/3 received" | 41,793 | 30.63 |

All three parts were acknowledged correctly; no re-sends needed.

## Implementation rounds

| Round | Sent (chars) | Files changed | Response tokens (usage.output_tokens) | Syntax | Demo result | Wall (s) |
|---|---|---|---|---|---|---|
| 1 (BEGIN) | 264 | 15 files (all modules + demo.py + README.md + __init__.py) | 31,371 | syntax ok | Ran through S1, S2(S3), started S3(S36) — crashed with `AssertionError` at demo.py:92 (`assert fresh_builder_k.seat.id != seat_to_block.id or not seat_to_block.presence`) inside S36 presence-avoidance check | 126.77 |
| 2 (feedback round 1) | 2,072 | 6 files (ledger.py, archivist.py, demo.py, kernel.py, store.py, redphone.py) | 44,230 | syntax ok | Ran to completion, all 5 stories (S1, S3, S36, S7, S39), printed final line: `Total Model Calls: 13 / Total Prompt Tokens: 1421 / Total Completion Tokens: 570` | 201.47 |
| 3 (feedback round 2, confirmation) | 2,411 | 0 (confirmation only) | 44,591 | — | agy replied exactly `DONE` | 263.11 |

Demo used 2 rounds of fixes (round 1 crashed, round 2 fixed and ran clean end to end); round 3
was the confirmation turn per protocol step 4, which agy answered with `DONE` after the clean
round-2 run was reported back to it.

## Deviations from protocol / notes

- No part of the brief delivery needed re-sending — all three chunk acks matched expected format.
- Round 1's crash was a single AssertionError in the S36 (seat presence) story; agy's round-2
  fix touched 6 of 15 files (not just demo.py) — it revised ledger.py, archivist.py, kernel.py,
  store.py, and redphone.py alongside demo.py to make the fix consistent, which the protocol
  permits ("return ONLY the files that need to change").
- File writes were done by extracting `=== FILE: <name> ===` blocks from the JSON `response`
  field with a small Python script and writing byte-for-byte (no reformatting, no import fixes).
- Everything else followed the stated protocol exactly (same flags every call, `--print` last,
  `--continue` for all turns after `--new-project`, 20-minute print-timeout, foreground Bash
  calls with 600000ms timeout, no `--dangerously-skip-permissions`).

## Totals

- Rounds used: 3 total implementation-related agy calls after brief delivery (1 BEGIN + 2
  feedback/fix rounds), well under the 6-round cap.
- Final demo result: exit 0, all 5 stories completed, final line:
  `Total Model Calls: 13 / Total Prompt Tokens: 1421 / Total Completion Tokens: 570`
- Sum of `usage.total_tokens` across all 6 agy calls (turn-1..3, round-1..3): 428,900
- Sum of `duration_seconds` across all 6 agy calls: ~642.6s (~10.7 min) of agy call time
  (plus local syntax checks and two demo runs, negligible wall time)
