"""pulse.py - Heartbeat: keepalive ("hi luv u") economics and quiet eviction."""

from __future__ import annotations
import time
from typing import Any
from cast import KernelRegistry
from ledger import SeatLedger
from store import Store
from archivist import Archivist
import cadence


class Pulse:
    """System heartbeat that maintains warm caches or lets them die."""

    def __init__(
        self,
        registry: KernelRegistry,
        ledger: SeatLedger,
        store: Store,
        archivist: Archivist,
    ) -> None:
        self.registry = registry
        self.ledger = ledger
        self.store = store
        self.archivist = archivist

    def tick(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        results: list[dict[str, Any]] = []

        for (role_name, convo_id), k in list(self.registry.active.items()):
            if k.state != "alive":
                continue

            p = cadence.p_return(k.cache_expires_at, [30.0, 60.0])
            c_cost = cadence.cold_cost(k)
            cold_penalty = c_cost["penalty"]
            ping_cost = 0.00005  # tiny ping token cost

            # Keepalive rule: p(return) * cold_cost > ping_cost
            if p * cold_penalty > ping_cost:
                reply = k.turn("hi luv u", tools=[])
                results.append({
                    "kernel_id": k.id,
                    "action": "keepalive",
                    "ping": "hi luv u",
                    "reply": reply,
                })
            else:
                k.die(reason="quiet", store=self.store, archivist=self.archivist, ledger=self.ledger)
                self.registry.remove(k)
                results.append({
                    "kernel_id": k.id,
                    "action": "let_go",
                    "reason": "quiet",
                })

        return results
