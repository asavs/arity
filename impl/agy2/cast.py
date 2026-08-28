"""cast.py - Per-prompt casting: warm cache check, scorecard ranking, seat filtering."""

from __future__ import annotations
import time
from typing import Any
from roles import Role
from ledger import Seat, SeatLedger
from scorecard import Scorecard
from store import Store
from harness import Harness
from kernel import Kernel
import tiers
import cadence


class KernelRegistry:
    """Tracks currently warm kernels per (role, convo_id)."""

    def __init__(self) -> None:
        self.active: dict[tuple[str, str], Kernel] = {}

    def get_warm(self, role_name: str, convo_id: str) -> Kernel | None:
        k = self.active.get((role_name, convo_id))
        if k and k.state == "alive":
            if time.time() < k.cache_expires_at:
                return k
        return None

    def register(self, kernel: Kernel) -> None:
        self.active[(kernel.role.name, kernel.convo_id)] = kernel

    def remove(self, kernel: Kernel) -> None:
        key = (kernel.role.name, kernel.convo_id)
        if key in self.active and self.active[key].id == kernel.id:
            del self.active[key]


class Caster:
    """Decides which model, seat, and effort to cast for every prompt."""

    def __init__(
        self,
        ledger: SeatLedger,
        scorecard: Scorecard,
        store: Store,
        registry: KernelRegistry | None = None,
    ) -> None:
        self.ledger = ledger
        self.scorecard = scorecard
        self.store = store
        self.registry = registry or KernelRegistry()
        self.harness = Harness(store)

    def cast(
        self,
        role: Role,
        task_context: str,
        convo_id: str = "convo_default",
        recent_gaps: list[float] | None = None,
        effort: str = "medium",
    ) -> Kernel:
        # 1. Warm cache check
        warm_k = self.registry.get_warm(role.name, convo_id)
        if warm_k:
            costs = cadence.cold_cost(warm_k)
            switch_gain = 0.0
            if costs["penalty"] > switch_gain:
                return warm_k

        # 2. Predicted gap & ranked candidates
        predicted_gap = cadence.predict(type("Convo", (), {"recent_gaps": recent_gaps or []})())
        ranked = self.scorecard.rank(role)
        candidate_models = [r["model"] for r in ranked]

        # 3. Filter ledger seats
        seats = self.ledger.seats_for(candidate_models)
        # Drop seats where human is active
        seats = [s for s in seats if not s.presence]
        # Drop seats whose cache window < predicted gap (unless api cheap)
        seats = [s for s in seats if s.cache_window >= predicted_gap or s.kind == "api"]

        if not seats:
            # Fallback to any non-presence seat
            seats = [s for s in self.ledger.seats if not s.presence]
        if not seats:
            raise RuntimeError("No available seat for casting.")

        # 4. Sort by dies soonest
        seats.sort(key=lambda s: min(s.reset_at, s.expires_at))
        chosen_seat = seats[0]

        # 5. Assemble brief & spawn kernel
        brief = tiers.assemble(role, task_context, store=self.store)
        kernel = Kernel(
            seat=chosen_seat,
            role=role,
            brief=brief,
            effort=effort,
            convo_id=convo_id,
            harness=self.harness,
        )
        self.registry.register(kernel)
        return kernel
