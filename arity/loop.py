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

One loop serves every bot. Because each bot's kernel may sit on a different
seat, the Model and Tools seams are looked up per State, from its spec.
"""
from __future__ import annotations

from collections import deque
from typing import Callable

from . import cast, ledger, store
from .harness import for_spec
from .moment import transition
from .seams import Console, LocalTools, ModelSeam, ObserverSeam, Quiet, StoreSeam, ToolSeam, TransportSeam
from .types import (
    CallModel, Event, ExecuteTool, Message, ModelFailed, Send, Spec, State, Status, StoreRecord,
    ToolCompleted,
)

MAX_TURNS = 40      # model calls per incoming message before we stop and say so


def local_tools_for(spec: Spec) -> ToolSeam:
    return LocalTools(list(spec.tools))


class Loop:
    def __init__(
        self,
        model_for: Callable[[Spec], ModelSeam] = for_spec,
        tools_for: Callable[[Spec], ToolSeam] = local_tools_for,
        archivist: ModelSeam | None = None,     # a different model is better; None means "same as the kernel"
        records: StoreSeam = store,             # the module itself is the naive plug
        transport: TransportSeam = Console(),
        observer: ObserverSeam = Quiet(),
    ):
        self.model_for = model_for
        self.tools_for = tools_for
        self.archivist = archivist
        self.records = records
        self.transport = transport
        self.observer = observer
        self.live: dict[str, State] = {}        # bot name -> its kernel, while it lives

    # -- one kernel, one incoming message, until it answers ------------------

    def run(self, state: State, first: Event) -> State:
        model = self.model_for(state.spec)
        tools = self.tools_for(state.spec)
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
                            queue.append(ModelFailed(f"stopped after {MAX_TURNS} model calls"))
                            continue
                        try:
                            queue.append(model.call(effect))
                        except Exception as exc:        # a 429, a timeout, a bad key
                            queue.append(ModelFailed(str(exc)[:200]))
                    case ExecuteTool():
                        queue.append(tools.execute(effect))
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
            # A person. Out through the transport. If the model was waiting on a
            # reply (it used the tool mid-turn), tell it the message was delivered
            # and let it finish; the person answers in a later Message, not now.
            self.transport.emit(send)
            if send.call_id is None:
                return None
            return ToolCompleted(send.call_id, "message",
                                 f"delivered to {send.to}. They will answer in a later message; "
                                 "finish your turn now.")

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
        model = self.model_for(state.spec)
        ledger.death_rites(state, model, self.archivist or model)
        state.status = Status.RETIRED

    def retire_all(self) -> None:
        for bot in list(self.live):
            self.retire(bot)
