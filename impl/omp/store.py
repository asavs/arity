"""Tiny honest storage: JSON lines and files rooted in this workspace."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def path(self, relative: str | Path) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise StoreError(f"path escapes workspace: {relative}")
        return candidate

    def write_text(self, relative: str | Path, text: str) -> Path:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def append(self, relative: str | Path, record: dict[str, Any]) -> None:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._lock, target.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def read(self, relative: str | Path) -> list[dict[str, Any]]:
        target = self.path(relative)
        if not target.exists():
            return []
        with self._lock:
            return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
