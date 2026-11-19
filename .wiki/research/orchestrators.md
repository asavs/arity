# Research: agent orchestrators and task boards

[Back to index](../README.md) · Compares against [Agents](../agent-tiers.md), [How work starts](../work.md) and [Instantiate](../instantiate.md).

A survey of existing multi-agent orchestrators, agent teams and swarms, persistent agents that sleep and wake, front-desk assistant agents, and orchestrators paired with a task board or issue tracker. All sources were checked on **2026-11-18**. Star counts and release dates come from each project's GitHub page or the GitHub API on that date, rounded. Anything that could not be confirmed from a primary source is marked **(unverified)**. Where a claim rests on a third-party write-up or a search snippet rather than the project's own docs, the source is named as such.

Method: four research passes ran in parallel (coding orchestrators with boards; vendor harness features; general frameworks; durable and assistant agents) and their findings were merged here. Each pass read primary sources. The claims below carry the URLs those passes cited, but they were not all re-read for this page.

## Contents

1. [What Arity is compared on](#1-what-arity-is-compared-on)
2. [The closest projects, in depth](#2-the-closest-projects-in-depth)
3. [Frameworks and protocols, shorter](#3-frameworks-and-protocols-shorter)
4. [The wide net](#4-the-wide-net)
5. [Comparison table](#5-comparison-table)
6. [Ideas worth borrowing](#6-ideas-worth-borrowing)
7. [Where Arity looks weaker or more complicated](#7-where-arity-looks-weaker-or-more-complicated)

---

## 1. What Arity is compared on

| # | Arity feature | Page |
|---|---|---|
| 1 | Three tiers: persistent secretary, long-lived project agents, short-lived subagents | [Agents](../agent-tiers.md#tiers) |
| 2 | The user can talk to any agent directly | [Agents](../agent-tiers.md#who-can-talk-to-whom) |
| 3 | Two calls only: message (any direction) and report (up, graded) | [Agents](../agent-tiers.md#two-calls) |
| 4 | Delivery by recipient state: steer or inbox if live, wake if cold, create if absent | [Agents](../agent-tiers.md#message) |
| 5 | A task board where a ready entry wakes or creates an agent that fits | [How work starts](../work.md#the-board) |
| 6 | Patch-in: the secretary hands the user to an agent with a pre-filled brief | [How work starts](../work.md#a-patch-in) |
| 7 | Fresh child or fork of the parent's context | [How work starts](../work.md#fresh-or-fork) |
| 8 | Blind model routing per agent; swappable harness | [Instantiate](../instantiate.md#steps) |

**Headline.** Every Arity feature already exists somewhere, but no project has more than about half of them. Three findings stand out:

- **Nobody does "a ready task creates or wakes an agent that fits it".** Boards that dispatch automatically (Symphony, Hermes Kanban, Paperclip, Claude Code's team task list) either spawn a generic worker per task or wake a *named* assignee. Matching by fit, and creating an agent when none fits, is new.
- **Steer versus inbox is well established**, under many names: pi `steer`/`follow_up`, OpenClaw `steer`/`followup`/`collect`/`interrupt`, Codex `interrupt_agent`/`send_message`/`followup_task`, Gas Town `nudge`/`mail`, LangGraph `interrupt`/`enqueue`. Several have more than two modes, for good reasons (section 6).
- **No project has patch-in, graded reports or blind model routing.** Amp's Puck is the nearest thing to a secretary. Hermes Kanban has a judge that grades work against the task card.

---

## 2. The closest projects, in depth

### Gas Town and Gas City

- **What / who:** Steve Yegge's multi-agent workspace manager for coding agents, now in the `gastownhall` org. It has been rebuilt as Gas City, an "orchestration-builder SDK" that ships Gas Town as a pack. <https://github.com/gastownhall/gastown>, <https://github.com/gastownhall/gascity>
- **Licence:** MIT (both).
- **Activity:** Gas Town about 18.8k stars, created 2025-12, last release v1.2.1 (2026-06-06), main pushed 2026-11-14. Gas City about 1.5k stars, v1.5.1 on 2026-10-24. Go plus tmux.
- **Hierarchy and lifetimes:** The most tiered system found ([glossary](https://github.com/gastownhall/gastown/blob/main/docs/glossary.md)):
  - **Mayor**: persistent chief of staff the human talks to.
  - **Deacon**: daemon supervisor, with **Dogs** as maintenance workers.
  - Per project ("rig"): a **Witness** that watches workers, a **Refinery** merge queue, and **Crew**, long-lived named agents that keep context.
  - **Polecats**: per-task workers with a persistent identity and work history but throwaway sessions.
  - `/handoff` refreshes a session. `gt seance` lets an agent question a predecessor session.
- **Messaging:** Two channels. `gt mail` is a persistent mailbox (stored in Beads, injected at session start or on check). `gt nudge` is "real-time messaging... without going through the mail system" ([README](https://github.com/gastownhall/gastown)). Escalations go Deacon → Mayor → human. The human can attach to the Mayor or to crew sessions in tmux. Whether attaching to polecats is supported is **(unverified)**.
- **Work assignment:** Mostly pushed. The Mayor groups Beads into "convoys" and runs `gt sling <bead> <rig>`, which puts the work on an agent's **Hook**. The pull rule, GUPP, is "If there is work on your Hook, YOU MUST RUN IT" ([glossary](https://github.com/gastownhall/gastown/blob/main/docs/glossary.md)). A scheduler caps polecat concurrency.
- **Child context:** Fresh. A polecat starts from its hook bead plus `gt prime`.
- **Runs on:** Local, tmux, git worktrees. Runtime presets for Claude, Codex, Gemini, Cursor, Copilot, Kiro, Amp, OpenCode, Auggie and pi. Runtime can be overridden per sling.
- **Against Arity:** (1) yes, richer than Arity's three tiers; (2) partly; (3) mail/nudge/escalate, no graded report; (4) nudge ≈ steer, mail ≈ inbox, handoff and seance ≈ wake; (5) push via sling, with hooks as a half-pull; (6) no; (7) fresh only; (8) swappable harness, manual model choice.
- **Verdict: reference, and the one to read first.** It is the closest existing match to Arity's tiers and messaging. Too opinionated and heavy (tmux, Dolt, over ten role types) to use as is. Gas City is the piece to evaluate if reuse is wanted.

### Beads

- **What / who:** "A distributed graph issue tracker for AI agents", on Dolt (version-controlled SQL). Started by Steve Yegge, now in `gastownhall`. <https://github.com/gastownhall/beads>
- **Licence:** MIT. **Activity:** about 28.8k stars, created 2025-10, v1.3.4 on 2026-11-04, pushed daily. Go.
- **What it does:** Storage only, no agents. `bd ready` lists tasks with no open blockers; `bd update --claim` claims atomically; `bd dep add` builds the dependency graph; hash IDs avoid merge collisions; compaction summarises old closed tasks ("memory decay"); a "Message" issue type holds threaded mail ([README](https://github.com/gastownhall/beads)). Embedded Dolt allows one writer; server mode allows many.
- **Against Arity:** a ready-made answer to the storage half of (5), and close to the inbox half of (4). It does not wake anything.
- **Verdict: reference, possibly use as is for the board schema** (dependency graph, ready query, atomic claim, compaction). The Dolt dependency is the cost, and it would overlap with whatever the sibling database survey recommends.

### Hermes Agent and its Kanban

- **What / who:** "The self-improving AI agent built by Nous Research": a TUI plus a gateway to messaging apps. <https://github.com/NousResearch/hermes-agent>, <https://hermes-agent.nousresearch.com>
- **Licence:** MIT. **Activity:** about 264.5k stars. Latest release date **(unverified)**. Kanban first shipped in 0.13.0, according to search snippets **(unverified)**.
- **Hierarchy and lifetimes:** Persistent **profiles** (named agents with their own memory) and per-task **worker** processes spawned by the board, plus in-process `delegate_task` subagents. Sandboxes on Modal or Daytona "hibernate when idle and wake on demand" ([README](https://github.com/NousResearch/hermes-agent)).
- **Messaging:** `delegate_task` returns a handle; the parent can `list`, `steer`, `stop` or `continue` its children. A steer waits for the child's next iteration boundary, and one that arrives too late comes back as `missed_steer`. Subagents "cannot interact with the user", but the user can steer or stop a worker from the TUI ([delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation)). On the board, comments are the protocol between agents.
- **Work assignment:** A board with an assignee profile per task ([kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban)):
  - States: triage, todo, ready, running, blocked, review, done, archived.
  - A dispatcher runs every 60 seconds. It promotes tasks whose parents are done, claims atomically, and spawns the assigned profile.
  - Triage tasks are decomposed automatically into a child graph routed to suitable profiles.
  - A worker must end with `kanban_complete`, `kanban_block` or `kanban_request_review`. Exiting without one is a `protocol_violation`.
  - Grading: a reviewer profile, or "goal mode", where a judge checks output against the card.
- **Child context:** `delegate_task` children get a "completely fresh conversation". Board workers get the task, parent handoffs, earlier attempts and comments. No fork found.
- **Runs on:** Local, Docker, SSH, Modal, Daytona, Vercel Sandbox. Any model; provider and model set per subagent.
- **Against Arity:** (1) partly: profiles ≈ project agents, workers ≈ subagents, no secretary tier; (2) partly; (3) close: terminal report calls plus review or judge; (4) steer at iteration boundary only, every run a new worker; (5) **very close**, but assignment is to a named profile except during triage; (6) no; (7) fresh only; (8) per-agent model, not blind.
- **Verdict: reference, the best one for the board.** Study the state machine, the required terminal call with `protocol_violation`, and the goal-mode judge.

### OpenClaw

- **What / who:** Self-hosted personal assistant reached through chat apps. Formerly Clawdbot and Moltbot. Created by Peter Steinberger; now stewarded by the OpenClaw Foundation. <https://github.com/openclaw/openclaw>, <https://docs.openclaw.ai>
- **Licence:** MIT. **Activity:** about 395k stars and 130k commits. Latest release date **(unverified)**.
- **Hierarchy and lifetimes:** A local Gateway is "the local control plane for sessions, tools, events, and channel connections". It hosts several persistent agents, each with its own workspace and SQLite session store ([multi-agent](https://docs.openclaw.ai/concepts/multi-agent)). `sessions_spawn` creates background child sessions, hidden by default, with a depth limit of 5. Proactive work comes from scheduled jobs; first setup adds a "Proactive check" every 30 minutes ([heartbeat](https://docs.openclaw.ai/gateway/heartbeat)).
- **Messaging:** Queue modes for a message that arrives during a run ([queue](https://docs.openclaw.ai/concepts/queue)):
  - `steer` (default): injected into the active run, even mid-tool.
  - `followup`: queued for the next turn.
  - `collect`: queued messages merged into one turn after a quiet window.
  - `interrupt`: the run is aborted and the newest message runs.
  - Cap of 20 queued messages; overflow policy `summarize`. Mode can be set per session, per channel or globally.

  Between agents, `sessions_send` has modes `followup`, `steer`, `notify` (adds context without starting a turn) and `resume`, and can wait for a reply or return at once ([session tool](https://docs.openclaw.ai/concepts/session-tool)). The human can reach any agent by binding a chat account to it or opening it in the Control UI.
- **Work assignment:** Pushed (spawn or send). No board.
- **Child context:** Fresh by default; `context: "fork"` is supported when the child is the same agent as the requester ([session tool](https://docs.openclaw.ai/concepts/session-tool)).
- **Runs on:** Local, with mobile and desktop companions. Model providers and harnesses are plugins.
- **Against Arity:** (1) partly, no project tier; (2) yes; (3) completion events, no grading; (4) **strongest match**, with four modes rather than two; waking a cold agent is session resume **(unverified)**; (5) no; (6) no; (7) yes; (8) swappable, not blind.
- **Verdict: reference, closely, for delivery modes and fork.** It is a single-user assistant, not an orchestrator of project agents.

### Paperclip

- **What / who:** "Orchestration for zero-human companies": a Node server and React UI with org charts, tickets, budgets, governance and heartbeats. Pitched as "If OpenClaw is an employee, Paperclip is the company". <https://github.com/paperclipai/paperclip>, <https://docs.paperclip.ing>
- **Licence:** MIT. **Activity:** about 115k stars, created 2026-03, release v2026.1117.0 on 2026-11-18.
- **Hierarchy and lifetimes:** An org chart with roles, reporting lines and per-agent budgets. Agents **do not run continuously**: they wake in short heartbeats. After each run the adapter saves the session (for example the Claude Code session ID) and resumes it on the next wake; "Reset Sessions" clears it ([how agents work](https://docs.paperclip.ing/guides/agent-developer/how-agents-work)).
- **Messaging:** Through task threads. A comment on an agent's task wakes it, and the wake carries `PAPERCLIP_WAKE_REASON` and `PAPERCLIP_WAKE_COMMENT_ID`; an @-mention alone does not. Wakes go through a database queue that merges duplicates. There is an "interrupt grace period" setting, but what happens to a comment that lands mid-run is **(unverified)**. Experimental Agent Chat plus Slack, Discord and Telegram connectors.
- **Work assignment:** Pushed by assignment. Assigning a task wakes the assignee (`issue_assigned`). An agent can only assign within its own reporting subtree. Tasks are checked out atomically with blocker dependencies. Routines create tasks on cron or webhook ([routines](https://docs.paperclip.ing/guides/projects-workflow/routines)). **New agents are not created automatically:** "hires" go to a human approval queue.
- **Child context:** Delegation means assigning to another agent, which resumes its own session. No fork.
- **Runs on:** Self-hosted server. Adapters for Claude Code, Codex, Cursor, Gemini, shell, HTTP bots (including OpenClaw). Model and harness per agent.
- **Against Arity:** (1) org chart, persistent identities; (2) yes; (3) comments, review gates instead of grades; (4) wake on assignment or comment plus session resume ≈ Arity's cold path, no steer; (5) board plus wake, but assignment is to a named agent, not by fit, and no automatic creation; (6) no; (7) no fork; (8) per-agent, not blind.
- **Verdict: reference for the wake model**: the wake reason passed to the agent, session resume, merging duplicate wakes, and human approval before a new agent exists. Too business-shaped to adopt.

### OpenAI Symphony

- **What / who:** OpenAI's spec and reference daemon that polls an issue tracker and runs one autonomous Codex run per eligible issue, so humans "manage work instead of supervising coding agents". Labelled an engineering preview. <https://github.com/openai/symphony>, [SPEC.md](https://github.com/openai/symphony/blob/main/SPEC.md)
- **Licence:** Apache-2.0. **Activity:** about 28.1k stars, created 2026-02, v0.0.3 on 2026-09-15, no commits since. The Elixir implementation is "prototype... evaluation only"; OpenAI suggests writing your own from the spec.
- **Hierarchy and lifetimes:** Flat: one orchestrator, one worker per issue. A worker keeps one Codex thread across up to 20 continuation turns. Stalled runs are killed and requeued with backoff (SPEC §8.4–8.5, §10.3).
- **Messaging:** None between agents. The human talks through the tracker: comments, or moving an issue's state. On each poll the orchestrator reconciles, and kills the worker if its issue left an active state (§8.5).
- **Work assignment:** **Pulled from the board.** Polls every 30 seconds for issues in `active_states`, respects blockers, bounds concurrency globally and per state. The orchestrator only reads the tracker; agents update tickets through adapter tools, and the tracker token never reaches the agent (§11.5). Success usually means moving the issue to a hand-off state such as "Human Review".
- **Child context:** Fresh: a prompt rendered from a `WORKFLOW.md` template plus the issue, with an `attempt` counter on retries (§12).
- **Runs on:** Local daemon, Codex only. Trackers: Linear, GitHub Issues, Jira, Asana, GitLab.
- **Against Arity:** the strongest match to the dispatch half of (5). Kill-on-state-change is a crude interrupt. Nothing for 1, 2, 3, 6, 7, 8.
- **Verdict: reference.** SPEC.md is a clean, language-neutral contract for a pull-from-board dispatcher (polling, claiming, retry, reconciliation, workspace hooks). Worth copying for Arity's dispatch layer.

### Claude Code: agent teams, subagents, forks, Projects

- **What / who:** Anthropic's built-in multi-agent features ([overview](https://code.claude.com/docs/en/agents.md)). Licence not checked **(unverified)**. Agent teams are experimental and off by default (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`); Projects is a public beta ([agent teams](https://code.claude.com/docs/en/agent-teams), [Projects](https://code.claude.com/docs/en/claude-projects.md)).
- **Hierarchy and lifetimes:**
  - Teams: one fixed lead plus teammates, one team per session, no nesting. A stopped teammate comes back with its saved conversation when the lead messages it, but not across `/resume` ([agent teams](https://code.claude.com/docs/en/agent-teams)).
  - Subagents nest 3 deep by default, up to 20 at once; a finished subagent resumes on `SendMessage` ([sub-agents](https://code.claude.com/docs/en/sub-agents)).
  - Projects add a long-running coordinating conversation plus parallel threads that share project memory ([Projects](https://code.claude.com/docs/en/claude-projects.md)).
- **Messaging:**
  - Each teammate has a mailbox file; delivery is automatic. The human can select any teammate and type, and Esc interrupts that teammate ([agent teams](https://code.claude.com/docs/en/agent-teams)).
  - Cross-session messages are read "between tool calls during an active turn"; an idle session starts a new turn ([cross-session](https://code.claude.com/docs/en/cross-session-messaging)).
  - A human message to a subagent normally waits; Ctrl+Enter makes it read sooner ([sub-agents](https://code.claude.com/docs/en/sub-agents)).
  - Messages from other agents never count as the user's consent ([agent teams](https://code.claude.com/docs/en/agent-teams)).
- **Work assignment:** A shared task list (pending, in progress, completed) with dependencies that unblock automatically. The lead assigns, or a teammate claims "the next unassigned, unblocked task" under a file lock. Hooks `TaskCreated`, `TaskCompleted`, `TeammateIdle`. Known limitation: task status can lag ([agent teams](https://code.claude.com/docs/en/agent-teams)). A new task does not create a teammate.
- **Child context:** Teammates and subagents start fresh: "The lead's conversation history does not carry over". A fork "inherits the entire conversation so far", shares the parent's prompt cache, model and tools, and cannot fork again ([sub-agents](https://code.claude.com/docs/en/sub-agents), [SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents.md)).
- **Runs on:** Local CLI with in-process or tmux panes; Projects in the cloud or locally. Claude models only.
- **Against Arity:** (1) partly (Projects → threads → subagents); (2) yes; (3) SendMessage plus a final answer, ungraded; (4) present but spread across several mechanisms; stopped teammates wake on message; (5) board with self-claim, no create-on-ready; (6) no; (7) yes, both, and the fork shares the cache exactly as Arity assumes; (8) no.
- **Verdict: reference for protocol and UI, and a candidate harness.** Teams are experimental, Claude-only and live inside one session, so they cannot be Arity's long-lived backbone.

### OpenAI Codex multi-agent (MultiAgentV2)

- **What / who:** OpenAI. <https://github.com/openai/codex>, [subagents docs](https://learn.chatgpt.com/docs/agent-configuration/subagents.md)
- **Licence:** Apache-2.0. **Activity:** about 135.8k stars; release rust-v0.170.0 on 2026-11-18.
- **Hierarchy and lifetimes:** Subagents form a thread tree with path addresses like `/root/schema_audit`, with a depth limit and a per-session concurrency cap. No persistent per-project agents.
- **Messaging:** The tool set, per a third-party source read ([Luna Shi, 2026-06-11](https://luna-shi.github.io/blog/codex-source-dive-iii-subagents-are-a-thread-tree/)):
  - `send_message`: "put information in the target mailbox; do not necessarily wake it".
  - `followup_task`: "may start another turn if idle".
  - `interrupt_agent`, `wait_agent`, `list_agents`, `spawn_agent`.

  For the human's own input, Enter steers into the running turn and Tab queues it ([third-party write-up](https://codex.danielvaughan.com/2026/03/29/codex-cli-mid-turn-steering/)). `/subagents` lets the human switch into a subagent's thread.
- **Work assignment:** Pushed by the parent. No board.
- **Child context:** `spawn_agent` takes `fork_turns`: `none`, last N, or `all`. Omitting it means full history, and a full fork inherits the parent's model ([issue #20077](https://github.com/openai/codex/issues/20077)).
- **Against Arity:** strong on (4) and (7), and splits Arity's inbox into "deliver quietly" versus "deliver and wake". Nothing for 1, 5, 6; 2 only through `/subagents`.
- **Verdict: reference for the message tool design, and a candidate harness.**

### pi and pi-durable

- **What / who:** Mario Zechner's pi coding agent, now published as `earendil-works/pi`; `badlogic/pi-mono` shows the same content (exact redirect **(unverified)**). Pi 1.0 shipped 2026-10-01 ([secondary report](https://letsdatascience.com/news/pi-10-adds-mcp-and-durable-harness-6241c6f7)). <https://github.com/earendil-works/pi>
- **Licence:** MIT. **Activity:** about 122.8k stars, 8,077 commits.
- **Hierarchy:** pi deliberately "skips features like sub-agents and plan mode" ([README](https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/README.md)). Zechner calls a sub-agent "a black box within a black box" and parallel sub-agents an anti-pattern; for observability, run another pi in tmux ([blog](https://www.mariozechner.at/posts/2025-11-30-pi-coding-agent/)).
- **Messaging:** Two queues ([RPC commands](https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/rpc-commands.md)). `steer` is "delivered after the current assistant turn finishes executing its tool calls, before the next LLM call". `follow_up` is "delivered only when agent has no more tool calls or steering messages". Each can deliver `all` or `one-at-a-time`.
- **Session tree:** Sessions are trees; the model sees only the active branch. `/tree`, `/fork` (from an earlier user message) and `/clone` ([sessions](https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/sessions.md)).
- **pi-durable** (`@earendil-works/pi-durable`): a "durable conversation, task, and document runtime", marked "Experimental. The API changes without notice" ([README](https://raw.githubusercontent.com/earendil-works/pi/main/packages/durable/README.md)):
  - Conversations are immutable entries plus typed JSON documents, including `pi.inbox` and `pi.live`.
  - Tasks are durable state machines, checkpointed every step. They can sleep with no process (`sleepUntil`) or wait on signals. Evictable hosts, such as Cloudflare Durable Objects, wake via `onWake`.
  - Messages to a busy conversation queue in the inbox: steers join the current run after the tool round, follow-ups start the next run.
  - A tool can create task-owned child conversations; `fork(entryId)` sees the parent up to that entry.
  - An example builds "persistent subagents behind one `subagent` tool that spawns, messages (steer or follow-up), waits for, stops".
  - Storage: memory, IndexedDB, SQLite, JSONL, or Durable Object SQLite.
- **Against Arity:** pi-durable implements (4) and (7) almost exactly, including sleep and wake. It has no tiers, no directory of agents the human can address, no board, no patch-in.
- **Verdict: the substrate to study first, since Arity already names it.** It is the closest runtime match to Arity's delivery model. The risk is its experimental, unstable API. pi itself is a strong harness candidate.

### Letta and Letta Code

- **What / who:** letta-ai, formerly MemGPT. The old server (`letta-ai/letta`, about 25.5k stars, last release v0.16.8 on 2026-05-14) is now marked legacy; active work is in Letta Code (`letta-ai/letta-code`, about 3.9k stars, pushed 2026-11-19). <https://github.com/letta-ai/letta-code>, <https://docs.letta.com>
- **Licence:** Apache-2.0.
- **Hierarchy and lifetimes:** Persistent agents with long-term memory, kept as a git-tracked "MemFS" shared by many conversations per agent ([conversations](https://docs.letta.com/concepts/conversations/index.md)). Background subagents. **Any existing persistent agent can be called as a subagent by `agent_id`** ([subagents](https://docs.letta.com/configuration/subagents/index.md)). Sleep-time agents, now called "dreaming", rewrite memory in the background, possibly on a bigger model ([memory](https://docs.letta.com/configuration/memory/index.md), [blog](https://letta.com/blog/sleep-time-compute)).
- **Messaging:** `SendAgentMessage` lets the parent "steer it while it works or send it a new task"; a child calls it with no ID to message its parent; `TaskStop` kills a child ([subagents](https://docs.letta.com/configuration/subagents/index.md)). Whether a steer lands mid-turn, and whether a message wakes an idle agent, is **(unverified)**. The stream API warns that concurrent requests to one agent are "undefined behavior" ([stream](https://docs.letta.com/api/resources/agents/subresources/messages/methods/stream/index.md)). The human reaches top-level agents from CLI, Slack, Telegram, Discord or the web; reaching a subagent directly is **(unverified)**.
- **Work assignment:** Pushed by the parent. No board.
- **Child context:** **Fork by default** ("starts with full context"); `fork_context: false` gives a fresh child. Each subagent can have its own model.
- **Against Arity:** (1) persistent agents plus subagents, not three named tiers; (2) top-level yes; (3) close, ungraded; (4) steer yes, the rest unclear; (5) no; (6) no; (7) exact match; (8) per-agent model, fixed harness.
- **Verdict: reference** for persistent agents that can also be called as subagents, and for background memory work. It is its own harness, so it does not sit under Claude Code or Codex.

### Amp and Puck

- **What / who:** Amp, a commercial coding agent ([manual](https://ampcode.com/manual)). Company and licence not checked **(unverified)**; appears closed source.
- **Hierarchy:** **Puck**, a "personal Amp assistant", starts agents, monitors them, messages them and brings results back. It is reachable from web, terminal, mobile, desktop, Slack, voice and MCP, and its conversations persist ([Puck](https://ampcode.com/docs/puck)). Orbs are per-thread machines that keep running when the laptop is closed.
- **Messaging:** In a thread, a message is "sent after the current step" by default; Cmd/Ctrl+Enter queues it until the agent finishes; Esc twice interrupts ([threads](https://ampcode.com/docs/threads)). Task subagents cannot be guided directly; you go through the main agent ([models and subagents](https://ampcode.com/docs/models-and-subagents)).
- **Child context:** Subagents get "the instructions and context the main agent gives them rather than the full conversation". **Handoff** starts a fresh thread carrying only the context that matters ([threads](https://ampcode.com/docs/threads)).
- **Against Arity:** Puck is the closest product to the secretary (1), and handoff is close to patch-in (6). No board; no direct line to subagents; fixed models.
- **Verdict: reference for the secretary's product shape.** Cannot be embedded.

### Vibe Kanban

- **What / who:** A local web app: a kanban board where each issue gets a workspace (branch, terminal, dev server) and a coding agent. Its company, bloop, announced its shutdown on 2026-04-10; the app continues as community-maintained and fully local ([shutdown post](https://www.vibekanban.com/blog/shutdown)). <https://github.com/BloopAI/vibe-kanban>
- **Licence:** Apache-2.0. **Activity:** about 28.7k stars, v0.1.46 on 2026-11-15.
- **Model:** Flat; no persistent agents. Sessions in a workspace share files but not conversation ([sessions](https://www.vibekanban.com/docs/workspaces/sessions.md)). Only human-to-agent messaging plus a Stop button; whether a mid-run message queues or interrupts is undocumented **(unverified)**. Work is pushed: a human, or an agent via the MCP server (`create_issue`, `start_workspace`, `run_session_prompt`), starts each workspace ([MCP server](https://www.vibekanban.com/docs/integrations/vibe-kanban-mcp-server.md)). Always fresh. Supports more than ten agent CLIs.
- **Verdict: reference for the board, worktree and review UI** and for its board MCP toolset. Do not build on it.

### Cloudflare Agents SDK

- **What / who:** Cloudflare's TypeScript SDK; "Each agent is a Durable Object". <https://github.com/cloudflare/agents>, <https://developers.cloudflare.com/agents/>
- **Licence:** MIT. **Activity:** about 6k stars; latest release **(unverified)**.
- **Model:** One instance per name, each with its own storage, alarm and connections ([routing](https://developers.cloudflare.com/agents/runtime/routing/)). Evicted when idle; wakes on the next request, alarm or WebSocket event ([lifecycle](https://developers.cloudflare.com/agents/runtime/lifecycle/)). Schedules survive eviction ([scheduler](https://developers.cloudflare.com/agents/runtime/capabilities/scheduler/)). Sub-agents have isolated storage. Overlapping chat messages are handled by `messageConcurrency`: `queue` (default), `latest`, `merge`, `drop`, `debounce`; Stop aborts only the in-memory turn ([chat agents](https://unpkg.com/agents@0.33.0/docs/chat-agents.md)). No board, no tiers.
- **Against Arity:** the cleanest primitive for "cold means wake, absent means create": addressing a name creates or wakes it. pi-durable already targets it as a host.
- **Verdict: reference, or the hosting layer** if Arity runs on Cloudflare.

---

## 3. Frameworks and protocols, shorter

| Project | What matters for Arity | Verdict |
|---|---|---|
| **LangGraph** (LangChain, MIT, about 45.1k stars) <https://github.com/langchain-ai/langgraph> | "Double texting" policy for a new input while a run is active: `reject`, `enqueue` (server default), `interrupt` (stop, keep progress, start a new run), `rollback` (cancel and revert) ([docs](https://docs.langchain.com/langsmith/double-texting)). Durable checkpoints per thread; `interrupt()` and `Command(resume=…)` for human-in-the-loop ([interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)). The swarm remembers the active agent, so the user keeps talking to whoever took over ([swarm](https://github.com/langchain-ai/langgraph-swarm-py)). `langgraph-supervisor` was archived 2026-09-20 ([repo](https://github.com/langchain-ai/langgraph-supervisor-py)). | Reference: copy `rollback` and `reject` as delivery options |
| **Microsoft Agent Framework** (MIT, successor to AutoGen, which is archived) <https://github.com/microsoft/agent-framework> | AutoGen Core created an agent on the first message to its `AgentId` ([docs](https://microsoft.github.io/autogen/0.7.2/user-guide/core-user-guide/framework/message-and-communication.html)), the same idea as Arity's create-on-message. Magentic orchestration keeps a task ledger and a per-round progress ledger, detects stalls and replans ([Magentic](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic)). Checkpoints after each superstep ([checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)). | Reference: stall detection |
| **CrewAI** (MIT, about 61.1k stars) <https://github.com/crewAIInc/crewAI> | Hierarchical process with a manager that "validates outcomes" ([docs](https://docs.crewai.com/en/learn/hierarchical-process)). Per-run crews, nothing persistent, no direct line to workers. | Skip |
| **MetaGPT** (MIT, about 71.3k stars) <https://github.com/FoundationAgents/MetaGPT> | Roles publish to a shared environment and react to the action types they watch ([tutorial](https://docs.deepwisdom.ai/main/en/guide/tutorials/multi_agent_101.html)): subscription by kind of work, which is the nearest prior art to "wake an agent that fits". | Reference for that idea only |
| **CAMEL Workforce / OWL** (Apache-2.0) <https://github.com/camel-ai/camel> | Coordinator, planner and workers over a shared task channel; a new worker is created when a task keeps failing ([docs](https://docs.camel-ai.org/reference/camel.societies.workforce.workforce)). | Reference only |
| **AgentScope** (Alibaba, Apache-2.0, about 36.3k stars) <https://github.com/agentscope-ai/agentscope> | Real-time interruption: `agent.interrupt()` cancels the reply and `handle_interrupt()` lets the agent react to being interrupted. | Reference for steer handling |
| **OpenAI Agents SDK** (MIT, about 30.8k stars) <https://github.com/openai/openai-agents-python> | Handoff (the specialist "becomes the active agent" and sees the whole history by default) versus agent-as-tool (the manager keeps control and passes only the input) ([handoffs](https://openai.github.io/openai-agents-python/handoffs/)). `input_filter` and `nest_handoff_history` trim or summarise what is handed over. Swarm is retired ([repo](https://github.com/openai/swarm)). | Reference for patch-in versus delegation |
| **Google ADK** (Apache-2.0, about 22.3k stars) <https://github.com/google/adk-python> | Three sub-agent modes ([collaboration](https://adk.dev/workflows/collaboration/)): `chat` (user talks to the sub-agent directly until it transfers back), `task` (may ask the user to clarify, ends with `finish_task`), `single_turn` (no user contact, can run in parallel). | Reference: the cleanest model of patch-in |
| **A2A protocol** (Linux Foundation, Apache-2.0, spec 1.0.0) <https://a2a-protocol.org/latest/specification/> | Task states include `INPUT_REQUIRED` and `AUTH_REQUIRED`; operations include send, stream, subscribe, cancel, and push-notification webhooks. No interrupt operation. | Reference, or the wire format for agents outside Arity |
| **OpenHands** (MIT, about 93.8k stars) <https://github.com/OpenHands/OpenHands> | A message sent while running is picked up on the next step; `pause()` and `run()` ([send while running](https://docs.openhands.dev/sdk/guides/convo-send-message-while-running)). Issue trackers as the board: an `openhands` label or `@openhands` comment starts a run ([Jira and Linear](https://docs.openhands.dev/openhands/usage/cloud/project-management/jira-integration)). History condenser ([condenser](https://docs.openhands.dev/sdk/arch/condenser)). | Reference; possible harness |
| **Temporal / Restate / Dapr Agents / Inngest AgentKit** | Durable runtimes. Temporal: Signals and Updates reach a running workflow, which is an inbox ([message passing](https://docs.temporal.io/encyclopedia/workflow-message-passing)). Restate: one Virtual Object per key, with concurrent requests to a key queued, which is a per-agent mailbox ([sessions](https://docs.restate.dev/ai/patterns/sessions)). Dapr Agents 1.0: actors plus pub/sub ([CNCF](https://www.cncf.io/announcements/2026/03/23/general-availability-of-dapr-agents-delivers-production-reliability-for-enterprise-ai/)). Inngest: router-driven networks ([networks](https://agentkit.inngest.com/concepts/networks)). | Reference only; pi-durable already covers this for Arity |

---

## 4. The wide net

One line each; not studied in depth.

**Agent orchestrator plus board** (the pattern the user noticed):

- **Multica**: issues assigned to agents "like a teammate", squads with a leader that routes work, chat, cron autopilots, local daemon. About 56.6k stars; licence reported as NOASSERTION. The strongest candidate for a further deep dive. <https://github.com/multica-ai/multica>
- **Agent Orchestrator** (formerly ComposioHQ): "plan, run and supervise fleets of agents from task to merged PR". Apache-2.0, about 14.9k stars. <https://github.com/OrchestratorInc/agent-orchestrator>
- **Cline Kanban**: board that works with any CLI agent; agents add and start tasks via MCP. Apache-2.0. <https://github.com/cline/kanban>
- **kandev**: self-hostable AI kanban and development environment. AGPL-3.0. <https://github.com/kdlbs/kandev>
- **veritas-kanban**: light agent harness with a board. MIT. <https://github.com/BradGroux/veritas-kanban>
- **kanbots**: each card is a Claude Code or Codex agent. MIT. <https://github.com/leodavinci1/kanbots>
- **ai-agent-board**: drag-and-drop board delegating to Copilot, Claude Code, Codex or OpenCode. MIT. <https://github.com/DanWahlin/ai-agent-board>
- **Camelot**: Elixir kanban orchestrator. GPL-2.0, tiny. <https://github.com/T0ha/camelot>
- **Overstory**: multi-agent orchestration with pluggable runtimes including pi. MIT. <https://github.com/jayminwest/overstory>
- **sudocode**: in-repo specs and issues for agents. Apache-2.0, quiet since 2026-03. <https://github.com/sudocode-ai/sudocode>
- **CCPM**: project-management skills on GitHub Issues plus worktrees. MIT. <https://github.com/automazeio/ccpm>
- **Claude Task Master**: turns a PRD into dependency-ordered tasks for one agent to pull. MIT with Commons Clause; slowing (last push 2026-04-28). <https://github.com/eyaltoledano/claude-task-master>
- **Backlog.md**: one markdown file per task in the repo, kanban, MCP. MIT, active. Reference for a diffable task format. <https://github.com/MrLesk/Backlog.md>
- **Ruflo** (formerly Claude Flow): very large agent and tool catalogue with "queen-led" swarms and a router. MIT, about 82k stars. Claims not verified; skip. <https://github.com/ruvnet/ruflo>
- **GitHub Agent HQ / Copilot coding agent**: assign issues to Copilot, Claude or Codex and steer them from one place. <https://github.blog/news-insights/company-news/welcome-home-agents/>
- **Linear agents**: delegate an issue to an agent while a human stays the assignee; built-in coding sessions. <https://linear.app/docs/assigning-issues>, <https://linear.app/docs/coding-sessions>

**Parallel session managers** (no board, no hierarchy): Claude Squad <https://github.com/smtg-ai/claude-squad>, Agent of Empires <https://github.com/agent-of-empires/agent-of-empires>, Crystal / Nimbalyst <https://github.com/stravu/crystal>, Sculptor <https://github.com/imbue-ai/sculptor>, Terragon (open-sourced after shutdown) <https://github.com/terragon-labs/terragon-oss>, Emdash <https://github.com/generalaction/emdash>, xum <https://github.com/coder/xum>, cmux <https://github.com/manaflow-ai/cmux>, Conductor (closed) <https://conductor.build>.

**Hosted coding agents** (orchestration model not checked, **(unverified)**): Devin <https://devin.ai>, Jules <https://jules.google>, Factory <https://factory.ai>, Cursor cloud agents <https://cursor.com/docs/integrations/linear>, Kiro <https://github.com/kirodotdev/Kiro>.

**Assistant and chief-of-staff projects:** Goose subagents and recipes <https://block.github.io/goose/docs/guides/subagents>; Edwin <https://github.com/bwelker/edwin>; CoS, whose cross-session work queue is visible to whichever agent you open next <https://github.com/grinnan/cos>; claude-chief-of-staff <https://github.com/mimurchison/claude-chief-of-staff>; May Framework, one interactive chief of staff plus headless "Missions" <https://krogsgard.github.io/may-framework>; Rivet Actors <https://rivet.dev/actors/learn/ai-agent.md>. Not checked this round: Lindy, Manus, ChatGPT agent, Khoj, Leon, Open Interpreter, elizaOS.

**Other frameworks:** Agno teams <https://docs.agno.com/teams/overview>, Mastra (agent networks deprecated for supervisors) <https://mastra.ai/reference/agents/network>, smolagents <https://huggingface.co/docs/smolagents/examples/multiagents>, Swarms <https://docs.swarms.world/architectures/hierarchical-swarm.md>, Strands <https://strandsagents.com/docs/user-guide/concepts/multi-agent/swarm/index.md>, Pydantic AI <https://pydantic.dev/docs/ai/guides/multi-agent-applications/>, BeeAI ACP (merged into A2A) <https://github.com/i-am-bee/acp>.

---

## 5. Comparison table

Key: **Y** has it, **P** partly, **N** no, **?** unverified. Column numbers match the table in section 1.

| Project | 1 Tiers | 2 Talk to any | 3 Msg + graded report | 4 Steer / inbox / wake | 5 Board wakes by fit | 6 Patch-in | 7 Fresh + fork | 8 Blind router / swap harness | Licence | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Arity | Y | Y | Y | Y | Y | Y | Y | Y | | |
| Gas Town / City | Y (more) | P | P (no grade) | Y (nudge/mail, seance) | P (sling + hook) | N | N (fresh) | P (harness swap) | MIT | Reference, read first |
| Hermes Kanban | P | P | Y (terminal calls + judge) | P (steer at boundary) | P (named assignee) | N | N (fresh) | P (per-agent model) | MIT | Reference for the board |
| OpenClaw | P | Y | P | Y (4 modes) | N | N | Y | P | MIT | Reference for delivery |
| Paperclip | Y (org chart) | Y | P (review gates) | P (wake, no steer) | P (named assignee) | N | N | P | MIT | Reference for wake model |
| Symphony | N | N | N | P (kill on state change) | P (pull, generic worker) | N | N (fresh) | N (Codex only) | Apache-2.0 | Reference for dispatcher spec |
| Beads | N | n/a | P (message beads) | P (inbox store) | P (ready + claim, no wake) | N | n/a | n/a | MIT | Reference or use for schema |
| Claude Code teams / Projects | P | Y | P (no grade) | Y (split across tools) | P (self-claim, no create) | N | Y | N | ? | Reference; harness |
| Codex MultiAgentV2 | N | P | P | Y (send / followup / interrupt) | N | N | Y (fork_turns) | N | Apache-2.0 | Reference; harness |
| pi + pi-durable | N | P | P | Y (steer / follow-up, sleep / wake) | N | N | Y | P (multi-model harness) | MIT | Study first; harness |
| Letta Code | P | P | P | P | N | N | Y (fork default) | P | Apache-2.0 | Reference |
| Amp Puck | P (secretary) | P | P | Y (step / queue / Esc) | N | P (handoff) | P | N | closed | Reference for secretary UX |
| Vibe Kanban | N | Y | N | ? | P (board, push) | N | N | P (harness swap) | Apache-2.0 | Reference for board UI |
| Cloudflare Agents | N | Y | N | P (queue / latest / merge; wake by name) | N | N | N | N | MIT | Hosting layer |
| LangGraph | N | P (active agent) | N | P (4 double-text modes) | N | P (swarm handoff) | Y | P | MIT | Reference |
| Google ADK | N | P (chat mode) | P (finish_task) | N | N | P (chat mode) | P | N | Apache-2.0 | Reference for patch-in |
| MS Agent Framework | N | P | N | P (create on message) | N | P | P | N | MIT | Reference |
| CrewAI | P (manager) | N | P (manager validates) | N | N | N | N (fresh) | N | MIT | Skip |

---

## 6. Ideas worth borrowing

Things existing projects do that Arity's pages do not yet describe.

1. **A required terminal call, and a penalty for skipping it.** Hermes workers must end with `complete`, `block` or `request_review`; leaving without one is a `protocol_violation` ([kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban)). Arity has one report kind. Adding **blocked** and **needs review** as report outcomes, and treating a missing report as a graded failure, would close a gap in [Agents](../agent-tiers.md#report).
2. **A goal judge that grades against the task card.** Hermes "goal mode" uses a separate judge, which fits Arity's grading and offers a way round [reports to a cold parent](../open-questions.md#reports-to-a-cold-parent).
3. **"Deliver quietly" as a third live mode.** Codex `send_message` (mailbox, does not wake) and OpenClaw `notify` (context only, no turn) differ from inbox: they add context without starting a turn when the agent is idle. Arity's inbox does not say whether it wakes an idle agent.
4. **Collect and coalesce.** OpenClaw `collect` merges queued messages into one turn after a quiet window, caps the queue at 20 and summarises overflow ([queue](https://docs.openclaw.ai/concepts/queue)). Paperclip merges duplicate wakes. Arity's inbox has no limit or merge rule.
5. **Reject and rollback.** LangGraph's `reject` and `rollback` ([double texting](https://docs.langchain.com/langsmith/double-texting)) cover "do not disturb" and "that run was wrong, undo it", which steer and inbox do not.
6. **A late steer is reported back.** Hermes returns `missed_steer` when the child has already finished. Arity's steer has no failure case.
7. **Wake reason passed to the agent.** Paperclip gives the woken agent `WAKE_REASON` and the triggering comment ID ([how agents work](https://docs.paperclip.ing/guides/agent-developer/how-agents-work)). This would slot into step 3 (Load context) of [Instantiate](../instantiate.md#steps).
8. **Atomic claim, ready query and reconciliation.** Beads (`bd ready`, `--claim`) and Symphony (poll, claim, retry with backoff, stall kill, kill when the task leaves an active state) together specify the board's dispatch loop. Arity's board says what wakes an agent, but not how a claim works, what happens when two agents fit, or what happens when a task is cancelled mid-run.
9. **Persistent identity, throwaway session.** Gas Town polecats and Paperclip agents keep an identity and work history across fresh sessions. This is a clean answer to [model on wake](../open-questions.md#model-on-wake): the identity persists, the session and model are picked again.
10. **Ask the predecessor.** Gas Town `gt seance` lets a fresh agent question the previous session instead of re-reading its whole history. A cheap middle option between fresh and fork.
11. **Partial fork.** Codex `fork_turns: last N` and pi `fork(entryId)` fork from a point, not only "everything". Arity has two settings, fresh and full fork.
12. **Three sub-agent modes for patch-in.** ADK's `chat` / `task` / `single_turn` ([collaboration](https://adk.dev/workflows/collaboration/)) separate "user talks to it directly", "may ask the user", and "never talks to the user". This makes patch-in a mode of the same call instead of a separate route.
13. **Subscription by kind of work.** MetaGPT roles watch action types. It is a simple, tested way to judge "a good fit" ([open question](../open-questions.md#judging-a-good-fit)): agents declare what they handle, and a task wakes the subscribers.
14. **Human approval before a new standing agent exists.** Paperclip sends agent "hires" to an approval queue. Arity creates agents freely; long-lived project agents might deserve a gate.
15. **The tracker token never reaches the agent.** Symphony agents update tickets only through adapter tools. This matches the access broker in [Access](../access.md).
16. **Agents' messages are not the user's consent.** Claude Code teams state this outright. Arity treats the user as one more participant; this rule is worth adding so an agent cannot relay approval.
17. **Stall detection and replanning.** Magentic's progress ledger ([docs](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic)) and Symphony's stall timeout. Arity has no watchdog; Gas Town's Witness is the same idea as a role.

---

## 7. Where Arity looks weaker or more complicated

1. **Board wake by fit is the most novel piece and the least specified.** Every working system either spawns a generic worker per task (Symphony, Hermes workers) or wakes a named assignee (Paperclip, Hermes profiles, Gas Town sling). Neither needs fit matching. Arity could start with an explicit assignee or role on each task, and add fit-matching later.
2. **Two delivery modes are probably too few.** Every mature system ended up with three or four: OpenClaw four, LangGraph four, Codex three, Cloudflare five. Arity will need at least a quiet mode, a coalescing rule and a queue limit. These can be options on the inbox rather than new calls, so the "two calls" principle survives.
3. **Steer timing is not truly "immediately".** pi, Claude Code, Hermes and Amp all deliver a steer at a step or tool boundary, not mid-generation; only OpenClaw injects mid-tool, and its `interrupt` aborts instead. Arity's page says steer "interrupts immediately". It should say "at the next step boundary", plus a separate abort.
4. **Waking any tier is more machinery than most use.** Gas Town and Paperclip keep identity and start a fresh or resumed session. pi-durable checkpoints tasks. Arity's "cold means cache expired, re-instantiate" ties waking to prompt caching, which is a cost detail. Defining cold as "no process running" and treating cache reuse as an optimisation would be simpler.
5. **Re-running the model router on every wake costs consistency.** Paperclip, Letta and Gas Town resume the same agent with the same settings. Re-picking a model mid-task breaks session resume for harnesses whose saved session is tied to one model (Claude Code, Codex). This argues for "keep the model within a task" on [model on wake](../open-questions.md#model-on-wake).
6. **Talking directly to short-lived subagents is unusual.** Amp, Hermes and Letta do not let the user guide task subagents directly; Claude Code and Codex allow it. It adds mailbox and routing work for agents that may live only minutes. It may be worth keeping only for project agents and the secretary, with subagents reached through their parent.
7. **Pi's author argues against subagents.** Zechner calls them "a black box within a black box" ([blog](https://www.mariozechner.at/posts/2025-11-30-pi-coding-agent/)). Arity plans to use pi as a harness and depends heavily on subagents. Arity's answer is grading and direct access, but the observability concern is real: every subagent should leave a readable transcript the user can open.
8. **Patch-in as a separate route.** ADK, the OpenAI Agents SDK and LangGraph swarm treat handing the user over as a handoff, a kind of delegation. Arity could make patch-in a message whose brief opens a direct line, rather than a second route beside the board.
9. **Building versus adopting.** Arity would build its own board, mailbox, dispatcher and durable runtime. pi-durable (runtime and inbox), Beads (board schema) and the Symphony spec (dispatch loop) cover most of that. The pieces Arity truly adds are the blind router, grading, fit-matching and patch-in. Concentrating original work there would shrink the build.
