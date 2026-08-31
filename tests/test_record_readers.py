from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gorkbot.handlers import JsonlRecordStore, default_record_store
from gorkbot.record_readers import (
    JsonlRecordReader,
    RecordChanged,
    RecordCorruption,
    RecordNotFound,
    RecordReadError,
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


def test_sqlite_reader_never_opens_the_no_wal_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "records.db"
    writer = SqliteRecordStore(path)
    writer.append(StoreRecord(kind="trial_event", record={"trial_id": "private"}))
    writer.close()
    scratch = tmp_path / "private-snapshots"
    scratch.mkdir()
    monkeypatch.setattr("gorkbot.record_readers.tempfile.tempdir", str(scratch))
    opened: list[str] = []
    original_connect = sqlite3.connect

    def observed_connect(database: str, *args: object, **kwargs: object):
        opened.append(str(database))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", observed_connect)
    reader = SqliteRecordReader(path)
    try:
        assert reader.query("trial_event")[0]["trial_id"] == "private"
        assert len(opened) == 1
        assert path.resolve().as_uri() not in opened[0]
        assert "arity-record-reader-" in opened[0]
    finally:
        reader.close()
    assert list(scratch.iterdir()) == []


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


def test_jsonl_reader_splits_only_lf_and_preserves_surrogates(tmp_path: Path) -> None:
    root = tmp_path / "records"
    root.mkdir()
    separators = "a\u0085b\u2028c\u2029d"
    (root / "trial_event.jsonl").write_bytes(
        (
            json.dumps({"value": separators}, ensure_ascii=False)
            + "\r\n"
            + '{"value":"\\ud800"}\r\n'
        ).encode("utf-8")
    )

    rows = JsonlRecordReader(root).query("trial_event")

    assert rows[0]["value"] == separators
    assert len(rows[1]["value"]) == 1
    assert ord(rows[1]["value"]) == 0xD800


@pytest.mark.parametrize(
    "encoded",
    [
        '{"value":1,"value":2}\n',
        '{"value":NaN}\n',
        '{"value":Infinity}\n',
        '{"value":1e999}\n',
        '{"value":' + "1" * 5000 + "}\n",
    ],
    ids=["duplicate-key", "nan", "infinity", "overflow-float", "huge-int"],
)
def test_jsonl_reader_rejects_ambiguous_or_unbounded_json(
    tmp_path: Path,
    encoded: str,
) -> None:
    root = tmp_path / "records"
    root.mkdir()
    (root / "trial_event.jsonl").write_bytes(encoded.encode("utf-8"))

    with pytest.raises(RecordCorruption, match="malformed JSONL"):
        JsonlRecordReader(root).query("trial_event")


def test_duplicate_key_diagnostic_does_not_echo_persisted_content(tmp_path: Path) -> None:
    root = tmp_path / "records"
    root.mkdir()
    (root / "trial_event.jsonl").write_text(
        '{"TOP_SECRET":1,"TOP_SECRET":2}\n', encoding="utf-8"
    )

    with pytest.raises(RecordCorruption) as failure:
        JsonlRecordReader(root).query("trial_event")
    assert "duplicate object key" in str(failure.value)
    assert "TOP_SECRET" not in str(failure.value)


def test_jsonl_kind_and_filters_are_type_and_case_strict(tmp_path: Path) -> None:
    root = tmp_path / "records"
    root.mkdir()
    (root / "Trial_Event.jsonl").write_text(
        '{"value":true,"nested":{"value":1}}\n'
        '{"value":1,"nested":{"value":true}}\n',
        encoding="utf-8",
    )
    reader = JsonlRecordReader(root)

    assert reader.query("trial_event") == []
    assert reader.query("Trial_Event", value=True) == [
        {"value": True, "nested": {"value": 1}},
    ]
    assert reader.query("Trial_Event", nested={"value": True}) == [
        {"value": 1, "nested": {"value": True}},
    ]


def test_jsonl_reader_reports_a_disappearing_root_as_changed(tmp_path: Path) -> None:
    root = tmp_path / "records"
    root.mkdir()
    reader = JsonlRecordReader(root)
    root.rename(tmp_path / "moved-records")

    with pytest.raises(RecordChanged, match="changed"):
        reader.query("trial_event")


def test_stat_failures_are_operational_not_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jsonl_root = tmp_path / "records"
    jsonl_root.mkdir()
    sqlite_path = tmp_path / "records.db"
    sqlite_path.write_bytes(b"placeholder")
    original_stat = Path.stat

    def denied(path: Path, *args: object, **kwargs: object):
        if path in {jsonl_root, sqlite_path}:
            raise PermissionError("denied for test")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)

    with pytest.raises(RecordReadError, match="could not inspect JSONL"):
        JsonlRecordReader(jsonl_root)
    with pytest.raises(RecordReadError, match="could not inspect SQLite"):
        SqliteRecordReader(sqlite_path)


def test_jsonl_file_stat_failures_are_operational(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "records"
    root.mkdir()
    record_path = root / "trial_event.jsonl"
    record_path.write_text('{}\n', encoding="utf-8")
    reader = JsonlRecordReader(root)
    original_stat = Path.stat

    def denied(path: Path, *args: object, **kwargs: object):
        if path == record_path:
            raise PermissionError("denied for test")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)

    with pytest.raises(RecordReadError, match="could not read record file"):
        reader.query("trial_event")


@pytest.mark.parametrize(
    "encoded",
    [
        b"\xff",
        '{"value":1,"value":2}',
        '{"value":NaN}',
        '{"value":' + "1" * 5000 + "}",
    ],
    ids=["invalid-utf8-blob", "duplicate-key", "nan", "huge-int"],
)
def test_sqlite_reader_types_decoder_failures_as_corruption(
    tmp_path: Path,
    encoded: bytes | str,
) -> None:
    path = tmp_path / "records.db"
    writer = SqliteRecordStore(path)
    writer.close()
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "INSERT INTO records(kind, record) VALUES (?, ?)",
            ("trial_event", encoded),
        )
    connection.close()

    reader = SqliteRecordReader(path)
    with pytest.raises(RecordCorruption, match="malformed JSON") as failure:
        reader.query("trial_event")
    assert failure.value.record_id == 1
    assert reader._connection is None
    reader.close()


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_reader_types_json_recursion_failures_as_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    def recurse(_encoded: str | bytes) -> object:
        raise RecursionError("too deep for test")

    monkeypatch.setattr("gorkbot.record_readers._strict_json_loads", recurse)
    if backend == "jsonl":
        root = tmp_path / "records"
        root.mkdir()
        (root / "trial_event.jsonl").write_text('{}\n', encoding="utf-8")
        reader = JsonlRecordReader(root)
    else:
        path = tmp_path / "records.db"
        writer = SqliteRecordStore(path)
        writer.append(StoreRecord(kind="trial_event", record={}))
        writer.close()
        reader = SqliteRecordReader(path)

    with pytest.raises(RecordCorruption):
        reader.query("trial_event")
    reader.close()


def test_sqlite_kind_and_filters_are_type_and_case_strict(tmp_path: Path) -> None:
    path = tmp_path / "records.db"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            """
            CREATE TABLE records (
                id INTEGER PRIMARY KEY NOT NULL,
                kind TEXT NOT NULL COLLATE NOCASE,
                record TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO records(kind, record) VALUES (?, ?)",
            [
                ("Trial_Event", '{"value":true}'),
                ("trial_event", '{"value":1}'),
            ],
        )
    connection.close()

    reader = SqliteRecordReader(path)
    assert reader.query("Trial_Event", value=True) == [{"value": True}]
    assert reader.query("trial_event", value=True) == []
    assert reader.query("trial_event", value=1) == [{"value": 1}]
    reader.close()


@pytest.mark.parametrize(
    "schema",
    [
        "CREATE TABLE source(id INTEGER, kind TEXT, record TEXT); "
        "CREATE VIEW records AS SELECT id, kind, record FROM source",
        "CREATE TABLE records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, record TEXT NOT NULL)",
        "CREATE TABLE records(id INTEGER PRIMARY KEY, kind INTEGER NOT NULL, record TEXT NOT NULL)",
        "CREATE TABLE records(id INTEGER PRIMARY KEY, kind TEXT, record TEXT NOT NULL)",
    ],
    ids=["view", "text-id", "integer-kind", "nullable-kind"],
)
def test_sqlite_reader_requires_a_real_compatible_records_table(
    tmp_path: Path,
    schema: str,
) -> None:
    path = tmp_path / "records.db"
    connection = sqlite3.connect(path)
    with connection:
        connection.executescript(schema)
    connection.close()

    with pytest.raises(RecordCorruption, match="compatible records table"):
        SqliteRecordReader(path)


def test_sqlite_lock_is_changed_not_corruption(tmp_path: Path) -> None:
    path = tmp_path / "records.db"
    writer = SqliteRecordStore(path)
    writer.close()
    locker = sqlite3.connect(path, timeout=0)
    locker.execute("BEGIN EXCLUSIVE")
    locker.execute(
        "INSERT INTO records(kind, record) VALUES (?, ?)",
        ("trial_event", '{}'),
    )
    try:
        with pytest.raises(RecordChanged, match="rollback journal"):
            SqliteRecordReader(path)
    finally:
        locker.rollback()
        locker.close()


def test_sqlite_open_io_failure_is_operational(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "records.db"
    path.write_bytes(b"placeholder")

    def cannot_open(*args: object, **kwargs: object):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(sqlite3, "connect", cannot_open)

    with pytest.raises(RecordReadError, match="unable to open") as failure:
        SqliteRecordReader(path)
    assert not isinstance(failure.value, RecordCorruption)


def test_sqlite_notadb_is_corruption(tmp_path: Path) -> None:
    path = tmp_path / "records.db"
    path.write_bytes(b"definitely not sqlite")

    with pytest.raises(RecordCorruption):
        SqliteRecordReader(path)


def test_sqlite_wal_is_read_from_private_snapshot_without_source_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "private-snapshots"
    scratch.mkdir()
    monkeypatch.setattr("gorkbot.record_readers.tempfile.tempdir", str(scratch))

    writer_path = tmp_path / "writer.db"
    writer = sqlite3.connect(writer_path)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute(
        """
        CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            record TEXT NOT NULL,
            replay_record TEXT
        )
        """
    )
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.execute(
        "INSERT INTO records(kind, record) VALUES (?, ?)",
        ("trial_event", '{"trial_id":"wal-only"}'),
    )
    writer.execute(
        "INSERT INTO records(kind, record) VALUES (?, ?)",
        ("bad_event", b"\xff"),
    )
    writer.commit()

    source = tmp_path / "source"
    source.mkdir()
    path = source / "records.db"
    wal_path = source / "records.db-wal"
    path.write_bytes(writer_path.read_bytes())
    wal_path.write_bytes(writer_path.with_name("writer.db-wal").read_bytes())
    assert not (source / "records.db-shm").exists()
    before = _tree_snapshot(source)

    try:
        reader = SqliteRecordReader(path)
        assert reader.query("trial_event") == [{"trial_id": "wal-only"}]
        with pytest.raises(RecordCorruption):
            reader.query("bad_event")
    finally:
        writer.close()

    def reject_schema(reader: SqliteRecordReader) -> None:
        raise RecordCorruption("incompatible snapshot", path=reader.path)

    monkeypatch.setattr(SqliteRecordReader, "_validate_schema", reject_schema)
    with pytest.raises(RecordCorruption, match="incompatible snapshot") as failure:
        SqliteRecordReader(path)
    assert failure.value.path == path

    assert _tree_snapshot(source) == before
    assert not (source / "records.db-shm").exists()
    assert list(scratch.iterdir()) == []
