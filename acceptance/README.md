# Installed resolution acceptance

Run this gate from a development environment that has `build` installed:

```bash
python acceptance/verify_installed.py
```

The wrapper archives the exact committed `HEAD`, builds its wheel, creates a fresh virtual
environment, and installs with `--no-index --no-deps`. It runs `resolution_envelope.py` under
isolated Python mode, then invokes the installed `arity watch --ascii --no-motion` console
script from an empty directory. The watch gate requires exact ANSI-free ASCII output and proves
that observing a missing store creates no `.gorkbot/` state. The resolution scenario uses only
the installed distribution and standard library. It proves a two-arm factual tie,
evaluator-controlled treatment delivery from frozen bytes, offline re-evaluation after
workspace deletion, and SQLite close/reopen replay.

Because the wrapper deliberately archives `HEAD`, commit acceptance changes before invoking it.
