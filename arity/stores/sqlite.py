from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class SqliteRecordStore:
    """A persistent, thread-safe store for JSON object records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # Borrowed from candidate B: make a nested database path usable directly.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.skipped = 0
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    record TEXT NOT NULL,
                    replay_record TEXT
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS records_kind ON records(kind)"
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS records_replay_unique
                ON records(kind, replay_record)
                WHERE replay_record IS NOT NULL
                """
            )

    @staticmethod
    def _copy_record(record: dict[str, Any]) -> dict[str, Any]:
        copied = json.loads(json.dumps(record))
        if not isinstance(copied, dict):
            raise TypeError("effect.record must be a JSON-serialisable dict")
        return copied

    @staticmethod
    def _canonical(record: dict[str, Any]) -> str:
        return json.dumps(record, sort_keys=True, separators=(",", ":"))

    def append(self, effect: Any) -> None:
        if not isinstance(effect.kind, str):
            raise TypeError("effect.kind must be a str")
        if not isinstance(effect.record, dict):
            raise TypeError("effect.record must be a dict")
        record = self._copy_record(effect.record)
        if "timestamp" not in record:
            record["timestamp"] = time.time()
        encoded = json.dumps(record, separators=(",", ":"))
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO records(kind, record) VALUES (?, ?)",
                (effect.kind, encoded),
            )

    def query(self, kind: str, **filters: Any) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT record FROM records WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        records = [json.loads(row[0]) for row in rows]
        missing = object()
        return [
            record
            for record in records
            if all(record.get(key, missing) == value for key, value in filters.items())
        ]

    def replay_jsonl(self, root: str | Path) -> int:
        imported = 0
        self.skipped = 0
        for path in sorted(Path(root).glob("*.jsonl")):
            kind = path.stem
            with path.open("r", encoding="utf-8") as source:
                for line in source:
                    try:
                        parsed = json.loads(line)
                        if not isinstance(parsed, dict):
                            raise ValueError("a record must be a JSON object")
                    except (json.JSONDecodeError, ValueError):
                        self.skipped += 1
                        continue
                    replay_record = self._canonical(parsed)
                    record = self._copy_record(parsed)
                    if "timestamp" not in record:
                        record["timestamp"] = time.time()
                    encoded = json.dumps(record, separators=(",", ":"))
                    with self._lock, self._connection:
                        existing = self._connection.execute(
                            "SELECT record FROM records WHERE kind = ?", (kind,)
                        ).fetchall()
                        if any(json.loads(row[0]) == parsed for row in existing):
                            continue
                        cursor = self._connection.execute(
                            """
                            INSERT OR IGNORE INTO records(kind, record, replay_record)
                            VALUES (?, ?, ?)
                            """,
                            (kind, encoded, replay_record),
                        )
                        imported += cursor.rowcount
        return imported

    def kinds(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT kind FROM records ORDER BY kind"
            ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
