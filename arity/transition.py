"""Pure state transition function for arity.

transition(state, event) -> (new_state, list[effects])

No I/O, no network calls, no subprocesses here. Pure logic and state transformations.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .types import (
    CallModel,
    Effect,
    EmitMessage,
    Event,
    ExecuteTool,
    Halt,
    HandoffCompleted,
    HandoffRequested,
    Interrupt,
    ModelCompleted,
    ModelFailed,
    PulseTick,
    SchedulePulse,
    SpawnHandoff,
    State,
    Status,
    StoreRecord,
    ToolCompleted,
    UserMessage,
)


def transition(state: State, event: Event) -> tuple[State, list[Effect]]:
    """Compute the next state and required effects for a given event."""
    effects: list[Effect] = []

    # 1. User Message arrives
    if isinstance(event, UserMessage):
        user_msg = {
            "role": "user",
            "content": event.text,
            "name": event.sender,
        }
        state.messages.append(user_msg)
        state.status = Status.WAITING_MODEL
        state.error_count = 0

        effects.append(
            StoreRecord(
                kind="message",
                record={
                    "session_id": state.session_id,
                    "role": "user",
                    "content": event.text,
                    "sender": event.sender,
                    "channel": event.channel,
                },
            )
        )
        effects.append(
            CallModel(
                messages=state.get_full_messages(),
                tools=state.active_tools,
            )
        )
        return state, effects

    # 2. Model completion arrives
    if isinstance(event, ModelCompleted):
        state.error_count = 0

        # Case A: Model wants to call tools
        if event.tool_calls:
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": event.content,
                "tool_calls": event.tool_calls,
            }
            state.messages.append(assistant_msg)
            state.status = Status.WAITING_TOOLS

            effects.append(
                StoreRecord(
                    kind="model_turn",
                    record={
                        "session_id": state.session_id,
                        "content": event.content,
                        "tool_calls_count": len(event.tool_calls),
                        "usage": event.usage,
                        "seat_id": event.seat_id,
                    },
                )
            )

            for tc in event.tool_calls:
                call_id = tc.get("id", uuid.uuid4().hex[:8])
                fn = tc.get("function", {})
                name = fn.get("name", "unknown")
                raw_args = fn.get("arguments", "{}")
                
                if isinstance(raw_args, dict):
                    args = raw_args
                else:
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except Exception:
                        args = {"raw": raw_args}

                state.pending_tool_calls[call_id] = {
                    "id": call_id,
                    "name": name,
                    "arguments": args,
                }

                effects.append(
                    ExecuteTool(
                        call_id=call_id,
                        name=name,
                        arguments=args,
                    )
                )

            return state, effects

        # Case B: Model returned final text answer
        assistant_msg = {
            "role": "assistant",
            "content": event.content,
        }
        state.messages.append(assistant_msg)
        state.status = Status.IDLE
        state.output = event.content

        effects.append(
            StoreRecord(
                kind="model_turn",
                record={
                    "session_id": state.session_id,
                    "content": event.content,
                    "usage": event.usage,
                    "seat_id": event.seat_id,
                },
            )
        )
        effects.append(
            EmitMessage(
                channel="main",
                recipient="user",
                text=event.content or "",
                metadata={"usage": event.usage, "seat_id": event.seat_id},
            )
        )
        return state, effects

    # 3. Tool execution completed
    if isinstance(event, ToolCompleted):
        # Resolve pending tool call
        state.pending_tool_calls.pop(event.call_id, None)

        tool_msg = {
            "role": "tool",
            "tool_call_id": event.call_id,
            "name": event.tool_name,
            "content": event.output,
        }
        state.messages.append(tool_msg)

        effects.append(
            StoreRecord(
                kind="tool_result",
                record={
                    "session_id": state.session_id,
                    "call_id": event.call_id,
                    "tool_name": event.tool_name,
                    "output_preview": event.output[:200] if event.output else "",
                    "is_error": event.is_error,
                },
            )
        )

        # If all pending tool calls in this batch finished, call model again
        if not state.pending_tool_calls:
            state.status = Status.WAITING_MODEL
            effects.append(
                CallModel(
                    messages=state.get_full_messages(),
                    tools=state.active_tools,
                )
            )

        return state, effects

    # 4. Model failed
    if isinstance(event, ModelFailed):
        state.error_count += 1
        effects.append(
            StoreRecord(
                kind="friction",
                record={
                    "session_id": state.session_id,
                    "error": event.error,
                    "seat_id": event.seat_id,
                    "retry_count": state.error_count,
                    "fatal": not event.retryable or state.error_count > state.max_errors,
                },
            )
        )

        if event.retryable and state.error_count <= state.max_errors:
            # Retry call
            effects.append(
                CallModel(
                    messages=state.get_full_messages(),
                    tools=state.active_tools,
                )
            )
        else:
            state.status = Status.HALTED
            state.output = f"Model failure: {event.error}"
            effects.append(
                EmitMessage(
                    channel="main",
                    recipient="user",
                    text=f"Failed after {state.error_count} attempts: {event.error}",
                )
            )
            effects.append(Halt(reason=event.error, output=state.output))

        return state, effects

    # 5. Handoff requested
    if isinstance(event, HandoffRequested):
        state.status = Status.WAITING_HANDOFF
        child_id = f"sub_{uuid.uuid4().hex[:8]}"
        effects.append(
            SpawnHandoff(
                session_id=child_id,
                target_role=event.target_role,
                brief=event.brief,
                budget=event.budget,
            )
        )
        return state, effects

    # 6. Handoff completed
    if isinstance(event, HandoffCompleted):
        handoff_msg = {
            "role": "user",
            "content": f"[Handoff result from {event.child_session_id} ({event.status})]:\n{event.output}",
        }
        state.messages.append(handoff_msg)
        state.status = Status.WAITING_MODEL
        effects.append(
            CallModel(
                messages=state.get_full_messages(),
                tools=state.active_tools,
            )
        )
        return state, effects

    # 7. Pulse tick
    if isinstance(event, PulseTick):
        # Keepalive or background check
        effects.append(
            StoreRecord(
                kind="pulse",
                record={"session_id": state.session_id, "timestamp": event.timestamp},
            )
        )
        return state, effects

    # 8. External interrupt
    if isinstance(event, Interrupt):
        state.status = Status.HALTED
        state.output = f"Interrupted: {event.reason}"
        effects.append(
            EmitMessage(
                channel="main",
                recipient="user",
                text=f"Session interrupted: {event.reason}",
            )
        )
        effects.append(Halt(reason=event.reason, output=state.output))
        return state, effects

    return state, effects
