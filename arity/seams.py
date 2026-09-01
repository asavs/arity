"""Arity seams — explicit interfaces for pluggable infrastructure grafts.

External model routers, tool harnesses, record stores, transports, and telemetry
integrations can implement these protocols. Trial verification and ranking remain
built-in orchestration stages rather than implementations of these seams.

Defined in this module:

    ModelProvider  Model routing: gateways, provider SDKs, direct HTTP calls.
    ToolRunner     Tool execution: sandboxes, MCP servers, local Python functions.
    RecordReader   Query-only record access for inspection, dashboards, and replay.
    RecordStore    RecordReader plus append: JSONL, SQLite, vector DBs, audit ledgers.
    Transport      User/channel I/O: CLI, webhook, Discord, Slack, SMS.
    Observer       Event/effect telemetry and evaluation monitoring.

Defined elsewhere. Each is expressed in terms of types declared in its own module, so
it stays beside them; hosting it here would mean importing those modules, and two of
them already import this one.

    ContextAdapter (arity.terrarium)
        One named, testable transform applied to a ContextEnvelope just before a
        candidate runtime starts.
    TrialEvaluator (arity.evidence)
        Turns a frozen EvidenceBundle into an Evaluation, so alternate evaluators can
        run later without rerunning the candidate harnesses.
    TrialJournal (arity.trial_events)
        A concrete class rather than a Protocol: persists ordered lifecycle events
        through any RecordStore, from which replay_trial reconstructs the trial.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from .types import (
    CallModel,
    Effect,
    EmitMessage,
    Event,
    ExecuteTool,
    ModelCompleted,
    ModelFailed,
    State,
    StoreRecord,
    ToolCompleted,
)


@runtime_checkable
class ModelProvider(Protocol):
    """Graft point for model routers, gateways, and provider SDKs."""

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        """Execute a model call and return completion or failure."""
        ...


@runtime_checkable
class ToolRunner(Protocol):
    """Graft point for tool harnesses, sandboxes, MCP servers, and bash runners."""

    def execute(self, effect: ExecuteTool) -> ToolCompleted:
        """Execute a tool call and return the result."""
        ...

    def get_schemas(self) -> list[dict[str, Any]]:
        """Return JSON Schema tool definitions exposed to the model."""
        ...


@runtime_checkable
class RecordReader(Protocol):
    """Query-only graft point for inspection, dashboards, and replay tooling."""

    def query(self, kind: str, **filters: Any) -> list[dict[str, Any]]:
        """Query records matching filter criteria."""
        ...


@runtime_checkable
class RecordStore(RecordReader, Protocol):
    """Graft point for persistence, transcripts, vector DBs, and audit ledgers."""

    def append(self, effect: StoreRecord) -> None:
        """Append a structured record."""
        ...

@runtime_checkable
class Transport(Protocol):
    """Graft point for messaging front-doors (CLI, Webhook, Discord, Slack, SMS)."""

    def emit(self, effect: EmitMessage) -> None:
        """Deliver an outgoing message to the user/channel."""
        ...


@runtime_checkable
class Observer(Protocol):
    """Graft point for event/effect telemetry and evaluation monitoring."""

    def on_event(self, state: State, event: Event) -> None:
        """Invoked on every incoming event before transition."""
        ...

    def on_effect(self, state: State, effect: Effect) -> None:
        """Invoked on every generated effect before execution."""
        ...
