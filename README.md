# arity

```
                 .  .  .  .
             .  '  *  *  *  '  .
          .  *  o  o  o  o  o  *  .
        .  *  o  x  x  x  x  o  *  .
       .  *  o  x  +  +  x  o  *  .
       .  *  o  x  + [1] +  x  o  *  .    r_n = c √n
       .  *  o  x  +  +  x  o  *  .      θ_n = n × 137.507764° (Golden Angle)
        .  *  o  x  x  x  x  o  *  .      Fibonacci spirals: 21, 34, 55, 89, 144
          .  *  o  o  o  o  o  *  .
             .  '  *  *  *  '  .
                 '  '  '  '
```

One moment. N kernels. The best of each.

Arity is a minimal, local-first agent harness and multi-agent kernel built in Rust and SwiftUI.

One append-only SQLite journal (`arity.db`) and git-tree workspace engine sit underneath both the `arity` CLI and the native macOS app (`Arity.app`). For the long-term multi-agent design—blind routing, composable loadouts, and graded runs—see [`.wiki/README.md`](.wiki/README.md).

---

## Quickstart (`0.0.1`)

### Build & Launch the Mac App

```bash
# Debug build + open app/build/Arity.app
./app/build.sh

# Release build + package app/build/Arity-0.0.1-arm64.dmg
./app/package.sh

# Release build + install /Applications/Arity.app and ~/.local/bin/arity
./app/package.sh --install
```

### CLI Usage

```bash
# Configure credentials (stored mode 0600 in ~/Library/Application Support/Arity)
arity key set openai sk-...
arity login                          # or sign in with ChatGPT (Plus/Pro OAuth)
arity providers                      # inspect configured providers & readiness

# Create an isolated git-worktree thread and run a turn
arity create --repo . --model openai:gpt-4.1 --title "Refactor parser"
arity chat 1 "Inspect core/src and summarize module boundaries"

# Steer or queue follow-ups on a live thread (--mode steer | after_turn | inbox)
arity steer 1 "Focus on store.rs invariants first"

# Time-travel: fork from any historical entry's exact git tree_sha onto another model
arity log 1 --causal
arity fork 1 --entry 12 --model mlx:mlx-community/Qwen3.5-4B-MLX-4bit

# Review changes against base checkout, apply cleanly, and verify projection replay
arity diff 1
arity apply 1
arity replay-check
```

---

## Architecture

| Path | Crate / Target | Responsibility |
| --- | --- | --- |
| `core/` | `arity-core` | Append-only SQLite spine (`store.rs`), detached git-tree workspaces (`workspace.rs`), path-jailed coding tools + process-group `bash` (`tools.rs`), pooled streaming codecs (`codec.rs`), `0600` secrets (`secrets.rs`), epoch-fenced turn loop (`runner.rs`), and thin UniFFI client (`ffi.rs`). |
| `cli/` | `arity` | Intent-only CLI commands plus the single-writer worker (`arity worker`). |
| `app/` | `Arity.app` | Native macOS SwiftUI client linked against `arity-core` via UniFFI; spawns bundled `Contents/MacOS/arity-worker` and polls SQLite off the main actor. |
| `.wiki/` | Design Wiki | Lasting architecture docs and SVG diagrams for tiers, instantiation, capabilities, and blind trials. |

### Core Invariants

1. **One Durable History (`entries`):** `threads` and `deliveries` are pure projections updated in the same `BEGIN IMMEDIATE` transaction as `entries` and verified byte-for-byte via `arity replay-check`.
2. **Single Writer (`arity-worker`):** Neither the CLI nor `Arity.app` executes model turns or tools in-process. Frontends only append idempotent intents (`request_id TEXT UNIQUE`); only the worker claims `epoch + 1` and advances `head_entry_id`.
3. **Truncation & Crash Safety:** Truncated or malformed streams never emit `model.completed` and never fire tools. Mutating tools (`write_file`, `edit_file`, `bash`) commit `tool.call` before running, reap child process groups via a pipe-EOF watchdog on `SIGKILL`, and stamp a shadow-index git `tree_sha` onto `tool.result`. Missing receipts on crash become `status = "unknown"` (never auto-rerun) and restore `last_tree_sha`.
4. **One Clean Data Directory (`$ARITY_HOME`):** Defaults to `~/Library/Application Support/Arity` and holds only `arity.db`, `workspaces/`, `keys.json` (`0600`), and `codex-auth.json` (`0600`)—no unlocked JSON cache sidecars.

---

## Agent Loadout Direction (`0.0.2` → `0.0.7`)

As Arity grows from raw `provider:model` strings (`0.0.1`) into full agent loadouts (`0.0.6`–`0.0.7`), configuration separates **Engine** (a 3-way compatibility triad) from **Kit** (a left-to-right capability pipeline):

1. **`[Account/Plan ⇄ Harness ⇄ Model]` *(Engine Triad)*:** Selecting any one filters valid options in the other two without wiping compatible choices—whether starting from quota/billing (*OpenAI ChatGPT Sign-In*, *OpenAI API Free Daily*, *OpenCode Go*, *Nous `:free`*, *Claude Max*, *This Mac MLX*), holding the harness fixed (*`arity` native* vs *`pi`*, *`claude`*, *`codex`*, *`agy`*), or choosing a model first.
2. **`Effort`:** `off | minimal | low | medium | high | xhigh`, mapped to provider/harness-native reasoning budgets.
3. **`System Prompt / Persona`:** Role prompt plus explicit, cache-warm project context toggles (`AGENTS.md` / `CLAUDE.md`, `cwd`, git status).
4. **`Tools`:** Enabled built-in workspace and coordination tools.
5. **`Skills`:** Domain `SKILL.md` instruction packs attached to the kit.
6. **`MCPs`:** External tool servers (such as `cua-driver` for background computer use).

Saved loadouts serve as personal presets in `0.0.6` and become multi-agent **Roles** and blind-trial **Variants** in `0.2.0`–`0.4.0`.

---

## Roadmap

Releases follow `0.MINOR.PATCH` SemVer (git tags omit a `v` prefix: `0.0.1`, `0.0.2`, …, `0.1.0`). Every release keeps `arity.db` strictly additive, preserves `arity replay-check` determinism, and passes parallel CLI + background-CUA app verification gates.

### Phase 0 (`0.0.1` → `0.1.0`): One Great, Pi-Grade Harness
*Make Arity's single-agent coding harness, quota visibility, loadout ergonomics, and macOS UI strong enough to self-host all future Arity development at `pi`-level benchmark quality.*

- **`0.0.1` (Current) — Event Spine, Git-Tree Workspaces & First App/CLI**
  - Append-only `entries`, rebuildable `threads` + `deliveries`, shadow-index `tree_sha` time travel, `SIGKILL` process-group reaping, OpenAI `Responses` + `Chat Completions`, single-writer `arity-worker`, and native SwiftUI `Arity.app`.
- **`0.0.2` — Accounts, Deduplicated Routes & Free Usage / Quota Bars**
  - Replace flat wire endpoints with **Account → Plans** grouping (`OpenAI`: ChatGPT Sign-In + API Key; `OpenCode`: Zen + Go on one key; `Nous`: `:free` + paid; `Anthropic`; `Google`; `This Mac`: auto-probed MLX), hiding signed-out OAuth routes.
  - Auto-refilling `x-ratelimit-*` quota bars with reset countdowns plus UTC daily token tallies summed from `entries.usage_json` (surfacing OpenAI complimentary daily free token usage alongside OpenCode and Nous limits).
- **`0.0.3` — Pi-Grade Inner Coding Loop**
  - End-to-end `effort` (`off` → `xhigh`) across SQLite, CLI, SwiftUI, and wire codecs.
  - Whitespace/Unicode-normalized fuzzy fallback + compact line-numbered diffs on `edit_file`, `rg`-grade regex `search_files`, `2000`-line/`50KB` `read_file`, and temp-file full-log spill paths when `bash` output exceeds `64KB`.
  - Replayable `thread.compacted` spine entry (summarizes older history while keeping ~20k recent tokens verbatim and preserving raw `entries` for fork/time-travel) and removal of the hard 32-step turn ceiling.
  - Cache-warm sectioned system prompt (`cwd`, git branch/status, explicit `AGENTS.md` / `CLAUDE.md` toggle).
- **`0.0.4` — Native Anthropic Messages & Google Gemini Codecs**
  - Native `Anthropic Messages` (`cache_control` prompt caching + `thinking`/`signature` blocks across tool turns) and native `Google Gemini` (`thoughtSignature` + streaming tool calls) inside `core/src/codec.rs`.
- **`0.0.5` — SwiftUI Craft & Native Liquid Glass**
  - macOS 26+ native Liquid Glass controls (`.glassEffect()`, `.buttonStyle(.glass)` on New Thread and floating Jump-to-Latest), `ScrollPosition` follow-until-scrolled-away, per-thread drafts, quiet borderless composer (`Return` = steer, `⌥Return` = after-turn), single-line human tool rows (click to inspect diff/fork), and streaming block Markdown.
- **`0.0.6` — Composable Loadouts & Presets**
  - First-class thread and preset configuration implementing `[Account/Plan ⇄ Harness ⇄ Model] → Effort → System Prompt → Tools → Skills → MCPs` across SQLite, CLI, and `Arity.app`.
- **`0.0.7` — External CLI Harness Bridges (`claude`, `codex`, `agy`, `pi`)**
  - Subprocess harness adapters on the Arity spine: `arity-worker` runs vendor CLIs inside the thread's detached git worktree with the assembled loadout, relays streaming turns, and stamps `tree_sha` snapshots onto `entries` so fork, diff, and apply work identically.
- **`0.1.0` — Self-Hosting Milestone ("Arity Builds Arity")**
  - Head-to-head coding benchmark pass (`arity` vs `pi` on real multi-file repo tasks), dogfood polish, and `0.1.0` cut.

### Phase 1 (`0.1.x` → `0.2.0`): The Unified Board, Drafts & Live Handoffs
- Replayable `items` (`task | doc | check`, `draft → open → ready → active → in_gates → done | canceled`), immutable `item_revisions`, and `edges` (`blocks`, `parent`, `assigned_to`, `ran_in_thread`, `requires_gate`).
- Four symmetric coordination tools shared by humans, chats, and workers: `put_item`, `read_board`, `send` (`steer | after_turn | inbox` + `fork`), and `resolve`.
- Blocker-driven `ready` dispatcher spawning isolated worktree threads while allowing live two-way messaging and mid-run steering between PM threads and workers.

### Phase 2 (`0.2.x` → `0.3.0`): Composable Gates on the Same Harness
- First-class `in_gates` verification pipelines (`cmd:<shell>` for deterministic build/lint/CLI suites, `agent:<role>` for blind spec/code review and background `cua-driver` app verification, and `human` review/apply inbox) with bounded bounce-back retries.

### Phase 3 (`0.3.x` → `0.4.0`): Blind Router, Telemetry & Pairwise Fork Trials
- Sealed `trials` linking `(model, harness, skills, mcp, persona)` and `(input_entry_id, item_rev_id, base_tree_sha)` to hard outcomes (gate pass-at-1, steer count, post-apply edit distance, `ttft`/`tps`, cost).
- Quota-aware routing (`smart`, `fast`, `free`, `private`) that spends expiring subscription windows and free allowances first, plus paired A/B fork trials from identical `(entry_id, tree_sha)` checkpoints.

---

## Contributing & Agent Guidance

- [CONTRIBUTING.md](CONTRIBUTING.md) — invariants, build/test workflow, QA gates, and release tagging.
- [AGENTS.md](AGENTS.md) — concise instructions for coding agents working inside this repository.
- [.wiki/CONTRIBUTING.md](.wiki/CONTRIBUTING.md) — rules for editing `.wiki/` pages and SVG diagrams.
