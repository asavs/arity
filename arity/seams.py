"""Seams: the five Protocols between the owned code and the commodity code.

The moment never sees these. The loop holds one plug for each and hands it
the matching effect. Everything on the far side of a seam is replaceable:
a urllib call today, LiteLLM tomorrow; a local subprocess today, a container
tomorrow; a JSONL file today, a database if reading it back ever gets slow.

1.0.0 ships with the naive plug behind every seam.
1.0.1 is the release where the plugs get better and the seams do not move.
"""
from __future__ import annotations

from typing import Any, Protocol

from .types import CallModel, ExecuteTool, ModelCompleted, Send, State, StoreRecord, ToolCompleted


class ModelSeam(Protocol):
    """Send the payload somewhere that can answer it. wire_*.py and harness.py plug in here."""
    def call(self, effect: CallModel) -> ModelCompleted: ...


class ToolSeam(Protocol):
    """Run one tool call and hand back what it said."""
    def execute(self, effect: ExecuteTool) -> ToolCompleted: ...
    def schemas(self) -> list[dict[str, Any]]: ...


class StoreSeam(Protocol):
    """Keep one record. store.py plugs in here."""
    def append(self, effect: StoreRecord) -> None: ...


class TransportSeam(Protocol):
    """Deliver a Send to a recipient who is not a bot. A console today; a TUI or a phone later."""
    def emit(self, effect: Send) -> None: ...


class ObserverSeam(Protocol):
    """Watch. Never changes anything. Metrics, a live view, a debugger."""
    def on_event(self, state: State, event: Any) -> None: ...
    def on_effect(self, state: State, effect: Any) -> None: ...


# ---------------------------------------------------------------------------
# The naive plugs for the three seams that don't get their own file
# ---------------------------------------------------------------------------

class LocalTools:
    """ToolSeam. Runs library tools as plain Python functions in this process."""

    def __init__(self, names: list[str]):
        from . import library
        self._schemas = [library.tool_schema(n) for n in names]
        self._runners = {n: library.tool_runner(n) for n in names}

    def schemas(self) -> list[dict[str, Any]]:
        return self._schemas

    def execute(self, effect: ExecuteTool) -> ToolCompleted:
        try:
            output = self._runners[effect.name](**effect.arguments)
        except Exception as exc:  # the model should hear about it, not the loop
            output = f"error: {exc}"
        return ToolCompleted(effect.call_id, effect.name, str(output))


class Console:
    """TransportSeam. Prints."""
    def emit(self, effect: Send) -> None:
        print(f"[to {effect.to}] {effect.text}")


class Quiet:
    """ObserverSeam. Watches nothing. The default."""
    def on_event(self, state: State, event: Any) -> None: ...
    def on_effect(self, state: State, effect: Any) -> None: ...
