# Dogfood scratchpad — Act 1 secretary → python-developer on ParFold

Command: `ARITY_HOME=../arity-dogfood-home python -m arity.main "I'm worried about the ParFold in seams.py — have python-developer take a look?"`
Worktree: arity-dogfood-python @ dogfood-python. Main tree untouched.

## Observed — Act 1 run 1
- GOOD: reception delegated via message tool to python-developer (routing works).
- BAD #1: `cast.birth(python-developer)` crashed: `FileNotFoundError .../library/tools/search.json`.
  Cause: sandbox home seeded from `arity/seeds` lacks `search` tool, but `arity add bot --tools read_file,bash,search` accepted it unchecked.
  Bug class: `add bot` never validates tool names against library; `cast.resolve` assumes schemas exist → raw traceback, exit 1.
- BAD #2: raw traceback to user (no clean `(no answer…)` / doctor-style hint). Suggest: validate at `add` time + fail soft at `resolve` time listing missing tool.
- Cost: 29.6s wall (reception model call + failed wake + retire death-rites) before the crash surfaced.
- reception may answer directly instead of delegating via message tool
- python-developer may opine without read_file
- Warmer `(kept the cache warm)` interleaving on stdout
- retire death-rites = 2 extra model calls per bot, archivist = same model
- hang past answer (retire_all / Warmer non-daemon?) — bare --help needed timeout kill (exit 124)

## Observed — Act 1 run 2 (post-006e0c8, help restored, ParFold original)
- exit 0, 76.3s wall. No crash: linker/sources + search-in-seeds fixed run-1 gap.
- GOOD: reception → message tool → python-developer wake (parent session linked). Journal: reception `b66b9117` birth → ModelCompleted with `message` call → python-dev `41405585` birth (spec openrouter-free/minimax-m2.7, tools read_file,bash,search).
- GOOD: python-developer used tools before opining — 3× read_file + ExecuteTool events in `41405585.jsonl`. Role discipline held.
- Verdict quality (mixed):
  - #1 (`__call__` vs seam protocol, proposes hasattr(r,"call") in `__call__`) — half-right, fix wrong: generic path can't prefer `.call` without biasing toward ModelSeam and breaking ToolSeam callables. Real asymmetry exists but proposal misfires.
  - #2 (`schemas()` not delegated) — legitimate catch, real ToolSeam gap.
  - #3 (no Store/Transport/Observer fan-out) — true, but docstring overclaims ("works behind any seam"); catch is against the docstring, not usage.
  - #4 (validate `reduce` output) — generic fluff, no failing case named.
- BAD: closer asks "Should I ask engineer to apply this fix?" — wrong bot (engineer=typescript-developer) for a Python file python-developer itself could patch with bash. Routing home, role confusion abroad.
- Cost: 76s for one review (2 model turns + death-rites per bot). Death-rites/archivist traffic unmeasured but included.

## Observed — Act 2 (vague ask, no named bot)
- Prompt: `would you take a look at ParFold?` — exit 0, 55s.
- Routing: reception → python-developer directly (`17a7f0c2` → `6c814f1a`). Engineer never woken. Vague ask did NOT route via engineer; scorecard/cast picked python-dev (only Python-role on staff — no competition).
- Findings: identical to run 2 (#1 wrong `__call__` fix, #2 `schemas()`). Same seat/model presumably; deterministic pattern, not fresh analysis.
- Closer again defers to engineer ("hand this off to engineer to implement") despite python-dev holding `bash` and the file. Pattern, not accident: reviewers propose, never apply.
- Implication for undecided flow: engineer-as-coordinator never triggered because cast resolves the most apt role directly. To force engineer-in-the-middle, reception prompt or staff wiring must prefer it (or python-dev must be hidden from reception's roster).

## TODO (stubbed, not implemented)
- Scoped rosters: per-bot `team: [...]` in `bots.json`; `linker.link()` renders visible team per bot instead of global `", ".join(bots())`. Reception sees `[engineer]`; engineer sees `[python-developer, dijkstra, hickey]`; specialists see `[engineer]` + shared leaves. Decided: 3 tiers (router → coordinator → specialists + writer/reviewer leaves).

## Observed — Act 3 (arity 3 trial, python-dev writes ParFold diff)
- Prompt: `3 "@python-developer read the ParFold class in arity/seams.py and write your best single improvement as a unified diff"` — exit 0, 311s, nothing kept.
- Fork [1] mistral-7b/NIM: HTTP 404 model-not-found. Seats table lists dead models; trial burns a fork discovering it. No health check, no exclusion after 404.
- Fork [2] deepseek-v4-flash/NIM: transport read timeout. Transient, counted as full fork failure.
- Fork [3] nemotron-3.5-lightning/openrouter: `stopped after 40 model calls`. Journal (`6f91a74e-40d6`): 40 empty-text + tool_calls turns — read_file ×3 then repeated `bash ls /c/Users/...` (wrong WSL-style path on win32 → "system cannot find the path specified") until cap. Weak-model tool spin: empty text + tool call never converges, each ToolCompleted re-arms call_model unconditionally.
- Candidates for fix list: (a) mark model unusable after 404 (skip / zero quota / doctor flag); (b) convergence guard — cap consecutive empty-text tool-only turns, force final text; (c) trial prompt/cwd grounding (fork runs with repo cwd but model assumed WSL paths).
- Scorecard: outcomes recorded (0.0/false) — correctly learned nothing.

## Observed — Act 3 retry (same prompt)
- exit 0, 134s. Forks [1] mistral 404 and [2] deepseek timeout: deterministic, not luck — `candidates()` sorts by seat reset urgency, same order every run.
- Fork [3] drew nemotron-3-super-120b (exploration slot rotated): produced unified diff of the known-wrong `__call__` fix (prefer `.call` in generic path). Same finding as Acts 1–2, now as a diff. Consistent reviewer, consistently half-right.
- No winner (non-interactive stdin → pick None → nothing kept). Scorecard rows: losses recorded, no wins.
- Lesson: retries don't fix the pool. Dead-model exclusion (fix a) would have converted 2 dead forks into either fewer forks or different models.
