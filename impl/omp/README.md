# arity v0

A small standard-library implementation of the continuity/composition core.

## Run

Requires Python 3.13 and at least two provider keys (`GEMINI_API_KEY`, `NVIDIA_NIM_API_KEY`):

```bash
python demo.py
```

The builder writes `workspace/brokie/schema.sql`; channel, tier, and scorecard records append under `state/`. Provider credentials remain isolated in the ledger's private HTTP boundary and never enter a kernel, prompt, or identity tuple.

## File map

- `store.py` — rooted workspace and JSON lines storage
- `cadence.py` — inter-message gap prediction and empirical arrival probabilities
- `scorecard.py` — model scorecard, claim counters, and standing
- `ledger.py` — seats, clocks, secret boundary, and axiom-7 cache economics
- `roles.py` — denial sets (tools, channels, paths, names, hosts) and allow containment
- `tiers.py` — universal facts, memory tiers, and leak-refusing brief compiler
- `harness.py` — real OpenAI-compatible HTTP chat loop, tool executor, and quota errors
- `kernel.py` — kernel identity tuple, lifecycle, self-reporting, and trace envelopes
- `archivist.py` — impartial entry writer, artifact verification, and scorecard docking
- `redphone.py` — channels, message logs, and bounded/returnable task handoffs
- `cast.py` — per-prompt router, warm kernel reuse, and seat selection
- `pulse.py` — heartbeat scheduler and keepalive ping
- `demo.py` — S1, S3, S36, S7, and S39 executable demonstration

## Honest limitations

- Presence, quota, reset, and expiry are v0 ledger fields; no automated provider scraping exists yet.
- Role enforcement is in-process and path-scoped; it does not use an OS user or seccomp sandbox.
- The archivist checks claimed changes against tool logs and filesystem artifacts deterministically rather than through a separately cast judge model.
