"""store.py - In-memory and disk store for tiers, records, and workspace files."""

from __future__ import annotations
import os
import time
import itertools
from dataclasses import dataclass, field
from typing import Any

_record_counter = itertools.count(1)


@dataclass
class Record:
    tier: int
    kind: str  # own_report, archivist_entry, absence, event, handoff
    body: Any
    by: str
    at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"rec_{next(_record_counter):04d}")


class Store:
    """Storage backing memory tiers, archivist entries, and sandboxed workspace files."""

    def __init__(self, workspace_dir: str = "workspace") -> None:
        self.workspace_dir = os.path.abspath(workspace_dir)
        os.makedirs(self.workspace_dir, exist_ok=True)
        self.records: list[Record] = []

    def write_record(self, tier: int, record: Record) -> Record:
        self.records.append(record)
        return record

    def get_records(self, tier: int | None = None, kind: str | None = None) -> list[Record]:
        out = self.records
        if tier is not None:
            out = [r for r in out if r.tier == tier]
        if kind is not None:
            out = [r for r in out if r.kind == kind]
        return out

    def write_file(self, rel_path: str, content: str) -> str:
        clean = rel_path.lstrip("/\\")
        full_path = os.path.abspath(os.path.join(self.workspace_dir, clean))
        if not full_path.startswith(self.workspace_dir):
            raise PermissionError(f"Path escape attempt: {rel_path}")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return full_path

    def read_file(self, rel_path: str) -> str:
        clean = rel_path.lstrip("/\\")
        full_path = os.path.abspath(os.path.join(self.workspace_dir, clean))
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"No such file: {rel_path}")
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def file_exists(self, rel_path: str) -> bool:
        clean = rel_path.lstrip("/\\")
        full_path = os.path.abspath(os.path.join(self.workspace_dir, clean))
        return os.path.exists(full_path)
