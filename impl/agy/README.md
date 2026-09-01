# arity v0

A modular multi-model kernel coordination system in standard library Python 3.13.

## Quickstart
```bash
python demo.py
```
To use live provider seats, export any of:
- `GEMINI_API_KEY` (Gemini Flash & Flash-Lite endpoints)
- `NVIDIA_NIM_API_KEY` (NVIDIA NIM endpoint)
- `OPENROUTER_API_KEY` (OpenRouter endpoint)

If no keys are exported, `demo.py` boots a local OpenAI-compatible HTTP daemon so all calls remain real `urllib` HTTP POST turns.

## File Map
- `store.py`: Channel messages, tool audit logs, workspace disk store.
- `ledger.py`: Seat registry, Axiom-7 cache metrics, human presence flags.
- `roles.py`: Denial sets (tools, channels, paths, names, hosts).
- `tiers.py`: Brief compilation with refusal on denied entity leakage.
- `cadence.py`: Inter-arrival timing and Bayesian return probability p(return).
- `scorecard.py`: Standing registry, audit verification, penalty debits.
- `harness.py`: Real `urllib` POST `/chat/completions` runner with tool loops.
- `kernel.py`: Kernel state, identity tuples, tool dispatch, self-reports.
- `archivist.py`: Impartial auditing of self-reports against tool logs.
- `redphone.py`: Cross-channel bounded handoffs (depth & token budgets).
- `cast.py`: Dynamic seat allocation avoiding human presence.
- `pulse.py`: Keepalive economic pulse (p(return) * cold > ping).
- `demo.py`: Executable demonstration of stories S1, S3, S36, S7, S39.

## Three Honest Limitations
1. In-memory message channels and state do not survive process restarts.
2. Single-process concurrency; multi-node coordination requires external transport.
3. Tool execution is synchronous within a turn loop; long-running shell jobs block the harness.
