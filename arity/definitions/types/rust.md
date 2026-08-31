---
name: rust
description: Rust toolkit, rules, and verification (stub - verify commands untested). Attaches to any role as role:rust.
skills: []
test_command: cargo test --quiet
test_globs: [tests/**/*.rs, src/**/*.rs]
hidden_dir: tests
hidden_command: cargo test --quiet --test '*'
tags: [rust, cargo]
---

Language: Rust, stable toolchain, `cargo` only. No `unsafe` without a `// SAFETY:` comment.
Prefer the standard library and well-known crates already in `Cargo.toml`; do not add
dependencies the brief did not ask for. A crate is done when `cargo test` is green and
`cargo clippy` is quiet. When judging Rust, ownership clarity and error handling with
`Result` beat cleverness; `unwrap()` in library code is a finding.
