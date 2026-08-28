# arity v0

A small standard-library implementation of the continuity/composition core.

## Run

Use Python 3.13 and export at least two supported provider keys. The five-story demo expects
`GEMINI_API_KEY` and `NVIDIA_NIM_API_KEY`, then makes real OpenAI-compatible requests:

```powershell
python demo.py
```

The builder writes `workspace/brokie/schema.sql`; channel, tier, and scorecard records append
under `state/`. Provider credentials stay in the ledger's private HTTP boundary and never enter
a kernel, prompt, tool log, or identity tuple.

## File map

- `store.py`, `memory.py` — rooted storage, tier compiler, typed memory records
- `roles.py`, `redphone.py` — denial sets, channels, bounded/returnable handoffs
- `ledger.py`, `casting.py` — seats, axiom-7 cache data, cadence, standing, cast, pulse
- `harness.py`, `runtime.py` — real chat/tool loop, kernel identity/lifecycle, two accounts
- `demo.py` — S1, S3, S36, S7, and S39 as one executable log

## Honest limitations

- Presence, quota, reset, and expiry are v0 ledger fields; no provider probing exists yet.
- Role enforcement is in-process, not an OS user, network sandbox, or credential proxy process.
- The archivist is deterministic and checks file claims against tool evidence; it is not a
  separately cast model and does not yet inspect diffs or test artifacts.
