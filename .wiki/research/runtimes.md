# Research: Rust agent runtimes

[Back to index](../README.md)

Survey of Rust projects that could replace or inform pi-durable as Arity's durable agent runtime, if Arity is written in Rust. Arity is now a Rust core with its own journal; see [Agents](../agent-tiers.md#one-mechanism-for-everyone).

All sources were read from shallow clones on **2026-11-20**. Nothing was built or run. Stars, dates and commit counts come from the GitHub and crates.io APIs that day and are a rough signal only. Anything not confirmed from source is marked **Unverified**.

## Summary

1. **Durable agent runtimes are common now, and none is ready to adopt.** Every candidate is either one person's fast-moving project, server-shaped, or missing something Arity needs at its core.
2. **None ports pi-durable.** The Rust ports of pi cover the coding agent (providers, loop, sessions) but not durable state or waking.
3. **The useful parts are designs to copy:** per-step transactional checkpoints and leases (io-harness), an append-only event log with fork at any event (exo), a journal plus a board of tasks with claims and leases (nanocode), and ACP over Claude Code, Codex and Cursor (goose).
4. **Two blockers recur.** Several runtimes refuse local endpoints by default, which rules out MLX until worked around. Several bind one provider to a whole tree of agents, which breaks a blind pick per subagent.
5. **Anthropic's terms rule out one shortcut.** Two projects reuse Claude Code's OAuth client to use a Claude subscription. Arity must not; Claude Max runs through Claude Code itself.

| Project | Licence | Shape | Durable | Fork | Wake | ACP | Local endpoint | Verdict |
|---|---|---|---|---|---|---|---|---|
| [io-harness](#io-harness) | Apache-2.0 | Library | Yes, per step | Yes | Approvals only | No | Off by default | Reference |
| [exo](#exo) | MIT | Server + Node harnesses | Yes, event log | Yes | Yes | No | Refused | Reference |
| [agent-abstraction](#agent-abstraction) | MIT | Library over vendor CLIs | Session ids only | Claude, Grok | No | Grok only | n/a | Reference |
| [nanocode](#nanocode) | MIT or Apache-2.0 | Library, ~145 crates | Yes, journal | Yes | **Unverified** | No | Loopback only | Spike first |
| [goose](#goose) | Apache-2.0 | Application | SQLite sessions | Yes | No | Client and server | Yes | Reference |
| [pi_agent_rust](#pi_agent_rust) | MIT with rider | CLI + SDK | Sessions | Yes | No | Yes | Yes | Reference |
| [pi-rs, nktkt/pi](#other-pi-ports) | MIT | Crates | Sessions | Partly | No | No | Yes | Skip |

## io-harness

[initorigin/io-harness](https://github.com/initorigin/io-harness), crate `io-harness`.

### Facts

| | |
|---|---|
| Maintainer | One person: 1,378 of about 1,400 commits, every release, all recent issues self-filed |
| Licence | Apache-2.0 ([LICENSE](https://github.com/initorigin/io-harness/blob/main/LICENSE)) |
| Activity | 95 versions from 0.1.0 (2026-07-23) to 0.86.1 (2026-11-07), often several a day in August and September. 7 stars |
| Maturity | "pre-1.0 and stays pre-1.0"; minor releases may break ([README](https://github.com/initorigin/io-harness/blob/main/README.md)). About 125k lines, Rust 1.95 |
| Depends on | tokio, reqwest, rusqlite (bundled), rmcp |

### How it works

- **One loop** in `src/run.rs`, used two ways: a one-shot run against a task contract, or a `Session` where each turn is its own run ([session.rs](https://github.com/initorigin/io-harness/blob/main/src/session.rs)).
- **Durability:** one SQLite file of about 40 tables. Each step's trace, budget draw and checkpoint commit in one transaction. Resume skips completed steps, adopts already-spawned children and restores the budget. A lease stops two processes driving one run. A tool call whose effect cannot be confirmed pauses the resume instead of repeating ([durable-runs guide](https://github.com/initorigin/io-harness/blob/main/docs/guide/durable-runs.md)).
- **Steering** (`say`, `interrupt`, `fold`) applies at the next step, for the root agent only. **Fork** is `Session::branch_from(turn_id)` on an append-only tree.
- **Waking** covers parked approvals and questions only. There is no scheduler; waking on a board event is the host's job.
- **Subagents** share one budget and a mailbox within one tree. There is no messaging between trees.
- **Permissions and sandbox:** a deny-first policy over read, write, exec and network, an approver, and an OS sandbox (Seatbelt on macOS).
- **Providers:** OpenAI-compatible chat and Anthropic Messages, streaming, tool calls. No Responses API and no Codex OAuth. Anthropic's URL is fixed. Loopback addresses are refused unless `IO_HARNESS_ALLOW_LOCAL_ADDRESSES=1` ([providers guide](https://github.com/initorigin/io-harness/blob/main/docs/guide/providers.md)).

### Fit

- **One provider per tree.** "There is no per-agent provider and no per-agent API key" ([agency guide](https://github.com/initorigin/io-harness/blob/main/docs/guide/agency.md)). A blind pick per subagent would need a custom provider that routes on the model string. **Unverified** that this works cleanly.
- **Storage is a concrete SQLite connection**, with no trait, so Chronicle cannot back it.
- **Built around a coding workspace:** a session requires a root directory.

**Verdict: reference.** Its durability design is the best of the group to copy: step transactions, leases, an append-only session tree, parked approvals, and pausing on unconfirmed effects.

## exo

[exoharness/exo](https://github.com/exoharness/exo). The link sometimes cited, `Alexsun1one/exo`, is a stale fork.

### Facts

| | |
|---|---|
| Maintainer | A small team from Braintrust and Berkeley, with a paper |
| Licence | MIT ([LICENSE](https://github.com/exoharness/exo/blob/main/LICENSE)) |
| Activity | Created 2026-05-20, last commit 2026-11-18, about 1.7k stars |
| Maturity | No releases or tags, not on crates.io. The README says "Under construction" |

### How it works

- **A hosted runtime,** run with `exo run agent.md` or over HTTP. Each thread is an append-only event log with UUIDv7 ids; clients watch and resume by event cursor ([data model](https://github.com/exoharness/exo/blob/main/website/docs-src/architecture/data-model.md)).
- **Fork** at the latest event or any earlier one. This copies history, not the sandbox's files; sandbox snapshots are separate.
- **Harnesses** (Codex, Claude Code, pi, Cursor) are TypeScript modules on Node. After a restart only Codex turns recover; Claude Code and pi turns end as failed ([harnesses](https://github.com/exoharness/exo/blob/main/website/docs-src/architecture/harnesses.md)).
- **Waking:** a scheduler and a conversation wakeup module.
- **Storage** is a byte-store trait over `object_store`.
- **Model endpoints** must be OpenAI, Anthropic or Gemini over HTTPS on port 443, so a local MLX server is refused ([vault.rs](https://github.com/exoharness/exo/blob/main/crates/exoharness/src/vault.rs#L205)).

**Verdict: reference.** The event log with fork at any event is the cleanest data model of the group. The project itself needs Node, is server-shaped and refuses local models.

## agent-abstraction

[pathscale/RustAgentAbstraction](https://github.com/pathscale/RustAgentAbstraction), crate `agent-abstraction`.

### Facts

| | |
|---|---|
| Maintainer | One account, 98 commits. Built for the author's own Tauri app |
| Licence | MIT ([LICENSE](https://github.com/pathscale/RustAgentAbstraction/blob/main/LICENSE)) |
| Activity | 0.4 to 0.6.0, 2026-07-28 to 2026-11-16. 1 star |

### How it works

- **Drives vendor CLIs as subprocesses:** Claude Code, Codex, Copilot and Grok, normalised into one request type and one event stream, with per-CLI model catalogues and version checks ([CHANGELOG](https://github.com/pathscale/RustAgentAbstraction/blob/main/CHANGELOG.md)).
- **The session store holds no transcripts,** only a JSON file mapping a caller's name to the CLI's own session id ([session.rs](https://github.com/pathscale/RustAgentAbstraction/blob/main/src/session.rs)).
- **Resume** works for all four. **Fork** works for Claude (`--fork-session`) and Grok; Codex and Copilot return `Unsupported`.
- **ACP** is used for Grok only.

**Verdict: reference.** A well-documented catalogue of CLI flags and quirks for driving Claude Code, which is how Claude Max is used legitimately.

## nanocode

[nanocodexyz/nanocode](https://github.com/nanocodexyz/nanocode), formerly `gakonst/nanocodex`.

### Facts

| | |
|---|---|
| Maintainer | Overwhelmingly one person: 7,279 commits, next contributor 94 |
| Licence | MIT or Apache-2.0 ([LICENSE-MIT](https://github.com/nanocodexyz/nanocode/blob/main/LICENSE-MIT)) |
| Activity | First commit about 2026-07-16, last 2026-11-20. About 735 stars |
| Maturity | About 145 crates. The crates.io releases stopped at 0.6.x in early October; the README says to depend on git |

### How it works

- **A library-first agent SDK** with its own Codex and Claude harnesses that call model APIs directly ([crates README](https://github.com/nanocodexyz/nanocode/blob/main/crates/README.md)).
- **Durability:** every model call and tool call is journaled. Storage is behind traits (`StateStore`, `BlobStore`, `KeyValue`, `Sql`) with JSONL, SQL and S3 stores. `CheckpointBranch` forks; subagents are durable.
- **A board:** a shared task list built from an event log, with tasks, claims, leases and steers ([board](https://github.com/nanocodexyz/nanocode/blob/main/docs/board.md)). Close to Arity's [board](../work.md).
- **Providers:** the Responses API with ChatGPT subscription OAuth; a chat-completions gateway whose base URL can be overridden to loopback only (**unverified** that the public API exposes this, which MLX needs); Anthropic Messages with an API key.
- **Claude subscription path:** it reads Claude Code's keychain credentials and reuses Claude Code's OAuth client ([claude_code.rs](https://github.com/nanocodexyz/nanocode/blob/main/crates/backends/claude/client/src/claude_code.rs)). Anthropic's terms forbid this, so Arity must not use it.
- Built around its own code-execution model on QuickJS. No ACP and no CLI driving.

**Verdict: spike first,** for local models, API keys and ChatGPT, with the Claude subscription path left out. Otherwise a strong reference for the journal and the board.

## goose

[aaif-goose/goose](https://github.com/aaif-goose/goose), for comparison.

### Facts

| | |
|---|---|
| Maintainer | Many contributors, about 56.8k stars |
| Licence | Apache-2.0 |
| Activity | v1.60.2 on 2026-11-20, releases every few days |
| Maturity | Not on crates.io (the `goose` crate there is unrelated) |

### How it works

- **Sessions** are rows in SQLite through a concrete session manager, with copy (fork), truncate (rewind), import and export ([session_manager.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/session/session_manager.rs)).
- **ACP server** with load and fork ([acp/server.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/acp/server.rs)).
- **ACP client:** providers `claude_acp` (through claude-agent-acp), `codex_acp`, `copilot_acp`, `cursor_agent` and `pi_acp`, plus `chatgpt_codex`, OpenAI, OpenRouter, Ollama and local inference ([providers](https://github.com/aaif-goose/goose/tree/main/crates/goose/src/providers)).

**Verdict: reference.** The best working example of driving Claude Code, Codex and Cursor over ACP, and of being an ACP server. Too application-shaped to embed.

## Rust ports of pi

### pi_agent_rust

[Dicklesworthstone/pi_agent_rust](https://github.com/Dicklesworthstone/pi_agent_rust), crate `pi_agent_rust`.

| | |
|---|---|
| Maintainer | One person: 6,130 commits. About 2k stars |
| Licence | "MIT License (with OpenAI/Anthropic Rider)": grants no rights to OpenAI, Anthropic or anyone acting for them, and forbids use in an "evaluation harness" ([LICENSE](https://github.com/Dicklesworthstone/pi_agent_rust/blob/main/LICENSE)) |
| Activity | v0.1.0 on 2026-02-17 to 0.8.0 on 2026-10-14 |
| Maturity | About 1.09M lines of source. Pinned nightly Rust. Its own async runtime and SQLite implementation instead of tokio and SQLite |

It is the only port with real Claude and Codex OAuth logins, steering and follow-ups, session forks, an RPC mode and ACP ([auth.rs](https://github.com/Dicklesworthstone/pi_agent_rust/blob/main/src/auth.rs)). The README says it aims for a coherent Rust agent, "not parity with pi-mono". It has no pi-durable equivalent.

**Verdict: reference.** Useful as a map of provider endpoints and OAuth flows. Not a dependency: the licence rider, the size and the custom runtime each rule it out.

### Other pi ports

- **[qhkm/pi-rs](https://github.com/qhkm/pi-rs)**, MIT. A clean tokio workspace of about 47k lines, dormant since 2026-03-05 and never published. Its Codex OAuth uses an invented client id and endpoints and would not work. **Skip.**
- **[nktkt/pi](https://github.com/nktkt/pi)**, MIT, the source of crates `pi-ai`, `pi-agent` and `pi-coding-agent` 1.0.0. About 5.3k lines written in one day, 2026-05-12. Providers and session files only. Its crates.io metadata points at the upstream pi repo. **Skip.**

## What a Rust Arity would write itself

Combining the above, a Rust Arity would adopt crates for the plumbing (tokio, reqwest, rusqlite or Chronicle, the `agent-client-protocol` crate) and write:

- **A model client** for OpenAI-compatible chat (MLX, Nous Portal, OpenRouter), Anthropic Messages, and the Responses API with ChatGPT OAuth.
- **The agent loop and core tools.**
- **Durability:** a per-step journal with fork at any entry, leases and pausing on unconfirmed effects, following io-harness and exo.
- **The board,** following nanocode's event-log design.
- **An ACP client** for Claude Code, Codex and Cursor, following goose.
