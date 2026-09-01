"""Arity pulse — keepalive economics, cache pricing, and quota harvesting.

Axiom 11: The system has a pulse. Keepalive while p(return) * cold_cost > ping_cost;
otherwise let the kernel die. The keepalive text is "hi luv u".
Axiom 3: Quota remainders — use expiring tokens for background tasks.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import cache_economics
from .ledger import Seat, SeatLedger


@dataclass(frozen=True)
class PulseAction:
    """An action determined by the pulse clock."""
    kind: str  # "keepalive_ping" | "let_die" | "harvest_quota"
    target_id: str
    reason: str
    message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CacheEconomics:
    """Prices prefix caches from the Axiom 7 table in `cache_economics` (the only copy)."""

    @classmethod
    def cold_cost(cls, provider: str, prefix_tokens: int) -> float:
        """Calculate the dollar cost penalty of having to re-send a cold prompt prefix.

        The wiki's cold-vs-warm penalty: a normal cold input turn minus the warm read it
        replaces, so `cold_cost(provider, 100_000)` reproduces the published column.
        """
        terms = cache_economics.profile(provider)
        lost_discount = terms.input_price_per_m * (1.0 - terms.cache_read_multiplier)
        return max(0.0001, (prefix_tokens / 1_000_000.0) * lost_discount)

    @classmethod
    def ping_cost(cls, provider: str) -> float:
        """Cost of sending a 3-token keepalive ping ('hi luv u')."""
        terms = cache_economics.profile(provider)
        # 3 tokens input + 2 tokens output
        return (5.0 / 1_000_000.0) * terms.input_price_per_m


class CadenceModel:
    """Predicts the probability P(return) that a user will continue the conversation."""

    @staticmethod
    def predict_p_return(seconds_since_last_turn: float, warm_window_seconds: float) -> float:
        """Decay curve: return probability drops as idle time approaches the warm window limit."""
        if seconds_since_last_turn < 0:
            return 1.0
        if seconds_since_last_turn >= warm_window_seconds:
            return 0.0

        # Exponential decay over the warm window
        tau = warm_window_seconds / 2.5
        return math.exp(-seconds_since_last_turn / tau)


class PulseEngine:
    """Coordinates keepalive pings and expiring quota harvesting."""

    def __init__(self, keepalive_text: str = "hi luv u"):
        self.keepalive_text = keepalive_text

    def evaluate_session(
        self,
        session_id: str,
        seat: Seat,
        seconds_idle: float,
        prefix_tokens: int,
    ) -> PulseAction:
        """Evaluate if an idle warm kernel should be pinged or allowed to die."""
        p_return = CadenceModel.predict_p_return(seconds_idle, seat.warm_window_seconds)
        cold_cost = CacheEconomics.cold_cost(seat.provider, prefix_tokens)
        ping_cost = CacheEconomics.ping_cost(seat.provider)

        expected_cold_loss = p_return * cold_cost

        if expected_cold_loss > ping_cost and seconds_idle < seat.warm_window_seconds:
            return PulseAction(
                kind="keepalive_ping",
                target_id=session_id,
                message=self.keepalive_text,
                reason=(
                    f"P(return)={p_return:.2f} * cold_cost(${cold_cost:.5f}) = "
                    f"${expected_cold_loss:.5f} > ping_cost(${ping_cost:.6f})"
                ),
                metadata={
                    "p_return": p_return,
                    "cold_cost": cold_cost,
                    "ping_cost": ping_cost,
                },
            )

        return PulseAction(
            kind="let_die",
            target_id=session_id,
            reason=(
                f"P(return)={p_return:.2f} * cold_cost(${cold_cost:.5f}) = "
                f"${expected_cold_loss:.5f} <= ping_cost(${ping_cost:.6f}) or expired"
            ),
            metadata={
                "p_return": p_return,
                "cold_cost": cold_cost,
                "ping_cost": ping_cost,
            },
        )

    def scan_expiring_seats(
        self,
        ledger: SeatLedger,
        now: Optional[float] = None,
        harvest_threshold_seconds: float = 3600.0,
    ) -> list[PulseAction]:
        """Discover quota seats nearing reset with substantial unused tokens."""
        curr_time = now if now is not None else time.time()
        actions = []

        for seat in ledger.list_available(now=curr_time, exclude_presence=True):
            if seat.kind == "quota" and seat.remaining > 50_000:
                time_left = seat.time_to_reset(curr_time)
                if 0 < time_left <= harvest_threshold_seconds:
                    actions.append(
                        PulseAction(
                            kind="harvest_quota",
                            target_id=seat.id,
                            reason=(
                                f"Seat '{seat.id}' has {seat.remaining:,.0f} unused tokens "
                                f"expiring in {time_left:.0f}s. Eligible for background tasks."
                            ),
                            metadata={
                                "seat_id": seat.id,
                                "remaining_tokens": seat.remaining,
                                "seconds_to_reset": time_left,
                            },
                        )
                    )

        return actions
