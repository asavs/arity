"""Strict, query-only readers for Arity's built-in record backends.

Writable stores optimize for inexpensive append operations and compatibility.
Inspection has a different trust boundary: it must not create state and it must
not silently turn malformed persistence into a shorter, apparently valid log.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import stat as stat_module
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

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

    configured = get_config_value("ARITY_STORE") or "jsonl"
    if configured.lower() == "sqlite":
        return StoreSpec("sqlite", Path(".arity/records.db"))
    # Preserve the writable store's historical behavior: only ``sqlite`` opts
    # away from JSONL, including for an unrecognized compatibility value.
    return StoreSpec("jsonl", Path(".arity/records"))


def _validate_kind(kind: str) -> str:
    if not isinstance(kind, str) or _KIND_PATTERN.fullmatch(kind) is None:
        raise ValueError(f"invalid record kind {kind!r}")
    return kind


def _strict_equal(left: Any, right: Any) -> bool:
    """Compare JSON-like values without Python's ``True == 1`` coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _strict_equal(left_value, right_value)
            for left_value, right_value in zip(left, right)
        )
    return bool(left == right)


def _matches(record: dict[str, Any], filters: dict[str, Any]) -> bool:
    missing = object()
    for key, value in filters.items():
        actual = record.get(key, missing)
        if actual is missing or not _strict_equal(actual, value):
            return False
    return True


class _AmbiguousJson(ValueError):
    """A syntactically accepted JSON value that is unsafe to project."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _AmbiguousJson("duplicate object key")
        result[key] = value
    return result


def _finite_float(encoded: str) -> float:
    value = float(encoded)
    if not math.isfinite(value):
        raise _AmbiguousJson("non-finite number")
    return value


def _reject_constant(encoded: str) -> Any:
    raise _AmbiguousJson(f"non-standard numeric constant {encoded}")


def _strict_json_loads(encoded: str | bytes) -> Any:
    return json.loads(
        encoded,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
    )


def _diagnostic_record_id(value: Any) -> int | None:
    """Return an ID safe for a diagnostic envelope, never coercing untrusted data."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _sqlite_failure(
    exc: sqlite3.DatabaseError,
    *,
    path: Path,
    action: str,
) -> RecordReadError:
    """Map SQLite's primary result codes onto inspection failure semantics."""
    raw_code = getattr(exc, "sqlite_errorcode", None)
    primary = raw_code & 0xFF if isinstance(raw_code, int) else None
    message = str(exc).lower()

    # Use SQLite's stable primary result-code values directly. The matching
    # ``sqlite3.SQLITE_*`` names and ``sqlite_errorcode`` attribute were not
    # both exposed on every supported Python 3.10 patch release.
    changed_codes = {5, 6, 17}  # BUSY, LOCKED, SCHEMA
    read_error_codes = {3, 7, 8, 10, 13, 14, 23}  # PERM .. AUTH
    corruption_codes = {11, 24, 26}  # CORRUPT, FORMAT, NOTADB

    detail = f"could not {action} SQLite record store {path}: {exc}"
    if (
        primary in changed_codes
        or "locked" in message
        or "schema has changed" in message
        or "no such table" in message
        or "no such column" in message
    ):
        return RecordChanged(detail, path=path)
    if (
        primary in corruption_codes
        or "not a database" in message
        or "database disk image is malformed" in message
        or "malformed database schema" in message
    ):
        return RecordCorruption(detail, path=path)
    if (
        primary in read_error_codes
        or "unable to open database" in message
        or "permission denied" in message
        or "readonly database" in message
        or "disk i/o error" in message
    ):
        return RecordReadError(detail, path=path)
    # An unknown operational failure is not evidence that persisted bytes are
    # corrupt. Keep it operational unless SQLite explicitly says otherwise.
    return RecordReadError(detail, path=path)


class JsonlRecordReader:
    """Strict snapshot reader that never creates or repairs a JSONL store."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        try:
            root_stat = self.root.stat()
        except FileNotFoundError as exc:
            raise RecordNotFound(
                f"JSONL record store does not exist: {self.root}", path=self.root,
            ) from exc
        except OSError as exc:
            raise RecordReadError(
                f"could not inspect JSONL record store {self.root}: {exc}",
                path=self.root,
            ) from exc
        if not stat_module.S_ISDIR(root_stat.st_mode):
            raise RecordCorruption(
                f"JSONL record store is not a directory: {self.root}", path=self.root,
            )

    def _path(self, kind: str) -> Path | None:
        expected = f"{_validate_kind(kind)}.jsonl"
        try:
            # Do not stat a synthesized path: that becomes case-insensitive on
            # Windows. Directory entry spelling makes kind lookup portable.
            for entry in self.root.iterdir():
                if entry.name == expected:
                    return entry
        except FileNotFoundError as exc:
            raise RecordChanged(
                f"JSONL record store changed while it was being read: {self.root}",
                path=self.root,
            ) from exc
        except OSError as exc:
            raise RecordReadError(
                f"could not list JSONL record store {self.root}: {exc}", path=self.root,
            ) from exc
        return None

    @staticmethod
    def _marker(path: Path) -> tuple[int, int, int, int]:
        file_stat = path.stat()
        if not stat_module.S_ISREG(file_stat.st_mode):
            raise RecordCorruption(
                f"JSONL record path is not a regular file: {path}", path=path,
            )
        return (
            file_stat.st_size,
            file_stat.st_mtime_ns,
            getattr(file_stat, "st_dev", 0),
            getattr(file_stat, "st_ino", 0),
        )

    def _snapshot(self, path: Path) -> bytes:
        for _attempt in range(2):
            try:
                before = self._marker(path)
                content = path.read_bytes()
                after = self._marker(path)
            except RecordCorruption:
                raise
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
        if path is None:
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
        # JSON permits raw U+0085/U+2028/U+2029 characters inside strings;
        # str.splitlines() would incorrectly split those valid records.
        lines = [] if not text else text[:-1].split("\n")
        for line_number, encoded in enumerate(lines, 1):
            if encoded.endswith("\r"):
                encoded = encoded[:-1]
            if not encoded.strip():
                raise RecordCorruption(
                    f"blank JSONL record at {path}:{line_number}",
                    path=path,
                    line=line_number,
                )
            try:
                record = _strict_json_loads(encoded)
            except (ValueError, RecursionError) as exc:
                raise RecordCorruption(
                    f"malformed JSONL record at {path}:{line_number}: {exc}",
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
    """SQLite reader opened without ever creating state beside the source DB."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._snapshot_directory: tempfile.TemporaryDirectory[str] | None = None

        try:
            database_stat = self.path.stat()
        except FileNotFoundError as exc:
            raise RecordNotFound(
                f"SQLite record store does not exist: {self.path}", path=self.path,
            ) from exc
        except OSError as exc:
            raise RecordReadError(
                f"could not inspect SQLite record store {self.path}: {exc}", path=self.path,
            ) from exc
        if not stat_module.S_ISREG(database_stat.st_mode):
            raise RecordCorruption(
                f"SQLite record store is not a file: {self.path}", path=self.path,
            )

        # SQLite's ``mode=ro`` can still create a ``-shm`` companion beside a
        # WAL database. Always open a bounded private snapshot, even when no WAL
        # is currently visible, so a concurrent journal-mode change cannot turn
        # inspection into a source mutation.
        open_path = self._private_snapshot()

        try:
            uri = open_path.resolve().as_uri() + "?mode=rw&cache=private"
        except (OSError, ValueError) as exc:
            self.close()
            raise RecordReadError(
                f"could not resolve SQLite record store {self.path}: {exc}", path=self.path,
            ) from exc
        try:
            self._connection = sqlite3.connect(
                uri,
                uri=True,
                check_same_thread=False,
                timeout=0.1,
            )
            self._connection.execute("PRAGMA query_only=ON")
            self._validate_schema()
        except RecordReadError:
            self.close()
            raise
        except sqlite3.DatabaseError as exc:
            self.close()
            raise _sqlite_failure(exc, path=self.path, action="open") from exc

    @staticmethod
    def _file_marker(path: Path) -> tuple[int, int, int, int]:
        file_stat = path.stat()
        if not stat_module.S_ISREG(file_stat.st_mode):
            raise RecordCorruption(
                f"SQLite persistence path is not a regular file: {path}", path=path,
            )
        return (
            file_stat.st_size,
            file_stat.st_mtime_ns,
            getattr(file_stat, "st_dev", 0),
            getattr(file_stat, "st_ino", 0),
        )

    @classmethod
    def _optional_file_marker(cls, path: Path) -> tuple[int, int, int, int] | None:
        try:
            return cls._file_marker(path)
        except FileNotFoundError:
            return None

    def _write_private_snapshot(self, snapshot: Mapping[Path, bytes]) -> Path:
        try:
            self._snapshot_directory = tempfile.TemporaryDirectory(
                prefix="arity-record-reader-",
            )
            private_root = Path(self._snapshot_directory.name)
            for source, content in snapshot.items():
                (private_root / source.name).write_bytes(content)
        except OSError as exc:
            if self._snapshot_directory is not None:
                self._snapshot_directory.cleanup()
                self._snapshot_directory = None
            raise RecordReadError(
                f"could not create a private SQLite snapshot for {self.path}: {exc}",
                path=self.path,
            ) from exc
        return private_root / self.path.name

    def _private_snapshot(self) -> Path:
        wal_path = self.path.with_name(f"{self.path.name}-wal")
        journal_path = self.path.with_name(f"{self.path.name}-journal")
        for _attempt in range(3):
            try:
                wal_before = self._optional_file_marker(wal_path)
                journal_before = self._optional_file_marker(journal_path)
                if journal_before is not None:
                    raise RecordChanged(
                        f"SQLite rollback journal is active: {journal_path}",
                        path=self.path,
                    )
                if wal_before is not None:
                    return self._private_wal_snapshot(wal_path)

                before = self._file_marker(self.path)
                content = self.path.read_bytes()
                after = self._file_marker(self.path)
                wal_after = self._optional_file_marker(wal_path)
                journal_after = self._optional_file_marker(journal_path)
            except (RecordChanged, RecordCorruption):
                raise
            except FileNotFoundError as exc:
                raise RecordChanged(
                    f"SQLite record store changed while it was being read: {self.path}",
                    path=self.path,
                ) from exc
            except OSError as exc:
                raise RecordReadError(
                    f"could not snapshot SQLite record store {self.path}: {exc}",
                    path=self.path,
                ) from exc

            if journal_after is not None:
                raise RecordChanged(
                    f"SQLite rollback journal appeared while reading: {journal_path}",
                    path=self.path,
                )
            if wal_after is not None:
                # Retry through the DB+WAL path; the source itself is never opened.
                continue
            if before == after and len(content) == after[0]:
                return self._write_private_snapshot({self.path: content})
        raise RecordChanged(
            f"SQLite record store kept changing while it was being read: {self.path}",
            path=self.path,
        )

    def _private_wal_snapshot(self, wal_path: Path) -> Path:
        paths = (self.path, wal_path)
        journal_path = self.path.with_name(f"{self.path.name}-journal")
        snapshot: dict[Path, bytes] | None = None
        for _attempt in range(2):
            try:
                if self._optional_file_marker(journal_path) is not None:
                    raise RecordChanged(
                        f"SQLite rollback journal is active: {journal_path}",
                        path=self.path,
                    )
                before = {path: self._file_marker(path) for path in paths}
                content = {path: path.read_bytes() for path in paths}
                after = {path: self._file_marker(path) for path in paths}
                journal_after = self._optional_file_marker(journal_path)
            except (RecordChanged, RecordCorruption):
                raise
            except FileNotFoundError as exc:
                raise RecordChanged(
                    f"SQLite WAL store changed while it was being read: {self.path}",
                    path=self.path,
                ) from exc
            except OSError as exc:
                raise RecordReadError(
                    f"could not snapshot SQLite WAL store {self.path}: {exc}",
                    path=self.path,
                ) from exc
            if journal_after is not None:
                raise RecordChanged(
                    f"SQLite rollback journal appeared while reading: {journal_path}",
                    path=self.path,
                )
            if before == after and all(
                len(content[path]) == after[path][0] for path in paths
            ):
                snapshot = content
                break
        if snapshot is None:
            raise RecordChanged(
                f"SQLite WAL store kept changing while it was being read: {self.path}",
                path=self.path,
            )

        return self._write_private_snapshot(snapshot)

    def _validate_schema(self) -> None:
        if self._connection is None:
            raise RecordReadError(
                f"SQLite record reader is closed: {self.path}", path=self.path,
            )
        row = self._connection.execute(
            "SELECT type FROM sqlite_schema WHERE name = ? COLLATE BINARY",
            ("records",),
        ).fetchone()
        if row is None or row[0] != "table":
            raise RecordCorruption(
                f"SQLite record store has no compatible records table: {self.path}",
                path=self.path,
            )

        columns = {
            str(column[1]).lower(): {
                "type": str(column[2]).strip().upper(),
                "not_null": bool(column[3]),
                "primary_key": int(column[5]),
            }
            for column in self._connection.execute("PRAGMA table_info(records)").fetchall()
        }
        id_column = columns.get("id")
        kind_column = columns.get("kind")
        record_column = columns.get("record")
        compatible = (
            id_column is not None
            and id_column["type"] == "INTEGER"
            and id_column["primary_key"] == 1
            and kind_column
            == {"type": "TEXT", "not_null": True, "primary_key": 0}
            and record_column
            == {"type": "TEXT", "not_null": True, "primary_key": 0}
            and all(
                name == "id" or column["primary_key"] == 0
                for name, column in columns.items()
            )
        )
        if not compatible:
            raise RecordCorruption(
                f"SQLite record store has no compatible records table: {self.path}",
                path=self.path,
            )

    def query(self, kind: str, **filters: Any) -> list[dict[str, Any]]:
        _validate_kind(kind)
        if self._connection is None:
            raise RecordReadError(
                f"SQLite record reader is closed: {self.path}", path=self.path,
            )
        try:
            rows = self._connection.execute(
                "SELECT id, record FROM records "
                "WHERE kind COLLATE BINARY = ? ORDER BY id",
                (kind,),
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            failure = _sqlite_failure(exc, path=self.path, action="query")
            self.close()
            raise failure from exc

        records: list[dict[str, Any]] = []
        for record_id, encoded in rows:
            diagnostic_id = _diagnostic_record_id(record_id)
            try:
                record = _strict_json_loads(encoded)
            except (UnicodeDecodeError, TypeError, ValueError, RecursionError) as exc:
                failure = RecordCorruption(
                    f"malformed JSON in SQLite record {record_id!r} at {self.path}",
                    path=self.path,
                    record_id=diagnostic_id,
                )
                self.close()
                raise failure from exc
            if not isinstance(record, dict):
                failure = RecordCorruption(
                    f"SQLite record {record_id!r} is not a JSON object at {self.path}",
                    path=self.path,
                    record_id=diagnostic_id,
                )
                self.close()
                raise failure
            if _matches(record, filters):
                records.append(record)
        return records

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._snapshot_directory is not None:
            self._snapshot_directory.cleanup()
            self._snapshot_directory = None


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
