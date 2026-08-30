"""arity stores — RecordStore seam implementations.

`sqlite.SqliteRecordStore` was built by a arity race (task `sqlite_record_store`, 2026-08-29):
tester-authored hidden tests, three builders, one conference round, delivered by `arity run`.
"""
from .sqlite import SqliteRecordStore

__all__ = ["SqliteRecordStore"]
