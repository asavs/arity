from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gorkbot.handlers import JsonlRecordStore, default_record_store
from gorkbot.record_readers import (
    JsonlRecordReader,
    RecordCorruption,
    RecordNotFound,
    SqliteRecordReader,
    StoreSpec,
    configured_store_spec,
    open_record_reader,
)
from gorkbot.stores.sqlite import SqliteRecordStore
from gorkbot.types import StoreRecord


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): _snapshot(path)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_configured_store_spec_is_shared_with_the_writable_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "sqlite")
    spec = configured_store_spec()
    store = default_record_store()

    assert spec == StoreSpec("sqlite", Path(".gorkbot/records.db"))
    assert isinstance(store, SqliteRecordStore)
    assert store.path == spec.path
    store.close()

    monkeypatch.setenv("ARITY_STORE", "jsonl")
    spec = configured_store_spec()
    store = default_record_store()
    assert spec == StoreSpec("jsonl", Path(".gorkbot/records"))
    assert isinstance(store, JsonlRecordStore)
    assert store.root == spec.path


def test_jsonl_reader_is_query_only_strict_and_byte_preserving(tmp_path: Path) -> None:
    root = tmp_path / "records"
    writer = JsonlRecordStore(root)
    writer.append(StoreRecord(kind="trial_event", record={"trial_id": "t", "value": None}))
    writer.append(StoreRecord(kind="trial_event", record={"trial_id": "other"}))
    before = _tree_snapshot(tmp_path)

    reader = JsonlRecordReader(root)
    assert not hasattr(reader, "append")
    assert [row["trial_id"] for row in reader.query("trial_event")] == ["t", "other"]
    assert len(reader.query("trial_event", value=None)) == 1
    reader.close()

    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b'{"trial_id":"t"}', "unterminated"),
        (b'{"trial_id":}\n', "malformed"),
        (b'[]\n', "not an object"),
        (b'\n', "blank"),
        (b'\xff\n', "UTF-8"),
    ],
)
def test_jsonl_reader_never_silently_skips_corruption(
    tmp_path: Path, content: bytes, message: str,
) -> None:
    root = tmp_path / "records"
    root.mkdir()
    (root / "trial_event.jsonl").write_bytes(content)

    with pytest.raises(RecordCorruption, match=message):
        JsonlRecordReader(root).query("trial_event")


def test_jsonl_reader_does_not_create_a_missing_store(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "records"
    with pytest.raises(RecordNotFound):
        JsonlRecordReader(missing)
    assert not missing.parent.exists()


def test_sqlite_reader_is_query_only_and_byte_preserving(tmp_path: Path) -> None:
    path = tmp_path / "records.db"
    writer = SqliteRecordStore(path)
    writer.append(StoreRecord(kind="trial_event", record={"trial_id": "t"}))
    writer.close()
    before = _tree_snapshot(tmp_path)

    with open_record_reader(StoreSpec("sqlite", path)) as reader:
        assert not hasattr(reader, "append")
        assert reader.query("trial_event", trial_id="t")[0]["trial_id"] == "t"

    assert _tree_snapshot(tmp_path) == before


def test_sqlite_reader_does_not_create_a_missing_store(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "records.db"
    with pytest.raises(RecordNotFound):
        SqliteRecordReader(missing)
    assert not missing.parent.exists()


def test_sqlite_reader_reports_malformed_records_and_schema(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.db"
    writer = SqliteRecordStore(malformed)
    writer.close()
    connection = sqlite3.connect(malformed)
    with connection:
        connection.execute(
            "INSERT INTO records(kind, record) VALUES (?, ?)",
            ("trial_event", "not-json"),
        )
    connection.close()
    with pytest.raises(RecordCorruption, match="malformed JSON"):
        with open_record_reader(StoreSpec("sqlite", malformed)) as reader:
            reader.query("trial_event")

    incompatible = tmp_path / "incompatible.db"
    connection = sqlite3.connect(incompatible)
    with connection:
        connection.execute("CREATE TABLE something_else (id INTEGER)")
    connection.close()
    with pytest.raises(RecordCorruption, match="compatible records table"):
        SqliteRecordReader(incompatible)
