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
    CallModel, Effect, Event, ExecuteTool, Message, ModelCompleted, ModelFailed,
    Send, State, Status, StoreRecord, Tick, ToolCompleted,
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

        # Someone spoke to this kernel. That is a task. Keep a task record with the
        # kind (the bot's role, for now) and a one-line summary (the first line, for
        # now) so a finer taxonomy of tasks can be grown from the store later.
        # Then append the message, remember who sent it, and ask the model.
        case Message(sender, text):
            keep("task", {"kind": state.spec.role, "summary": text.splitlines()[0][:120],
                          "sender": sender})
            state.messages.append({"role": "user", "content": f"[{sender}] {text}"})
            state.talking_to = sender
            state.status = Status.WAITING_MODEL
            keep("user", {"sender": sender, "content": text})
            call_model()

        # The model answered and wants tools. Append the turn, keep it, ask for each tool.
        # A call to `message` is not a tool the loop runs; it is a Send to another bot.
        case ModelCompleted(text, tool_calls, usage) if tool_calls:
            state.messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
            state.status = Status.WAITING_TOOLS
            keep("model", {"content": text, "tool_calls": len(tool_calls), "usage": usage})
            for call in tool_calls:
                args = call["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                call_id = call.get("id") or new_id()
                if call["name"] == "message":
                    effects.append(Send(to=args["to"], text=args["content"], call_id=call_id))
                else:
                    effects.append(ExecuteTool(call_id, call["name"], args))

        # The model answered and is done. Append, keep, send the answer to whoever asked.
        case ModelCompleted(text, _, usage):
            state.messages.append({"role": "assistant", "content": text})
            state.output = text
            state.status = Status.IDLE
            keep("model", {"content": text, "usage": usage})
            effects.append(Send(to=state.talking_to, text=text))

        # The wire could not get an answer. Say so to whoever asked, keep it, go idle.
        # Nothing is appended to messages: the conversation is exactly as it was.
        case ModelFailed(reason):
            state.output = f"(no answer: {reason})"
            state.status = Status.IDLE
            keep("failure", {"reason": reason})
            effects.append(Send(to=state.talking_to, text=state.output))

        # A tool returned (or another bot replied). Append it; if it was the last one
        # outstanding, ask the model again.
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
