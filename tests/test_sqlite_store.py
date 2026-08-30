from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from gorkbot.stores.sqlite import SqliteRecordStore


@dataclass
class Effect:
    kind: str
    record: dict[str, object]


def test_append_query_copies_and_filters(tmp_path: Path) -> None:
    store = SqliteRecordStore(tmp_path / "records.sqlite")
    original: dict[str, object] = {
        "session_id": "abc",
        "nested": {"values": [1, 2]},
    }
    store.append(Effect("tool_result", original))
    original["session_id"] = "changed"
    nested = original["nested"]
    assert isinstance(nested, dict)
    nested["values"] = []

    records = store.query("tool_result", session_id="abc")
    assert len(records) == 1
    assert records[0]["nested"] == {"values": [1, 2]}
    assert isinstance(records[0]["timestamp"], float)
    assert "timestamp" not in original
    assert store.query("tool_result", session_id="missing") == []


def test_filter_requires_key_even_when_value_is_none(tmp_path: Path) -> None:
    store = SqliteRecordStore(tmp_path / "records.sqlite")
    store.append(Effect("event", {"timestamp": 1}))
    store.append(Effect("event", {"timestamp": 2, "value": None}))
    assert store.query("event", value=None) == [
        {"timestamp": 2, "value": None}
    ]


def test_timestamp_is_preserved_and_duplicate_appends_are_records(tmp_path: Path) -> None:
    store = SqliteRecordStore(tmp_path / "records.sqlite")
    effect = Effect("scorecard", {"timestamp": 123.5, "score": 9})
    store.append(effect)
    store.append(effect)

    assert store.query("scorecard") == [effect.record, effect.record]


def test_persists_after_close_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "records.sqlite"
    first = SqliteRecordStore(path)
    first.append(Effect("trial_axes", {"timestamp": 1, "axis": ["a", "b"]}))
    first.close()

    second = SqliteRecordStore(path)
    assert second.query("trial_axes") == [
        {"timestamp": 1, "axis": ["a", "b"]}
    ]
    second.close()


def test_constructor_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "directory" / "records.sqlite"
    store = SqliteRecordStore(path)
    store.close()
    assert path.is_file()


def test_1000_concurrent_appends_are_all_stored_once(tmp_path: Path) -> None:
    store = SqliteRecordStore(tmp_path / "records.sqlite")
    thread_count = 20
    per_thread = 50

    def append_batch(worker: int) -> None:
        for sequence in range(per_thread):
            store.append(
                Effect(
                    "concurrent",
                    {"timestamp": worker, "id": f"{worker}:{sequence}"},
                )
            )

    threads = [
        threading.Thread(target=append_batch, args=(worker,))
        for worker in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = store.query("concurrent")
    ids = [record["id"] for record in records]
    assert len(records) == 1000
    assert len(set(ids)) == 1000


def test_replay_jsonl_skips_bad_lines_preserves_order_and_deduplicates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "jsonl"
    root.mkdir()
    (root / "zeta.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"timestamp": 1, "value": {"x": [1, 2]}}),
                "not json",
                json.dumps({"value": 2}),
                json.dumps({"timestamp": 1, "value": {"x": [1, 2]}}),
            ]
        ),
        encoding="utf-8",
    )
    (root / "alpha.jsonl").write_text(
        json.dumps({"timestamp": 4, "ok": True}) + "\n[]\n",
        encoding="utf-8",
    )
    store = SqliteRecordStore(tmp_path / "records.sqlite")

    assert store.replay_jsonl(root) == 3
    assert store.skipped == 2
    assert store.kinds() == ["alpha", "zeta"]
    zeta = store.query("zeta")
    assert [record["value"] for record in zeta] == [{"x": [1, 2]}, 2]
    assert "timestamp" in zeta[1]

    assert store.replay_jsonl(root) == 0
    assert store.skipped == 2
    assert len(store.query("zeta")) == 2


def test_replay_deduplication_survives_reopening_without_timestamps(
    tmp_path: Path,
) -> None:
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "events.jsonl").write_text('{"name": "legacy"}\n', encoding="utf-8")
    path = tmp_path / "records.sqlite"

    first = SqliteRecordStore(path)
    assert first.replay_jsonl(root) == 1
    first.close()

    second = SqliteRecordStore(path)
    assert second.replay_jsonl(root) == 0
    assert len(second.query("events")) == 1


def test_query_results_are_independent_plain_dicts(tmp_path: Path) -> None:
    store = SqliteRecordStore(tmp_path / "records.sqlite")
    store.append(Effect("kind", {"timestamp": 1, "nested": [1]}))
    result = store.query("kind")
    result[0]["nested"] = []

    assert store.query("kind") == [{"timestamp": 1, "nested": [1]}]


def test_json_serialization_errors_do_not_insert_rows(tmp_path: Path) -> None:
    store = SqliteRecordStore(tmp_path / "records.sqlite")
    with pytest.raises(TypeError):
        store.append(Effect("bad", {"value": object()}))
    assert store.query("bad") == []
    assert store.kinds() == []
