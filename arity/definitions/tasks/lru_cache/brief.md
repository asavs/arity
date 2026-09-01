---
name: lru_cache
description: Fixed-capacity least-recently-used cache with O(1) get/put and a measurable speed budget.
module: lru_cache
entrypoint: LRUCache
tags: [python, data-structures, performance]
---

Build a fast LRU cache in `lru_cache.py` exposing a class `LRUCache(capacity: int)`.

- `get(key)` returns the stored value, or `None` if absent, and marks the key most recently used.
- `put(key, value)` inserts or updates; when over capacity, the least recently used key is evicted.
- `__len__` returns the number of stored keys; `__contains__` works without touching recency.
- `capacity` must be a positive integer; raise `ValueError` otherwise.
- Both `get` and `put` are O(1). "Fast" means 200,000 mixed operations complete in under one second.

Write your own unit tests in `test_lru_cache.py`.
