"""pulse.py - keepalive decision loop evaluating p(return) * cold_cost > ping_cost."""

from __future__ import annotations
from dataclasses import dataclass
from .cadence import CadenceTracker
from .ledger import AXIOM7_CACHE
from .kernel import Kernel


@dataclass
class KeepaliveResult:
    action: str
    p_return: float
    cold_cost: float
    ping_cost: float
    expected_cold_cost: float
    message_sent: str | None = None
    kept_alive: bool = False


class Pulse:
    def __init__(self, cadence: CadenceTracker) -> None:
        self.cadence = cadence

    def evaluate_and_pulse(
        self,
        kernel: Kernel,
        session_id: str,
        elapsed_sec: float | None = None,
    ) -> KeepaliveResult:
        p_ret = self.cadence.p_return(session_id, elapsed=elapsed_sec)
        provider = kernel.identity.provider
        cache_data = AXIOM7_CACHE.get(provider, AXIOM7_CACHE["gemini"])

        cold_cost = cache_data.get("cold_cost", 0.002)
        ping_cost = cache_data.get("ping_cost", 0.00005)
        expected_cold_cost = p_ret * cold_cost

        if expected_cold_cost > ping_cost:
            keepalive_text = "hi luv u"
            _ = kernel.step(keepalive_text)
            return KeepaliveResult(
                action="PING",
                p_return=p_ret,
                cold_cost=cold_cost,
                ping_cost=ping_cost,
                expected_cold_cost=expected_cold_cost,
                message_sent=keepalive_text,
                kept_alive=True,
            )
        else:
            kernel.die(reason="pulse_timeout", file_report=True)
            return KeepaliveResult(
                action="LET_DIE",
                p_return=p_ret,
                cold_cost=cold_cost,
                ping_cost=ping_cost,
                expected_cold_cost=expected_cold_cost,
                message_sent=None,
                kept_alive=False,
            )
