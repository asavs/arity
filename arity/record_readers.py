"""Strict, query-only readers for Arity's built-in record backends.

Writable stores optimize for inexpensive append operations and compatibility.
Inspection has a different trust boundary: it must not create state and it must
not silently turn malformed persistence into a shorter, apparently valid log.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from .seams import RecordReader


RecordBackend = Literal["jsonl", "sqlite"]
_KIND_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


class RecordReadError(RuntimeError):
    """Base class for typed, user-facing failures while reading persisted records."""

    code = "record_read_error"

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        line: int | None = None,
        record_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.line = line
        self.record_id = record_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "path": None if self.path is None else str(self.path),
            "line": self.line,
            "record_id": self.record_id,
        }


class RecordNotFound(RecordReadError):
    code = "record_store_not_found"


class RecordCorruption(RecordReadError):
    code = "record_store_corrupt"


class RecordChanged(RecordReadError):
    code = "record_store_changed"


@dataclass(frozen=True)
class StoreSpec:
    """The configured built-in backend and its active compatibility path."""

    backend: RecordBackend
    path: Path

    def __post_init__(self) -> None:
        if self.backend not in {"jsonl", "sqlite"}:
            raise ValueError(f"unsupported record backend {self.backend!r}")
        object.__setattr__(self, "path", Path(self.path))


def configured_store_spec() -> StoreSpec:
    """Resolve the same backend and path used by ``default_record_store``."""
    from .tools import get_config_value

    configured = (
        get_config_value("ARITY_STORE")
        or get_config_value("ARITY_STORE")
        or "jsonl"
    )
    if configured.lower() == "sqlite":
        return StoreSpec("sqlite", Path(".arity/records.db"))
    # Preserve the writable store's historical behavior: only ``sqlite`` opts
    # away from JSONL, including for an unrecognized compatibility value.
    return StoreSpec("jsonl", Path(".arity/records"))


def _validate_kind(kind: str) -> str:
    if not isinstance(kind, str) or _KIND_PATTERN.fullmatch(kind) is None:
        raise ValueError(f"invalid record kind {kind!r}")
    return kind


def _matches(record: dict[str, Any], filters: dict[str, Any]) -> bool:
    missing = object()
    return all(record.get(key, missing) == value for key, value in filters.items())


class JsonlRecordReader:
    """Strict snapshot reader that never creates or repairs a JSONL store."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.exists():
            raise RecordNotFound(
                f"JSONL record store does not exist: {self.root}", path=self.root,
            )
        if not self.root.is_dir():
            raise RecordCorruption(
                f"JSONL record store is not a directory: {self.root}", path=self.root,
            )

    def _path(self, kind: str) -> Path:
        return self.root / f"{_validate_kind(kind)}.jsonl"

    @staticmethod
    def _marker(path: Path) -> tuple[int, int, int]:
        stat = path.stat()
        return (stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ino", 0))

    def _snapshot(self, path: Path) -> bytes:
        for _attempt in range(2):
            try:
                before = self._marker(path)
                content = path.read_bytes()
                after = self._marker(path)
            except FileNotFoundError as exc:
                raise RecordChanged(
                    f"record file changed while it was being read: {path}", path=path,
                ) from exc
            except OSError as exc:
                raise RecordReadError(
                    f"could not read record file {path}: {exc}", path=path,
                ) from exc
            if before == after and len(content) == after[0]:
                return content
        raise RecordChanged(
            f"record file kept changing while it was being read: {path}", path=path,
        )

    def query(self, kind: str, **filters: Any) -> list[dict[str, Any]]:
        path = self._path(kind)
        if not path.exists():
            return []
        content = self._snapshot(path)
        if content and not content.endswith(b"\n"):
            raise RecordCorruption(
                f"JSONL record file has an unterminated final line: {path}", path=path,
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RecordCorruption(
                f"JSONL record file is not valid UTF-8: {path}",
                path=path,
                line=content[: exc.start].count(b"\n") + 1,
            ) from exc

        records: list[dict[str, Any]] = []
        for line_number, encoded in enumerate(text.splitlines(), 1):
            if not encoded.strip():
                raise RecordCorruption(
                    f"blank JSONL record at {path}:{line_number}",
                    path=path,
                    line=line_number,
                )
            try:
                record = json.loads(encoded)
            except json.JSONDecodeError as exc:
                raise RecordCorruption(
                    f"malformed JSONL record at {path}:{line_number}: {exc.msg}",
                    path=path,
                    line=line_number,
                ) from exc
            if not isinstance(record, dict):
                raise RecordCorruption(
                    f"JSONL record is not an object at {path}:{line_number}",
                    path=path,
                    line=line_number,
                )
            if _matches(record, filters):
                records.append(record)
        return records

    def close(self) -> None:
        """Mirror resource-owning readers without performing any work."""


class SqliteRecordReader:
    """SQLite reader opened in URI read-only and query-only modes, without DDL."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise RecordNotFound(
                f"SQLite record store does not exist: {self.path}", path=self.path,
            )
        if not self.path.is_file():
            raise RecordCorruption(
                f"SQLite record store is not a file: {self.path}", path=self.path,
            )
        uri = self.path.resolve().as_uri() + "?mode=ro&cache=private"
        self._connection: sqlite3.Connection | None = None
        try:
            self._connection = sqlite3.connect(
                uri, uri=True, check_same_thread=False,
            )
            self._connection.execute("PRAGMA query_only=ON")
            columns = {
                str(row[1])
                for row in self._connection.execute("PRAGMA table_info(records)").fetchall()
            }
            if not {"id", "kind", "record"}.issubset(columns):
                raise RecordCorruption(
                    f"SQLite record store has no compatible records table: {self.path}",
                    path=self.path,
                )
        except RecordCorruption:
            if self._connection is not None:
                self._connection.close()
            raise
        except sqlite3.DatabaseError as exc:
            if self._connection is not None:
                self._connection.close()
            raise RecordCorruption(
                f"could not open SQLite record store {self.path}: {exc}", path=self.path,
            ) from exc

    def query(self, kind: str, **filters: Any) -> list[dict[str, Any]]:
        _validate_kind(kind)
        if self._connection is None:
            raise RecordReadError(
                f"SQLite record reader is closed: {self.path}", path=self.path,
            )
        try:
            rows = self._connection.execute(
                "SELECT id, record FROM records WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise RecordCorruption(
                f"could not query SQLite record store {self.path}: {exc}", path=self.path,
            ) from exc

        records: list[dict[str, Any]] = []
        for record_id, encoded in rows:
            try:
                record = json.loads(encoded)
            except (json.JSONDecodeError, TypeError) as exc:
                raise RecordCorruption(
                    f"malformed JSON in SQLite record {record_id} at {self.path}",
                    path=self.path,
                    record_id=int(record_id),
                ) from exc
            if not isinstance(record, dict):
                raise RecordCorruption(
                    f"SQLite record {record_id} is not a JSON object at {self.path}",
                    path=self.path,
                    record_id=int(record_id),
                )
            if _matches(record, filters):
                records.append(record)
        return records

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


@contextmanager
def open_record_reader(spec: StoreSpec | None = None) -> Iterator[RecordReader]:
    """Open and always close one strict reader for a built-in store specification."""
    selected = spec or configured_store_spec()
    reader: JsonlRecordReader | SqliteRecordReader
    if selected.backend == "sqlite":
        reader = SqliteRecordReader(selected.path)
    else:
        reader = JsonlRecordReader(selected.path)
    try:
        yield reader
    finally:
        reader.close()
