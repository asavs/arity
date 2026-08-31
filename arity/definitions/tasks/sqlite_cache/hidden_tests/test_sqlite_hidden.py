"""Hidden acceptance tests for the sqlite_cache task. Expects `from sqlite_cache import SQLiteCache`."""
import pytest

from sqlite_cache import SQLiteCache


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "cache.db")


def test_set_get_roundtrip_preserves_json_types(db_path):
    c = SQLiteCache(db_path)
    c.set("k", {"a": [1, 2, {"b": None}]})
    assert c.get("k") == {"a": [1, 2, {"b": None}]}


def test_missing_key_returns_default(db_path):
    assert SQLiteCache(db_path).get("nope", default="dflt") == "dflt"


def test_ttl_expiry_uses_injected_clock(db_path):
    clock = FakeClock()
    c = SQLiteCache(db_path, clock=clock)
    c.set("k", 1, ttl=10)
    clock.t += 9
    assert c.get("k") == 1
    clock.t += 2
    assert c.get("k") is None


def test_values_persist_across_reopen(db_path):
    SQLiteCache(db_path).set("k", "v")
    assert SQLiteCache(db_path).get("k") == "v"


def test_delete_missing_is_noop_and_delete_existing_removes(db_path):
    c = SQLiteCache(db_path)
    c.delete("ghost")
    c.set("k", 1)
    c.delete("k")
    assert c.get("k") is None


def test_purge_returns_count_of_expired_rows(db_path):
    clock = FakeClock()
    c = SQLiteCache(db_path, clock=clock)
    c.set("a", 1, ttl=5)
    c.set("b", 2, ttl=5)
    c.set("c", 3)
    clock.t += 6
    assert c.purge() == 2
    assert c.get("c") == 3
