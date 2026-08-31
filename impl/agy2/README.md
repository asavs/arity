# Arity v0

Axiomatic, multi-kernel assistant core in Python 3.13 standard library.

## How to Run

```bash
python demo.py
```

Runs the 5 core stories (S1, S3, S36, S7, S39) against configured provider seats or deterministic fallback.

## File Map

- `store.py`: In-memory records and sandboxed workspace files.
- `ledger.py`: Seat accounts, quota clocks, presence flags, and reservations.
- `roles.py`: Role definitions, aptitude desires, and strict denial sets.
- `tiers.py`: Memory compiler with leak scanner preventing denied path/name exposure.
- `cadence.py`: Axiom-7 prompt cache economics, inter-message prediction, cold cost math.
- `scorecard.py`: Evidence store, aptitude ranking, and standing penalties.
- `harness.py`: Real HTTP tool-calling loop (`POST /chat/completions`) with tool executor.
- `kernel.py`: Single model runtime, identity tuple, self-report turn, and death trace.
- `archivist.py`: Impartial auditor verifying claims against tool logs and disk.
- `redphone.py`: Public/private channels, DMs, bounded handoffs, and voice brief.
- `cast.py`: Per-prompt routing across warm cache, aptitude, presence, and expiration.
- `pulse.py`: Heartbeat keepalive (`hi luv u`) and cold eviction.
- `demo.py`: Executable demonstration of stories S1, S3, S36, S7, and S39.

## Three Honest Limitations

1. **Static Cadence Heuristics**: Predicted gap uses empirical median without multi-day circadian modeling.
2. **Synchronous Transports**: Standard library HTTP requests are blocking per turn rather than asynchronous event loops.
3. **Local Filesystem Sandboxing**: Denial sets enforce path barriers at compiler and runtime levels without full OS container isolation.

