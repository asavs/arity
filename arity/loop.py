"""The loop. Pop an event, call the moment, hand each effect to its seam, push what
comes back. Repeat until there is nothing left to pop.

This is the only place that knows which concrete plug is behind each seam.
The moment never does. That is the whole point of the split: the moment can
be read as a single pure function, and the loop can be read as a switch
over four kinds of effect.

The loop is also the post office. A Send addressed to a person goes out the
Transport seam. A Send addressed to a bot wakes that bot's kernel (cast, if
it has none yet), runs it until it answers, and hands the answer back to the
sender as a tool result. Kernels stay alive between messages, in `live`,
until `retire` performs their death rites (ledger.py).
"""
from __future__ import annotations

from collections import deque

from . import cast, ledger, store
from .harness import for_spec
from .moment import transition
from .seams import Console, LocalTools, ModelSeam, ObserverSeam, Quiet, StoreSeam, ToolSeam, TransportSeam
from .types import (
    CallModel, Event, ExecuteTool, Message, Send, State, Status, StoreRecord, ToolCompleted,
)

MAX_TURNS = 40      # model calls per incoming message before we stop and say so


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
        self.live: dict[str, State] = {}       # bot name -> its kernel, while it lives

    # -- one kernel, one incoming message, until it answers ------------------

    def run(self, state: State, first: Event) -> State:
        queue: deque[Event] = deque([first])
        turns = 0
        while queue:
            event = queue.popleft()
            self.observer.on_event(state, event)

            state, effects = transition(state, event)

            for effect in effects:
                self.observer.on_effect(state, effect)
                match effect:
                    case CallModel():
                        turns += 1
                        if turns > MAX_TURNS:
                            state.output = f"(stopped after {MAX_TURNS} model calls)"
                            state.status = Status.IDLE
                            return state
                        queue.append(self.model.call(effect))
                    case ExecuteTool():
                        queue.append(self.tools.execute(effect))
                    case StoreRecord():
                        self.records.append(effect)
                    case Send():
                        reply = self.deliver(state, effect)
                        if reply is not None:
                            queue.append(reply)
        return state

    # -- the post office -----------------------------------------------------

    def deliver(self, sender: State, send: Send) -> ToolCompleted | None:
        """Route one Send. Returns the event the sender should see next, if any."""
        if not cast.is_bot(send.to):
            # A person. Out through the transport; nothing comes back into the kernel.
            self.transport.emit(send)
            return None

        if send.call_id is None:
            # A bot finished its turn talking to another bot. That bot is waiting
            # on this kernel's `output` (see the last line of this method), so
            # there is nothing to route.
            return None

        # A bot messaged a bot mid-turn and is waiting. Wake the recipient and run it.
        recipient = self.wake(send.to)
        recipient = self.run(recipient, Message(sender=sender.bot, text=send.text))
        return ToolCompleted(send.call_id, "message", recipient.output or "")

    def wake(self, bot: str) -> State:
        """The bot's live kernel, or a new one from cast."""
        if bot not in self.live:
            self.live[bot] = cast.birth(bot)
        return self.live[bot]

    def retire(self, bot: str) -> None:
        """Death rites for one bot's kernel. Called when the conversation ends."""
        state = self.live.pop(bot)
        ledger.death_rites(state, self.model, self.archivist)
        state.status = Status.RETIRED

    def retire_all(self) -> None:
        for bot in list(self.live):
            self.retire(bot)


def default_loop(spec) -> Loop:
    """The loop the front door and trial use: wire or CLI per the spec, local tools."""
    return Loop(model=for_spec(spec), tools=LocalTools(list(spec.tools)))
