"""The moment. transition(state, event) -> (state, effects). Pure. No I/O.

This is the whole kernel. Everything else in the package either feeds this
function or remembers what it did.

It reads a State and one Event, appends to the messages, changes the status,
and returns a list of effects it would like done. It never does them. It
never opens a file, never makes a request, never sleeps. The loop does those.

Because it is pure, a moment can be forked: copy the State, change one
field, feed the same event, and you have two moments to compare. That fork
is what a trial is (see trial.py).

The only freshness is `new_id`, passed in, so a session can be replayed by
passing a deterministic one.
"""
from __future__ import annotations

import json
import uuid
from typing import Callable

from .types import (
    CallModel, Effect, EmitMessage, Event, ExecuteTool, Halt,
    ModelCompleted, State, Status, StoreRecord, Tick, ToolCompleted, UserMessage,
)


def default_new_id() -> str:
    return uuid.uuid4().hex[:8]


def transition(
    state: State,
    event: Event,
    new_id: Callable[[], str] = default_new_id,
) -> tuple[State, list[Effect]]:
    effects: list[Effect] = []

    def keep(kind: str, record: dict) -> None:
        """Ask the loop to write one line to the session's store."""
        effects.append(StoreRecord(state.session_id, state.spec.seat, kind, record))

    def call_model() -> None:
        """Ask the loop to send the payload. This is the four things and nothing else."""
        effects.append(CallModel(
            system=state.system_text(),
            tools=state.tools,
            messages=list(state.messages),
        ))

    match event:

        # A person typed. Append it, keep it, ask the model.
        case UserMessage(text):
            state.messages.append({"role": "user", "content": text})
            state.status = Status.WAITING_MODEL
            keep("user", {"content": text})
            call_model()

        # The model answered and wants tools. Append the turn, keep it, ask for each tool.
        case ModelCompleted(text, tool_calls, usage) if tool_calls:
            state.messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
            state.status = Status.WAITING_TOOLS
            keep("model", {"content": text, "tool_calls": len(tool_calls), "usage": usage})
            for call in tool_calls:
                args = call["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                effects.append(ExecuteTool(call.get("id") or new_id(), call["name"], args))

        # The model answered and is done talking. Append, keep, show the person, halt.
        case ModelCompleted(text, _, usage):
            state.messages.append({"role": "assistant", "content": text})
            state.output = text
            state.status = Status.HALTED
            keep("model", {"content": text, "usage": usage})
            effects.append(EmitMessage(text))
            effects.append(Halt("end of turn"))

        # A tool returned. Append its result; if it was the last one, ask the model again.
        case ToolCompleted(call_id, name, output):
            state.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": output})
            keep("tool", {"name": name, "output": output[:2000]})
            if _all_tools_answered(state):
                state.status = Status.WAITING_MODEL
                call_model()

        # The pulse fired. Tell the model the time and let it decide if that means anything.
        case Tick(at):
            state.messages.append({"role": "user", "content": f"[tick] {at}"})
            state.status = Status.WAITING_MODEL
            call_model()

    return state, effects


def _all_tools_answered(state: State) -> bool:
    """True when every tool_call in the last assistant turn has a tool message after it."""
    asked = set()
    answered = set()
    for m in reversed(state.messages):
        if m["role"] == "tool":
            answered.add(m["tool_call_id"])
        elif m["role"] == "assistant":
            asked = {c["id"] for c in m.get("tool_calls", [])}
            break
    return asked <= answered
