"""Hidden acceptance tests for sqlite_record_store. Expects `from sqlite_record_store import SqliteRecordStore`."""
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from sqlite_record_store import SqliteRecordStore


@dataclass
class Rec:
    kind: str
    record: dict


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "records.db")


def test_append_then_query_returns_equal_dicts_in_order(db):
    s = SqliteRecordStore(db)
    s.append(Rec("scorecard", {"model": "a", "n": 1, "nested": {"x": [1, 2]}}))
    s.append(Rec("scorecard", {"model": "b", "n": 2}))
    rows = s.query("scorecard")
    assert [r["model"] for r in rows] == ["a", "b"]
    assert rows[0]["nested"] == {"x": [1, 2]}
    assert all("timestamp" in r for r in rows)


def test_filters_match_top_level_keys_exactly(db):
    s = SqliteRecordStore(db)
    s.append(Rec("tool_result", {"session_id": "s1", "tool_name": "read_file"}))
    s.append(Rec("tool_result", {"session_id": "s2", "tool_name": "read_file"}))
    s.append(Rec("tool_result", {"session_id": "s1", "tool_name": "write_file"}))
    assert len(s.query("tool_result", session_id="s1")) == 2
    assert len(s.query("tool_result", session_id="s1", tool_name="write_file")) == 1
    assert s.query("tool_result", session_id="nope") == []
    assert s.query("no_such_kind") == []


def test_kinds_are_separate_and_persist_across_reopen(db):
    s = SqliteRecordStore(db)
    s.append(Rec("a", {"v": 1}))
    s.append(Rec("b", {"v": 2}))
    del s
    s2 = SqliteRecordStore(db)
    assert s2.kinds() == ["a", "b"]
    assert s2.query("a") and s2.query("a")[0]["v"] == 1
    assert s2.query("b")[0]["v"] == 2


def test_existing_timestamp_is_kept(db):
    s = SqliteRecordStore(db)
    s.append(Rec("k", {"timestamp": 123.5, "v": 1}))
    assert s.query("k")[0]["timestamp"] == 123.5


def test_concurrent_appends_store_every_record_exactly_once(db):
    s = SqliteRecordStore(db)

    def worker(i):
        for j in range(50):
            s.append(Rec("burst", {"worker": i, "j": j}))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = s.query("burst")
    assert len(rows) == 400
    assert len({(r["worker"], r["j"]) for r in rows}) == 400


def test_replay_jsonl_is_lossless_skips_bad_lines_and_is_idempotent(db, tmp_path):
    root = tmp_path / "records"
    root.mkdir()
    (root / "scorecard.jsonl").write_text(
        json.dumps({"model": "a", "standing_after": 11.0}) + "\n"
        + "{not json\n"
        + json.dumps({"model": "b", "standing_after": 9.0, "skills": ["x", "y"]}) + "\n",
        encoding="utf-8",
    )
    (root / "friction.jsonl").write_text(json.dumps({"note": "hi"}) + "\n", encoding="utf-8")
    s = SqliteRecordStore(db)
    assert s.replay_jsonl(root) == 3
    assert s.skipped == 1
    assert [r["model"] for r in s.query("scorecard")] == ["a", "b"]
    assert s.query("scorecard")[1]["skills"] == ["x", "y"]
    assert s.query("friction")[0]["note"] == "hi"
    assert s.replay_jsonl(root) == 0          # second replay adds nothing
    assert len(s.query("scorecard")) == 2
