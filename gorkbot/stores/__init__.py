"""Arity stores — RecordStore seam implementations.

Historical provenance: ``sqlite.SqliteRecordStore`` was built by a pre-rename
``gorkbot race`` (task ``sqlite_record_store``, 2026-08-29), then delivered by
``gorkbot run`` after hidden tests, three builders, and one conference round.
"""
from .sqlite import SqliteRecordStore

__all__ = ["SqliteRecordStore"]
