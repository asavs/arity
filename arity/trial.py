"""Trial: a fork.

    1. product over the fields of a spec you want to vary  ->  N specs
    2. N States from one base State, one per spec
    3. the same event into each
    4. N results, keyed by spec, handed to the scorecard

That is the whole idea the package exists for. Because the moment is pure
and the State is all values, forking is copy.deepcopy and a field swap.
Terrarium, race and evidence in the old package are three thousand-line
elaborations of these forty lines.

`arity 3 "..."` is the front door's way in: the same bot on the three best
(seat, model) pairs that have quota, answers printed side by side, the person
picks the winner, the scorecard learns. See main.py.

On what it costs. The cached prefix at the provider is tools, then system,
then messages, and any change invalidates everything after it. So:

    varying the runner behind a tool (invisible to the model)   cache shared
    varying a text block appended late in the system            mostly shared
    varying a tool schema, or the role at the top               both forks cold

Worth recording which one a trial varied, because the cost column means
something different in each case.
"""
from __future__ import annotations

import copy
import itertools
import uuid

from . import cast, scorecard, seats, store
from .loop import Loop
from .types import Event, Spec, State


def product(base: Spec, **axes: list) -> list[Spec]:
    """Every combination of the given fields. `product(spec, model=[a, b], skills=[x, y])` -> 4 specs."""
    names = list(axes)
    return [
        Spec(**{**base.__dict__, **dict(zip(names, values))})
        for values in itertools.product(*axes.values())
    ]


def candidates(base: Spec, n: int) -> list[Spec]:
    """The default trial: the same bot on the N best distinct models that have quota.

    "Best" is the seat table's order: the seat closest to its reset first,
    because that quota is the quota most likely to go to waste.
    """
    pairs = [(seat, model)
             for seat in seats.all_seats() if seat.remaining > 0 and seat.provider != "mock"
             for model in seat.models]
    pairs.sort(key=lambda p: (p[0].resets_at or "9999", -p[0].remaining))
    chosen: list[Spec] = []
    for seat, model in pairs:
        if any(c.model == model for c in chosen):
            continue
        chosen.append(Spec(**{**base.__dict__, "seat": seat.id, "model": model}))
        if len(chosen) == n:
            break
    return chosen


def fork(base: State, spec: Spec) -> State:
    """One copy of the base State, re-resolved for a different spec.

    The messages so far are kept (that is the point: same conversation, different
    kernel). The system text and tools are re-resolved because the spec changed.
    """
    fresh = cast.resolve(spec, base.bot)
    fresh.messages = copy.deepcopy(base.messages)
    fresh.session_id = f"{base.session_id}-{uuid.uuid4().hex[:4]}"
    # Its own journal: a birth line pointing at the base, then the base's
    # events so far, so the fork replays on its own.
    store.birth(fresh, parent={"session": base.session_id})
    store.fork(base.session_id, fresh.session_id)
    return fresh


def run(base: State, specs: list[Spec], event: Event, loop: Loop | None = None) -> list[State]:
    """Fork once per spec, feed every fork the same event, return the forks."""
    loop = loop or Loop()
    return [loop.run(fork(base, spec), event) for spec in specs]


def judge(forks: list[State], pick: int | None) -> list[scorecard.Scored]:
    """Hand the forks to the scorecard. `pick` is the index the person chose, or None.

    Task kind is the role, same as everywhere else. The outcome is written back
    into each fork's own session so the tally can be rebuilt from the store.
    """
    results = [scorecard.Result(spec=f.spec, task_kind=f.spec.role, session_id=f.session_id,
                                output=f.output or "", usage={})
               for f in forks]
    ranked = scorecard.score(results, pick=pick)
    scorecard.record_outcome(ranked)
    return ranked
