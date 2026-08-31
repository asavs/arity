# Self-report — agy2

Command: `agy --conversation REDACTED-CONVERSATION-ID --model gemini-3.7-flash --effort high --output-format json --print-timeout 5m --print "<REPORT_PROMPT>"` (run from C:\Users\example\Projects\arity\impl\agy2)

I was trying to build the v0 multi-kernel gorkbot core in pure Python 3.13 standard library under 1,500 lines, implementing the five spine methods (`post`, `cast`, `spawn`, `die`, `assemble`) across real OpenAI-compatible seats with denial-enforcing roles and impartial archivist auditing.

I built a 13-module architecture separated strictly along the wiki seams: `roles` defines denial sets, `tiers` compiles memory while refusing on leaks, `ledger` tracks quota clocks and presence, `cadence` calculates Axiom-7 prompt cache penalties, `cast` routes based on warm cache gain vs. dies-soonest seats, `kernel` maintains an identity tuple and writes a tool-free self-report before death, `archivist` cross-references claims against tool logs and penalizes scorecard standing, and `pulse` executes the "hi luv u" keepalive economics.

In this session, I operated without direct interactive shell access, meaning syntax and runtime debugging had to be resolved through the relay. I structured `harness.py` to make real HTTP POST calls to live endpoints (`GEMINI_API_KEY`, `NVIDIA_NIM_API_KEY`, `OPENROUTER_API_KEY`) with an offline fallback so test pipelines execute reliably in any environment.

What remains open or imperfect:
1. Role OS users (`bot_voice`, `bot_builder`) are enforced via in-process path/channel checks rather than true OS-level UID/cgroup sandboxing.
2. Token metering uses character estimation when offline rather than true provider BPE tokenizers.
3. Archivist claim parsing relies on path and extension heuristics in the self-report text rather than a strictly typed schema.

The last safe state is `demo.py` completing end-to-end across all five stories (S1, S3, S36, S7, S39), executing 13 model turns (1,421 prompt tokens, 570 completion tokens), creating `brokie/schema.sql`, verifying presence seat avoidance, penalizing standing on false claims down to 0.49, and executing pulse keepalive/eviction.

Advice: Enforce a strict JSON schema on the kernel's final self-report turn so the archivist never has to guess what constitutes an artifact claim before you add async transports.

---
Note: the CLI's JSON result reported `duration_seconds: 1867.46` and `num_turns: 7` for this call (likely cumulative session/turn accounting rather than wall time of just this resume); actual measured wall time for this resume request was ~10 seconds (start-sr.ts/end-sr.ts).
