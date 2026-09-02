"""Ledger: what makes a bot outlive a kernel. One append-only file per bot.

    ledger/<bot>.jsonl

A bot is a name with a memory. A kernel is one model holding that name for a
while. When the kernel dies, the ledger is how the next kernel knows what the
last one did.

Read at birth (by cast), written at death (by the loop). Every entry is one
of two kinds:

    "report"     first person. The kernel's own account of what it did and
                 what it thinks it meant. Asked for with no tools, as its
                 last model call.
    "archive"    third person. The archivist reads the session's store and
                 writes an impartial account of what actually happened.

The two are not redundant. The report is the best evidence for what the
kernel meant; the archive is the best evidence for what it did. If a kernel
dies without writing a report, the archive is the only entry and says so.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .seams import ModelSeam
from .types import CallModel, State

ROOT = Path(__file__).parent / "ledger"


def read(bot: str, last: int = 5) -> list[dict]:
    """The most recent entries, oldest first. Cast puts these in the wake text."""
    path = ROOT / f"{bot}.jsonl"
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    return [json.loads(line) for line in lines[-last:]]


def append(bot: str, kind: str, text: str, session_id: str) -> None:
    ROOT.mkdir(exist_ok=True)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "session_id": session_id,
        "text": text,
    }
    with (ROOT / f"{bot}.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Death rites
# ---------------------------------------------------------------------------

REPORT_PROMPT = (
    "You are about to stop. With no tools, in a few sentences: what did you do, "
    "and what do you think it meant? First person. This goes in your ledger and "
    "the next kernel to hold your name will read it."
)

ARCHIVE_PROMPT = (
    "You are an archivist. Below is the full record of a session. Write an "
    "impartial third-person account of what actually happened: what was asked, "
    "what was done, what was left undone. Do not guess at motives."
)


def death_rites(state: State, model: ModelSeam, archivist: ModelSeam) -> None:
    """Called by the loop when the moment returns Halt.

    Two model calls, then two appends. The kernel's own report first, while it
    is still warm. Then the archivist, who is a different model with a
    different job, reading the store rather than the kernel's memory.
    """
    # 1. The kernel's own report. Same model, same context, no tools.
    ask = state.messages + [{"role": "user", "content": REPORT_PROMPT}]
    reply = model.call(CallModel(system=state.system_text(), tools=[], messages=ask))
    append(state.bot, "report", reply.text, state.session_id)

    # 2. The archivist's account. Reads the record, not the kernel.
    transcript = json.dumps(store.read(state.session_id), indent=1)
    ask = [{"role": "user", "content": f"{ARCHIVE_PROMPT}\n\n{transcript}"}]
    reply = archivist.call(CallModel(system="", tools=[], messages=ask))
    append(state.bot, "archive", reply.text, state.session_id)
