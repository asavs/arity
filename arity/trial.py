"""Trial: a fork.

    1. product over the fields of a spec you want to vary  ->  N specs
    2. N States from one base State, one per spec
    3. the same event into each
    4. N results, keyed by spec, handed to the scorecard

That is the whole idea the package exists for. Because the moment is pure
and the State is all values, forking is copy.deepcopy and a field swap.
Terrarium, race and evidence in the old package are three thousand-line
elaborations of these forty lines.

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

from . import cast, scorecard, seams
from .harness import for_spec
from .loop import Loop
from .types import Event, Spec, State


def product(base: Spec, **axes: list) -> list[Spec]:
    """Every combination of the given fields. `product(spec, model=[a, b], skills=[x, y])` -> 4 specs."""
    names = list(axes)
    return [
        Spec(**{**base.__dict__, **dict(zip(names, values))})
        for values in itertools.product(*axes.values())
    ]


def fork(base: State, spec: Spec) -> State:
    """One copy of the base State, re-resolved for a different spec.

    The messages so far are kept (that is the point: same conversation, different
    kernel). The system text and tools are re-resolved because the spec changed.
    """
    fresh = cast.resolve(spec, base.bot)
    fresh.messages = copy.deepcopy(base.messages)
    fresh.session_id = f"{base.session_id}-{uuid.uuid4().hex[:4]}"
    return fresh


def run(base: State, specs: list[Spec], event: Event, task_kind: str, pick: int | None = None):
    """Fork, run each, score. Returns the scorecard's ranking."""
    results = []
    for spec in specs:
        state = fork(base, spec)
        loop = Loop(model=for_spec(spec), tools=seams.LocalTools(list(spec.tools)))
        final = loop.run(state, event)
        results.append(scorecard.Result(
            spec=spec,
            task_kind=task_kind,
            session_id=final.session_id,
            output=final.output or "",
            usage={},
        ))
    ranked = scorecard.score(results, pick=pick)
    scorecard.record_outcome(ranked)
    return ranked
