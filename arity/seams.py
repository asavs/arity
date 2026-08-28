"""arity seams — Explicit interfaces for pluggable infrastructure grafts.

Any external engine (e.g. LiteLLM router, OpenRouter gateway, Docker sandbox,
SQLite/Vector memory store, Discord bot transport, blind eval scorecard)
implements one of these protocols.
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
class RecordStore(Protocol):
    """Graft point for persistence, transcripts, vector DBs, and audit ledgers."""

    def append(self, effect: StoreRecord) -> None:
        """Append a structured record."""
        ...

    def query(self, kind: str, **filters: Any) -> list[dict[str, Any]]:
        """Query records matching filter criteria."""
        ...


@runtime_checkable
class Transport(Protocol):
    """Graft point for messaging front-doors (CLI, Webhook, Discord, Slack, SMS)."""

    def emit(self, effect: EmitMessage) -> None:
        """Deliver an outgoing message to the user/channel."""
        ...


@runtime_checkable
class Observer(Protocol):
    """Graft point for archivists, blind judges, telemetry, and eval monitors."""

    def on_event(self, state: State, event: Event) -> None:
        """Invoked on every incoming event before transition."""
        ...

    def on_effect(self, state: State, effect: Effect) -> None:
        """Invoked on every generated effect before execution."""
        ...
