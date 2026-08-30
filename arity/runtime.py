"""arity runtime — Event loop and pluggable effect dispatcher.

The runtime coordinates state transitions and dispatches side-effects
to the injected seam handlers (Model, Tools, Store, Transport, Observers).
"""
from __future__ import annotations

import queue
import uuid
from typing import Optional

from .handlers import (
    default_record_store,
    ConsoleTransport,
    JsonlRecordStore,
    LocalToolRunner,
    MetricsObserver,
    create_default_model_provider,
)
from .seams import ModelProvider, Observer, RecordStore, ToolRunner, Transport
from .transition import transition
from .types import (
    CallModel,
    Effect,
    EmitMessage,
    Event,
    ExecuteTool,
    Halt,
    HandoffCompleted,
    ModelCompleted,
    ModelFailed,
    SpawnHandoff,
    State,
    Status,
    StoreRecord,
    ToolCompleted,
    UserMessage,
)


class Runtime:
    """The execution chassis. Plugs seams together with the pure transition function."""

    def __init__(
        self,
        model_provider: Optional[ModelProvider] = None,
        tool_runner: Optional[ToolRunner] = None,
        store: Optional[RecordStore] = None,
        transport: Optional[Transport] = None,
        observers: Optional[list[Observer]] = None,
    ):
        self.model = model_provider or create_default_model_provider()
        self.tools = tool_runner or LocalToolRunner()
        self.store = store or default_record_store()
        self.transport = transport or ConsoleTransport()
        self.observers: list[Observer] = observers or [MetricsObserver()]

    def step(self, state: State, event: Event) -> tuple[State, list[Event]]:
        """Run one state transition and execute all generated effects.

        Returns (updated_state, list_of_newly_generated_events).
        """
        # 1. Notify observers of the incoming event
        for obs in self.observers:
            try:
                obs.on_event(state, event)
            except Exception:
                pass

        # 2. Pure state transition
        new_state, effects = transition(state, event)

        # 3. Notify observers of generated effects
        for effect in effects:
            for obs in self.observers:
                try:
                    obs.on_effect(new_state, effect)
                except Exception:
                    pass

        # 4. Dispatch effects to seam handlers and collect resulting events
        new_events: list[Event] = []

        for effect in effects:
            # Effect: Store a record
            if isinstance(effect, StoreRecord):
                try:
                    self.store.append(effect)
                except Exception:
                    pass

            # Effect: Emit a message to transport
            elif isinstance(effect, EmitMessage):
                try:
                    self.transport.emit(effect)
                except Exception:
                    pass

            # Effect: Execute a tool
            elif isinstance(effect, ExecuteTool):
                result_event = self.tools.execute(effect)
                new_events.append(result_event)

            # Effect: Call the model
            elif isinstance(effect, CallModel):
                # Ensure tool schemas from runner are included if active_tools isn't overridden
                call_tools = effect.tools or self.tools.get_schemas()
                actual_effect = CallModel(
                    messages=effect.messages,
                    tools=call_tools,
                    seat=effect.seat,
                    temperature=effect.temperature,
                    max_tokens=effect.max_tokens,
                )
                model_res = self.model.call(actual_effect)
                new_events.append(model_res)

            # Effect: Spawn a subagent handoff
            elif isinstance(effect, SpawnHandoff):
                child_state = State(
                    session_id=effect.session_id,
                    role=effect.target_role,
                    system_prompt=f"You are a specialized subagent for: {effect.target_role}",
                    active_tools=self.tools.get_schemas(),
                )
                child_runtime = Runtime(
                    model_provider=self.model,
                    tool_runner=self.tools,
                    store=self.store,
                    transport=self.transport,
                    observers=self.observers,
                )
                final_child_state = child_runtime.run(
                    child_state,
                    initial_event=UserMessage(text=effect.brief, sender="parent"),
                )
                new_events.append(
                    HandoffCompleted(
                        child_session_id=effect.session_id,
                        output=final_child_state.output or "(no output)",
                        status=final_child_state.status.value,
                    )
                )

            # Effect: Halt
            elif isinstance(effect, Halt):
                break

        return new_state, new_events

    def run(self, state: State, initial_event: Optional[Event] = None) -> State:
        """Run the event-effect loop until state enters IDLE or HALTED with no pending events."""
        event_queue: queue.Queue[Event] = queue.Queue()
        if initial_event:
            event_queue.put(initial_event)

        while not event_queue.empty() and state.is_active():
            ev = event_queue.get()
            state, next_events = self.step(state, ev)
            for next_ev in next_events:
                event_queue.put(next_ev)

        return state

    def chat(
        self,
        prompt: str,
        state: Optional[State] = None,
        system_prompt: str = "You are a helpful assistant.",
    ) -> tuple[Optional[str], State]:
        """Convenience method to run a conversational turn."""
        if state is None:
            state = State(
                session_id=f"sess_{uuid.uuid4().hex[:8]}",
                system_prompt=system_prompt,
                active_tools=self.tools.get_schemas(),
            )
        else:
            if not state.active_tools:
                state.active_tools = self.tools.get_schemas()

        state = self.run(state, initial_event=UserMessage(text=prompt))
        return state.output, state
