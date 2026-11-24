# Contributing to Arity

Arity is developed in small, SemVer-tagged increments (`0.0.1` → `0.1.0` → `0.4.0`; see [`README.md#roadmap`](README.md#roadmap)). Every change must keep both the `arity` CLI and `Arity.app` working cleanly over the same SQLite spine.

## Prerequisites

- **macOS 14+** (macOS 26+ SDK recommended for SwiftUI Liquid Glass work in `0.0.5`+)
- **Rust** (`edition = "2024"`, `cargo`, `clippy`)
- **Xcode / Swift toolchain** (`xcodebuild`, `xcrun actool`, `swift build`, `hdiutil`)

## Development Workflow

### 1. Build & Test Core + CLI

```bash
# Strict lint check (zero warnings allowed)
cargo clippy --all-targets -- -D warnings

# Unit + integration + real OS SIGKILL subprocess tests
cargo test
```

For manual CLI testing without touching your personal `~/Library/Application Support/Arity` data:

```bash
export ARITY_HOME="$(mktemp -d)"
cargo run -p arity-cli -- status
cargo run -p arity-cli -- replay-check
```

### 2. Build & Package `Arity.app`

```bash
# Debug build without stealing focus
./app/build.sh --no-open

# Debug build and launch
./app/build.sh

# Release build + DMG in app/build/Arity-<version>-<arch>.dmg
./app/package.sh
```

Note: Inside `Arity.app/Contents/MacOS/`, the main SwiftUI executable is `Arity` and the bundled worker binary is named `arity-worker` so case-insensitive APFS never collides.

## Architectural Rules

1. **Replayability First:** Any new durable state belongs as an `EntryKind` in `entries` with deterministic projection handling in `Store::apply_entry_to_projections`. Always add a `rebuild_projections()` test proving replay parity.
2. **Idempotent Intents:** Every client-facing mutation (`CLI` and `ArityClient` FFI) accepts an optional `request_id` and checks `entries.request_id` before performing side effects such as `git apply`.
3. **Additive SQLite Schema:** `arity.db` migrations are strictly additive so existing user histories upgrade in place without data loss.
4. **Wiki Hygiene:** When updating system design docs or SVG diagrams under `.wiki/`, follow [`.wiki/CONTRIBUTING.md`](.wiki/CONTRIBUTING.md) (describe the design as it is now; regenerate diagrams with `python3 .wiki/diagrams/build.py`).

## Release Gates & Tagging

Follow the [release naming rules](.github/release.md) for tags, titles, and headings.

Before cutting a release tag:

1. Bump versions in `core/Cargo.toml`, `cli/Cargo.toml`, and `app/Info.plist` (`CFBundleShortVersionString` + `CFBundleVersion`).
2. Run **Gate 1 (CLI Gate):** `cargo clippy --all-targets -- -D warnings`, `cargo test`, and an isolated `ARITY_HOME` end-to-end CLI smoke run ending with `arity replay-check` (`ok: true`).
3. Run **Gate 2 (App Gate):** Build `./app/build.sh --no-open`, launch `Arity.app` against an isolated `ARITY_HOME`, and verify core UI flows in background app-scoped CUA without disturbing foreground focus.
4. Build the distributable image via `./app/package.sh`.
5. Create an annotated git tag with **no `v` prefix**:
   ```bash
   git tag -a 0.0.1 -m "Arity 0.0.1"
   ```
