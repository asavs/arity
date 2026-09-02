"""The moment. transition(state, event) -> (state, effects). Pure. No I/O.

This is the whole kernel. Everything else in the package either feeds this
function or remembers what it did.

It reads a State and one Event, appends to the messages, changes the status,
and returns a list of effects it would like done. It never does them. It
never opens a file, never makes a request, never sleeps. The loop does those.

It also never asks for anything to be recorded. The loop journals every
event before calling this, so the State is always a fold over the journal:

    State = fold(transition, birth, events)

Because it is pure, a moment can be forked: copy the State, change one
field, feed the same event, and you have two moments to compare. That fork
is what a trial is (see trial.py). And a crashed kernel is resumed by
folding its journal again (see loop.py).

The only freshness is `new_id`, passed in, so a replay gives the same ids.
"""
from __future__ import annotations

import json
import uuid
from typing import Callable

from .types import (
    CallModel, Effect, Event, ExecuteTool, Message, ModelCompleted, ModelFailed,
    Send, State, Status, Tick, ToolCompleted,
)


def default_new_id() -> str:
    return uuid.uuid4().hex[:8]


def transition(
    state: State,
    event: Event,
    new_id: Callable[[], str] = default_new_id,
) -> tuple[State, list[Effect]]:
    effects: list[Effect] = []

    def call_model() -> None:
        """Ask the loop to send the payload. This is the four things and nothing else."""
        effects.append(CallModel(
            system=state.system_text(),
            tools=state.tools,
            messages=list(state.messages),
        ))

    match event:

        # Someone spoke to this kernel. Append it, remember who, ask the model.
        case Message(sender, text):
            state.messages.append({"role": "user", "content": f"[{sender}] {text}"})
            state.talking_to = sender
            state.status = Status.WAITING_MODEL
            call_model()

        # The model answered and wants tools. Append the turn, ask for each tool.
        # A call to `message` is not a tool the loop runs; it is a Send to another bot.
        case ModelCompleted(text, tool_calls, _) if tool_calls:
            state.messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
            state.status = Status.WAITING_TOOLS
            for call in tool_calls:
                args = call["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                call_id = call.get("id") or new_id()
                if call["name"] == "message":
                    effects.append(Send(to=args["to"], text=args["content"], call_id=call_id))
                else:
                    effects.append(ExecuteTool(call_id, call["name"], args))

        # The model answered and is done. Append, send the answer to whoever asked.
        case ModelCompleted(text, _, _):
            state.messages.append({"role": "assistant", "content": text})
            state.output = text
            state.status = Status.IDLE
            effects.append(Send(to=state.talking_to, text=text))

        # The wire could not get an answer. Say so to whoever asked, go idle.
        # Nothing is appended to messages: the conversation is exactly as it was.
        case ModelFailed(reason):
            state.output = f"(no answer: {reason})"
            state.status = Status.IDLE
            effects.append(Send(to=state.talking_to, text=state.output))

        # A tool returned (or another bot replied). Append it; if it was the last one
        # outstanding, ask the model again.
        case ToolCompleted(call_id, name, output):
            state.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": output})
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
