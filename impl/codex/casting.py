"""The small composition point: cadence, standing, seats, and pulse."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import exp
from typing import TYPE_CHECKING

from ledger import Ledger
from memory import Task, Tiers
from roles import Role
from store import Store

if TYPE_CHECKING:
    from runtime import Kernel, KernelRegistry


@dataclass
class Conversation:
    id: str
    kind: str = "dm"
    recent_gaps: list[float] = field(default_factory=list)


class Cadence:
    priors = {"call": 5.0, "dm": 1500.0, "project": 7200.0}

    def predict(self, convo: Conversation, horizon_s: float | None = None) -> dict[str, float]:
        gaps = convo.recent_gaps[-8:] or [self.priors.get(convo.kind, 1500.0)]
        p50 = float(statistics.median(gaps))
        horizon = horizon_s if horizon_s is not None else p50
        empirical = sum(gap <= horizon for gap in gaps) / len(gaps)
        # A small prior keeps one surprising old gap from making certainty out of one sample.
        probability = .8 * empirical + .2 * (1 - exp(-horizon / max(p50, 1)))
        return {"p50": p50, "p_return": min(1.0, max(0.0, probability))}


class Scorecard:
    def __init__(self, store: Store):
        self.store, self.standing = store, {}
        for row in store.read("state/scorecard.jsonl"):
            self.standing[(row["role"], row["model"])] = float(row["standing_after"])

    def rank(self, role: Role, available: list[str]) -> list[str]:
        wanted = list(dict.fromkeys(role.preferred_models + tuple(available)))
        return sorted((m for m in wanted if m in available),
                      key=lambda m: (-self.standing.get((role.name, m), 1.0), wanted.index(m)))

    def record(self, role: Role, model: str, event: str, detail: str) -> None:
        key = (role.name, model)
        before = self.standing.get(key, 1.0)
        after = before * .75 if event == "unsupported_change_claim" else min(1.0, before + .02)
        self.standing[key] = after
        self.store.append("state/scorecard.jsonl", {"role": role.name, "model": model,
                          "event": event, "detail": detail, "standing_before": before,
                          "standing_after": after, "at": datetime.now(timezone.utc).isoformat()})


class Caster:
    def __init__(self, ledger: Ledger, tiers: Tiers, scorecard: Scorecard,
                 registry: "KernelRegistry", spawn):
        self.ledger, self.tiers, self.scorecard = ledger, tiers, scorecard
        self.registry, self.spawn = registry, spawn
        self.cadence = Cadence()

    def cast(self, task: Task, role: Role, convo: Conversation | None = None) -> "Kernel":
        if convo and (warm := self.registry.warm_for(role.name, convo.id)):
            costs = self.ledger.cold_cost(warm.seat.provider, warm.prefix_tokens, warm.is_warm())
            if costs["penalty"] >= 0:
                return warm
        gap = self.cadence.predict(convo)["p50"] if convo else 0
        models = self.scorecard.rank(role, [seat.model for seat in self.ledger.seats])
        seats = self.ledger.candidates(models, gap)
        if not seats:
            raise RuntimeError(f"no spendable seat for {role.name}")
        brief = self.tiers.assemble(role, task)
        effort = "high" if task.stakes == "high" else "low" if task.size < 500 else "medium"
        return self.spawn(seats[0], role, brief, effort, convo)


class Pulse:
    KEEPALIVE = "hi luv u"

    def __init__(self, cadence: Cadence, ledger: Ledger):
        self.cadence, self.ledger = cadence, ledger

    def tick(self, kernel: "Kernel", convo: Conversation, ping_cost: float = .0001) -> str:
        horizon = max(0.0, (kernel.cache_expires_at - datetime.now(timezone.utc)).total_seconds())
        probability = self.cadence.predict(convo, horizon)["p_return"]
        penalty = self.ledger.cold_cost(kernel.seat.provider, kernel.prefix_tokens,
                                        kernel.is_warm())["penalty"]
        if probability * penalty > ping_cost:
            kernel.turn(self.KEEPALIVE, tools={})
            return "keepalive"
        kernel.die("quiet")
        return "let_go"
