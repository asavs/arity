"""Hidden acceptance tests for the lru_cache task. Expects `from lru_cache import LRUCache`."""
import time

import pytest

from lru_cache import LRUCache


def test_get_missing_returns_none():
    assert LRUCache(2).get("nope") is None


def test_put_then_get_roundtrip():
    c = LRUCache(2)
    c.put("a", 1)
    assert c.get("a") == 1


def test_evicts_least_recently_used_not_oldest_inserted():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")          # 'a' is now most recent; 'b' is LRU
    c.put("c", 3)
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3


def test_update_existing_key_refreshes_recency_without_growing():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)      # update, not insert
    c.put("c", 3)       # should evict 'b'
    assert len(c) == 2
    assert c.get("b") is None
    assert c.get("a") == 10


def test_contains_does_not_touch_recency():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert "a" in c
    c.put("c", 3)       # 'a' is still LRU because `in` must not refresh it
    assert "a" not in c


def test_invalid_capacity_raises():
    with pytest.raises(ValueError):
        LRUCache(0)
    with pytest.raises(ValueError):
        LRUCache(-3)


def test_fast_means_200k_ops_under_one_second():
    c = LRUCache(1024)
    start = time.perf_counter()
    for i in range(100_000):
        c.put(i, i)
        c.get(i - 512)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"200k mixed ops took {elapsed:.2f}s; budget is 1.0s"
