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

from . import paths, store
from .seams import ModelSeam
from .types import CallModel, State


def read(bot: str, last: int = 5) -> list[dict]:
    """The most recent entries, oldest first. Cast puts these in the wake text."""
    path = paths.ledger() / f"{bot}.jsonl"
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    return [json.loads(line) for line in lines[-last:]]


def append(bot: str, kind: str, text: str, session_id: str) -> None:
    paths.ledger().mkdir(exist_ok=True)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "session_id": session_id,
        "text": text,
    }
    with (paths.ledger() / f"{bot}.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Death rites
# ---------------------------------------------------------------------------

REPORT_PROMPT = (
    "[arity] You are about to stop. In three sentences, first person, no tools: "
    "what did you do, and what do you think it meant? This goes in your ledger and "
    "the next kernel to hold your name will read it."
)

ARCHIVE_PROMPT = (
    "You are an archivist. Below is the transcript of one session, one line per "
    "message, each starting with who sent it. In three sentences, third person, "
    "say what was asked, what was done, and what was left undone. Do not guess "
    "at motives, and do not describe the transcript's format."
)


def transcript(session_id: str) -> str:
    """The session as the archivist should see it: who said what, in order.
    Not the raw records; those carry seat ids and usage counts that an
    archivist would only narrate back at us."""
    lines = []
    for r in store.read(session_id):
        if r["kind"] == "user":
            lines.append(f"[{r['sender']}] {r['content']}")
        elif r["kind"] == "model" and r.get("content"):
            lines.append(f"[model] {r['content']}")
        elif r["kind"] == "tool":
            lines.append(f"[tool {r['name']}] {r['output'][:300]}")
        elif r["kind"] == "failure":
            lines.append(f"[failure] {r['reason']}")
    return "\n".join(lines)


def death_rites(state: State, model: ModelSeam, archivist: ModelSeam) -> None:
    """Called by the loop when a kernel is retired.

    Two model calls, then two appends. The kernel's own report first, while it
    is still warm. Then the archivist, who is a different model with a
    different job, reading the store rather than the kernel's memory.

    If the report call fails (the seat is out of quota, the model is down),
    the archive is written alone and says so. That is axiom 9's fallback: a
    kernel that dies without speaking still gets an account.
    """
    # 1. The kernel's own report. Same model, same context, no tools.
    try:
        ask = state.messages + [{"role": "user", "content": REPORT_PROMPT}]
        reply = model.call(CallModel(system=state.system_text(), tools=[], messages=ask))
        append(state.bot, "report", reply.text, state.session_id)
        reported = True
    except Exception as exc:
        reported = False
        note = f"(no report: the kernel could not answer at death: {str(exc)[:120]})"

    # 2. The archivist's account. Reads the record, not the kernel.
    ask = [{"role": "user", "content": f"{ARCHIVE_PROMPT}\n\n{transcript(state.session_id)}"}]
    try:
        reply = archivist.call(CallModel(system="", tools=[], messages=ask))
        text = reply.text
    except Exception as exc:
        text = f"(no archive either: {str(exc)[:120]})"
    if not reported:
        text = note + " " + text
    append(state.bot, "archive", text, state.session_id)
