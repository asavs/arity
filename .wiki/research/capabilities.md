# Research: capabilities and harnesses

[Back to index](../README.md)

This is a survey of existing software that Arity could reuse or learn from. It covers the [capabilities](../capabilities.md) side (registry, roles, loadouts, [slots](../blind-testing.md#slots)) and the harness dimension of [blind testing](../blind-testing.md#what-is-tested).

**Checked 2026-11-18.** Every claim has its source URL next to it. Stars, licences and last-push dates come from the GitHub API on that date; some read 2026-11-19 because they are in UTC. Package versions come from registry.npmjs.org or PyPI on the same day.

- **[UNVERIFIED]** means the claim comes only from a secondary source, or from inference, or could not be checked at all.
- **[SECONDARY]** means a non-primary source was the only one found.

Verdicts use three words: **use** (as is), **reference** (learn from it, don't depend on it), **skip**.

## Summary

1. **"agy" is Google's Antigravity CLI and "pi" is Mario Zechner's pi coding agent.** Both are confirmed from primary sources ([agy](#agy-google-antigravity-cli), [pi](#pi)). pi has moved to `earendil-works/pi`, and in version 1.x it has built-in MCP.
2. **ACP (Agent Client Protocol) is the one interface all four starting harnesses can speak today.** Claude Code and Codex use official adapters, agy has it natively, and pi uses a community adapter. See [Layers that put several harnesses behind one interface](#layers-that-put-several-harnesses-behind-one-interface).
3. **Vercel AI SDK 7 `HarnessAgent` and Rivet Sandbox Agent are the closest existing pieces to "one API, swappable harness".** Both are experimental.
4. **For skills, the Agent Skills spec (agentskills.io) is the cross-harness format.** Claude Code, Codex, agy and pi all read `SKILL.md`. They differ in which folders they scan.
5. **For MCP servers, the official MCP Registry's `server.json` is the model for describing an entry.** It still has preview status.
6. **No surveyed system has role inheritance or blind slots.** Arity has to build both itself. Each harness's flat agent file is the compile target.
7. **Keeping context small is now usually done with deferred loading plus tool search.** A small, explicit loadout is still needed for clean blind trials.

---

## Part 1: Capabilities

### MCP server registries and directories

#### Official MCP Registry

- **What it is:** "the official centralized metadata repository for publicly accessible MCP servers", backed by Anthropic, GitHub, PulseMCP, Microsoft and others ([about](https://modelcontextprotocol.io/registry/about)). It stores only metadata; the packages themselves stay on npm, PyPI, Docker Hub and similar.
- **URL:** https://github.com/modelcontextprotocol/registry. It has about 7.5k stars and was pushed 2026-11-18.
- **Licence:** a LICENSE file exists, but GitHub reports NOASSERTION. **[UNVERIFIED type]**
- **Maturity:** preview since 2025-09-08, with "breaking changes or data resets may occur" ([README](https://github.com/modelcontextprotocol/registry)). The API has been frozen at v0.1 since 2025-10-24, and v1 is reserved for general availability. No GA announcement was found ([about](https://modelcontextprotocol.io/registry/about)).
- **How it describes a server** ([server.json reference](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md)):
  - **Names** are reverse-DNS, such as `io.github.user/server`, and namespace ownership is verified by GitHub, DNS or HTTP challenge ([README](https://github.com/modelcontextprotocol/registry)).
  - **Top-level fields:** `name`, `title`, `description`, `version`, `websiteUrl`, `repository`, `_meta`.
  - **`packages[]`:** `registryType` (npm, pypi, nuget, oci, cargo, mcpb), `identifier`, `version`, `transport` (stdio, streamable-http, sse), `runtimeHint`, arguments, and `environmentVariables[]` with `isRequired`, `isSecret`, `default` and `choices`.
  - **`remotes[]`:** URL plus `headers[]` in the same shape.
  - **`_meta`:** an extension namespace for each publisher or subregistry.
- **Versioning** ([versioning](https://modelcontextprotocol.io/registry/versioning)):
  - Every server must have a version, and each published version is unique and immutable.
  - Semver is recommended. Ranges are rejected.
  - Only `status` (deprecated or deleted) can change after publishing ([aggregators](https://modelcontextprotocol.io/registry/registry-aggregators)).
- **Subregistries:** a subregistry is an aggregator that implements the same [OpenAPI spec](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/openapi.yaml) and adds its own `_meta` key. The registry code is "not designed for self-hosting" ([about](https://modelcontextprotocol.io/registry/about)).
- **Fit:** the best model for Arity's MCP registry entries. Arity's registry can act like a private subregistry: the same shape, with slot, role and trial data under an Arity `_meta` key.
- **Verdict: reference** for the schema, and optionally an upstream data source. Don't depend on it being up, and don't self-host its code.

#### GitHub MCP Registry

- **What it is:** GitHub's discovery page for MCP servers, at https://github.com/mcp, launched in September 2025 ([changelog](https://github.blog/changelog/2025-09-16-github-mcp-registry-the-fastest-way-to-discover-ai-tools/)). It shows servers from the open community registry ([blog](https://github.blog/ai-and-ml/meet-the-github-mcp-registry-the-fastest-way-to-discover-mcp-servers)). It is not open source.
- **Verdict: skip** as a dependency. It is a view onto the official registry.

#### Docker MCP Catalog, Toolkit and Gateway

- **Catalog** https://github.com/docker/mcp-registry: MIT, about 590 stars, pushed 2026-11-18.
  - Curated and contributed by pull request.
  - Docker builds and signs images as `mcp/<name>` with provenance and SBOMs ([repo](https://github.com/docker/mcp-registry)).
  - The per-server metadata format was not checked. **[UNVERIFIED]**
- **Gateway** https://github.com/docker/mcp-gateway: MIT, about 1.6k stars, pushed 2026-10-26.
  - **Profiles** group servers and can be shared as OCI artefacts.
  - Each profile has tool allow and deny lists in `<server>.<tool>` form.
  - Each server runs in its own container ([repo](https://github.com/docker/mcp-gateway)).
- **Dynamic MCP:**
  - `mcp-find` and `mcp-add` let an agent find and add servers mid-session.
  - A "code-mode" exists, which Docker itself calls "not yet reliable" ([docs](https://docs.docker.com/ai/mcp-catalog-and-toolkit/dynamic-mcp/)).
- **Fit:**
  - A profile maps closely to a loadout, and the tool allowlist trims a server's tools.
  - Container isolation suits risky servers such as computer use.
- **Verdict: use** for running servers in isolation, if Docker Desktop is acceptable on the computer pool. **Reference** for the profile model.

#### Smithery

- **What it is:** a hosted registry of MCP servers and skills.
- **Ownership:** Arcade.dev announced in August 2026 that it had acquired Smithery ([Arcade blog](https://arcade.dev/blog/smithery-joins-arcade)).
- **CLI:** https://github.com/arcadeai-labs/smithery-cli, AGPL-3.0, about 850 stars, last push 2026-05-31.
- **Verdict: skip** as a dependency. Ownership is changing, the CLI is AGPL, and it is a hosted runtime. It is fine for browsing.

#### Glama, PulseMCP, mcp.so and mcpservers.org

- **Glama** (https://glama.ai/mcp/servers) lists about 123k servers ([Glama](https://glama.ai/mcp/servers)).
  - It grades each for licence, quality and maintenance, lists the tools each server provides, and has a directory API.
  - The operator was not stated on the page. **[UNVERIFIED]**
- **PulseMCP** (https://www.pulsemcp.com/servers) lists about 21.5k servers and is one of the official registry's backers ([PulseMCP](https://www.pulsemcp.com/servers), [about](https://modelcontextprotocol.io/registry/about)).
- **mcp.so** lists about 20.8k servers, with config snippets for each client ([mcp.so](https://mcp.so)). Its source is https://github.com/chatmcp/mcp-directory (Apache-2.0).
- **mcpservers.org** is the web form of https://github.com/wong2/awesome-mcp-servers (MIT). The site refused automated fetches. **[UNVERIFIED contents]**
- **Verdict: reference.** These are places to look for candidate options when filling a slot. Glama's per-tool listings and grades are the most useful.

#### Awesome lists and reference servers

- **punkpeye/awesome-mcp-servers** (https://github.com/punkpeye/awesome-mcp-servers): MIT, about 98k stars, pushed 2026-11-17. It tags each entry with language, scope (local, cloud or embedded) and OS ([README](https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md)).
  - **Verdict: reference.** Its tags are a usable taxonomy for registry entries.
- **appcypher/awesome-mcp-servers** (https://github.com/appcypher/awesome-mcp-servers) is archived.
  - **Verdict: skip.**
- **modelcontextprotocol/servers** (https://github.com/modelcontextprotocol/servers) holds the reference servers, such as fetch, filesystem and git. It has about 92k stars and is active.
  - **Verdict: use** for those servers.

#### Anthropic connectors directory and MCP bundles (.mcpb)

- **Directory:** Anthropic's catalogue of MCP servers for claude.ai, Desktop and Claude Code ([docs](https://www.claude.com/docs/connectors/directory)). It accepts remote servers, desktop extensions (MCPB) and MCP Apps ([submission](https://claude.com/docs/connectors/building/submission)).
- **MCPB** (formerly DXT): https://github.com/modelcontextprotocol/mcpb. Existing code is MIT and new contributions are Apache-2.0. It has about 2.2k stars, last push 2026-05-26.
  - A `.mcpb` file is a packed local server with a manifest ([MANIFEST.md](https://raw.githubusercontent.com/modelcontextprotocol/mcpb/main/MANIFEST.md)).
  - The manifest version is 0.3. Required fields: `name`, `version`, `description`, `author`, `server`.
  - Useful optional fields:
    - `user_config`, which types the values a user must supply (directory, file, string, number, boolean)
    - `compatibility`, which covers client versions, platforms and runtimes
    - `tools` and `prompts`
  - `mcpb` is also one of the official registry's package types ([server.json reference](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md)).
- **Fit:** `user_config` and `compatibility` are good patterns for registry fields such as "needs a workspace path" or "macOS only", which the computer pool needs to know.
- **Verdict: reference.** Use it as is only for distributing local servers to Claude Desktop.

### Skill formats and collections

#### Agent Skills open standard (agentskills.io)

- **What it is:** "originally developed by Anthropic, released as an open standard" ([agentskills.io](https://agentskills.io)).
- **URL:** https://github.com/agentskills/agentskills. Apache-2.0, about 27.4k stars, pushed 2026-11-04.
- **Format** ([specification](https://agentskills.io/specification)):
  - A skill is a folder containing `SKILL.md`, with optional `scripts/`, `references/` and `assets/` folders.
  - **Required frontmatter:** `name`, which is 1–64 characters of `[a-z0-9-]` and must match the folder name, and `description`, up to 1024 characters, saying what the skill does and when to use it.
  - **Optional frontmatter:** `license`, `compatibility`, `metadata` (a string map) and an experimental `allowed-tools`.
  - **There is no version field.** The examples put one in `metadata.version`.
- **Progressive disclosure** ([specification](https://agentskills.io/specification)):
  - The name and description, about 100 tokens, load at startup.
  - The body loads when the skill is used. The spec recommends keeping it under 5k tokens.
  - Referenced files load only when needed.
- **Adoption:** all four of Arity's starting harnesses read it.
  - Claude Code ([skills docs](https://code.claude.com/docs/en/skills))
  - Codex ([build skills](https://learn.chatgpt.com/docs/build-skills))
  - agy ([migration guide](https://antigravity.google/docs/cli/gcli-migration))
  - pi ([skills doc](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/skills.md))
  - Secondary sources count 30+ adopting tools **[SECONDARY]** ([noqta](https://www.noqta.tn/en/news/skill-md-open-standard-30-ai-coding-tools-adoption-2026)).
- **Verdict: use** as Arity's canonical skill format. Arity versions skills itself, for example by git commit and content hash.

#### Where each harness looks for skills

The format is shared but the folders are not.

| Harness | Skill folders |
|---|---|
| Claude Code | `~/.claude/skills/`, `.claude/skills/`, plugin `skills/`. It does **not** read `.agents/` ([skills](https://code.claude.com/docs/en/skills), [memory](https://code.claude.com/docs/en/memory)) |
| Codex | `.agents/skills` from the working directory up to the repo root, `~/.agents/skills`, `/etc/codex/skills`. Extra roots can be set at runtime through the app-server's `skills/extraRoots/set` ([build skills](https://learn.chatgpt.com/docs/build-skills), [app-server](https://learn.chatgpt.com/docs/app-server)) |
| agy | `~/.gemini/skills/` or `~/.gemini/antigravity-cli/skills/` globally; `.agents/skills/` in the workspace ([migration guide](https://antigravity.google/docs/cli/gcli-migration)) |
| pi | Its own folders, plus `~/.agents/skills/` and `.agents/skills/` ([skills doc](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/skills.md)) |

**Consequence for Arity:** `.agents/skills/` covers Codex, agy and pi. Claude Code needs the same skills placed in `.claude/skills/`, or passed through `--plugin-dir`.

#### Claude Code's extra skill fields

- **What Claude Code adds** beyond the spec ([skills docs](https://code.claude.com/docs/en/skills)):
  - `when_to_use`
  - `disable-model-invocation`, which removes the skill from the listing entirely
  - `user-invocable`
  - `allowed-tools` and `disallowed-tools`
  - `model` and `effort`
  - `context: fork` with `agent`, which runs the skill as a subagent
  - `hooks`
  - `paths`, which limits the skill to matching file globs
- **Behaviour:** unknown fields are ignored. The combined description and `when_to_use` are cut at 1,536 characters.
- **Fit:** `paths` and `disable-model-invocation` are direct ways to keep a loadout's skill listing short.
- **Verdict: use** when the harness is Claude Code. Keep these fields in a Claude Code overlay on the registry entry, not in the shared `SKILL.md` frontmatter. Other surfaces, such as the Skills API, reject unknown keys **[per the subagent's reading of the Claude docs; UNVERIFIED by me]**.

#### anthropics/skills

- **URL:** https://github.com/anthropics/skills. About 197k stars, pushed 2026-11-16.
- **Licence:** no repo-wide licence. Many skills are Apache-2.0, including the document skills (docx, pdf, pptx, xlsx). Check each folder ([README](https://github.com/anthropics/skills)).
- **Distribution:** installable as a Claude Code plugin marketplace.
- **Verdict: use** the document skills. **Reference** for how to write skills.

#### Claude Code plugins and marketplaces

- **marketplace.json** lives at `.claude-plugin/marketplace.json` and lists plugins ([marketplace docs](https://code.claude.com/docs/en/plugin-marketplaces)).
  - Each plugin's source can be a path, GitHub, git subfolder, URL, archive or npm.
  - Git sources can be pinned to a `ref` or `sha`.
- **Official directory:** https://github.com/anthropics/claude-plugins-official. Apache-2.0, about 47.7k stars.
- **What a plugin bundles:** skills, agents, commands, hooks and MCP servers.
- **Caveat:** subagents shipped in a plugin ignore `mcpServers`, `hooks` and `permissionMode` ([subagents docs](https://code.claude.com/docs/en/sub-agents)). A role that needs its own MCP servers must be written as a `.claude/agents/` file or passed with `--agents`, not shipped in a plugin.
- **Verdict: use** as a delivery format for Claude Code. **Reference** for pinning sources.

#### obra/superpowers

- **URL:** https://github.com/obra/superpowers. MIT, about 321k stars, pushed 2026-11-03.
- **What it is:** a software-development method built from skills, including brainstorming, writing and executing plans, TDD, systematic debugging, verification before completion, code review and git worktrees.
- **Harnesses:** installs into Claude Code, Codex, Cursor, OpenCode, Gemini CLI and others ([README](https://github.com/obra/superpowers)).
- **Fit:** a strong candidate for the software engineer role's standing skills.
- **Verdict: use**, cherry-picking skills. Its meta skill, which fires on its own, costs context, and that cost should be measured as a trial.

#### Skill installers and lists

- **vercel-labs/skills (`npx skills`)** (https://github.com/vercel-labs/skills): MIT, about 40k stars, pushed 2026-11-17.
  - It installs skills into "OpenCode, Claude Code, Codex, Cursor, and 75 more".
  - Its `skills-lock.json` records the git commit and a content hash for each skill, and supports `--frozen-lockfile`.
  - Telemetry is on by default; `DISABLE_TELEMETRY=1` turns it off ([README](https://github.com/vercel-labs/skills)).
  - **Verdict: use or reference.** It has the best skill pinning model found, and Arity's registry can copy the commit-plus-hash idea.
- **SkillsMP** (https://skillsmp.com) collects 21k+ skills scraped from GitHub, with little filtering ([about](https://skillsmp.com/about)).
  - **Verdict: skip.**
- **Lists:**
  - VoltAgent/awesome-agent-skills (https://github.com/VoltAgent/awesome-agent-skills, MIT)
  - ComposioHQ/awesome-claude-skills (https://github.com/ComposioHQ/awesome-claude-skills, no licence)
  - hesreallyhim/awesome-claude-code (https://github.com/hesreallyhim/awesome-claude-code)
  - **Verdict: reference** for discovery. A list with no licence gives no right to copy.

### Roles, personas and system prompts

#### Claude Code subagent files

- **Format:** Markdown files in `.claude/agents/` or `~/.claude/agents/`, or JSON passed with `--agents` ([subagents docs](https://code.claude.com/docs/en/sub-agents)). The body is the system prompt.
- **Frontmatter fields:**
  - `name` and `description` (required)
  - `tools`, `disallowedTools`
  - `model`, `effort`, `permissionMode`, `maxTurns`
  - `skills`, which preloads skills
  - `mcpServers`, inline or by reference; inline servers connect only while that agent runs
  - `hooks`, `memory`, `isolation: worktree`
- **Using one as the main agent:** `claude --agent <name>` runs the main session as that agent ([CLI reference](https://code.claude.com/docs/en/cli-reference)). That is exactly "start an agent in a role".
- **No inheritance:** one agent cannot extend another.
- **Verdict: use** as the compile target for a resolved role when the harness is Claude Code.

#### Codex custom agents

- **Format:** one TOML file per agent in `~/.codex/agents/` or `.codex/agents/` ([subagents docs](https://learn.chatgpt.com/docs/agent-configuration/subagents)).
  - Required keys: `name`, `description`, `developer_instructions`.
  - Optional keys: model settings and `skills.config`.
- **Limit:** a custom agent **cannot set its own `mcp_servers`, sandbox, approval policy or model provider**; it takes these from the parent.
- **Workaround:** for the main agent, Arity can still inject MCP servers through `-c` overrides or a per-agent `CODEX_HOME`.
- **Verdict: reference.** It is a weaker compile target than Claude Code's. Arity should configure Codex per process, not per subagent file.

#### AGENTS.md

- **What it is:** a project instruction file, where the nearest file wins. Stewarded by the Agentic AI Foundation under the Linux Foundation, and used by "over 60k open-source projects" ([agents.md](https://agents.md)).
- **URL:** https://github.com/agentsmd/agents.md (MIT).
- **Who reads it:**
  - Codex reads it natively ([AGENTS.md docs](https://learn.chatgpt.com/docs/agent-configuration/agents-md)).
  - So do agy ([migration guide](https://antigravity.google/docs/cli/gcli-migration)) and pi ([configuration](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/configuration.md)).
  - Claude Code reads it only when no CLAUDE.md exists ([memory](https://code.claude.com/docs/en/memory)).
- **Fit:** this is project context, which Instantiate step 3 loads. It is not a role.
- **Verdict: use** for project context. It does not replace roles.

#### Role and prompt collections

| Project | Facts | Fit | Verdict |
|---|---|---|---|
| wshobson/agents https://github.com/wshobson/agents | MIT, about 42.7k stars, pushed 2026-11-18. 199 agents, 185 skills, grouped into 93 plugins. Written once in Claude Code's format, then **generated for Codex, Cursor, OpenCode, Antigravity, Copilot and Pi**, validated in CI ([README](https://github.com/wshobson/agents)) | Plugins grouped by domain look like role kits. The generator is the "compile a role to each harness" step Arity needs | **Reference** for the cross-harness compiler. Cherry-pick prompts to seed roles |
| VoltAgent/awesome-claude-code-subagents https://github.com/VoltAgent/awesome-claude-code-subagents | MIT, about 26.7k stars. 100+ subagents, including an iOS developer. No inheritance ([README](https://github.com/VoltAgent/awesome-claude-code-subagents)) | Prompt library | **Reference** for seeding role prompts |
| CrewAI https://github.com/crewAIInc/crewAI | MIT, about 61k stars. Agents are `role`, `goal`, `backstory` with `{placeholder}` templating. No inheritance ([docs](https://docs.crewai.com/en/concepts/agents)) | The role, goal and backstory split is a reasonable structure for a role's prompt | **Reference.** Skip the framework |
| contains-studio/agents https://github.com/contains-studio/agents | No licence, last push 2025-07-28 | | **Skip** |
| f/prompts.chat https://github.com/f/prompts.chat | Generic chat personas | | **Skip** |
| Leaked system prompt collections, e.g. https://github.com/x1xhlol/system-prompts-and-models-of-ai-tools | Questionable provenance | | **Reference at most.** Do not copy into roles |

**No surveyed format supports one role extending another.** Arity's "software engineer, then iOS developer" needs its own resolver. The resolver merges the prompt and the kit along the chain and writes one flat file per harness.

### Keeping loadouts small

#### Deferred tools and tool search

- **Claude API tool search** ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)):
  - It is generally available.
  - Tools marked `defer_loading: true` stay out of context until a regex or BM25 search returns them. You can also supply your own search.
  - The docs cite about 55k tokens for five servers, typically cut by more than 85%.
  - The docs advise that selection accuracy degrades past 30–50 tools, and to keep 3–5 tools always loaded.
- **Claude Code** has MCP tool search on by default ([MCP docs](https://code.claude.com/docs/en/mcp)):
  - Only names load until a tool is needed.
  - `alwaysLoad` on a server keeps it loaded.
  - `ENABLE_TOOL_SEARCH` controls the behaviour.
- **pi** has a tool-search exposure mode for MCP ([MCP doc](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/mcp.md)).
- **Docker** has `mcp-find`/`mcp-add` ([docs](https://docs.docker.com/ai/mcp-catalog-and-toolkit/dynamic-mcp/)).
- **Cursor** reports 46.9% fewer tokens from syncing tool descriptions to files and reading them on demand ([blog](https://cursor.com/blog/dynamic-context-discovery)).
- **Verdict: use.** The loadout becomes the always-loaded set.
- **Caveat for blind slots:** do not put a slot's other options in the deferred set. Search could surface them and spoil the trial.

#### Code execution with MCP

- **Idea:** Anthropic's engineering post presents MCP servers as code files that the agent reads only when needed and calls from a sandbox. In its example, context drops from 150,000 to 2,000 tokens ([post](https://www.anthropic.com/engineering/code-execution-with-mcp)).
- **In practice:** pi has a "codemode" for MCP ([MCP doc](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/mcp.md)), and Docker has an experimental one.
- **Verdict: reference.** It is worth a later trial for tools that move a lot of data.

#### Per-server filtering

- **Example:** the GitHub MCP server takes `--toolsets`, `--tools`, `--read-only` and `--lockdown-mode` ([README](https://github.com/github/github-mcp-server)).
- **Codex:** has `enabled_tools`/`disabled_tools` for any server ([MCP docs](https://learn.chatgpt.com/docs/extend/mcp)).
- **Claude Code:** `--disallowedTools` with a bare tool name removes that tool from context ([CLI reference](https://code.claude.com/docs/en/cli-reference)).
- **Fit:** a registry entry should record a server's own filter flags, so that a loadout can say "GitHub, pull requests only, read-only".
- **Verdict: use.**

#### Gateways and proxies

| Project | Facts | Fit | Verdict |
|---|---|---|---|
| Docker MCP Gateway https://github.com/docker/mcp-gateway | MIT, about 1.6k stars. Profiles, tool filters, containers | One profile per loadout | **Use** if Docker is acceptable |
| IBM ContextForge https://github.com/IBM/mcp-context-forge | Apache-2.0, about 4.9k stars, pushed 2026-11-18. **Virtual servers** built from tools picked across backends, federation, a registry, 40+ plugins. Docs reference `1.0.0-RC-3` ([repo](https://github.com/IBM/mcp-context-forge)) | A virtual server is the closest existing thing to "a loadout as one endpoint" | **Reference**, or use if a self-hosted registry plus gateway is wanted. Heavy |
| 1MCP https://github.com/1mcp-app/agent | Apache-2.0, about 530 stars. Presets, filters, opt-in lazy loading ([repo](https://github.com/1mcp-app/agent)) | Light. Presets fit loadouts | **Reference**. Small community |
| MetaMCP https://github.com/metatool-ai/metamcp | MIT, **archived 2026-11-12** ([repo](https://github.com/metatool-ai/metamcp)) | Had overrides that rewrite a tool's description | **Skip.** The override idea is worth noting |
| Microsoft MCP Gateway https://github.com/microsoft/mcp-gateway | MIT. Kubernetes and Entra ID ([repo](https://github.com/microsoft/mcp-gateway)) | Enterprise | **Skip** |
| sparfenyuk/mcp-proxy https://github.com/sparfenyuk/mcp-proxy | MIT, about 2.8k stars. stdio to HTTP bridge | Transport only | **Use** if a bridge is needed |
| MCPJungle https://github.com/mcpjungle/MCPJungle | MPL-2.0, about 1.6k stars. Self-hosted registry and gateway **[features UNVERIFIED]** | | **Reference** |

#### Retrieval research

- **Paper:** RAG-MCP, [arXiv:2505.03275](https://arxiv.org/abs/2505.03275).
- **Method:** it retrieves the relevant MCP servers before the model sees any of them.
- **Result reported in the abstract:** more than 50% fewer prompt tokens, and tool-selection accuracy of 43.13% against a 13.62% baseline.
- **Verdict: reference.** It supports building the task-specific part of a loadout by matching the task against registry descriptions.

**Slots:** nothing surveyed picks blindly between interchangeable servers and records the outcome. Gateway profiles and virtual servers are building blocks only.

---

## Part 2: Harnesses

### Identifying "agy" and "pi"

#### agy: Google Antigravity CLI

Confirmed from three independent primary sources:

- Google's repo https://github.com/google-antigravity/antigravity-cli says to start it with `agy`.
- The official headless docs are titled "Headless mode (`agy`)" ([docs](https://antigravity.google/docs/cli/headless.md)).
- The Gemini CLI README names "Antigravity CLI (`agy`)" as its successor for free and Google AI Pro/Ultra users from 2026-06-18 ([README](https://cdn.jsdelivr.net/npm/@google/gemini-cli@0.68.0/README.md)).

It is a terminal agent, not just a launcher for the Antigravity desktop app. It shares settings with the desktop app and can hand sessions to it ([repo](https://github.com/google-antigravity/antigravity-cli)). No other tool called `agy` that fits was found.

**Gemini CLI**, the other candidate, is still Apache-2.0 and maintained ([repo](https://github.com/google-gemini/gemini-cli); about 109k stars). It is now positioned for enterprise Code Assist and paid API keys, and is "not optimized for Gemini 4+" ([README](https://cdn.jsdelivr.net/npm/@google/gemini-cli@0.68.0/README.md)).

- **Verdict:** use agy as the Google harness on a consumer plan. Gemini CLI is the fallback if an open-source Google harness is required.

#### pi: pi coding agent

Confirmed:

- The repo https://github.com/earendil-works/pi calls itself "AI agent toolkit: unified LLM API, agent loop, TUI, coding agent CLI". It is MIT, has about 122.8k stars, and was pushed 2026-11-18.
- It moved from `badlogic/pi-mono` and `@mariozechner/*`. The last `@mariozechner/pi-coding-agent` release was 0.73.1 on 2026-05-07, and `@earendil-works/pi-coding-agent` 0.74.0 appeared the same day ([npm](https://registry.npmjs.org/@earendil-works/pi-coding-agent)). Its README links badlogic's projects ([README](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/README.md)).
- Vercel's AI SDK, Cloudflare, Rivet and Sculptor all name this project "Pi" (sources in [Layers](#layers-that-put-several-harnesses-behind-one-interface)).

**pi-durable**, which the [roadmap](../roadmap.md) lists as a third-party piece, is in fact first-party:

- It is `@earendil-works/pi-durable` in the same monorepo. MIT, marked "Experimental. The API changes without notice" ([README](https://cdn.jsdelivr.net/npm/@earendil-works/pi-durable@1.5.2/README.md)).
- It commits conversations, turns, tool calls and app state before showing them, so a crashed process resumes from storage. Storage can be memory, SQLite or JSONL.
- It is a library, not the `pi` command.
- There is also an unrelated third-party "durable-pi" by disler **[UNVERIFIED details]** ([listing](https://skillsmp.com/creators/disler/durable-pi)). The roadmap may have meant either.

**Other "pi" candidates:**

| Candidate | Why it does not fit |
|---|---|
| Inflection's Pi | A consumer chatbot with no agent harness **[general knowledge, not re-verified]** |
| oh-my-pi (can1357/oh-my-pi) | A pi derivative, listed separately by Paradev ([README](https://github.com/ParadevAI/Paradev)) |
| Raspberry Pi | Unrelated |

### Claude Code

- **What it is:** Anthropic's coding agent CLI. The repo is https://github.com/anthropics/claude-code.
- **Licence:** proprietary, "© Anthropic PBC. All rights reserved." under the Commercial Terms ([LICENSE.md](https://raw.githubusercontent.com/anthropics/claude-code/main/LICENSE.md)).
- **Activity:** the latest npm release is 2.1.328, published 2026-11-18 ([npm](https://registry.npmjs.org/@anthropic-ai/claude-code)).
- **Agent SDK:**
  - TypeScript: `@anthropic-ai/claude-agent-sdk` 0.3.328, under the Commercial Terms ([npm](https://registry.npmjs.org/@anthropic-ai/claude-agent-sdk)).
  - Python: `claude-agent-sdk` 0.2.184, MIT ([PyPI](https://pypi.org/pypi/claude-agent-sdk/json)).
  - Both run the Claude Code binary as a subprocess ([TS docs](https://code.claude.com/docs/en/agent-sdk/typescript), [Python repo](https://github.com/anthropics/claude-agent-sdk-python)).

**Headless driving** ([CLI reference](https://code.claude.com/docs/en/cli-reference), [headless](https://code.claude.com/docs/en/headless)):

- **Basic flags:**
  - `-p` runs once and exits.
  - `--output-format json|stream-json` and `--input-format stream-json` allow multi-turn input over stdin.
  - `--json-schema` gives structured output.
  - `--max-turns` and `--max-budget-usd` cap a run.
- **Bare mode:**
  - `--bare` turns off automatic discovery of hooks, skills, subagents, plugins, MCP servers and CLAUDE.md.
  - What the agent needs is then passed explicitly: `--append-system-prompt[-file]`, `--settings`, `--mcp-config`, `--agents`, `--plugin-dir`.
  - The docs call it the recommended mode for scripts.
  - **Hooks passed in `--settings` do not run in bare mode.**
- **Events:** the first stream event, `system/init`, lists the model, tools, MCP servers with any connection errors, and plugins. A run can therefore check that its loadout really loaded.
- **ACP:** https://github.com/agentclientprotocol/claude-agent-acp is Apache-2.0 on GitHub, about 2.8k stars, built on the Agent SDK. The ACP registry lists it as "proprietary" ([registry](https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json)), so the licence metadata conflicts.

**Sessions** ([sessions](https://code.claude.com/docs/en/sessions)):

- **Storage:**
  - Sessions are JSONL in `~/.claude/projects/<project>/<id>.jsonl`. The format is internal and changes, so read sessions through the SDK or hook `transcript_path` instead.
  - `CLAUDE_CONFIG_DIR` moves storage, which is useful for isolating each agent.
- **Resume and continue:** `--continue`, `--resume <id>` and `--session-id <uuid>`.
- **Fork:** `--fork-session` with resume gives a new ID. Two processes resuming the same session can lose messages, so fork for concurrent use.
- **Not restored on resume:** `--mcp-config`, `--plugin-dir`, `--settings` and `--add-dir` must be passed again. Arity's Instantiate must therefore re-attach the loadout on every wake, not only on first spawn.

**Loading** ([skills](https://code.claude.com/docs/en/skills), [MCP](https://code.claude.com/docs/en/mcp), [subagents](https://code.claude.com/docs/en/sub-agents), [memory](https://code.claude.com/docs/en/memory)):

- **Skills:** `.claude/skills/`, `~/.claude/skills/`, or plugins.
- **MCP servers:**
  - `.mcp.json`, `~/.claude.json`, or `--mcp-config`.
  - `--strict-mcp-config` uses only the given servers.
  - Values can use `${VAR}` expansion.
- **System prompt:**
  - `--system-prompt[-file]` replaces the default, and `--append-system-prompt[-file]` adds to it.
  - `--agent <name>` runs the main session as a defined agent.
  - `--agents '<json>'` defines agents for this session only.
- **Instruction files:** CLAUDE.md, or AGENTS.md when no CLAUDE.md exists.
- **Tools:**
  - `--tools` limits the built-in set.
  - `--disallowedTools` removes tools.
  - `--permission-mode` sets how much is approved automatically.
- **Models:**
  - Claude only. It can go through Bedrock, Google Cloud, Microsoft Foundry or a gateway via `ANTHROPIC_BASE_URL` ([third-party](https://code.claude.com/docs/en/third-party-integrations)).
  - Anthropic "doesn't support routing Claude Code to non-Claude models through any gateway" ([gateway](https://code.claude.com/docs/en/llm-gateway)).

**Grading signals:**

- **Hooks** ([hooks](https://code.claude.com/docs/en/hooks)):
  - `Stop` and `SubagentStop` carry `last_assistant_message` and transcript paths.
  - A hook can block until something is done, by exit code 2 or `decision: "block"`.
  - A hook can be a command, an HTTP POST, or an MCP tool.
- **Cost** ([cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)):
  - The result carries `total_cost_usd` and per-model token usage.
  - The cost is a client-side estimate.

**Verdict: use.** It is in the starting set. Drive it through the Agent SDK or stream-json in `--bare` mode, with everything injected explicitly.

### Codex

- **What it is:** OpenAI's coding agent CLI, written in Rust. https://github.com/openai/codex has about 136k stars.
- **Licence:** Apache-2.0.
- **Activity:** the latest release is `rust-v0.170.0` on 2026-11-18 ([releases](https://api.github.com/repos/openai/codex/releases)).
- **SDKs:**
  - TypeScript: `@openai/codex-sdk` 0.170.0 ([npm](https://registry.npmjs.org/@openai/codex-sdk)).
  - Python: `openai-codex` 0.170.0 ([PyPI](https://pypi.org/pypi/openai-codex/json)).
- OpenAI's Codex docs now redirect from developers.openai.com to learn.chatgpt.com.

**Headless driving:**

- **`codex exec`** ([non-interactive](https://learn.chatgpt.com/docs/non-interactive-mode)):
  - `--json` streams JSONL events: `thread.started`, `turn.*` and `item.*`.
  - Other flags: `--output-schema`, `--sandbox`, `--ephemeral`, `--ignore-user-config`.
- **The app-server** is the main programmatic interface ([app-server](https://learn.chatgpt.com/docs/app-server)):
  - JSON-RPC over stdio.
  - Methods include `thread/start`, `thread/resume`, `thread/fork`, `turn/start`, `turn/steer`, `turn/interrupt`, `skills/list`, `skills/extraRoots/set` and `config/value/write`.
  - Approval requests come back to the client.
  - `codex app-server generate-json-schema` emits the schemas.
- **SDKs:**
  - The TypeScript SDK drives `exec` ([README](https://raw.githubusercontent.com/openai/codex/main/sdk/typescript/README.md)).
  - The Python SDK drives the app-server ([README](https://raw.githubusercontent.com/openai/codex/main/sdk/python/README.md)).
- **`codex mcp-server` has been removed** in favour of app-server ([CLI docs](https://learn.chatgpt.com/docs/developer-commands?surface=cli)).
- **ACP:** https://github.com/agentclientprotocol/codex-acp, Apache-2.0 per the ACP registry, built on app-server ([archived predecessor note](https://github.com/zed-industries/codex-acp)).

**Sessions:**

- **Commands:** `codex resume`, `codex fork`, `codex exec resume <id>|--last` ([CLI docs](https://learn.chatgpt.com/docs/developer-commands?surface=cli)).
- **App-server:** `thread/resume` and `thread/fork`; forks carry `forkedFromId` ([app-server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)).
- **Storage:**
  - State lives under `CODEX_HOME`, by default `~/.codex` ([advanced config](https://learn.chatgpt.com/docs/config-file/config-advanced)).
  - The per-session rollout file path is documented only in secondary sources. **[UNVERIFIED]**
  - Rollouts may be compressed, so don't parse them directly.

**Loading:**

- **Config:**
  - `~/.codex/config.toml`. A project `.codex/config.toml` is read only for trusted projects.
  - A profile is now a separate file selected with `--profile`.
  - Any key can be overridden per run with `-c key=value` ([config reference](https://learn.chatgpt.com/docs/config-file/config-reference)).
- **Skills:**
  - `.agents/skills/`, as listed above.
  - An optional `agents/openai.yaml` sets the invocation policy and dependencies.
  - Skills are switched on and off with `[[skills.config]]` ([build skills](https://learn.chatgpt.com/docs/build-skills)).
- **MCP servers** ([MCP docs](https://learn.chatgpt.com/docs/extend/mcp)):
  - `[mcp_servers.<name>]` tables in TOML.
  - Per-server keys include `enabled_tools`, `disabled_tools`, timeouts and approval mode.
  - There is no `--strict-mcp-config` equivalent. Isolating an agent's MCP servers means a per-agent `CODEX_HOME` or `--ignore-user-config` plus `-c`. **[UNVERIFIED how these combine]**
- **System prompt** ([config reference](https://learn.chatgpt.com/docs/config-file/config-reference)):
  - `model_instructions_file` replaces the built-in instructions.
  - `developer_instructions` adds to them.
  - AGENTS.md files are concatenated from the repo root down, up to 32 KiB ([AGENTS.md docs](https://learn.chatgpt.com/docs/agent-configuration/agents-md)).
- **Models** ([config reference](https://learn.chatgpt.com/docs/config-file/config-reference)):
  - Any provider with an OpenAI **Responses** API endpoint, via `[model_providers.<id>]`. `wire_api` accepts only `responses`.
  - Built-in providers include Ollama, LM Studio and Bedrock. `--oss` runs a local model.

**Grading signals:**

- **Hooks** ([hooks](https://learn.chatgpt.com/docs/hooks.md)) deliberately mirror Claude Code's:
  - `Stop` and `SubagentStop` carry `last_assistant_message`.
  - Exit code 2 or `decision: "block"` forces the agent to continue.
- **`notify`:** a separate setting that runs a program on `agent-turn-complete` ([advanced config](https://learn.chatgpt.com/docs/config-file/config-advanced)).
- **Usage:** tokens only. `turn.completed.usage` has input, cached, output and reasoning tokens. No USD cost was found. **[absence inferred]**

**Verdict: use.** It is in the starting set. Drive it through app-server, or codex-acp, which wraps app-server, with a `CODEX_HOME` per agent.

### agy (Antigravity CLI)

- **Maintainer:** Google.
- **URL:** https://github.com/google-antigravity/antigravity-cli. The repo holds a README, changelog and examples only. It has about 2.9k stars and was pushed 2026-11-19 UTC.
- **Licence:** closed source. There is no licence in the repo, and The Register reports it is closed ([The Register](https://www.theregister.com/ai-ml/2026/05/20/bye-bye-gemini-cli-google-nudges-devs-toward-antigravity/5243605)).
- **Activity:** about a dozen releases in November 2026, v1.5.0 to v1.5.12, per a third-party changelog aggregator **[SECONDARY]** ([havoptic](https://www.havoptic.com/releases/antigravity-cli/2026/11)).

**Headless driving** ([headless docs](https://antigravity.google/docs/cli/headless.md)):

- **Output:** `-p` with `--output-format text|json|stream-json`. The stream events are `init`, `step_update` and `result`.
- **Multi-turn input:** `--input-format stream-json` over stdin.
- **Other flags:** `--json-schema`, `--model`, `--effort`, `--agent <name>`, `--sandbox`.
- **Permissions:** there are no prompts in headless mode; anything not on the allow rules is soft-denied.
- **Login:** a headless run needs credentials cached from an earlier interactive login.
- **Open bug:** stdout was dropped without a terminal in [issue 76](https://github.com/google-antigravity/antigravity-cli/issues/76). **[fix status UNVERIFIED]**
- **ACP:** native, with `agy acp` ([ACP docs](https://antigravity.google/docs/cli/acp.md)). It is listed in the ACP registry as `antigravity-acp`.
- **SDK:** a Python Antigravity SDK is in preview ([overview](https://antigravity.google/docs/sdk/overview)). Architecture details are **[SECONDARY]**.

**Sessions:**

- Called conversations. Each has a `conversation_id` and can be resumed with `--continue` or `--conversation <id>` ([headless docs](https://antigravity.google/docs/cli/headless.md)).
- Storage location and forking: **[UNVERIFIED]**.

**Loading** ([migration guide](https://antigravity.google/docs/cli/gcli-migration)):

- **Instruction files:** `GEMINI.md` and `AGENTS.md`.
- **Skills:** `.agents/skills/` in the workspace, `~/.gemini/skills/` globally.
- **MCP servers:** `.agents/mcp_config.json` or `~/.gemini/config/mcp_config.json`.
- **Plugins:** `agy plugin import gemini` converts Gemini CLI extensions.
- **Also present:** hooks and custom subagents.
- **System prompt:** no documented flag to replace it was found. **[UNVERIFIED]**

**Models:** mainly Gemini. Some Claude and GPT-OSS models have been offered on some plans **[SECONDARY]** ([example](https://ralph-tui.com/docs/plugins/agents/antigravity)). It is not bring-your-own-provider.

**Risks:**

- Whether Google allows outside orchestrators to drive `agy` headless on a subscription account is unanswered in Google's own forum ([thread 1](https://discuss.ai.google.dev/t/is-external-orchestration-of-antigravity-cli-headless-mode-supported-with-account-based-usage/183051), [thread 2](https://discuss.ai.google.dev/t/is-it-allowed-for-an-open-source-tool-to-call-the-official-agy-cli-in-headless-mode/183429)).
- It is closed source.

**Verdict: use**, as planned, through `agy acp` or stream-json. Treat the terms-of-service question as open.

### pi

- **Maintainer:** Mario Zechner and Earendil.
- **URL:** https://github.com/earendil-works/pi.
- **Licence:** MIT.
- **Maturity:** v1.0.0 on 2026-10-01, v1.5.2 on 2026-11-10 ([npm](https://registry.npmjs.org/@earendil-works/pi-coding-agent)).
- **Packages:**
  - `pi-ai`, a unified multi-provider LLM API
  - `pi-agent-core`
  - `pi-coding-agent`, the CLI
  - `pi-tui`
  - `pi-durable`
  - `chord`

**Headless driving** ([CLI](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/cli.md), [RPC](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/rpc.md)):

- `-p` runs once.
- `--mode json` streams events as JSONL.
- `--mode rpc` is a long-lived JSONL command and event protocol over stdin and stdout, with a TypeScript `RpcClient`.
- There is also an in-process TypeScript SDK.
- **ACP:** no native support. The community adapter https://github.com/svkozak/pi-acp (MIT, v0.0.34, about 780 stars) is in the ACP registry ([registry](https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json)). Whether it supports pi 1.5 is **[UNVERIFIED]**.

**Sessions** ([sessions](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/sessions.md)):

- **Storage:** `~/.pi/agent/sessions/`, grouped by working directory.
- **Flags:** `--continue`, `--resume`, `--session`, `--fork`, `--no-session`.
- **Sessions are trees:**
  - `/tree` moves around a session.
  - `/fork` starts a new session from an earlier message.
  - `/clone` copies a branch.
  - A summary of the abandoned branch can be carried over.

This is the richest fork model of the four, and it maps onto Arity's [fresh or fork](../work.md#fresh-or-fork).

**Loading:**

- **Skills:** Agent Skills, including `.agents/skills/` ([skills](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/skills.md)).
- **MCP:** built in since 1.x ([MCP](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/mcp.md)):
  - Configured with `pi mcp add`, or in `~/.pi/agent/mcp.json` and `.pi/mcp.json`.
  - Supports stdio and streamable HTTP, and OAuth.
  - Has a tool-search mode and a codemode.
  - `--no-mcp` turns it off.
- **System prompt and instruction files** ([configuration](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/configuration.md)):
  - `--system-prompt <text|path>` and `--append-system-prompt`.
  - `SYSTEM.md` and `APPEND_SYSTEM.md`.
  - It reads `AGENTS.md`, `AGENTS.override.md` and `CLAUDE.md`, and discovery can be turned off.
- **Extensions:** a large TypeScript extension API, and packages via npm or git.
- **Models:** any provider, via `--provider` and `--model provider/id:thinking`, with custom providers in `models.json` ([CLI](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/cli.md)).

**Verdict: use**, as planned. pi is the only harness in the set that can run any model. That makes it the harness for holding the harness fixed while the model varies, and for running local models.

---

## Layers that put several harnesses behind one interface

### How each harness can be driven

| Harness | Native structured interface | ACP |
|---|---|---|
| Claude Code | Agent SDK, or stream-json in and out | claude-agent-acp (official org) |
| Codex | app-server JSON-RPC, or `exec --json` | codex-acp (official org, wraps app-server) |
| agy | `-p` with stream-json in and out | native `agy acp` |
| pi | `--mode rpc`, or the in-process SDK | community pi-acp |

### Protocols

- **Agent Client Protocol (ACP)**: https://agentclientprotocol.com, https://github.com/agentclientprotocol/agent-client-protocol.
  - **What it is:** JSON-RPC over stdio between a client and an agent.
  - **Licence and activity:** Apache-2.0, about 4.7k stars. Release v1.19.0 and a v2.0.0 alpha, both 2026-11-18.
  - **Registry:** 40+ agents, including all four harnesses in Arity's starting set ([registry](https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json)).
  - **Features:** the v2 Claude adapter supports new, list, resume, fork and delete for sessions, MCP over ACP, and subagents ([README](https://github.com/agentclientprotocol/claude-agent-acp)).
  - **Caveats:** vendor-specific features travel in `_meta`, v2 is alpha, and the pi adapter is a 0.0.x community project.
  - **Verdict: use** as the default harness interface, with a native escape hatch for each harness.
- **A2A** (https://github.com/a2aproject/A2A) is for communication between opaque agents, not for driving coding CLIs.
  - **Verdict: skip** for this layer. It is possibly a reference for agent-to-agent messages.
- **Agent Protocol** (now https://github.com/agi-inc/agent-protocol) was last pushed 2025-04-08.
  - **Verdict: skip.**

### SDK layers

| Project | Facts | How it drives harnesses | Verdict |
|---|---|---|---|
| **Vercel AI SDK 7 `HarnessAgent`** ([changelog](https://vercel.com/changelog/program-agent-harnesses-with-ai-sdk), [docs](https://ai-sdk.dev/docs/ai-sdk-harnesses)) | `@ai-sdk/harness` with adapters `harness-claude-code`, `harness-codex`, `harness-pi`, `harness-opencode`, `harness-acp`. Apache-2.0, about v1.0.19x ([npm](https://registry.npmjs.org/@ai-sdk/harness-pi/latest)). Experimental | Claude Code through the Agent SDK. Codex through app-server ([README](https://cdn.jsdelivr.net/npm/@ai-sdk/harness-codex@latest/README.md)). pi in-process ([README](https://cdn.jsdelivr.net/npm/@ai-sdk/harness-pi@latest/README.md)). Any ACP agent through `harness-acp` ([README](https://cdn.jsdelivr.net/npm/@ai-sdk/harness-acp@latest/README.md)). One `HarnessAgent({harness, tools, skills, instructions})` API. Sandbox-centric | **Use to prototype** if Arity's orchestrator is TypeScript. agy through `harness-acp` is **[UNVERIFIED]**. Pin versions |
| **Rivet Sandbox Agent** (https://github.com/rivet-dev/agents) | Apache-2.0, about 1.65k stars, v0.5.6 (2026-11-05) | One HTTP API across Claude Code, Codex, OpenCode, Cursor, Amp and Pi, with a common session schema that can be saved to Postgres ([README](https://github.com/rivet-dev/agents/blob/main/sandbox-agent/README.md)). Exposes an ACP-over-HTTP endpoint. No agy | **Reference**, or use if an HTTP control plane is wanted and agy can be added |
| **vercel-labs/durable-harness** (https://github.com/vercel-labs/durable-harness) | Apache-2.0, 0.0.2 | Durable Claude Code, Codex, OpenCode and Pi sessions on Postgres ([docs](https://vercel.com/docs/agent-harnesses/harnesses)) | **Reference** for durability and reconnecting |
| **OpenHands Software Agent SDK `ACPAgent`** (https://github.com/OpenHands/software-agent-sdk) | MIT, v1.62.0 (2026-11-11) | Spawns any ACP server. Defaults are claude-agent-acp, codex-acp, gemini ([docs](https://docs.openhands.dev/sdk/guides/agent-acp)) | **Reference**, or use if the host is Python |
| ai-sdk-provider-claude-code and others (ben-vargas) | MIT, community | Wrap a CLI agent as a "model", which loses sessions and tool events | **Skip** |
| mozilla-ai/any-agent (https://github.com/mozilla-ai/any-agent) | Maintenance mode | Wraps frameworks, not CLIs | **Skip** |

### Orchestrators and managers

| Project | Facts | Mechanism | Verdict |
|---|---|---|---|
| vibe-kanban https://github.com/BloopAI/vibe-kanban | Apache-2.0, about 28.7k stars. The company shut down on 2026-04-10, and the project continues as community-maintained ([post](https://www.vibekanban.com/blog/shutdown)) | One executor per harness: Claude via stream-json, Codex via app-server, Gemini via ACP, plus a generic ACP executor ([claude.rs](https://github.com/BloopAI/vibe-kanban/blob/main/crates/executors/src/executors/claude.rs)) | **Reference.** The best real example of a mixed native-plus-ACP executor layer, and a task board too |
| Sculptor https://github.com/imbue-ai/sculptor | MIT, v0.50.0 (2026-11-16) | Claude Code via stream-json, **pi via RPC mode**, anything else as a plain terminal ([docs](https://github.com/imbue-ai/sculptor/blob/main/docs/help/integrated_harnesses.md)) | **Reference** for deep integration with pi |
| Paradev (formerly Superset) https://github.com/ParadevAI/Paradev | MIT, about 16.4k stars | Terminals per worktree. Lists agy, Claude Code, Codex, Pi as supported | **Reference** for workspace management only |
| claude-squad https://github.com/smtg-ai/claude-squad | AGPL-3.0 | tmux and worktrees | **Skip** |
| ccmanager https://github.com/kbwo/ccmanager | MIT | Scrapes terminal output | **Skip** |
| Conductor https://www.conductor.build | Closed Mac app | Not documented | **Skip** |
| coder/agentapi https://github.com/coder/agentapi | Archived | Terminal screen-scraping | **Skip** |
| Terragon https://github.com/terragon-labs/terragon-oss | Shut down | | **Skip** |
| pal-mcp-server `clink` https://github.com/BeehiveInnovations/pal-mcp-server | Last push 2025-12-15 | Runs other CLIs as an MCP tool | **Reference** for "another harness as a tool" |
| AgentPipe https://github.com/kevinelliott/agentpipe | Low activity | `exec` wrapping | **Skip** |

### Other harnesses and model proxies

- **OpenCode** (https://github.com/anomalyco/opencode, formerly sst/opencode): MIT, about 228k stars.
  - `opencode serve` exposes an OpenAPI HTTP API, including session fork ([docs](https://opencode.ai/docs/server/)).
  - **Verdict: reference** for session API design. It is also a candidate fifth harness.
- **goose** (https://github.com/aaif-goose/goose): Apache-2.0. It is both an ACP server and an ACP client that can drive Claude Code and Codex ([blog](https://goose-docs.ai/blog/2026/04/08/goose-acp-and-new-tui)).
  - **Verdict: reference.**
- **Claude Code Router** (https://github.com/musistudio/claude-code-router): MIT, about 38k stars. It swaps models under Claude Code, Codex, Pi and others ([README](https://github.com/musistudio/claude-code-router)).
  - **Verdict:** a model-layer tool, not a harness layer. **Reference.** Using it against Anthropic's stated support policy is a risk.
- **LiteLLM** (https://github.com/BerriAI/litellm): a model API gateway.
  - **Verdict: use** only as a model endpoint, for example for pi's custom providers.

---

## Harness comparison

Checked 2026-11-18. Sources are in the sections above.

| | Claude Code | Codex | agy | pi |
|---|---|---|---|---|
| Maintainer | Anthropic | OpenAI | Google | Mario Zechner / Earendil |
| Licence | Proprietary; Python SDK MIT | Apache-2.0 | Closed source | MIT |
| Latest (checked) | 2.1.328 | 0.170.0 | v1.5.x **[SECONDARY]** | 1.5.2 |
| Models | Claude only | Responses-API providers, Ollama, LM Studio, Bedrock | Gemini mainly; set by Google plan | Any provider via pi-ai |
| Headless | `-p`, stream-json in and out, `--bare` | `exec --json` | `-p`, stream-json in and out | `-p`, `--mode json` |
| Long-lived protocol | Agent SDK (TS, Py) | app-server JSON-RPC | `--input-format stream-json`; Python SDK preview | `--mode rpc`, TS SDK |
| ACP | claude-agent-acp (official org) | codex-acp (official org) | Native `agy acp` | pi-acp (community, 0.0.x) |
| Session store | `~/.claude/projects/…jsonl` (`CLAUDE_CONFIG_DIR`) | `CODEX_HOME` | **[UNVERIFIED]** | `~/.pi/agent/sessions/` |
| Resume | `--resume`, `--continue`, `--session-id` | `exec resume`, `thread/resume` | `--continue`, `--conversation` | `--continue`, `--resume`, `--session` |
| Fork | `--fork-session` | `codex fork`, `thread/fork` | **[UNVERIFIED]** | `--fork`, `/fork`, `/clone`; tree sessions |
| Skills folder | `.claude/skills/` only | `.agents/skills/` | `.agents/skills/`, `~/.gemini/skills/` | `.agents/skills/` and its own |
| MCP config | `.mcp.json`, `--mcp-config`, `--strict-mcp-config` | `[mcp_servers.*]` TOML, `-c` | `mcp_config.json` | `mcp.json`, `pi mcp add` |
| Per-server tool filter | `--disallowedTools` | `enabled_tools`, `disabled_tools` | **[UNVERIFIED]** | **[UNVERIFIED]** |
| Replace system prompt | `--system-prompt[-file]` | `model_instructions_file` | none found **[UNVERIFIED]** | `--system-prompt`, `SYSTEM.md` |
| Append system prompt | `--append-system-prompt[-file]` | `developer_instructions` | via `GEMINI.md`/`AGENTS.md` | `--append-system-prompt`, `APPEND_SYSTEM.md` |
| Role as main agent | `--agent`, `--agents` JSON | agent TOML (no own MCP) | `--agent` | via prompt flags |
| Instruction file | CLAUDE.md (AGENTS.md if none) | AGENTS.md | GEMINI.md, AGENTS.md | AGENTS.md, CLAUDE.md |
| Grading hooks | `Stop`, `SubagentStop` | Same names and fields, plus `notify` | hooks exist; fields **[UNVERIFIED]** | via extensions **[UNVERIFIED]** |
| Cost in output | USD estimate + tokens | Tokens only | `usage` in JSON | **[UNVERIFIED]** |
| Main risk | Licence; Claude-only | No per-subagent MCP | Closed; terms for orchestration unclear | ACP adapter immature |

---

## How a registry entry should describe things so it works across harnesses

The principle: **store each entry once, in a neutral form close to the existing open standards, and generate each harness's files at Instantiate step 6.** No harness should be the source of truth. The wshobson/agents generator shows this works for skills and agents ([README](https://github.com/wshobson/agents)).

### MCP server entry

- **Base it on `server.json`:** reverse-DNS `name`, `version`, `packages[]`/`remotes[]` with transport, and env vars with `isSecret` ([reference](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md)).
- **Add an Arity block under `_meta`:**
  - `slot`: the capability tags it can fill, such as `computer-use`
  - `tool_filter`: the server's own filter flags, plus an allow or deny list of tool names
  - `requirements`: OS, a GUI, a workspace path; borrow MCPB's `compatibility` and `user_config`
  - `grants`: which credentials the [access broker](../access.md) must issue, mapped from the `isSecret` variables
  - `context_cost`: measured token cost of its tool descriptions
  - `always_load`: whether it stays loaded or can be deferred
- **Generate** `--mcp-config` JSON for Claude Code, `[mcp_servers.x]` or `-c` for Codex, `mcp_config.json` for agy, and `mcp.json` for pi.

### Skill entry

- **Store a plain Agent Skills folder.** Keep the frontmatter to the spec's fields only ([specification](https://agentskills.io/specification)).
- **Pin it** by git commit and content hash, in the style of `skills-lock.json` ([vercel-labs/skills](https://github.com/vercel-labs/skills)).
- **Keep harness overlays beside it:** a Claude Code block (`paths`, `disable-model-invocation`, `context: fork`) and a Codex `agents/openai.yaml`. Merge the overlay only when building for that harness.
- **Install it** into `.agents/skills/` for Codex, agy and pi, and `.claude/skills/` (or a generated plugin) for Claude Code.

### Built-in tool entry

- **Record a neutral capability name**, such as `shell`, `edit-files` or `web-fetch`.
- **Map it to each harness's own tool name or switch.** Examples: `--tools` in Claude Code, `features.*` in Codex, allow rules in agy.
- Some harnesses cannot switch some tools off. Record that as "not removable on harness X", so that a trial does not assume a removal took effect.

### Role entry

- **Arity's own file:**
  - `extends:` naming the parent role
  - prompt sections, optionally split into role, goal and backstory as in CrewAI ([docs](https://docs.crewai.com/en/concepts/agents))
  - the standing kit, as registry names and slots
  - the default variant for each dimension
- **The resolver flattens the chain:**
  - Prompts concatenate parent first.
  - Kits are unioned.
  - A child can remove what a parent added.
- **It writes the result as:**
  - Claude Code: `--agents` JSON or a `--system-prompt-file`
  - Codex: `model_instructions_file`/`developer_instructions` plus per-process MCP config
  - agy: `--agent` plus `AGENTS.md`
  - pi: `--system-prompt`

### Harness entry

The harness is a registry entry like any other, and it fills the "harness" dimension. It records:

- driver: ACP command, or native mode
- supported models
- how to isolate state (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, pi's session dir)
- how to resume and fork
- which grading events it emits
- whether it reports cost or only tokens

The router reads this to know which models a harness can pair with. Instantiate reads it to know how to build and wake the agent.

---

## What contradicts or would improve the current design

1. **Harness and model are not independent dimensions.** [Blind testing](../blind-testing.md#what-is-tested) lists them separately, but:
   - Claude Code runs only Claude ([gateway docs](https://code.claude.com/docs/en/llm-gateway)).
   - agy runs Google's offered models.
   - Codex needs a Responses-API provider.
   - Only pi runs anything.

   The router should pick a valid (harness, model) pair. Comparisons of harnesses on the same model are only possible through pi, or through Codex with a Responses-compatible endpoint.
2. **"Some harnesses for their own models, plus pi" also has terms-of-service weight.**
   - Driving agy headless on a subscription account has no clear answer from Google ([forum](https://discuss.ai.google.dev/t/is-external-orchestration-of-antigravity-cli-headless-mode-supported-with-account-based-usage/183051)).
   - Claude Code's licence is proprietary ([LICENSE.md](https://raw.githubusercontent.com/anthropics/claude-code/main/LICENSE.md)).

   The [endpoint pool](../model-router.md) should record, for each subscription, which harnesses it may be driven through.
3. **The roadmap's "pi-durable" is first-party pi, not a third-party piece.** It is a library harness for building a durable app around pi's core ([README](https://cdn.jsdelivr.net/npm/@earendil-works/pi-durable@1.5.2/README.md)). It could serve as Arity's durable agent runtime, not just a slot option.
4. **Waking a cold agent must re-attach the loadout.** Claude Code does not restore `--mcp-config`, `--plugin-dir` or `--settings` on resume ([sessions](https://code.claude.com/docs/en/sessions)). [Instantiate](../instantiate.md)'s "creating and waking are the same six steps" is the right shape. Step 6 must assume nothing survives in the harness.
5. **The loadout should be injected explicitly, never discovered.**
   - Use Claude Code `--bare` with `--strict-mcp-config`, a per-agent `CODEX_HOME` or `--ignore-user-config` for Codex, and `--no-mcp`/discovery off for pi plus explicit config.
   - Otherwise user-level or project-level files leak into the agent, and the trial record no longer says what the agent actually had.
   - Claude Code's `system/init` event lists the tools and servers that really loaded ([headless](https://code.claude.com/docs/en/headless)). Store it in the trial record.
6. **Blind slots need the unused options kept out completely.** With tool search on by default, Claude Code and pi will surface anything deferred and configured ([Claude Code MCP](https://code.claude.com/docs/en/mcp)). Only the chosen option should be configured at all.
7. **Grading can use one hook adapter across Claude Code and Codex.** Their `Stop`/`SubagentStop` inputs share names and fields ([Claude hooks](https://code.claude.com/docs/en/hooks), [Codex hooks](https://learn.chatgpt.com/docs/hooks.md)). This fits [the grading hook](../blind-testing.md#grading).
   - Caveat: Claude Code hooks do not run in `--bare` mode unless passed another way, such as SDK callbacks.
   - agy and pi need their own adapters.
8. **Cost is reported unevenly.** Claude Code gives a USD estimate. Codex gives tokens only. Scores that use cost need Arity's own price table for every model.
9. **Roles need Arity's own resolver.** No existing format supports inheritance. Codex subagent files cannot carry their own MCP servers ([subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)), and Claude Code plugin agents ignore `mcpServers` ([subagents](https://code.claude.com/docs/en/sub-agents)). Arity should configure each agent as its own harness process, not as a harness-native subagent, whenever the loadout matters.
10. **Fresh-or-fork maps unevenly onto harnesses.**
    - Claude Code, Codex and pi all fork natively.
    - agy's fork support is **[UNVERIFIED]**.
    - A fork across harnesses, such as a Claude Code parent with a pi child, cannot reuse the transcript. It needs a summary handoff, like pi's branch summary ([sessions](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@1.5.2/docs/sessions.md)).

    [How work starts](../work.md#fresh-or-fork) should say what a cross-harness fork means.
11. **Skill versions are not part of the standard.** The [blind testing](../blind-testing.md#what-is-tested) "which version of a skill" dimension needs the registry to hold versions itself, by commit and hash.
12. **A ready-made harness layer exists for a TypeScript orchestrator.** AI SDK 7 `HarnessAgent` and the ACP registry cover most of the harness dimension. Building a thin ACP client, with native escape hatches, is likely cheaper than four bespoke drivers. vibe-kanban's executors are the working example ([executors](https://github.com/BloopAI/vibe-kanban/blob/main/crates/executors/src/executors/claude.rs)).
13. **Registry-in-database** ([open question](../open-questions.md#registry-in-the-database)): the official MCP Registry's model supports a yes. Entries are immutable by version, only `status` changes, and anything extra lives in a namespaced `_meta`. That shape is easy to store as database rows, with trial scores attached.

## Unverified items in one place

- The official MCP Registry's licence type.
- Docker catalog's per-server metadata format.
- Contents of mcpservers.org.
- Claude Code as an MCP server (`claude mcp serve`): not in current docs.
- The Codex per-session rollout path, and how `--ignore-user-config` combines with `-c`.
- No USD cost in Codex output: inferred from absence.
- agy:
  - session storage and fork
  - whether there is a system-prompt flag
  - hook fields
  - per-server tool filter
  - the stdout bug fix
  - which non-Gemini models are offered
  - whether outside orchestration is allowed
- pi:
  - pi-acp support for pi 1.5
  - hook equivalents and cost reporting
- `harness-acp` driving `agy acp`.
- Which "pi-durable" the roadmap meant.
- The claude-agent-acp licence: Apache-2.0 on GitHub, "proprietary" in the ACP registry.
