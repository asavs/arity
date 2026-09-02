"""The loop. Pop an event, call the moment, hand each effect to its seam, push what
comes back. Repeat until the moment halts and there is nothing left to pop.

This is the only place that knows which concrete plug is behind each seam.
The moment never does. That is the whole point of the split: the moment can
be read as a single pure function, and the loop can be read as a switch
over five kinds of effect.

On Halt, the loop performs the death rites (ledger.py): the kernel's own
report, then the archivist's account, both appended to the bot's ledger.
"""
from __future__ import annotations

from collections import deque

from . import ledger, store
from .moment import transition
from .seams import Console, ModelSeam, ObserverSeam, Quiet, StoreSeam, ToolSeam, TransportSeam
from .types import (
    CallModel, EmitMessage, Event, ExecuteTool, Halt, State, Status, StoreRecord,
)


class Loop:
    def __init__(
        self,
        model: ModelSeam,
        tools: ToolSeam,
        archivist: ModelSeam | None = None,
        records: StoreSeam = store,           # the module itself is the naive plug
        transport: TransportSeam = Console(),
        observer: ObserverSeam = Quiet(),
    ):
        self.model = model
        self.tools = tools
        self.archivist = archivist or model    # a different model is better; same one is allowed
        self.records = records
        self.transport = transport
        self.observer = observer

    def run(self, state: State, first: Event) -> State:
        """Drive one kernel from an event until it halts."""
        queue: deque[Event] = deque([first])
        while queue and state.status != Status.HALTED:
            event = queue.popleft()
            self.observer.on_event(state, event)

            state, effects = transition(state, event)

            for effect in effects:
                self.observer.on_effect(state, effect)
                match effect:
                    case CallModel():
                        queue.append(self.model.call(effect))
                    case ExecuteTool():
                        queue.append(self.tools.execute(effect))
                    case StoreRecord():
                        self.records.append(effect)
                    case EmitMessage():
                        self.transport.emit(effect)
                    case Halt():
                        ledger.death_rites(state, self.model, self.archivist)
        return state
