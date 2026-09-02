"""The nouns.

Every other file in this package only moves these around. There are four kinds:

    Spec    a row of names. No text. Points into the library and the seat table.
    State   a kernel's whole memory. All values, all copies. Forkable.
    Event   the only thing that moves a moment forward.
    Effect  what a moment asks the loop to do on its behalf.

Spec is the last thing that is "by name". State is the first thing that is
"by value". Cast is the function that turns one into the other (see cast.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Spec: a row of names
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Spec:
    """Which seat, which model, which harness, which role, which skills, which tools.

    Frozen so it can be a dictionary key: the scorecard counts wins per spec,
    and a trial varies one field at a time to make N specs from one.

    Nothing in here is text. Every field is a name that resolves in a store.
    """
    seat: str                       # key into seats.py      e.g. "anthropic-max"
    model: str                      # a model id              e.g. "claude-opus-5"
    role: str                       # key into library/roles  e.g. "typescript-developer"
    skills: tuple[str, ...] = ()    # keys into library/skills
    tools: tuple[str, ...] = ()     # keys into library/tools
    harness: str = "kernel"         # where the kernel runs; "kernel" is our own loop


# ---------------------------------------------------------------------------
# State: a kernel's whole memory
# ---------------------------------------------------------------------------

class Status(Enum):
    IDLE = "idle"
    WAITING_MODEL = "waiting_model"
    WAITING_TOOLS = "waiting_tools"
    HALTED = "halted"


@dataclass
class State:
    """Everything a kernel knows. Copies, not references.

    The two ids are its only pointers:
        session_id  -> the file in store.py where its records go
        bot         -> the file in ledger.py it wakes from and dies into

    Everything else is text or schemas that cast already copied in.
    Because it is all values, a State can be forked with copy.deepcopy.
    """
    session_id: str
    bot: str
    spec: Spec
    system: list[str]                       # resolved text blocks, in order
    tools: list[dict[str, Any]]             # tool schemas the model will see
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: Status = Status.IDLE
    output: str | None = None               # the last thing the model said in plain text

    def system_text(self) -> str:
        """The system prompt is just the blocks joined. Order is set by cast."""
        return "\n\n".join(self.system)


# ---------------------------------------------------------------------------
# Events: what happened
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserMessage:
    """A person typed."""
    text: str


@dataclass(frozen=True)
class ModelCompleted:
    """A model answered. tool_calls is empty when it just talked."""
    text: str
    tool_calls: list[dict[str, Any]]
    usage: dict[str, int]


@dataclass(frozen=True)
class ToolCompleted:
    """A tool returned."""
    call_id: str
    name: str
    output: str


@dataclass(frozen=True)
class Tick:
    """The pulse fired and nobody said anything. The kernel decides if it means something."""
    at: str


Event = UserMessage | ModelCompleted | ToolCompleted | Tick


# ---------------------------------------------------------------------------
# Effects: what the moment would like done
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CallModel:
    """The payload. The four things the model receives, and nothing else.

    tool schemas + system text + the conversation so far + the newest event
    (the newest event is the last entry in messages).
    """
    system: str
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class ExecuteTool:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class StoreRecord:
    """One line for the session's JSONL file.

    Carries session_id and seat so that, later, a record can point back to
    both the conversation it came from and the spec that produced it.
    """
    session_id: str
    seat: str
    kind: str                       # "user" | "model" | "tool"
    record: dict[str, Any]


@dataclass(frozen=True)
class EmitMessage:
    """Show the person something."""
    text: str


@dataclass(frozen=True)
class Halt:
    """The kernel is done. The loop performs the death rites (see ledger.py)."""
    reason: str


Effect = CallModel | ExecuteTool | StoreRecord | EmitMessage | Halt
