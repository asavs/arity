# AGENTS.md

Operational guide for coding agents (`arity`, `pi`, Claude Code, Codex, Cursor) working in this repository.

## Repository Map

- `core/src/types.rs` — Domain types, `EntryKind`, `DeliveryMode`, `ThreadStatus`, `ToolReceiptStatus`, `WireCodec`, `StoreError`.
- `core/src/store.rs` — SQLite WAL spine (`arity.db`), transactional `apply_entry_to_projections`, `rebuild_projections()`, `request_id` idempotency, `epoch` fencing, `providers`, and `rate_limits`.
- `core/src/workspace.rs` — Detached git worktrees (`workspaces/`), shadow-index `snapshot_tree` (`GIT_INDEX_FILE`), `restore_tree`, `diff_trees`, and 3-way `apply_tree`.
- `core/src/tools.rs` — Path-jailed `list_files`, `read_file`, `search_files`, atomic `write_file` / `edit_file`, and `bash` with pipe-EOF parent-death process-group reaping.
- `core/src/secrets.rs` & `core/src/codec.rs` — `0600` `keys.json` + SIWC `codex-auth.json`, `x-ratelimit-*` header parser, cross-codec chain normalization, and pooled `Responses` + `ChatCompletions` streaming.
- `core/src/runner.rs` — Epoch-fenced single-writer turn loop and crash recovery.
- `core/src/ffi.rs` — Thin UniFFI facade (`ArityClient`) used by SwiftUI; appends intents and reads SQLite snapshots only.
- `cli/src/main.rs` — `arity` CLI and `arity worker` subprocess entrypoint.
- `cli/tests/exit_bar_sigkill.rs` — Real OS `SIGKILL` (`kill -9`) subprocess safety and cross-codec fork/apply tests.
- `app/Sources/Arity/` — Native macOS SwiftUI app (`Arity.app`), built by `app/build.sh` and packaged by `app/package.sh`.
- `.wiki/` — System design wiki and diagrams. Read `.wiki/CONTRIBUTING.md` before editing anything under `.wiki/`.

## Hard Invariants (Never Violate)

1. **`entries` is the sole durable history.** Every projection table (`threads`, `deliveries`, and future `items`/`edges`) must be updated inside `apply_entry_to_projections` and verified by `Store::rebuild_projections()` (`arity replay-check`).
2. **Single writer.** Never execute model turns, network streaming, or workspace tools inside `core/src/ffi.rs`, SwiftUI, or non-worker CLI commands. Frontends only append idempotent intent entries (`request_id TEXT UNIQUE`) and spawn/signal `arity worker` (`Contents/MacOS/arity-worker` in the app bundle).
3. **Truncation & crash safety.** Never execute tool calls unless a complete `model.completed` entry is committed first. Never auto-rerun a mutating `tool.call` lacking a `tool.result` receipt on recovery; restore `last_tree_sha`, record `status = "unknown"`, and halt the turn.
4. **Additive schema & zero sidecars.** Schema changes on `arity.db` must be strictly additive (`CREATE TABLE IF NOT EXISTS` / guarded migrations). Never introduce unlocked JSON state sidecar files beside `arity.db` (`0600` `keys.json` and `codex-auth.json` are the sole exceptions).
5. **Isolated test state.** Never touch the user's live `~/Library/Application Support/Arity` during automated tests or QA runs. Always set `ARITY_HOME="$(mktemp -d)"`.
6. **Tag naming.** Git release tags use bare SemVer (`0.0.1`, `0.0.2`, `0.1.0`) with **no `v` prefix**.

## Verification Commands

Run these before finishing any code change:

```bash
cargo clippy --all-targets -- -D warnings
cargo test
./app/build.sh --no-open
```

When driving `Arity.app` for UI verification on macOS, always target the app in the background (`scope: "app"` / `cua-driver`) against a temporary `ARITY_HOME`—never steal foreground focus or move the user's cursor.
