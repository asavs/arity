# arity v0

One voice, a staff, and a door to each — small and real. Python 3.13, stdlib only, 1,497 lines
across 11 modules. Nothing is mocked: every turn is a POST to a provider you have a key for, and
every file the builder writes is a real file.

```
set GEMINI_API_KEY=...        # and/or NVIDIA_NIM_API_KEY, OPENROUTER_API_KEY
python demo.py
```

Seats come from whichever of those exist; the kernel never sees a key (the proxy in `ledger.py`
injects it). Output goes to stdout and `run.log`; artifacts to `run/` — `workspace/` is what the
builder built, `tiers/` is the journal, `kernels/` holds each dead kernel's evidence envelope.
`ARITY_SEAT_BUDGET` caps tokens per seat (default 400000). The demo plays S1 (DM → handoff →
builder writes `brokie/schema.sql` → archivist verifies), S3 (sporadic cadence keeps the warm
kernel), S36 (presence pushes a fresh cast elsewhere), S7 (a death with a report, then a starved
seat so the report is genuinely ABSENT) and S39 (keepalive, then let go), and prints totals.

## File map

- `roles.py` — denial sets and `enforce`; channel permission is per message *kind*
- `ledger.py` — seats from the env, quota, presence, the axiom 7 table, `cold_cost`
- `tiers.py` — the store seam plus the brief compiler that refuses rather than leak
- `clock.py` — cadence and pulse: predicting the gap, then keepalive-or-die
- `harness.py` — the real POST loop and the tools a role is allowed
- `kernel.py` — identity tuple, `turn`, and dying properly
- `archivist.py` — claims checked against tool log and disk; the prose is one real turn
- `redphone.py` — channels, DMs, handoff records, escalation
- `cast.py` — the composer and the scorecard, plus `Core` holding everything
- `demo.py` — the five stories

## Three honest limitations

1. **Denial is by construction, not by the OS.** A leaf is confined by relative-path resolution
   and a substring scan, and never holds a key because we don't hand it one. It should be a user
   that cannot see the home directory at all.
2. **The quota wall in S7 is our ledger's, not a provider's.** Real metered tokens exhaust a
   deliberately small seat and the report is genuinely absent — but we chose the budget.
3. **`probe` mostly guesses,** and presence is set by hand rather than learned from session
   files. Every seat row is a guess with a confidence, and none of them are high.
