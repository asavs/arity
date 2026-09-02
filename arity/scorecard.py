"""Scorecard: results in, ranking out. Not a store; a count over the store.

Two halves.

    score(results)           THE MAGIC BOX. Given N results from one trial,
                             say which was best. This is the part that will
                             be ground on for a long time. Today it is the
                             simplest thing that has the right shape.

    best_spec(task_kind)     Who has been winning this kind of task, counted
                             over store.rows(). Cast reads this to choose a
                             spec on evidence. Rebuildable any time, because
                             the store is the truth and this is arithmetic.

What makes a result "win" is deliberately not settled here. Models working
in parallel usually each do something good, and the real job is cherry
picking the best of each. Line count is not quality. A judge model might be
part of it, or might merge into the parent. For now: a box with the right
signature, and one naive body.

Everything a cleverer selector could want is already in a row: spec, task
summary, tokens, failures, outcome, parent. Cost-aware ranking, per-kind
ratings, credit flowing from a winning parent to the child that did the
work: all of it reads rows and nothing else.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from . import store
from .types import Spec


@dataclass
class Result:
    """One fork's outcome, as trial.py hands it over."""
    spec: Spec
    task_kind: str
    session_id: str
    output: str
    usage: dict[str, int]


@dataclass
class Scored:
    result: Result
    score: float
    won: bool


# ---------------------------------------------------------------------------
# The magic box
# ---------------------------------------------------------------------------

def score(results: list[Result], pick: int | None = None) -> list[Scored]:
    """Rank the results of one trial.

    Naive body: if the caller pointed at a winner, that one wins. Otherwise
    nobody wins and every result gets score 0. That is enough for the rest of
    the system to have the right shape while this box is worked on.

    Everything the box will eventually do (a judge, a merge, a diff against
    hidden tests, a human vote) fits behind this one signature.
    """
    scored = []
    for i, r in enumerate(results):
        won = pick is not None and i == pick
        scored.append(Scored(result=r, score=1.0 if won else 0.0, won=won))
    return sorted(scored, key=lambda s: -s.score)


def record_outcome(scored: list[Scored]) -> None:
    """Write each result's outcome into its own session file, so the tally can
    be rebuilt from the store alone."""
    for s in scored:
        store.record(s.result.session_id, "outcome", score=s.score, won=s.won)


# ---------------------------------------------------------------------------
# The tally cast reads
# ---------------------------------------------------------------------------

def tally() -> dict[tuple[str, Spec], tuple[int, int]]:
    """(task kind, spec) -> (wins, trials), over every session that has an outcome."""
    counts: dict[tuple[str, Spec], list[int]] = defaultdict(lambda: [0, 0])
    for row in store.rows():
        if row["won"] is None:
            continue
        key = (row["spec"].role, row["spec"])
        counts[key][1] += 1
        counts[key][0] += int(row["won"])
    return {k: (w, t) for k, (w, t) in counts.items()}


def best_spec(task_kind: str) -> Spec | None:
    """Cast's question: who has been winning this kind of task?

    Highest win rate, ties broken by more trials. None if we have no evidence,
    in which case cast falls back to a default spec.
    """
    candidates = [(spec, w / t, t) for (kind, spec), (w, t) in tally().items()
                  if kind == task_kind and t > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[1], -c[2]))
    return candidates[0][0]
