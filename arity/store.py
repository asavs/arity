"""Store: one JSONL file per session. Keyed by session id. Append-only.

    ~/.arity/store/<session_id>.jsonl

The file is a journal of Events, and the State is a fold over it:

    State = fold(transition, birth, events)

That single property is the crash story (replay the file and continue), the
lineage story (the first line says who this kernel's parent is), and the
evidence story (one row per session for the scorecard to count). Nothing is
ever edited in place, so a file is always a valid replay up to its last line,
which is exactly what a crash leaves behind.

Three kinds of line:

    birth      first line. bot, spec, parent pointer, when.
    event      one per Event the loop popped, verbatim. Message, ModelCompleted,
               ModelFailed, ToolCompleted.
    record     anything else worth keeping that is not an event: an outcome
               from the scorecard, the retired mark from the loop.

The moment never touches this file. The loop journals every event before
handing it to the moment (this module is the plug behind the Store seam).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from . import library, paths, types
from .types import Event, Spec, State

EVENTS = {cls.__name__: cls for cls in (types.Message, types.ModelCompleted, types.ModelFailed,
                                         types.ToolCompleted)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(session_id: str, line: dict) -> None:
    with (paths.store() / f"{session_id}.jsonl").open("a") as f:
        f.write(json.dumps(line) + "\n")


# -- writing ------------------------------------------------------------------

def birth(state: State, parent: dict | None = None) -> None:
    """First line of a session. `parent` is {"session": ..., "call": ...} for a bot
    woken by another bot's message, {"session": ...} for a trial fork, None for a
    kernel the person woke directly."""
    _append(state.session_id, {"kind": "birth", "at": _now(), "bot": state.bot,
                               "spec": asdict(state.spec), "parent": parent,
                               "epoch": library.epoch()})


def fork(base_session: str, new_session: str) -> None:
    """A fork's file starts as a copy of its parent's events, so it replays on its own."""
    for line in read(base_session):
        if line["kind"] == "event":
            _append(new_session, line)


def adopt(base_session: str, fork_session: str) -> None:
    """The base takes the fork's turn as its own: every event line the fork has
    that the base does not is appended to the base, so the base's State stays a
    fold over its own file after a trial. The fork's file is left alone."""
    had = sum(1 for line in read(base_session) if line["kind"] == "event")
    new = [line for line in read(fork_session) if line["kind"] == "event"][had:]
    for line in new:
        _append(base_session, line)
    _append(base_session, {"kind": "adopted", "at": _now(), "fork": fork_session,
                           "events": len(new)})


def event(session_id: str, ev: Event) -> None:
    """The Store seam. One event in, one line out."""
    _append(session_id, {"kind": "event", "at": _now(), "event": type(ev).__name__, **asdict(ev)})


def record(session_id: str, kind: str, **fields: Any) -> None:
    _append(session_id, {"kind": kind, "at": _now(), **fields})


# -- reading ------------------------------------------------------------------

def read(session_id: str) -> list[dict]:
    path = paths.store() / f"{session_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def birth_of(session_id: str) -> dict | None:
    lines = read(session_id)
    return lines[0] if lines and lines[0]["kind"] == "birth" else None


def events(session_id: str) -> list[Event]:
    """The journal back as Event objects, in order, ready to fold."""
    out = []
    for line in read(session_id):
        if line["kind"] != "event":
            continue
        cls = EVENTS[line["event"]]
        fields = {k: v for k, v in line.items() if k not in ("kind", "at", "event")}
        out.append(cls(**fields))
    return out


def usage(session_id: str) -> dict[str, int]:
    """Tokens in and out for one session, summed over its model turns."""
    tokens_in = tokens_out = 0
    for line in read(session_id):
        if line["kind"] == "event" and line["event"] == "ModelCompleted":
            u = line.get("usage") or {}
            tokens_in += u.get("input_tokens", u.get("prompt_tokens", 0)) or 0
            tokens_out += u.get("output_tokens", u.get("completion_tokens", 0)) or 0
    return {"tokens_in": tokens_in, "tokens_out": tokens_out}


def sessions() -> list[str]:
    return sorted(p.stem for p in paths.store().glob("*.jsonl"))


def unfinished() -> list[str]:
    """Sessions with no retired mark: crashed, or still open somewhere."""
    return [s for s in sessions() if not any(l["kind"] == "retired" for l in read(s))]


def rows() -> list[dict]:
    """One row per session: the whole dataset the scorecard, and any cleverer
    selector later, ever needs. It reads this and nothing else.

        session, bot, spec, parent, task, calls, tokens_in, tokens_out,
        failures, tool_calls, worked, won, score, retired, epoch, current,
        started, ended

    `current` is whether the session was born under the library's present
    ruleset epoch; the scorecard only counts current rows. `worked` is whether
    any tool ran: the one split between kinds of task that is true by
    construction rather than by someone's label. Task kind beyond the role is
    otherwise undecided; `task` keeps the raw material for deciding it later.
    """
    out = []
    for session_id in sessions():
        lines = read(session_id)
        if not lines or lines[0]["kind"] != "birth":
            continue
        b = lines[0]
        row = {"session": session_id, "bot": b["bot"], "spec": Spec(**{
                   k: tuple(v) if isinstance(v, list) else v for k, v in b["spec"].items()}),
               "parent": b["parent"], "task": None, "calls": 0, "tokens_in": 0, "tokens_out": 0,
               "failures": 0, "won": None, "score": None, "retired": False,
               "tool_calls": 0, "worked": False,
               "epoch": b.get("epoch", 1), "current": b.get("epoch", 1) == library.epoch(),
               "started": b["at"], "ended": lines[-1]["at"]}
        for l in lines[1:]:
            if l["kind"] == "event" and l["event"] == "Message" and row["task"] is None:
                row["task"] = l["text"].splitlines()[0][:120]
            elif l["kind"] == "event" and l["event"] == "ModelCompleted":
                row["calls"] += 1
                u = l.get("usage") or {}
                row["tokens_in"] += u.get("input_tokens", u.get("prompt_tokens", 0)) or 0
                row["tokens_out"] += u.get("output_tokens", u.get("completion_tokens", 0)) or 0
            elif l["kind"] == "event" and l["event"] == "ToolCompleted":
                row["tool_calls"] += 1
                row["worked"] = True            # it did something, not just said something
            elif l["kind"] == "event" and l["event"] == "ModelFailed":
                row["failures"] += 1
            elif l["kind"] == "outcome":
                row["won"], row["score"] = l["won"], l["score"]
            elif l["kind"] == "retired":
                row["retired"] = True
        out.append(row)
    return out
