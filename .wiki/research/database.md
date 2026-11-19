# Research: software for the database

[Back to index](../README.md) · Compared against [Database](../database.md), [How work starts](../work.md) and [Open questions](../open-questions.md#database-storage)

A survey of existing software that could serve as Arity's database or show how to build it. **All facts were checked on 2026-11-18.** Stars, licences, push dates and release dates come from each project's GitHub repository page or the GitHub REST API (`api.github.com/repos/<owner>/<repo>`) unless another source is cited. **[unverified]** marks a claim that rests only on a secondary source, on sources that disagree, or on nothing fetched. "(inference)" marks a judgement of ours with no source behind it.

Verdicts are one of **use as is**, **use as reference**, or **skip**.

## What Arity needs

These are the requirements every project below is measured against:

1. **A wiki-shaped graph.** Pages at paths (`people/asas`, `projects/arity/tasks/...`), typed links between them, and progressive disclosure.
2. **One kind of page for people and models.**
3. **A task board with typed fields:** clearance, priority, blocks and blockers, requirements, gates, tags and due date. A ready task wakes an agent.
4. **Grading data in the same graph:** scores, sealed trial records, profiles and papercuts.
5. **Many concurrent writers:** many agents on several computers, all reading and writing.
6. **Exact queries.** The test query throughout is **"all ready tasks I am cleared for"**: tasks with no open blocker, whose clearance admits the asker.

---

## 1. Task trackers and issue stores for agents

### Beads (`bd`)
- **What it is.** A "distributed graph issue tracker for AI agents, powered by Dolt", created by Steve Yegge. The repository moved from `steveyegge/beads` to `gastownhall/beads`. https://github.com/gastownhall/beads
- **Licence:** MIT.
- **Activity:** about 28.8k stars, v1.3.4 on 2026-11-04, pushed 2026-11-19 UTC. Very active (https://github.com/gastownhall/beads).
- **Data model.**
  - Issues live in a Dolt database (version-controlled SQL). `.beads/issues.jsonl` is only an export, "not the source of truth" (https://github.com/gastownhall/beads, Storage Modes).
  - IDs are hash-based (`bd-a1b2`) and hierarchical (`bd-a3f8.1.1`).
  - Dependency types are typed (https://github.com/gastownhall/beads/blob/main/docs/core-concepts/dependencies.md):
    - Blocking: `blocks`, `parent-child`, `conditional-blocks`, `waits-for`.
    - Non-blocking: `related`, `tracks`, `discovered-from`, `caused-by`, `validates`, `supersedes`.
  - The same document describes **gates** that wait on pull requests, CI runs or timers.
  - Other features: deferral (`bd defer --until`), message issues with threads, and compaction of old closed issues (https://github.com/gastownhall/beads).
- **Concurrency and machines** (https://github.com/gastownhall/beads).
  - Embedded mode has a single writer.
  - `bd init --server` uses a `dolt sql-server` "for multiple concurrent writers".
  - Machines sync with `bd dolt push/pull` through the git remote, using Dolt's cell-level merge. Hash IDs avoid ID collisions.
  - An HTTP thin-client mode (`bd serve`, `bd connect`) is experimental, and `ready --claim` is not always transactional over it.
- **Agent query.**
  - `bd ready` lists unblocked work.
  - `bd update <id> --claim` sets the assignee and status atomically; `bd ready --claim` does both steps at once.
  - `--json` output everywhere. `bd list` filters by label pattern, assignee, priority, due date, overdue and deferred.
  - There is an MCP package on PyPI (`beads-mcp`) and hooks for several agent harnesses.
  - The test query: "ready" yes. "Cleared for" needs clearance stored as a label or custom field. **[unverified]** whether Beads has arbitrary typed custom fields.
- **Fit.** The nearest existing match to Arity's task board: typed blocking links, gates, priority, labels, due dates, a ready queue, atomic claims, and multi-writer and multi-machine modes. It has no general page graph for people, models and agent runs, and it brings in Dolt as a dependency.
- **Verdict: use as reference.** It is the strongest candidate to borrow from. Copy its link taxonomy, its ready and claim semantics, its gates and its hash IDs. It could be used as is for the board alone, but Arity would then run two stores, because people, models, runs and scores are not issues.

### beads_rust (`br`)
- **What it is.** A Rust port of Beads that keeps the older SQLite-plus-JSONL design, by Dicklesworthstone. https://github.com/Dicklesworthstone/beads_rust
- **Activity:** about 1.2k stars, v0.7.6 on 2026-10-15.
- **Licence.** The GitHub API reports NOASSERTION. The LICENSE file is titled "MIT License (with OpenAI/Anthropic Rider)", a custom licence.
- **Model.** SQLite with a JSONL export kept in git. The user commits and resolves merge conflicts by hand. `br ready` lists unblocked work (https://docs.rs/crate/beads_rust/0.1.14/source/README.md).
- **Verdict: use as reference.** A file-based take on Beads. Read the licence rider before reusing any code.

### Backlog.md
- **What it is.** Task management for "humans and AI Agents in a git ecosystem", by MrLesk. https://github.com/MrLesk/Backlog.md
- **Licence:** MIT.
- **Activity:** about 7.4k stars, v1.55.3 on 2026-10-23, pushed 2026-11-18.
- **Data model.** One markdown file per task with frontmatter, under `backlog/tasks/`. IDs are sequential with a prefix (`TASK-1`). Fields include status, assignees, labels, priority, parent, `dependencies`, `dueDate`, acceptance criteria and Definition of Done (https://github.com/MrLesk/Backlog.md/blob/main/CLI-INSTRUCTIONS.md).
- **Readiness.** `isReady` is computed when tasks are read, never stored, and it **fails closed**: an unknown dependency blocks the task. `task list --ready` filters on it (same source).
- **Concurrency.** It relies on git and reads task state across active branches from the last 30 days. There is no server and no locking (https://github.com/MrLesk/Backlog.md/blob/main/ADVANCED-CONFIG.md). Sequential IDs can collide on parallel branches **[unverified]** how this is handled.
- **Agent query.**
  - Versioned JSON output (`--json`).
  - `task list --json --watch` streams a fresh list whenever something changes.
  - An MCP server.
  - An `onStatusChange` shell hook (same sources).
- **Verdict: use as reference.** Worth copying: readiness computed on read that fails closed, a versioned JSON contract, and a watch stream that can wake agents. It does not handle many writers on many machines.

### Task Master (claude-task-master)
- **What it is.** An AI tool that breaks a requirements document into tasks, by Eyal Toledano and Ralph Khreish. https://github.com/eyaltoledano/claude-task-master
- **Licence:** MIT plus the Commons Clause (https://github.com/eyaltoledano/claude-task-master/blob/main/LICENSE).
- **Activity:** about 28.4k stars. Last release 0.43.1 on 2026-03-31, last commit to main 2026-04-23. Slowing.
- **Model.** A single `.taskmaster/tasks/tasks.json` with numeric IDs and a `dependencies` array. `task-master next` returns the next task (https://tryhamster.com/docs/taskmaster/capabilities/task-structure). One file with numeric IDs suits a single writer (inference).
- **Verdict: skip.** Single-file store, a restrictive clause, and a different purpose: planning from a document.

### git-bug
- **What it is.** A "distributed, offline-first bug tracker integrated in git". https://github.com/git-bug/git-bug
- **Licence:** GPL-3.0.
- **Activity:** about 10.9k stars, v0.12.0 on 2026-11-07.
- **Model.** Each entity is a chain of signed operations stored as git objects under its own ref, ordered by Lamport clocks. It behaves like an operation-based CRDT, so concurrent edits from different machines merge without conflict. It doubles as a framework for building other distributed entities (https://github.com/git-bug/git-bug/blob/trunk/doc/design/data-model.md).
- **Agent query.** Filters by status, label and author, with JSON output, plus a GraphQL API. **No dependency links**, so there is no ready queue (https://github.com/git-bug/git-bug/blob/trunk/doc/md/git-bug_bug.md).
- **Verdict: use as reference** for one thing: storing many machines' writes in git as an operation log. Its tracker features are too thin.

### Taskwarrior and the TaskChampion sync server
- **What they are.** A long-running command-line task manager and its sync server, both from GothenburgBitFactory.
  - https://github.com/GothenburgBitFactory/taskwarrior: MIT, v3.5.0 on 2026-08-16.
  - https://github.com/GothenburgBitFactory/taskchampion-sync-server: MIT, v0.7.1 on 2026-08-15.
- **Model.** Tasks have UUIDs, `depends`, and user-defined attributes (https://github.com/GothenburgBitFactory/taskwarrior/blob/develop/doc/devel/rfcs/task.md). Virtual tags include `+BLOCKED`, `+UNBLOCKED` and `+READY` (https://taskwarrior.org/docs/tags/).
- **Sync.**
  - Every machine keeps a full copy and rebases its local operations onto the server's history (https://gothenburgbitfactory.org/taskchampion/sync-model.html).
  - The server stores only client-encrypted blobs (https://gothenburgbitfactory.org/taskchampion/encryption.html), in SQLite or Postgres.
- **Agent query.** `task +READY export` gives JSON (inference from the documented virtual tags; not run).
- **Verdict: use as reference** for its sync model. It is single-user with flat fields and no typed links.

### Gas Town (`gt`)
- **What it is.** A "multi-agent workspace manager" from the same organisation as Beads. https://github.com/gastownhall/gastown
- **Licence:** MIT.
- **Activity:** about 18.8k stars, v1.2.1 on 2026-06-06, pushed 2026-11-14.
- **How it works** (https://github.com/gastownhall/gastown).
  - Work state is kept in Beads.
  - Roles: a coordinator, workers, a watcher that detects stuck agents, and a merge queue that runs verification gates.
  - Mailboxes, handoffs, escalation by severity, and a scheduler that caps concurrency.
  - It claims to scale to "20-30 agents".
- **Verdict: use as reference.** It shows an orchestrator built on a Beads store. The orchestration side belongs to the orchestrators survey; for this page the takeaway is that Beads has been run with many agents at once.

### CCPM
- **What it is.** A project-management skill for agents, built on GitHub Issues and git worktrees. https://github.com/automazeio/ccpm
- **Licence:** MIT, about 8.5k stars, no releases.
- **Model.** Task files carry `depends_on`, `parallel` and `conflicts_with` (same source).
- **Verdict: skip.** It is a workflow, not a store. The `conflicts_with` field is worth noting: two tasks that must not run at once.

### GitHub Issues dependencies, and Linear
- **GitHub Issues.**
  - The REST API reads and writes "blocked by" and "blocking" (https://docs.github.com/en/rest/issues/issue-dependencies). `gh issue view --json blockedBy,blocking` exposes them on the command line (https://docs.github.com/en/enterprise-cloud@latest/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies).
  - No native "ready" search was found **[unverified]**.
- **Linear.**
  - Agents are apps that can be delegated issues while a person stays the owner. Delegation opens an agent session through a webhook (https://linear.app/developers/agents).
  - Relations: blocks, blocked by, related and duplicate (https://linear.app/docs/issue-relations).
- **Verdict: skip both as the database.** They are hosted, rate-limited, and would put task data on someone else's servers. Linear's split between the delegated agent and the owning person is a useful reference.

### Vibe Kanban
- **What it is.** A kanban board that dispatches coding agents. https://github.com/BloopAI/vibe-kanban
- **Licence:** Apache-2.0.
- **Status.** Its company shut down on 2026-04-10, and the README says it "is sunsetting" (https://www.vibekanban.com/blog/shutdown).
- **Verdict: skip.**

---

## 2. Agent memory systems and knowledge graphs

All but one of these systems build their graph by having a model extract facts from text, then search it by similarity. **That cannot answer "all ready tasks I am cleared for" exactly, which a task board, scores and sealed records all need.** They are references for schema and tooling, not candidates for the system of record.

### OptMem ("optmem")
- **Identification.** The name maps to one clear project, **OptMem by Victor Taelin**: https://github.com/VictorTaelin/OptMem. The other repositories with the name are ports, forks or plugins of it (https://api.github.com/search/repositories?q=optmem):
  - Go ports: `samuelstevens/optmem`, `gaoxingxs/OptMem-go`.
  - Rust ports: `chillerno1/optmem-rs`, `tomharmon/optmem-rs`, `lennix1337/OptMem`.
  - Forks: `mneves75`, `sabitm`.
  - A Hermes plugin with BM25 search: `rarf/optmem-hermes-plugin`.
  - Unrelated projects that share the name: `chihaya-optmem-peerstore` and `optmempy`.
  - Nothing called optmem is on PyPI or npm.
- **What it is.** "Permanent memory for AI agents. A 426-token prompt, a script, plug and play." It is one dependency-free Python script, `memo`, plus a block of instructions pasted into AGENTS.md or CLAUDE.md (https://github.com/VictorTaelin/OptMem).
- **Activity:** about 2.4k stars. Created 2026-07-25, last pushed 2026-07-31, no releases.
- **Licence: none.** The API reports a null licence and there is no LICENSE file (https://api.github.com/repos/VictorTaelin/OptMem), so there are no rights to reuse its code.
- **Data model.**
  - `LOG.txt` is an append-only log of fixed-width records of at most 280 bytes, where "position is identity".
  - `TREE/` is a binary tree of one-line summaries that can be rebuilt from the log.
  - Commands: `wake`, `note`, `nap`, `recall <regex>`, `zoom`, `forget`.
  - There are no types, links or structured queries (https://github.com/VictorTaelin/OptMem).
- **Concurrency.** No locking is documented. For several machines it says to point the memory folder at a synced folder or git repository (same source). A review notes there is "no arbitration" between parallel sessions (https://moclaw.ai/blog/what-is-optmem).
- **Verdict: use as reference** for one pattern: an immutable log is the truth, a summary tree is a rebuildable cache, and `wake` returns a fixed-size digest. That is progressive disclosure for an agent's own history, and it suits agent-run pages. Do not reuse the code.

### Graphiti (Zep)
- **What it is.** An open-source temporal knowledge-graph framework from getzep, and the engine inside Zep's commercial service. https://github.com/getzep/graphiti
- **Licence:** Apache-2.0.
- **Activity:** about 32.9k stars, v0.31.2 on 2026-10-20.
- **Zep Community Edition** is deprecated (https://help.getzep.com/ce-deprecation-notice).
- **Data model.**
  - Episodes hold the raw input and provenance. Entities carry summaries. Facts are typed edges, and custom types are defined as Pydantic models.
  - **Edges are bi-temporal:** `valid_at` and `invalid_at` record when a fact was true; `created_at` and `expired_at` record when the system knew it. A superseded fact is invalidated, not deleted (https://raw.githubusercontent.com/getzep/graphiti/main/graphiti_core/edges.py).
  - Backends: Neo4j, FalkorDB or Neptune.
- **Concurrency.** Ingestion is serial per `group_id` by default. Concurrent ingestion is tested only on FalkorDB (https://github.com/getzep/graphiti).
- **Agent query.**
  - Hybrid search and MCP tools such as `search_nodes` and `search_memory_facts` (https://github.com/getzep/graphiti/tree/main/mcp_server).
  - The MCP server has no authentication.
  - The test query needs raw Cypher on the backing database (inference).
- **Verdict: use as reference** for the bi-temporal link schema and episode provenance, the best such reference in this survey. Model extraction is wrong for authoritative records.

### Basic Memory
- **What it is.** Markdown notes as a knowledge graph for agents, by Basic Machines. https://github.com/basicmachines-co/basic-memory
- **Licence:** AGPL-3.0-or-later.
- **Activity:** about 4.6k stars, v1.0.0 on 2026-10-29 (https://pypi.org/project/basic-memory/).
- **Data model** (https://github.com/basicmachines-co/basic-memory).
  - Markdown files are the source of truth. Frontmatter holds `type`, `permalink` and `tags`.
  - Observations use the form `- [category] fact #tag`. Relations are typed wikilinks: `- relation_type [[Target]]`.
  - It keeps a SQLite index (Postgres optional) with full-text and semantic search.
- **Agent query.**
  - MCP tools include `bm_read`, `bm_write`, `bm_search`, `bm_context` (a page's graph neighbourhood) and `bm_schema_*` for validating note types (https://github.com/basicmachines-co/basic-memory/releases/tag/v1.0.0).
  - The test query is only partly possible through the tools **[unverified]**; the SQLite index could be queried directly.
- **Concurrency.** Not documented **[unverified]**. Sync between machines goes through its commercial cloud.
- **Fit.** The closest existing thing to "a graph that reads like a wiki": markdown pages, typed links, and neighbourhood reads that act as progressive disclosure.
- **Verdict: use as reference.** The page format (typed wikilinks in markdown) and the `bm_context` read are worth copying for Arity's wiki view. It cannot be the board: it has no claims, no ready queue and no multi-writer story.

### Official MCP memory server
- **What it is.** The knowledge-graph memory server in modelcontextprotocol/servers. https://github.com/modelcontextprotocol/servers/blob/main/src/memory/README.md
- **Version:** npm 2.2.1, published 2026-11-18 (https://registry.npmjs.org/@modelcontextprotocol/server-memory).
- **Data model.** Entities have a name, a type and a list of observations; relations are typed. Everything sits in one JSONL file.
- **Concurrency.**
  - A lock file guards each write, and the whole file is rewritten every time.
  - It is "not safe on network filesystems".
  - Version 2.2.0 fixed a race that "had allowed two writers to lose a write" (https://raw.githubusercontent.com/modelcontextprotocol/servers/main/src/memory/CHANGELOG.md).
- **Agent query.** `read_graph`, `search_nodes` and `open_nodes`. No attribute filters, so the test query cannot be answered.
- **Verdict: use as reference** for a minimal tool surface (create entities, create relations, add observations, open nodes). It is toy-scale as a store.

### Cognee
- **What it is.** A memory pipeline from topoteretes that extracts a graph and vectors from documents. https://github.com/topoteretes/cognee
- **Licence:** Apache-2.0.
- **Activity:** about 33.4k stars, v1.6.10 on 2026-11-18.
- **Storage.** The default graph store, Ladybug (a Kuzu fork), is "not suitable for concurrent use from different agents or processes". Neo4j supports concurrent writers (https://docs.cognee.ai/setup-configuration/graph-stores).
- **Agent query.** Raw Cypher is possible only when switched on with `ALLOW_CYPHER_QUERY=true` (https://docs.cognee.ai/core-concepts/main-operations/search).
- **Verdict: use as reference** for pluggable graph and vector backends. Skip as the store.

### Letta (formerly MemGPT)
- **Licence:** Apache-2.0. https://github.com/letta-ai/letta
- **Status.** Its README moves active work to `letta-code`, which tracks memory in git (https://github.com/letta-ai/letta-code).
- **Memory blocks** can be shared between agents. Concurrent writes are last-write-wins (https://docs.letta.com/guides/agents/memory-blocks).
- **Verdict: use as reference** for size-limited shared context blocks. Skip as the store: there is no typed graph and the platform keeps changing.

### Mem0, LangMem, MemOS, A-MEM
- **Mem0** (https://github.com/mem0ai/mem0, Apache-2.0, about 69k stars).
  - Flat facts extracted by a model, scoped by user, agent and run, with metadata filters (https://docs.mem0.ai/open-source/features/metadata-filtering).
  - Graph memory now links only facts that share an entity, with no typed relations (https://docs.mem0.ai/open-source/features/graph-memory).
  - **Skip.**
- **LangMem** (https://github.com/langchain-ai/langmem, MIT).
  - Last release 0.0.30 on 2025-10-27 (https://pypi.org/pypi/langmem/json). Similarity search only.
  - **Skip.**
- **MemOS** (https://github.com/MemTensor/MemOS, Apache-2.0, v2.0.36 on 2026-11-09).
  - Model-managed "memory cubes" stored in Neo4j and Qdrant.
  - **Skip.**
- **A-MEM** (https://github.com/agiresearch/A-mem, MIT, research code).
  - The model rewrites older notes, which conflicts with sealed records.
  - **Skip.**

---

## 3. Wikis and notes with typed links

### Logseq (database version)
- **What it is.** Logseq, AGPL-3.0, about 45.6k stars. https://github.com/logseq/logseq
- **Data model** (https://raw.githubusercontent.com/logseq/docs/master/db-version.md).
  - Pages and blocks are one kind of node.
  - Tags act as classes with inheritance.
  - Typed properties include a **Node** type, which is a typed link that can be restricted to one tag.
  - Storage is a SQLite file.
- **Agent query.** The CLI offers Datalog queries and an MCP server, but only in nightly builds (same source).
- **Maturity.** The database version is a 2.0 beta that warns of possible data loss (https://www.issoh.co.jp/md/tech/4954) **[secondary source]**.
- **Verdict: use as reference.** "Tags as classes, Node-typed properties as links" maps almost one to one onto Arity's pages and typed links. It is too immature, and its storage cannot be diffed.

### Anytype
- **What it is.** Typed objects with relations, synced local-first through any-sync.
- **Licence.**
  - Clients use the "Any Source Available License", which allows non-commercial use only (https://github.com/anyproto/anytype-ts/blob/main/LICENSE.md).
  - The sync protocol and MCP server are MIT (https://github.com/anyproto/any-sync, https://github.com/anyproto/anytype-mcp).
- **Concurrency.** Encrypted DAGs merged as a CRDT. Indexes are local and never synced (https://doc.anytype.io/anytype-docs/advanced/data-and-security/how-we-keep-your-data-safe).
- **Agent query.** A headless `anytype serve` runs with a bot account (https://github.com/anyproto/anytype-cli).
- **Verdict: use as reference.** It has the best multi-machine story of any tool in the survey. The data does not live at paths or in git, it locks you into any-sync, and the licence is restrictive.

### SilverBullet
- **What it is.** A markdown wiki, MIT, about 6.6k stars. https://github.com/silverbulletmd/silverbullet
- **Data model.** Objects are extracted from markdown into a rebuildable index. "Tags are the SilverBullet equivalent of SQL tables" (https://silverbullet.md/Tag).
- **Agent query.** Lua Integrated Query (https://v1.silverbullet.md/Space%20Lua/Lua%20Integrated%20Query).
- **Concurrency.** The index lives in the browser client; the server is "a dumb file store" (https://docs.silverbullet.md/Architecture).
- **Verdict: use as reference** for "markdown wiki that is also a database". Agents have no server-side query.

### Obsidian with Dataview or Bases
- **Querying.**
  - Dataview: MIT, https://github.com/blacksmithgu/obsidian-dataview.
  - Bases saves views as YAML filters over frontmatter (https://obsidian.md/help/bases/syntax).
  - The official CLI has `base:query` with JSON output, but needs the app running (https://obsidian.md/help/cli).
- **Links** are untyped wikilinks.
- **Sync** is a paid service that three-way merges markdown (https://obsidian.md/help/sync/troubleshoot).
- **Verdict: use as reference,** and possibly as a person's viewer over Arity's exported pages. The index lives inside a GUI app, which rules it out as a back end for agents.

### Fossil
- **What it is.** A version-control system with a built-in wiki and tickets. https://fossil-scm.org
- **Data model.**
  - Every artifact is named by a hash of its content (https://fossil-scm.org/home/doc/trunk/www/fileformat.wiki).
  - **Tickets are stored only as change artifacts.** The ticket tables are derived by replaying the changes and can always be rebuilt. Conflicting field writes resolve by timestamp (https://fossil-scm.org/home/doc/trunk/www/tickets.wiki).
- **Licence and maintainer** **[unverified]**.
- **Verdict: use as reference.** It is the template for "an append-only log is the truth, the tables are a view". That fits sealed trial records and task history.

### Semantic MediaWiki
- **What it is.** Typed properties on wiki pages; a property whose type is Page is a typed link. GPL-2.0+, v7.4.1 on 2026-11-10 (https://www.mediawiki.org/wiki/Extension:Semantic_MediaWiki).
- **Verdict: use as reference.** The classic "wiki with typed links". It needs a heavy PHP and MySQL server.

### Trilium Notes (TriliumNext)
- **What it is.** Notes with labels and named relations to other notes. AGPL-3.0 (https://docs.triliumnotes.org/user-guide/advanced-usage/attributes, https://github.com/TriliumNext/Trilium).
- **Storage and sync.** A single SQLite file with a self-hosted sync server.
- **Verdict: use as reference.**

### Others
- **TiddlyWiki** (BSD-3, https://github.com/TiddlyWiki/TiddlyWiki5): untyped fields, and no editing files while the server runs. **Skip.**
- **Dendron**: in maintenance mode since 2023 (https://randomgeekery.org/post/2023/02/dendron-is-officially-in-maintenance-mode/). **Skip.**
- **Foam** (MIT): an editing aid for VS Code. **Skip.**
- **org-roam** (GPL-3.0, https://www.orgroam.com/manual.html): tied to Emacs. **Skip.**
- **Notion**, **Tana**, **Capacities**: hosted and proprietary. Their relation and supertag vocabulary is a reference only. **Skip.**

### Markdown indexers
- **zk** (GPL-3.0, https://github.com/zk-org/zk).
  - Keeps a SQLite index at `.zk/notebook.db` (https://zk-org.github.io/zk/notes/notebook.html).
  - Filters: `--link-to`, `--linked-by`, `--recursive`, `--max-distance`, with JSON output (https://zk-org.github.io/zk/notes/note-filtering.html).
  - Links are untyped.
  - **Use as reference.**
- **markdowndb** (MIT, https://github.com/datopian/markdowndb).
  - Indexes markdown and frontmatter into SQLite tables, with `getFiles`, `getLinks` and a watch mode.
  - **Use as reference,** or fork it for a prototype index.
- **Datasette** (Apache-2.0, https://datasette.io/).
  - A read-only browser with a JSON endpoint over any SQLite database.
  - **Use as is** if a quick inspection view is wanted.
- **mdq**, **nb**, **Quartz**, **markdown-to-sqlite**: **skip.** They query only one file, offer no typed query, generate static sites, or have been stale since 2022.

---

## 4. Graph databases

None of these earns its place at Arity's size. Most queries are one or two hops, and Postgres 19 adds standard graph queries over ordinary tables (see [section 6](#6-postgres-features-that-matter)).

| Project | Licence | Status | Concurrency | Verdict |
|---|---|---|---|---|
| Kuzu (https://github.com/kuzudb/kuzu) | MIT | **Archived 2025-10-10**, last release v0.11.3 (https://www.theregister.com/2025/10/14/kuzudb_abandoned/) | Embedded | Skip |
| LadybugDB, a Kuzu fork (https://github.com/LadybugDB/ladybug) | MIT | Community maintained; v0.17.1 in June 2026 **[unverified]** | **One read-write process per file**; the docs advise a server in front for several writers (https://docs.ladybugdb.com/concurrency) | Reference |
| Bighorn, a Kuzu fork (https://github.com/Kineviz/bighorn) | MIT | Archived 2026-10-23 | — | Skip |
| Neo4j Community (https://github.com/neo4j/neo4j) | GPLv3 | Calendar versions, 2026.08.1 (https://neo4j.com/download/other-releases/) | Single server; clustering is Enterprise only | Skip |
| Memgraph (https://memgraph.com/legal) | BSL 1.1 | v3.14.0, 2026-10-21 (https://memgraph.com/release-notes) | In-memory server | Skip |
| FalkorDB (https://docs.falkordb.com/references/license) | SSPLv1 | v6.0.4 (https://github.com/FalkorDB/FalkorDB/releases) | Redis module | Skip |
| Apache AGE (https://age.apache.org/release-notes) | Apache-2.0 | 1.7.0, 2026-01-21, for Postgres 17 and 18 | Inherits Postgres | Reference; optional later |
| SurrealDB (https://github.com/surrealdb/license) | BSL 1.1, becomes Apache after 4 years | 3.0 in Feb 2026 (https://surrealdb.com/releases/3.0); 4.0.1 shown on GitHub **[year unverified]** | Embedded or server | Reference (record links, edge tables). Licence and churn risk |
| TerminusDB (https://github.com/terminusdb/terminusdb) | Apache-2.0 | v12.0.x | Server with git-like branch, merge and push | Reference: a "versioned graph of documents" |
| Gel, formerly EdgeDB (https://github.com/geldata/gel) | Apache-2.0 | **Company shut down Dec 2025** (https://www.geldata.com/blog) | On Postgres | Reference (links as a first-class feature of the schema) |
| Dgraph (https://github.com/hypermodeinc/dgraph) | Apache-2.0 **[unverified]** | Sold to Istari on 2025-10-23 (https://www.istaridigital.com/resources/istari-acquires-dgraph) | Cluster | Skip |
| CozoDB (https://github.com/cozodb/cozo) | MPL-2.0 **[unverified]** | Last release Dec 2023 | Embedded | Skip |
| DuckPGQ (https://duckdb.org/community_extensions/extensions/duckpgq) | MIT **[unverified]** | Community extension | Built for analysis, not for many writers | Skip, except as an analysis sidecar |
| simple-graph (https://github.com/dpapathanasiou/simple-graph) | MIT | Small | SQLite | Reference: almost exactly Arity's two-table design |
| sqlite-graph (https://github.com/agentflare-ai/sqlite-graph) | MIT | Alpha, "not recommended for production" | SQLite | Skip |
| GraphQLite (https://pypi.org/project/graphqlite/) | MIT | 0.x | SQLite extension with `cypher()` | Reference |

---

## 5. SQLite across machines, and versioned SQL

| Project | Licence | Status | Several writers on several machines? | Verdict |
|---|---|---|---|---|
| Litestream (https://github.com/benbjohnson/litestream) | Apache-2.0 **[unverified]** | v0.5.18, 2026-11-03 | No. Streams the write-ahead log to S3; one writer; read replicas experimental | Use as is, but only as SQLite backup |
| LiteFS (https://fly.io/docs/litefs/) | Apache-2.0 **[unverified]** | Beta; Fly offers no support (https://community.fly.io/t/sunsetting-litefs-cloud/20829) | Single primary | Skip |
| rqlite (https://github.com/rqlite/rqlite) | MIT **[unverified]** | v10.7.3 | Yes, through a Raft leader, but **explicit BEGIN/COMMIT is unsupported** (https://rqlite.io/docs/faq/) | Skip: claiming a task needs a transaction |
| libSQL (https://github.com/tursodatabase/libsql) | MIT | Maintained, but new features go to Turso Database | One primary, many replicas | Skip |
| Turso Database (https://github.com/tursodatabase/turso) | MIT | v0.8.3, pre-1.0. Supabase announced its acquisition on 2026-10-02 (https://supabase.com/blog/supabase-is-acquiring-turso) | `BEGIN CONCURRENT` with row-level MVCC. The hosted version is an "early preview" (https://turso.tech/blog/concurrent-writes-in-practice) | Reference; watch it |
| cr-sqlite (https://github.com/vlcn-io/cr-sqlite) | MIT | Stalled since 2024 **[unverified]** | Each machine writes locally and merges as a CRDT | Reference. Merged writes cannot give exclusive task claims |
| Dolt (https://github.com/dolthub/dolt) | Apache-2.0 **[unverified]** | v2.7.5. Doltgres 1.0, which speaks the Postgres protocol, shipped 2026-08-06 (https://dolthub.com/blog/2026-08-06-doltgres-1-0/) | Yes, through `dolt sql-server`; machines can also clone, push and pull like git | Reference; the only versioned SQL worth a prototype. It is what Beads runs on |
| ElectricSQL (https://electric-sql.com/blog/2025/03/17/electricsql-1.0-released), PowerSync (https://powersync.com/blog/new-open-era-for-powersync) | Apache-2.0; FSL | Sync from Postgres to app clients | Postgres stays the truth | Skip: agents can connect to Postgres directly |

**SQLite's own limits:** "there can only be one writer at a time", and write-ahead logging "will not work on a network filesystem" (https://www.sqlite.org/wal.html).

---

## 6. Postgres features that matter

The core Postgres pages were cited from the standard documentation and not re-fetched.

- **`SELECT … FOR UPDATE SKIP LOCKED`** (https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE).
  - Many agents can claim ready tasks with no double claims.
  - No SQLite-family option offers this across machines.
- **`LISTEN` / `NOTIFY`** (https://www.postgresql.org/docs/current/sql-notify.html).
  - A trigger can announce "this task became ready" to listeners on every computer, so nothing has to poll.
  - This is the "ready entry wakes an agent" mechanism.
- **Row-level security** (https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
  - A policy can enforce clearance inside the database, so an agent's connection cannot see what it is not cleared for.
  - The same mechanism can keep trial records sealed.
- **JSONB with GIN indexes** (https://www.postgresql.org/docs/current/datatype-json.html).
  - Per-type page fields can live in one column, which keeps the design at two tables.
- **Recursive CTEs** (https://www.postgresql.org/docs/current/queries-with.html).
  - Walk blocker chains of any depth.
- **pgvector** (https://github.com/pgvector/pgvector).
  - Similarity search over pages in the same database. It could help [judge a good fit](../open-questions.md#judging-a-good-fit).
- **SQL/PGQ, the SQL:2023 property-graph standard.**
  - **Postgres 18 lacks it.**
  - Postgres 19 adds `CREATE PROPERTY GRAPH` and `GRAPH_TABLE (… MATCH …)` as a read-only graph view over existing tables, rewritten into ordinary joins (https://www.postgresql.org/docs/19/sql-create-property-graph.html, https://www.depesz.com/tag/graphs/).
  - Paths of variable length are deferred to a later release (https://thebuild.com/blog/sqlpgq-in-postgresql-19-graph-queries-without-the-graph-database/).
  - Release candidate 1 came out on 2026-10-15, and **general availability is planned for 2026-11-19, so it is not yet confirmed** (https://www.postgresql.org/about/news/postgresql-19-rc-1-released-3409/).
  - With it, the `pages` and `links` tables can be queried in graph syntax with no migration.

---

## 7. Patterns

### A graph in relational tables
- **Adjacency list.** `pages(id, path, type, fields)` and `links(src, type, dst)`, traversed with recursive CTEs. Use `UNION`, not `UNION ALL`, so that cycles stop (https://www.sqlite.org/lang_with.html).
  - simple-graph does exactly this in SQLite (https://github.com/dpapathanasiou/simple-graph).
- **Closure table.** Stores every ancestor-to-descendant path. Fast subtree reads, at the cost of extra writes on moves (https://www.percona.com/blog/moving-subtrees-in-closure-table/).
  - Arity's paths are already a hierarchy, so an indexed prefix match (`path LIKE 'projects/arity/%'`) covers most subtree reads (inference).

### Files with a side index
- zk, markdowndb, org-roam, SilverBullet and Fossil all treat the index as derived from the files and rebuildable at any time (https://zk-org.github.io/zk/notes/notebook.html, https://docs.silverbullet.md/Object, https://fossil-scm.org/home/doc/trunk/www/tickets.wiki).
- Because of SQLite's one-writer rule, the index is per machine or behind one service, never shared over a network filesystem (https://www.sqlite.org/wal.html).

### Git with many writers
- **Pushes must fast-forward,** so concurrent writers are rejected and must fetch and rebase. `--force-with-lease` acts as a compare-and-swap on a ref, and `--atomic` makes a push of several refs all-or-nothing (https://git-scm.com/docs/git-push).
- **One file per page** confines conflicts to concurrent edits of the same page. Append-only files can use the `merge=union` driver, which comes with a warning about its implications (https://git-scm.com/docs/gitattributes).
- **One ref per entity holding an operation log** (git-bug) merges without conflicts (https://github.com/git-bug/git-bug/blob/trunk/doc/design/data-model.md).
- **No surveyed tool uses git as the live store for exclusive task claiming.** Claiming needs a compare-and-swap, and git offers that only per ref, with a fetch round trip each time (inference).

### Append-only logs for audit and sealing
- **Event sourcing.** Record every change as an event and derive state by replaying them. The log doubles as the audit trail (https://martinfowler.com/eaaDev/EventSourcing.html).
  - Fossil tickets and git-bug apply this on top of version control.
- **Verifiable logs.** In an append-only Merkle tree, a record "can't be modified, added or deleted without resulting in a completely new tree head hash" (https://transparency.dev/verifiable-data-structures/).
  - Recording a trial record's hash when it is sealed gives tamper evidence for the reveal (inference).

---

## Comparison table

How well each serious candidate meets the requirements in [What Arity needs](#what-arity-needs). Yes means built in; partial means possible with work or a convention; no means absent.

| Candidate | Wiki-shaped graph | Typed task fields and links | Ready queue | Atomic claim | Many writers, many machines | Exact query for "ready and cleared" | History | Licence | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **Postgres, two tables** | Partial (render pages from rows) | Yes (columns and JSONB) | Yes (a query) | Yes (`SKIP LOCKED`) | Yes | Yes, with clearance enforced by RLS | Partial (needs an event or revision table) | PostgreSQL licence | **Use as is** |
| SQLite, two tables | Partial | Yes | Yes | Yes on one machine | Only behind one service | Yes | Partial | Public domain | Use as is for a single-machine prototype |
| Beads on Dolt | No (issues only) | Yes for tasks; clearance unclear | Yes (`bd ready`) | Yes (`--claim`) | Yes (server mode, push and pull) | Partial | Yes (Dolt commits) | MIT | Reference |
| Dolt | Partial | Yes | Yes (a query) | Yes **[unverified under load]** | Yes | Yes | Yes (branch, diff, merge) | Apache-2.0 **[unverified]** | Reference; possible prototype |
| Backlog.md | Yes (markdown files) | Partial | Yes (`--ready`) | No | No (git only) | Partial | Yes (git) | MIT | Reference |
| Basic Memory | Yes | Partial (typed wikilinks) | No | No | No | Partial | Yes (if in git) | AGPL-3.0 | Reference |
| Markdown in git plus a side index | Yes | Yes (frontmatter) | Yes (from the index) | No (only with per-ref compare-and-swap) | Poor (rebase on every conflict) | Yes, from the index | Yes | — | Reference; good as an export |
| Graphiti | Partial | Yes (typed edges, bi-temporal) | No | No | Through a shared Neo4j | Raw Cypher only | Yes (temporal edges) | Apache-2.0 | Reference |
| Logseq DB | Yes | Yes (Node properties) | Partial (Datalog) | No | Paid sync, untested with agents | Yes (Datalog) | No | AGPL-3.0 | Reference |
| Anytype | Partial | Yes | No | No | Yes (CRDT) | Partial | Yes | Source-available | Reference |
| Fossil tickets | Yes (wiki) | Partial (untyped fields) | No | No (last writer wins) | Yes (sync) | Partial | Yes (artifacts) | BSD **[unverified]** | Reference |
| Neo4j, LadybugDB and other graph databases | Partial | Yes | Yes (a query) | Server only | Server only | Yes (Cypher) | No | Mixed | Skip |
| OptMem | No | No | No | No | No | No | Yes (append-only log) | None | Reference (pattern only) |

---

## Recommendation on [database storage](../open-questions.md#database-storage)

**Use Postgres for the database, with the two-table design, plus an append-only event table. Generate the wiki as a read-only view of markdown pages, optionally committed to git for browsing. Do not make markdown in git the live store, and do not adopt a graph database.**

Reasoning:

1. **Claiming is the deciding requirement.**
   - When a task becomes ready, several agents on several computers may try to take it.
   - Postgres's `FOR UPDATE SKIP LOCKED` solves that directly ([section 6](#6-postgres-features-that-matter)).
   - SQLite allows one writer and cannot be shared over a network filesystem (https://www.sqlite.org/wal.html).
   - rqlite has no explicit transactions (https://rqlite.io/docs/faq/).
   - CRDT and git-merge approaches (cr-sqlite, git-bug, Anytype) merge concurrent writes rather than refusing one, which is the wrong behaviour for an exclusive claim (inference).
2. **The other board mechanics come built in.**
   - `LISTEN`/`NOTIFY` is the wake signal.
   - Row-level security enforces clearance, so the test query is a plain query that cannot leak.
   - JSONB keeps typed fields without new tables.
3. **The graph does not need a graph database.**
   - Queries are one or two hops deep. Recursive CTEs cover blocker chains.
   - Postgres 19's SQL/PGQ gives graph syntax over the same tables when wanted.
   - Kuzu's archiving and Gel's shutdown show that graph-database vendors carry real continuity risk ([section 4](#4-graph-databases)).
4. **Wiki-ness is about presentation, not storage.**
   - A page's path is a column. Progressive disclosure is a read that returns a summary plus links, the way Basic Memory's `bm_context` does.
   - Agents can read rendered markdown pages, and a person can browse the same pages in an editor or Obsidian.
   - The tools that keep markdown as the truth (Backlog.md, Basic Memory, zk) are all single-writer or single-machine.
5. **History and sealing come from an event log.**
   - Fossil and git-bug show that state can be derived from an append-only log of changes. Event sourcing makes that log the audit trail.
   - Writing each change as an event, and computing page state from events (or maintaining both), gives the history that git would have given.
   - It also gives a natural place to seal trial records.
6. **Running it is modest.**
   - One Postgres server on one always-on computer, reached over a private network, backed up with `pg_dump` or WAL archiving ([section 6](#6-postgres-features-that-matter)).
   - It fits with [Computers](../computers.md): every machine in the pool is a client.
7. **The alternative worth a prototype is Dolt,** which Beads already runs on.
   - It gives git-style branches, diffs and push and pull of the data itself.
   - Choose it over Postgres only if versioning the data is worth a smaller ecosystem.
   - Its behaviour under concurrent claims was not checked.

**What to borrow, whatever the store:**
- From Beads: the link taxonomy, the ready and claim commands, and gates.
- From Backlog.md: readiness that fails closed, and versioned JSON output with a watch stream.
- From Graphiti: bi-temporal links.
- From Basic Memory: the page format and neighbourhood reads.
- From Fossil: derived state.

## What this contradicts or would improve in the current design

1. **"Starting with SQLite and moving to Postgres later does not change the design" is only half true** ([Database: Storage](../database.md#storage)).
   - The tables carry over. The board's mechanics do not: claiming (`SKIP LOCKED`), waking (`LISTEN`/`NOTIFY`) and clearance enforcement (row-level security) have no SQLite equivalents.
   - With SQLite, all writes from every computer would have to go through one service that implements them by hand.
2. **The storage table lists "a dedicated graph database" as the heaviest option.** With Postgres 19's SQL/PGQ, graph queries are available over the two tables themselves, so that option mostly falls away.
3. **Task states need an atomic claim.**
   - [Task states](../database.md#task-states) go from Ready to Assigned, but a ready task "wakes an agent that fits it" ([How work starts](../work.md#the-board)), and several may wake.
   - The step from Ready to Assigned should be defined as a compare-and-set, as `bd update --claim` does.
4. **Blocks and blockers should be typed links with kinds, not one field.** Beads separates blocking kinds (`blocks`, `parent-child`, `conditional-blocks`, `waits-for`) from non-blocking ones (`discovered-from`, `caused-by`, `supersedes`, `related`).
   - `discovered-from` fits papercuts, which are filed while working on another task.
   - `supersedes` fits a fix variant replacing an old one.
5. **Gates could be pages that block "done".**
   - Beads models gates as entries that wait on a PR, a CI run or a timer.
   - Modelling Arity's gates the same way lets the In gates state reuse the same readiness logic, and gives each gate result its own page to link a score to.
6. **Readiness should be computed on read and fail closed** (Backlog.md). A blocker that is missing or that the reader cannot see should count as open, not as done.
7. **Clearance belongs in the database, not only in instantiation.** With row-level security, a connection made for an agent cannot read tasks above its clearance at all. The same mechanism can enforce [blindness](../database.md#blindness): sealed trial records are unreadable to every role except the router until the run is graded.
8. **Sealed trial records should be append-only, with a hash recorded at sealing,** so the reveal can be checked against what was sealed ([section 7](#append-only-logs-for-audit-and-sealing)).
9. **Links should carry time.**
   - Ratings, model scores, "ran on" and "works on" all change.
   - Graphiti's split between when something was true and when the system recorded it ([section 2](#graphiti-zep)) lets old scores stay visible without being mistaken for current ones.
10. **Pages need a stable ID beside the path.**
    - Paths change when things are renamed or moved, and two computers can mint the same path.
    - Use a random or hash ID as the key and the path as a unique, changeable column. Beads and git-bug both use hash IDs for the second reason.
11. **Two tables become three.** Pages, links, and an event log that holds history, sealing and papercut provenance. Fields that queries filter on (state, clearance, priority, due date) are best as real or generated columns, not only inside a JSON blob.
12. **Two fields seem missing compared with existing boards:**
    - **Not before** (Beads' `defer --until`), distinct from due date: a task that is ready but should not start yet.
    - **Conflicts with** (CCPM): two tasks that must not run at the same time, for example ones that touch the same files.
13. **Agent memory products are not the database.**
    - Every surveyed memory system except Basic Memory builds its graph by model extraction and answers by similarity search. None gives exact answers about tasks, scores or seals.
    - Agent memory, if Arity wants it, is a separate layer that can sit on the same Postgres through pgvector.
14. **OptMem's pattern suits agent-run pages.** An append-only run log as the truth, a rebuildable summary as the page, and a fixed-size digest on wake. The [exit report](../blind-testing.md#grading) idea fits the same shape.
