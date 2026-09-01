# Self-report — codex

Command: `codex exec resume --last --skip-git-repo-check "<REPORT_PROMPT>"` (run from C:\Users\example\Projects\arity\impl\codex; session id REDACTED-SESSION-ID, model gpt-5.6-sol)

I was implementing a standard-library-only arity v0 with real OpenAI-compatible model calls and the required S1/S3/S36/S7/S39 lifecycle stories. I built 10 small modules—storage/memory, roles/red phone, ledger/casting, harness/runtime, and demo—so the seams can be cherry-picked independently; the final demo used Gemini and NVIDIA NIM, made 11 model calls, consumed 6,683 reported tokens, and wrote `workspace/brokie/schema.sql`.

Sandboxed networking initially failed, so I reran with approved network access. I could not provide OS-user isolation, a separate credential-proxy process, real quota/presence probes, or a separately cast model archivist; those remain explicitly documented limitations. The archivist is deterministic, checks file claims only against successful tool logs, and does not inspect diffs or tests. NIM cache behavior is treated as unverified/zero-window, and provider clocks are seeded estimates.

The last thing I know is safe is the final successful `python demo.py` run: all five story assertions passed, compilation passed, and denied-name brief leakage was refused. My advice is to preserve the existing seams while replacing the in-process security and synthetic ledger observations first; don't expand the feature surface until those boundaries are real.
