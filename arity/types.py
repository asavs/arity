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
    effort: str | None = None       # paired with the model, not the provider: "low",
                                    # "medium", "high", or the model's own vocabulary.
                                    # None sends nothing and lets the model default.


# ---------------------------------------------------------------------------
# State: a kernel's whole memory
# ---------------------------------------------------------------------------

class Status(Enum):
    IDLE = "idle"                   # nothing pending; the kernel is between turns
    WAITING_MODEL = "waiting_model"
    WAITING_TOOLS = "waiting_tools"
    RETIRED = "retired"             # death rites done; never runs again


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
    talking_to: str = ""                    # who sent the message this turn answers
    output: str | None = None               # the last thing the model said in plain text
    last_call_at: float = 0.0               # when the wire last touched this prefix (keepalive)

    def system_text(self) -> str:
        """The system prompt is just the blocks joined. Order is set by cast."""
        return "\n\n".join(self.system)


# ---------------------------------------------------------------------------
# Events: what happened
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Message:
    """Someone sent this kernel a message. A person or another bot; the kernel
    does not care which. `sender` is a name in bots.json, or a person's name."""
    sender: str
    text: str


@dataclass(frozen=True)
class ModelCompleted:
    """A model answered. tool_calls is empty when it just talked."""
    text: str
    tool_calls: list[dict[str, Any]]
    usage: dict[str, int]


@dataclass(frozen=True)
class ModelFailed:
    """The wire could not get an answer: a 429, a timeout, a bad key. The moment
    treats it like a very short answer that says so, so the person hears
    something and the kernel goes idle instead of the whole program dying."""
    reason: str


@dataclass(frozen=True)
class ToolCompleted:
    """A tool returned."""
    call_id: str
    name: str
    output: str


Event = Message | ModelCompleted | ModelFailed | ToolCompleted

# There is no pulse. Nothing wakes a kernel on a clock to ask if it has
# anything to say. What there is instead is a keepalive (loop.py): a cheap
# throwaway call that keeps a provider's cached prefix warm while the person
# is still around, so the next real message is not paid for cold.


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
    max_tokens: int | None = None   # the wire's default unless a caller has a reason


@dataclass(frozen=True)
class ExecuteTool:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Send:
    """Deliver text to a recipient: a person, or another bot.

    This is the one way anything leaves a kernel. There is no hierarchy: every
    bot can message every bot, and a person is just a recipient that is not a
    bot. Two flavours:

        call_id set    the model used the `message` tool mid-turn and is waiting
                       for a reply; the loop wakes the recipient, gets its answer,
                       and hands it back as the tool result.
        call_id None   the model finished its turn; this is its answer to whoever
                       spoke to it (`State.talking_to`).
    """
    to: str
    text: str
    call_id: str | None = None


Effect = CallModel | ExecuteTool | Send

# There is no "keep a record" effect. The loop journals every Event to the
# store before the moment sees it (store.py), so the conversation is on disk
# by construction and the moment has nothing to remember.
