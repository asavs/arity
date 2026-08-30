---
name: sqlite_record_store
description: A SQLite-backed append-only record store implementing arity's RecordStore seam, with lossless JSONL replay.
module: sqlite_record_store
entrypoint: SqliteRecordStore
tags: [python, sqlite, persistence, seam]
---

Build `sqlite_record_store.py` exposing `SqliteRecordStore(path: str | Path)`, a drop-in for an
append-only JSONL record store. Standard library only (`sqlite3`, `json`, `threading`, `pathlib`, `time`).

The contract (a "seam": callers only ever use these two methods):

- `append(effect)` — `effect` is any object with two attributes: `effect.kind` (a `str`, e.g. `"scorecard"`,
  `"trial_axes"`) and `effect.record` (a JSON-serialisable `dict`). Store a copy of the dict under that kind.
  If the dict has no `"timestamp"` key, add one (`time.time()`). Return `None`.
- `query(kind: str, **filters) -> list[dict]` — every record of that kind whose top-level keys equal the given
  filter values (`query("tool_result", session_id="abc")`), in insertion order. No filters returns all of that kind.
  Records come back as plain dicts equal to what was stored (nested lists/dicts preserved).

Also:

- Persistence: records survive closing and re-opening the store at the same path. The constructor creates the
  file and schema if missing.
- Concurrency: many threads may call `append` at once on one store instance; every record is stored exactly
  once and nothing is corrupted. Use a lock and/or per-connection handling as you see fit.
- `replay_jsonl(root: str | Path) -> int` — import an existing JSONL store: every `<root>/<kind>.jsonl` file,
  one JSON object per line, appended in file order under that kind. Skip lines that are not valid JSON
  (count them separately as `self.skipped`). Return the number of records imported. Replaying the same root twice
  must not duplicate records (dedupe on exact record equality within a kind).
- `kinds() -> list[str]` — the kinds that have at least one record, sorted.
- Keep the schema simple: one table is fine. Do not add third-party dependencies. Aim for under ~150 lines.

Write your own tests in `test_sqlite_record_store.py`.
