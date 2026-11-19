# Research: the named third-party pieces

[Back to index](../README.md)

Survey of the pieces named for slotting into Arity: "cua driver, anima, optmem, pi-durable, computer-use". Each section says what the name refers to, how mature it is, how it works, and where it would sit in the design.

All sources were checked on **2026-11-18**. Star counts and dates come from the GitHub API on that day and are a rough signal only. Anything not confirmed from a primary source is marked **Unverified**.

## pi-durable

### What the name refers to

Not ambiguous. `@earendil-works/pi-durable`, a package in Earendil's `pi` monorepo at `packages/durable` ([repo](https://github.com/earendil-works/pi), [README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md)). It is separate from the pi coding agent CLI (`packages/coding-agent` in the same repo), which is the "pi" in Arity's harness list.

### Facts

| | |
|---|---|
| Maintainer | Earendil ([earendil-works org](https://github.com/earendil-works)) |
| Licence | MIT ([package.json](https://github.com/earendil-works/pi/blob/main/packages/durable/package.json), [README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#license)) |
| Activity | Monorepo pushed 2026-11-18, about 123k stars; releases v1.3.0 (2026-10-15) to v1.5.2 (2026-11-10) ([releases](https://github.com/earendil-works/pi/releases)) |
| Maturity | "**Experimental.** The API changes without notice between releases" ([README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md)). Cloudflare's `PiHarness` wrapper "is in beta" ([changelog](https://developers.cloudflare.com/changelog/post/2026-10-02-pi-harness/)) |
| Depends on | `@earendil-works/pi-ai` (model access), `@earendil-works/chord` (document state), pi-codemode, pi-schema, pi-telemetry ([package.json](https://github.com/earendil-works/pi/blob/main/packages/durable/package.json)). Storage is pluggable: memory, IndexedDB, SQLite (Node), JSONL, or a SQLite-backed Cloudflare Durable Object ([README, Storage](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#storage)) |

### How it works

Everything a conversation does (inputs, model turns, tool calls, the app's own state) is committed to storage before it is shown. If the process dies, reopening the storage resumes from the last checkpoint ([README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#persist-and-resume)). The core terms ([Concepts](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#concepts)):

- A **conversation** is an immutable transcript. It can be **forked** at any entry, and the fork keeps the parent's agent choices ([forks](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#more-conversations-and-forks)).
- A **task** is a durable state machine that checkpoints every step. Tasks can **sleep** until a time or a **signal**, and survive restarts while asleep ([sleeping](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#sleeping-and-waking-up)).
- **Submitting to a busy conversation** either steers (placed after the current tool round) or follows up (placed when the run answers), set by `whenBusy: "steer"` ([busy conversations](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#busy-conversations)).
- **Subagents** are conversations owned by a tool's task. Example 22 shows persistent subagents that can be spawned, steered, stopped and listed, with answers reported back, restart-safe ([subagents](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#abort-and-subagents)).
- **Hooks** include a `beforeTool` approval gate, `afterRun` work (example: run tests after edits), and `onYield` review ([hooks](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#hooks), [examples](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#examples)).
- An **execution environment** is chosen per tool call and can be "a directory per conversation or a container per conversation". pi-env provides `RemoteExecutionEnv` ([environment](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#environment)).
- **Usage and cost** are kept per conversation and per `provider/model` ([usage](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#usage-and-cost)).

On Cloudflare, `PiHarness` is a "Lifecycle capability" of the Agents SDK: "Pi Durable provides the agent harness and the Lifecycle is responsible for keeping the agent running in the Durable Object" ([docs](https://developers.cloudflare.com/agents/harnesses/pi/)). Its tables live in the object's SQLite with a `pi_` prefix. The recommended layout is "one Durable Object per conversation". An evicted object is restarted by its alarm and the run continues. Limits the docs state: alarm invocations run up to 15 minutes, so the harness hands off every 10 minutes; there is no approval step for tool calls yet; conversations cannot be deleted yet ([docs](https://developers.cloudflare.com/agents/harnesses/pi/)). The docs say nothing about price or plan. **Unverified:** whether a paid Workers plan is needed.

### Could it be the single durable harness on the Agents page?

For the **agent runtime**, it fits very closely. Most of [Agents](../agent-tiers.md) maps onto it directly:

| Arity | pi-durable |
|---|---|
| Steer and inbox delivery | `whenBusy: "steer"` and follow-ups |
| Live, cold, woken | Durable Object hibernation, alarm wake, `resume()` |
| Fresh or fork ([How work starts](../work.md)) | `createConversation` and `fork(entryId)` |
| Subagent reports up | Owned child conversations whose answer returns to the parent tool call |
| "User's eyes" gate | A task sleeping on a signal (example 39, "ask a person") |
| Tokens and cost metrics ([Blind testing](../blind-testing.md#grading)) | `pi.usage` per model |
| Gates such as tests | `afterRun` hooks |

What it cannot do is **wrap Claude Code, Codex or agy**. pi-durable calls models itself through pi-ai; it is a harness, not a supervisor of other harnesses. So "one durable harness" and "the harness is a swappable dimension of the variant" ([Blind testing](../blind-testing.md#what-is-tested)) cannot both hold as written. Two readings work:

1. **pi-durable is the substrate, not a variant.** It owns delivery, state, forks and waking for every agent. When the router picks Claude Code, Codex or agy, a pi-durable tool runs that CLI as a child process on the agent's workspace and relays messages. The "harness" variant dimension then means the inner loop only.
2. **pi-durable is one harness among four.** Durability then exists only for agents that drew pi, and the others need their own wake and steer plumbing.

Reading 1 keeps the "one small recursive mechanism" aim. It needs a bridge per CLI, which is real work (**unverified** that any such bridge exists yet).

### Where it slots

- [Agents](../agent-tiers.md#one-mechanism-for-everyone): the durable runtime behind message delivery and the agent lifecycle.
- [Instantiate](../instantiate.md) step 6 "Assemble": building a pi-durable agent (model, extensions, tools, instructions, cwd) is exactly a role's prompt plus loadout.
- [Computers](../computers.md): `ExecutionEnv` per conversation is where the "two machines" rule lives: the agent in a Durable Object, its commands on a remote workspace.

**Verdict: use, as the agent runtime**, accepting an experimental API. Pin versions. Decide between the two readings above before building.

### Alternatives

- Cloudflare Agents SDK without Pi, and Project Think ([blog](https://blog.cloudflare.com/project-think/)).
- [asoules/pi-worker](https://github.com/asoules/pi-worker): pi coding agent in a Worker with a persistent virtual filesystem. **Unverified** beyond the search listing.
- [@funtuantw/pi-agent-cf](https://libraries.io/npm/@funtuantw%2Fpi-agent-cf): deploys pi agents on Workers with Durable Objects. **Unverified** beyond the listing.
- General durable-execution engines (Temporal, Restate, Inngest) could hold the same state but bring no agent model. Not researched here.

## Anima: Anima Labs' Connectome and Chronicle

### What the name refers to

"anima" on its own is ambiguous. Candidates:

| Candidate | Evidence | Likely? |
|---|---|---|
| **Anima Labs' Connectome stack** (agent-framework, context-manager, membrane, chronicle, connectome-host) | The hint "anima labs connectome and chronicle can be the secretary and project agents backends" names it. GitHub org [anima-research](https://github.com/orgs/anima-research/repositories) | **Yes** |
| [GetAnima/anima](https://github.com/GetAnima/anima) | "Identity and memory infrastructure for AI agents", MIT, 6 stars, last push 2026-02-23 | No: small, inactive |
| [xuiltul/animaworks](https://github.com/xuiltul/animaworks) | "Organization-as-Code for autonomous AI agents", Apache-2.0, 269 stars | No, but a related idea |
| [dancinlab/anima](https://github.com/dancinlab/anima) | A "consciousness agent" engine | No |

The rest of this section is about Anima Labs.

### Facts

Anima Labs is "an independent research institute studying language models as complex entities in multi-agent environments" ([ecosystem overview](https://github.com/anima-research/ecosystem-overview)).

| Repo | What | Licence | Activity |
|---|---|---|---|
| [chronicle](https://github.com/anima-research/chronicle) | Embedded store: append-only records with causation links, copy-on-write branches, time travel, content-addressed blobs. Rust with N-API, npm `@animalabs/chronicle` 0.7.0 | MIT per README and package.json; GitHub detects no LICENSE file | Pushed 2026-11-18, 12 stars |
| [agent-framework](https://github.com/anima-research/agent-framework) | Multi-agent orchestration with pluggable modules, `@animalabs/agent-framework` 0.34.0 | MIT | Pushed 2026-11-18, 25 stars |
| [context-manager](https://github.com/anima-research/context-manager) | Separates the stored messages from the context window; compression strategies | MIT per README | Pushed 2026-11-18, 13 stars |
| [membrane](https://github.com/antra-tess/membrane) | Multi-participant, multi-provider LLM layer (Anthropic, Bedrock, OpenRouter, OpenAI-compatible, Gemini) | MIT | Pushed 2026-11-18, 11 stars |
| [connectome-host](https://github.com/anima-research/connectome-host) | Reference runtime: recipe-driven host for long-lived agents, TUI, daemon, fleets. `@animalabs/connectome-host` 0.25.0 | **None declared** (package.json licence is null, no LICENSE file) | Pushed 2026-11-18, 141 stars |

All on pre-1.0 version numbers. Licence sources: [chronicle README](https://github.com/anima-research/chronicle#license), [context-manager README](https://github.com/anima-research/context-manager#license), package.json files in each repo.

### How it works

Layering, bottom up ([ecosystem overview](https://github.com/anima-research/ecosystem-overview)): chronicle (persistence) → membrane (model access) → context-manager (memory) → agent-framework (agents and modules) → connectome-host (runtime).

- **Chronicle** keeps every record with `causedBy` links, so you can ask what a record caused. Branches are cheap forks of the whole store. Named states can be read as of any earlier sequence number ([README](https://github.com/anima-research/chronicle)).
- **Context-manager** keeps an immutable message store and a separate editable context log sent to the model. Strategies include an "autobiographical" one that compresses old messages into hierarchical summaries ([overview](https://github.com/anima-research/ecosystem-overview#context-manager)).
- **Agent-framework** has ephemeral subagents with isolated namespaced stores, undo and redo over inference turns, and abort across streaming and tool waits ([overview](https://github.com/anima-research/ecosystem-overview#connectome-agent-framework--context-manager--membrane)).
- **Connectome-host** runs "residents": named agents "who live on Discord or Zulip for months", with "memory that folds instead of truncating", warm prompt caches "at half-million-token contexts", cost and quota accounting, and import of a claude.ai conversation, a Claude Code session or a Codex rollout. It also runs fleets: a conductor supervising headless children ([README](https://github.com/anima-research/connectome-host)).
- **MCPL** ("MCP Live") is their MCP extension adding push events, context hooks and server-initiated inference ([overview](https://github.com/anima-research/ecosystem-overview#mcpl-mcp-live)). Related: [mcpl-cc-bridge](https://github.com/anima-research/mcpl-cc-bridge) hosts MCPL servers inside Claude Code, and [cc-spawner](https://github.com/anima-research/cc-spawner) spawns and reaps Claude Code sessions in tmux.

### Could Connectome and Chronicle back the secretary and project agents?

**The fit is real.** A connectome-host "resident" is close to Arity's secretary and project agents: persistent, named, with long memory that compresses instead of dropping, and accounting for cost. Chronicle's causation links and branches suit [Database](../database.md) agent runs and forks.

**The cost is a second runtime.** Connectome has its own agent loop (membrane plus agent-framework), its own subagents, and its own store. Adopting it beside pi-durable means two runtimes with two ideas of delivery, forks and subagents, which works against "one small recursive mechanism". Further cautions:

- It is a research codebase. Pre-1.0 numbers, few stars outside connectome-host, and its stated purpose is research into model cognition and welfare, not a product.
- connectome-host declares no licence. Reuse of its code is unclear until it does.
- Its surfaces are Discord and Zulip. Arity's message and report calls would need a module.
- **Unverified:** whether chronicle can run on Cloudflare (it is a native Node module with prebuilt binaries for Linux, macOS and Windows per its [README](https://github.com/anima-research/chronicle#install)). It probably cannot run inside a Durable Object.

### Where it slots

- **context-manager's compression strategies**: a reference for how the secretary and project agents handle weeks of context. In pi-durable terms that is a custom compaction ([pi-durable Compaction](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#compaction)).
- **chronicle**: a candidate store for [Database](../database.md) agent-run history, if the database ends up off Cloudflare. Its causation links answer "which report came from which message".
- **connectome-host**: a whole alternative backend for the secretary and project-agent tiers on [Agents](../agent-tiers.md).
- **MCPL push events**: relevant to delivering inbox messages to agents running in Claude Code ([Agents, Message](../agent-tiers.md#message)).

**Verdict: use as a reference** for memory and long-lived agents; consider chronicle on its own as a store. Do not run it as the secretary and project-agent backend alongside pi-durable unless the router is meant to trial whole backends, in which case it is one option in a "backend" slot.

### Alternatives

Letta (formerly MemGPT) for stateful long-lived agents; [AnimaWorks](https://github.com/xuiltul/animaworks) for "organisation as code" with consolidating memory. Neither researched in depth here.

## OptMem

### What the name refers to

Most likely [VictorTaelin/OptMem](https://github.com/VictorTaelin/OptMem), "Permanent memory for AI agents. A 426-token prompt, a script, plug and play." Search found forks and offshoots: [doeixd/OptMem](https://github.com/doeixd/OptMem) (project-scoped memory, semantic recall), [Moeblack/OptMemo](https://github.com/Moeblack/OptMemo), and an [optmem-hermes-plugin](https://hermesatlas.com/projects/rarf/optmem-hermes-plugin). These are **unverified** beyond search listings.

### Facts

| | |
|---|---|
| Maintainer | Victor Taelin ([repo](https://github.com/VictorTaelin/OptMem)) |
| Licence | **None declared.** GitHub detects no licence and the repo has no LICENSE file ([file list](https://github.com/VictorTaelin/OptMem)). Legally, all rights reserved |
| Activity | Created 2026-07-25, last push 2026-07-31, about 2.4k stars. A one-week project, then quiet |
| Depends on | Python 3 only. One file, `~/.optmem/memo`, no dependencies ([README](https://github.com/VictorTaelin/OptMem)) |

### How it works

From the [README](https://github.com/VictorTaelin/OptMem): every memory is one line (at most 280 bytes) in an append-only log. Summaries form a binary tree over the log, as a cache rebuildable from the log. Nothing runs in the background: when a merge is due, `memo note` asks the agent to write the summary before its next action. The integration is a prompt block pasted into `AGENTS.md` or `CLAUDE.md`: run `memo wake` first in every session, `memo note` when something worth keeping happens, `memo recall <regex>` to search. `$MEMORY_DIR` moves the store. The prompt tells subagents never to run `memo`, because they "cannot judge what is already known".

### Where it slots

- **A memory option in a slot** ([Blind testing, Slots](../blind-testing.md#slots)), given to secretary and project agents only. Its own subagent rule matches Arity's tiers.
- It is **harness-neutral**: any harness that can run a shell command can use it, which suits a variant that changes harness.
- One store per agent via `$MEMORY_DIR` on the agent's workspace, since its default ("parallel sessions on this machine are all you") would mix projects.

**Verdict: use as a reference, or as a slot option once licensed.** The design is small enough to reimplement. Without a licence, do not copy the code.

### Alternatives

Claude Code's file-based auto memory, Letta, and the compression strategies in Anima's context-manager (above).

## Cua Driver

### What the name refers to

Not ambiguous. Cua Driver, part of the [trycua/cua](https://github.com/trycua/cua) repository at `libs/cua-driver` ([docs](https://cua.ai/docs/cua-driver)).

### Facts

| | |
|---|---|
| Maintainer | Cua (trycua) |
| Licence | MIT. Cua Spaces, its Keyvault and Cua Driver Bench are FSL-1.1-MIT ([README, licensing](https://github.com/trycua/cua#licensing)) |
| Activity | Pushed 2026-11-18, about 32.6k stars; `cua-driver-rs-v0.54.0` released 2026-11-18 ([releases](https://github.com/trycua/cua/releases)) |
| Platforms | macOS 14+, Windows 10/11/Server with a user signed in, 64-bit Linux "with some limits". Wayland, KDE and Hyprland have limits or are experimental ([platform support](https://cua.ai/docs/cua-driver/concepts/platform-support)) |
| Needs | On macOS, Accessibility and Screen Recording, granted to `CuaDriver.app` itself ([how it works](https://cua.ai/docs/cua-driver/concepts/how-cua-driver-works)) |

### How it works

"Cua Driver lets an agent use the apps on the computer it runs on, in the background." It "does not choose or run a model" ([docs](https://cua.ai/docs/cua-driver)). It is exposed as an MCP server (`cua-driver mcp`), a CLI against a daemon (`cua-driver serve`, `cua-driver call <tool>`), or typed SDKs ([how it works](https://cua.ai/docs/cua-driver/concepts/how-cua-driver-works)). By default it does not raise windows, move the real pointer or change the frontmost app; the agent gets its own cursor overlay. It falls back to the foreground for games, canvas apps and apps that poll focus ([how it works](https://cua.ai/docs/cua-driver/concepts/how-cua-driver-works)). It acts on the machine it is installed on; the pages read do not describe driving a remote machine.

The rest of the repo matters for [Computers](../computers.md) ([README](https://github.com/trycua/cua)):

- **cua SDK and CLI**: sandboxes (`cua sb create ubuntu` makes a gVisor container), local runtimes, and "your own cloud" on AWS, Google Cloud or Modal. "There is no agent layer in the SDK": drive sandboxes from your own harness.
- **Lume**: macOS and Linux VMs on Apple Silicon.
- **Cua Spaces**: desktops on your own Macs, other machines or your cloud account. "Teleport" moves a signed-in app into a Space, with sessions kept in the **Cua Keyvault** on your Mac and released "only after you approve". Free for individuals; Pro $18 a month ([README](https://github.com/trycua/cua#cua-spaces), [teams](https://cua.ai/docs/spaces/guides/teams)).
- Cua's hosted cloud has ended and several older packages are deprecated ([deprecated list](https://github.com/trycua/cua#deprecated-packages), [migration guide](https://cua.ai/docs/cua-sdk/guides/cua-cloud-ended)). `cuabot` has "known security issues"; do not run it.

### Where it slots

- **[Blind testing, Slots](../blind-testing.md#slots)**: the prime option for the computer-use slot. It is model- and harness-neutral MCP, so the router can pair it with any model.
- **[Computers](../computers.md#the-computer-pool)**: install it on each pool machine that offers "computer use". It runs on the execution machine, which fits the "two machines" rule.
- **The cua SDK, Lume and Spaces** are candidates for the pool itself: Lume for macOS VMs on the user's own Macs, the SDK for gVisor sandboxes and cloud VMs, and Spaces' sleep and resume for workspaces that follow the agent's life.
- **[Access](../access.md#the-rule-the-model-never-sees-the-secret)**: Spaces' Keyvault with teleported, already-signed-in apps is close to the access broker's "signed-in sessions" idea. Its licence (FSL) allows self-hosting but not a competing hosted service.

**Verdict: use as is** for the computer-use slot. Evaluate the cua SDK and Lume for the computer pool. Treat Spaces and its Keyvault as a reference for the access broker.

### Alternatives

[iFurySt/open-computer-use](https://github.com/iFurySt/open-computer-use) (MIT, about 3k stars, MCP, macOS/Linux/Windows, inspired by Codex Computer Use); [domdomegg/computer-use-mcp](https://github.com/domdomegg/computer-use-mcp) (MIT, about 400 stars); others listed in [this roundup](https://pinggy.io/blog/best_computer_use_mcp_servers/) (secondary source).

## computer-use

### What the name refers to

Ambiguous. Candidates:

| Candidate | Evidence | Likely? |
|---|---|---|
| **Claude Code's built-in `computer-use` MCP server** | Literally named `computer-use`; enabled via `/mcp` ([docs](https://code.claude.com/docs/en/computer-use)). Claude Code is one of Arity's harnesses | **Most likely** |
| Anthropic's computer use API tool, current version `computer_toolset_20260801` | The general capability behind the above ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)) | Possible |
| Codex Computer Use (OpenAI) | Background computer use in the Codex Mac app, announced 2026-04-16 ([OpenAI](https://openai.com/index/codex-for-almost-everything/), [use case](https://developers.openai.com/codex/use-cases/use-your-computer-with-codex)). Codex is one of Arity's harnesses | Possible |
| [domdomegg/computer-use-mcp](https://github.com/domdomegg/computer-use-mcp) | npm package named `computer-use-mcp`, MIT | Less likely |

The verdict below treats "computer-use" as **the computer-use built into each harness** (Claude Code's and Codex's), as distinct from Cua Driver, which works under any harness. That reading fits the list: one third-party driver, one built-in option.

### Facts

- **Claude Code `computer-use`**: "a research preview on macOS that requires a Pro, Max, or Ultra plan". Not available in non-interactive mode with `-p`. CLI is macOS only; desktop app macOS and Windows. Needs claude.ai sign-in, not Bedrock, Vertex or Foundry. Approval per app per session; one session holds the lock at a time ([docs](https://code.claude.com/docs/en/computer-use)).
- **Anthropic computer use tool**: `computer_toolset_20260801`, no beta header, for current Claude models. "Computer use is a client-side tool. You must provide a virtualized or containerized environment, an implementation of the actions, an agent loop" ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)). Reference implementation: [anthropics/claude-quickstarts](https://github.com/anthropics/claude-quickstarts) (MIT).
- **Codex Computer Use**: Mac app only at launch, sees the screen and uses its own cursor; needs Screen Recording and Accessibility ([OpenAI](https://openai.com/index/codex-for-almost-everything/), [MacStories](https://www.macstories.net/?p=196899), secondary). **Unverified** from OpenAI docs: Codex docs redirected to another host and were not followed. Whether the Codex CLI can use it headless is unverified.

### Where it slots

- **[Blind testing, Slots](../blind-testing.md#slots)**: a second family of options for the computer-use slot, but tied to one harness each. Selecting Claude Code's computer-use forces the harness to Claude Code and the model to Claude.
- **[Computers](../computers.md)**: Claude Code's version needs an interactive session on a macOS machine with a signed-in user plan. That conflicts with headless subagents in pool machines.
- **[Access](../access.md#what-the-screen-shows)**: both send screenshots to their own provider, so clearance must treat "this harness's computer use" as cloud-model use.

**Verdict:** Claude Code's built-in computer-use: **skip for agents**, keep for the user's own interactive sessions; it requires an interactive session and a personal plan. Anthropic's API tool: **use as a reference** (or as a harness-specific slot option under pi, which calls models directly). Codex Computer Use: **skip until verified** headless.

## The harnesses named alongside

Brief identification only; each is a harness option in [Blind testing](../blind-testing.md#what-is-tested).

- **pi**: the pi coding agent CLI from [earendil-works/pi](https://github.com/earendil-works/pi) (MIT), `packages/coding-agent`. Cloudflare documents running it in a Sandbox ([docs](https://developers.cloudflare.com/sandbox/coding-agents/pi/)).
- **agy**: Google's Antigravity CLI, command `agy`, which replaced Gemini CLI ([Flutter docs](https://docs.flutter.dev/ai/antigravity-cli), [Google codelab](https://codelabs.developers.google.com/genai-for-dev-antigravity-cli)). Non-interactive mode is `agy -p` ([DataCamp](https://www.datacamp.com/tutorial/antigravity-cli), secondary). One guide says it is closed source, and another that it dropped ACP support; both **unverified**.
- **Claude Code** and **Codex**: not researched here.

## Summary

| Piece | What it is | Where it slots | Verdict |
|---|---|---|---|
| pi-durable | Earendil's durable agent harness: crash-safe conversations, tasks, forks, steer and follow-up, subagents; runs in a Cloudflare Durable Object | [Agents](../agent-tiers.md) runtime; [Instantiate](../instantiate.md) step 6; execution env for [Computers](../computers.md) | **Use** as the agent runtime; experimental, pin versions |
| Anima Labs Connectome | Research stack for long-lived agents: agent-framework, context-manager, membrane, connectome-host | Secretary and project agents on [Agents](../agent-tiers.md); memory strategies | **Reference**; a second runtime would duplicate pi-durable |
| Anima Labs Chronicle | Branchable, time-travel record store with causation links (Rust, MIT) | [Database](../database.md) agent-run history and forks | **Reference**, possible store if the database is off Cloudflare |
| OptMem | One-file memory tool: append-only log plus summary tree, driven by a prompt | Memory slot for secretary and project agents | **Reference**; no licence, so do not copy code |
| Cua Driver | MIT, model-neutral background computer use over MCP, CLI or SDK on macOS, Windows, Linux | Computer-use slot; installed on pool machines | **Use as is** |
| Cua SDK, Lume, Spaces | Sandboxes, macOS VMs, desktops with a Keyvault for signed-in apps | Computer pool; access broker | **Evaluate** SDK and Lume; Spaces as a reference |
| computer-use (Claude Code built-in) | Claude Code's `computer-use` MCP server, macOS research preview, interactive only | Harness-specific option in the computer-use slot | **Skip** for agents |
| Anthropic computer use API tool | `computer_toolset_20260801`, client-side tool | Option under pi | **Reference** |

## What this contradicts or would improve in the design

1. **"A single durable harness" against "the harness is a variant".** pi-durable runs its own model loop and cannot wrap Claude Code, Codex or agy. Either pi-durable is the substrate under every agent and drives the other CLIs as subprocesses (so "harness" in a variant means only the inner loop), or durability applies only to agents that drew pi. [Agents](../agent-tiers.md#one-mechanism-for-everyone) and [Blind testing](../blind-testing.md#what-is-tested) should say which.
2. **Steer and inbox already exist.** pi-durable's `whenBusy: "steer"` and follow-ups are Arity's two delivery modes. Its "write" kind (an entry the model sees without starting a run) is a third mode Arity could use for context such as user activity.
3. **Approval gates are missing upstream.** Cloudflare's PiHarness has "no approval or permission step for tool calls yet" ([docs](https://developers.cloudflare.com/agents/harnesses/pi/)), although pi-durable's own `beforeTool` hook can build one. The [Access](../access.md#approval) approval list needs that hook.
4. **Cold means hibernating, not just cache expiry.** On Durable Objects "cold" is free storage with alarm wake. That makes waking cheap; [Agents, lifecycle](../agent-tiers.md#agent-lifecycle) defines cold only by cache expiry.
5. **Built-in computer use is harness-bound and interactive.** Claude Code's `computer-use` needs an interactive macOS session and a personal plan, so it cannot serve headless subagents. Cua Driver can, and it lets the router keep model, harness and computer-use tool independent, which keeps trial records attributable.
6. **Memory has no home in the design.** OptMem and Anima's context-manager both target long-lived agents. A "memory" slot (or a loadout entry) for secretary and project agents is worth adding to [Capabilities](../capabilities.md), with subagents excluded, as OptMem itself requires.
7. **The access broker has a working precedent.** Cua Spaces' Keyvault holds signed-in app sessions on the user's Mac and releases them to a desktop only after approval. That is close to the vault's "signed-in sessions" in [Access](../access.md#what-the-vault-holds).
8. **Licences.** OptMem and connectome-host declare none. Treat both as reference only until they do.
