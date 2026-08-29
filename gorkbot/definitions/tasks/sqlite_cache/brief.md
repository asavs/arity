---
name: sqlite_cache
description: Persistent key-value cache on SQLite with TTL expiry, standard library only.
module: sqlite_cache
entrypoint: SQLiteCache
tags: [python, sqlite, persistence]
---

Build a persistent key-value cache in `sqlite_cache.py` exposing `SQLiteCache(path: str, clock=time.time)`.

- `set(key: str, value, ttl: float | None = None)` stores any JSON-serialisable value; `ttl` is seconds until expiry.
- `get(key, default=None)` returns the value, or `default` if the key is missing or expired.
- `delete(key)` removes a key; deleting a missing key is not an error.
- `purge()` deletes every expired row and returns how many were removed.
- Values survive closing and reopening the cache at the same path.
- Standard library only (`sqlite3`, `json`, `time`). The constructor must accept a `clock` callable
  (default `time.time`) so expiry is testable without sleeping.

Write your own unit tests in `test_sqlite_cache.py`.
