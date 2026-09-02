"""Scorecard: results in, ranking out. Not a store; a count over the store.

Two halves.

    score(results)           THE MAGIC BOX. Given N results from one trial,
                             say which was best. This is the part that will
                             be ground on for a long time. Today it is the
                             simplest thing that has the right shape.

    standings(role, factor)  Who has been winning this kind of task, counted
                             over store.rows(), pairwise: role × one factor.
                             Cast reads `ranked(role)` to choose a model on
                             evidence. Rebuildable any time, because the
                             store is the truth and this is arithmetic.

Pairwise, not the full cross-product. A key of (role, model) accumulates
evidence across every trial that varied skills, tools, harness or effort. A
key of the whole Spec would start from zero every time any field changed,
and the scorecard would look like it works while never having enough trials
to say anything. To ask about a second factor, ask a second pairwise
question: standings(role, "skills"), standings(role, "effort").

What makes a result "win" is deliberately not settled here. Models working
in parallel usually each do something good, and the real job is cherry
picking the best of each. Line count is not quality. A judge model might be
part of it, or might merge into the parent. For now: a box with the right
signature, and one naive body.

Everything a cleverer selector could want is already in a row: spec, task
summary, tokens, failures, outcome, parent, epoch. Cost-aware ranking,
per-kind ratings, credit flowing from a winning parent to the child that did
the work: all of it reads rows and nothing else.
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


@dataclass(frozen=True)
class Standing:
    value: object           # a model id, a skills tuple, an effort...
    wins: int
    trials: int

    @property
    def rate(self) -> float:
        return self.wins / self.trials if self.trials else 0.0


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
# The standings cast reads
# ---------------------------------------------------------------------------

def standings(role: str, factor: str = "model") -> list[Standing]:
    """(wins, trials) per value of one Spec field, for one role, best first.

    Only sessions with an outcome count. Only the current ruleset epoch
    counts: evidence from before a role or tool changed is not comparable.
    Highest win rate first, more trials breaking ties.
    """
    counts: dict[object, list[int]] = defaultdict(lambda: [0, 0])
    for row in store.rows():
        if row["won"] is None or row["spec"].role != role or not row.get("current", True):
            continue
        key = getattr(row["spec"], factor)
        counts[key][1] += 1
        counts[key][0] += int(row["won"])
    table = [Standing(value, w, t) for value, (w, t) in counts.items()]
    return sorted(table, key=lambda s: (-s.rate, -s.trials))


def ranked(role: str) -> list[str]:
    """Cast's question: which models have been winning this kind of task, best first.
    Empty when there is no evidence, in which case cast uses its default."""
    return [s.value for s in standings(role, "model")]


def least_tried(role: str, among: list[str]) -> str | None:
    """The exploration question: of these models, which do we know least about?"""
    trials = {s.value: s.trials for s in standings(role, "model")}
    return min(among, key=lambda m: trials.get(m, 0)) if among else None
