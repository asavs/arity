"""Arity core types — events, effects, state, and messages.

Transitions update state and return effect descriptions: transition(state, event) -> (state, effects).
Effects describe what side-effects need to happen (I/O, network, disk, timers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from .telemetry import UsageEvidence


class Status(str, Enum):
    IDLE = "idle"
    WAITING_MODEL = "waiting_model"
    WAITING_TOOLS = "waiting_tools"
    WAITING_HANDOFF = "waiting_handoff"
    HALTED = "halted"


# -----------------------------------------------------------------------------
# Events: Inputs to the state machine (from user, model, tools, timers, subagents)
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class UserMessage:
    """A message arriving from a human or client channel."""
    text: str
    sender: str = "user"
    channel: str = "main"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelCompleted:
    """The model finished a completion turn."""
    content: Optional[str]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = "stop"
    seat_id: Optional[str] = None
    usage_evidence: Optional["UsageEvidence"] = None


@dataclass(frozen=True)
class ModelFailed:
    """A model call failed (network, 4xx/5xx, rate limit, quota)."""
    error: str
    seat_id: Optional[str] = None
    retryable: bool = True


@dataclass(frozen=True)
class ToolCompleted:
    """A tool finished execution."""
    call_id: str
    tool_name: str
    output: str
    is_error: bool = False


@dataclass(frozen=True)
class PulseTick:
    """A keepalive or heartbeat timer tick."""
    timestamp: float = 0.0


@dataclass(frozen=True)
class HandoffRequested:
    """Request to delegate a subtask to a specialized role/subagent."""
    target_role: str
    brief: str
    budget: float = 1.0


@dataclass(frozen=True)
class HandoffCompleted:
    """A delegated subagent completed its work."""
    child_session_id: str
    output: str
    status: str = "completed"


@dataclass(frozen=True)
class Interrupt:
    """External interruption or cancellation request."""
    reason: str = "user_cancel"


Event = Union[
    UserMessage,
    ModelCompleted,
    ModelFailed,
    ToolCompleted,
    PulseTick,
    HandoffRequested,
    HandoffCompleted,
    Interrupt,
]


# -----------------------------------------------------------------------------
# Effects: Side-effect descriptions returned by the transition function
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class CallModel:
    """Call a model provider with current conversation and available tools."""
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    seat: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None


@dataclass(frozen=True)
class ExecuteTool:
    """Execute a single tool call."""
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class EmitMessage:
    """Send a message out to a human or transport channel."""
    channel: str
    recipient: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoreRecord:
    """Persist an audit, scorecard, friction, or memory record."""
    kind: str
    record: dict[str, Any]


@dataclass(frozen=True)
class SpawnHandoff:
    """Spawn a subagent statechart to handle a delegated brief."""
    session_id: str
    target_role: str
    brief: str
    budget: float


@dataclass(frozen=True)
class SchedulePulse:
    """Schedule a future pulse tick."""
    delay_seconds: float


@dataclass(frozen=True)
class Halt:
    """Halt the statechart session with a final reason and optional output."""
    reason: str
    output: Optional[str] = None


Effect = Union[
    CallModel,
    ExecuteTool,
    EmitMessage,
    StoreRecord,
    SpawnHandoff,
    SchedulePulse,
    Halt,
]


# -----------------------------------------------------------------------------
# State: The session data container
# -----------------------------------------------------------------------------

@dataclass
class State:
    session_id: str
    status: Status = Status.IDLE
    role: str = "assistant"
    system_prompt: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_tools: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    output: Optional[str] = None
    error_count: int = 0
    max_errors: int = 3

    def is_active(self) -> bool:
        return self.status != Status.HALTED

    def get_full_messages(self) -> list[dict[str, Any]]:
        """Return full messages list including system prompt if set."""
        if self.system_prompt:
            return [{"role": "system", "content": self.system_prompt}] + self.messages
        return list(self.messages)
