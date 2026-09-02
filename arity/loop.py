"""The loop. Pop an event, journal it, call the moment, hand each effect to its
seam, push what comes back. Repeat until there is nothing left to pop.

This is the only place that knows which concrete plug is behind each seam.
The moment never does. That is the whole point of the split: the moment can
be read as a single pure function, and the loop can be read as a switch
over three kinds of effect.

The loop is also the post office. A Send addressed to a person goes out the
Transport seam. A Send addressed to a bot wakes that bot's kernel (cast, if
it has none yet), runs it until it answers, and hands the answer back to the
sender as a tool result. Kernels stay alive between messages, in `live`,
until `retire` performs their death rites (ledger.py).

And the loop is where a kernel comes back from a crash. Every event was
journaled before the moment saw it, so `resume` folds the journal into a
State and redoes the last event, whose effects may not have happened.

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
    CallModel, Event, ExecuteTool, Message, ModelFailed, Send, Spec, State, Status, ToolCompleted,
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
        journal: StoreSeam = store,             # the module itself is the naive plug
        transport: TransportSeam = Console(),
        observer: ObserverSeam = Quiet(),
    ):
        self.model_for = model_for
        self.tools_for = tools_for
        self.archivist = archivist
        self.journal = journal
        self.transport = transport
        self.observer = observer
        self.live: dict[str, State] = {}        # bot name -> its kernel, while it lives

    # -- one kernel, one incoming message, until it answers ------------------

    def run(self, state: State, first: Event, first_is_new: bool = True) -> State:
        model = self.model_for(state.spec)
        tools = self.tools_for(state.spec)
        queue: deque[Event] = deque([first])
        turns = 0
        while queue:
            event = queue.popleft()
            if first_is_new or event is not first:
                self.journal.event(state.session_id, event)     # on disk before the moment sees it
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
        recipient = self.wake(send.to, parent={"session": sender.session_id, "call": send.call_id})
        recipient = self.run(recipient, Message(sender=sender.bot, text=send.text))
        return ToolCompleted(send.call_id, "message", recipient.output or "")

    # -- births and deaths ---------------------------------------------------

    def wake(self, bot: str, parent: dict | None = None, spec: Spec | None = None) -> State:
        """The bot's live kernel, or a new one from cast. A new one gets a birth line."""
        if bot not in self.live:
            state = cast.resolve(spec, bot) if spec else cast.birth(bot)
            self.journal.birth(state, parent)
            self.live[bot] = state
        return self.live[bot]

    def resume(self, session_id: str) -> State:
        """Fold the journal back into a State and redo its last event.

        The system text and tools are re-resolved from the spec, so a resumed
        kernel wakes with today's library and ledger, not the ones it was born
        with. The conversation is exact. The last event is redone because its
        effects (a model call, a tool run) may be what the crash interrupted.
        """
        b = store.birth_of(session_id)
        state = cast.resolve(Spec(**{k: tuple(v) if isinstance(v, list) else v
                                     for k, v in b["spec"].items()}), b["bot"])
        state.session_id = session_id
        events = store.events(session_id)
        for ev in events[:-1]:
            state, _ = transition(state, ev)            # effects already happened
        self.live[state.bot] = state
        if events:
            state = self.run(state, events[-1], first_is_new=False)
        return state

    def retire(self, bot: str) -> None:
        """Death rites for one bot's kernel. Called when the conversation ends."""
        state = self.live.pop(bot)
        model = self.model_for(state.spec)
        ledger.death_rites(state, model, self.archivist or model)
        self.journal.record(state.session_id, "retired")
        state.status = Status.RETIRED

    def retire_all(self) -> None:
        for bot in list(self.live):
            self.retire(bot)
