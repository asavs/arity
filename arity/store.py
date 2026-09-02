"""Store: one JSONL file per session. Keyed by session id.

    store/<session_id>.jsonl

Every message, model turn and tool result the moment asked to keep, one
line each, in the order they happened. This is the conversation, and it is
the raw evidence: the scorecard counts over it, the archivist reads it.

The moment never touches this file. It emits a StoreRecord effect and the
loop hands the effect here (this module is the plug behind the Store seam).

Naive version: open, write one line, close. Reading is a loop over lines.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .types import StoreRecord


def append(effect: StoreRecord) -> None:
    """The Store seam. One record in, one line out."""
    paths.store().mkdir(exist_ok=True)
    line = {
        "at": datetime.now(timezone.utc).isoformat(),
        "session_id": effect.session_id,
        "seat": effect.seat,
        "kind": effect.kind,
        **effect.record,
    }
    with (paths.store() / f"{effect.session_id}.jsonl").open("a") as f:
        f.write(json.dumps(line) + "\n")


def read(session_id: str) -> list[dict]:
    """The whole conversation back, oldest first."""
    path = paths.store() / f"{session_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def sessions() -> list[str]:
    """Every session id we have a file for. The scorecard walks these."""
    if not paths.store().exists():
        return []
    return [p.stem for p in paths.store().glob("*.jsonl")]
