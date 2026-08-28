"""Per-prompt routing and kernel composition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from archivist import Archivist
from cadence import Cadence, Conversation
from harness import ChatHarness
from kernel import Kernel, KernelRegistry
from ledger import Ledger
from roles import Role
from scorecard import Scorecard
from tiers import Task, Tiers


class Caster:
    def __init__(self, ledger: Ledger, tiers: Tiers, scorecard: Scorecard,
                 registry: KernelRegistry, harness: ChatHarness, archivist: Archivist,
                 cadence: Cadence | None = None):
        self.ledger, self.tiers, self.scorecard = ledger, tiers, scorecard
        self.registry, self.harness, self.archivist = registry, harness, archivist
        self.cadence = cadence or Cadence()

    def cast(self, task: Task, role: Role, convo: Conversation | None = None) -> Kernel:
        if convo:
            warm = self.registry.warm_for(role.name, convo.id)
            if warm and warm.role.name == role.name:
                costs = self.ledger.cold_cost(warm.seat.provider, warm.prefix_tokens, warm.is_warm())
                if costs["penalty"] >= 0:
                    return warm

        gap = self.cadence.predict(convo)["p50"] if convo else 0.0
        available_models = [seat.model for seat in self.ledger.seats]
        ranked_models = self.scorecard.rank(role, available_models)
        candidate_seats = self.ledger.candidates(ranked_models, gap)

        if not candidate_seats:
            raise RuntimeError(f"no spendable seat for {role.name}")

        chosen_seat = candidate_seats[0]
        effort = "high" if task.stakes == "high" else "low" if task.size < 500 else "medium"
        brief = self.tiers.assemble(role, task)

        return Kernel(chosen_seat, role, brief, effort, convo,
                      self.harness, self.ledger, self.tiers, self.archivist, self.registry)
