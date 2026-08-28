"""gorkbot composer — Casting engine and multi-seat decision maker.

Axiom 3: The model behind a bot is chosen per prompt, on evidence (Provider, Model, Effort).
Axiom 3 Corollary: Many kernels per task (A/B testing candidates on real tasks).
Axiom 36: Never choose a seat a human is live on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .ledger import Seat, SeatLedger
from .roles import Role, VOICE_ROLE


@dataclass(frozen=True)
class CastingDecision:
    """The result of casting a role to one or more candidate seats."""
    role: Role
    primary_seat: Seat
    candidates: list[Seat] = field(default_factory=list)
    reason: str = ""


# Default model aptitude preferences by role family
APTITUDE_MATRIX: dict[str, list[str]] = {
    "voice": ["gpt-4o", "claude-3-5-sonnet", "gemini-3.6-flash", "llama"],
    "architect": ["claude-3-5-sonnet", "gpt-4o", "gemini-3.6-flash"],
    "builder": ["gemini-3.6-flash", "gpt-4o", "nemotron", "llama"],
    "reviewer": ["claude-3-5-sonnet", "gpt-4o", "gemini-3.6-flash"],
}


class CastingComposer:
    """Composes seat availability, quota expiration, and role aptitudes to cast seats."""

    def __init__(self, ledger: SeatLedger, aptitude_matrix: Optional[dict[str, list[str]]] = None):
        self.ledger = ledger
        self.aptitudes = aptitude_matrix or APTITUDE_MATRIX

    def cast(
        self,
        role: Role,
        task: str,
        candidates_count: int = 1,
        now: Optional[float] = None,
    ) -> CastingDecision:
        """Select the best seat(s) for a role and task."""
        curr_time = now if now is not None else time.time()
        available_seats = self.ledger.list_available(now=curr_time, exclude_presence=True)

        if not available_seats:
            raise RuntimeError("No available seats found in ledger (all exhausted or presence-locked).")

        preferred_models = self.aptitudes.get(role.name.lower(), ["*"])

        def rank_key(seat: Seat) -> tuple[int, float, float]:
            # 1. Aptitude score (lower is better; index in preferred list)
            aptitude_rank = 99
            for idx, pattern in enumerate(preferred_models):
                if pattern == "*" or pattern.lower() in seat.model.lower() or pattern.lower() in seat.provider.lower():
                    aptitude_rank = idx
                    break

            # 2. Effective cost (lower is better; expiring quota approaches 0)
            eff_cost = seat.effective_cost(curr_time)

            # 3. Quota priority: quota seats dying sooner sort earlier
            time_to_expire = seat.time_to_reset(curr_time) if seat.kind == "quota" else 999999.0

            return (aptitude_rank, eff_cost, time_to_expire)

        ranked_seats = sorted(available_seats, key=rank_key)
        top_candidates = ranked_seats[:max(1, candidates_count)]
        primary = top_candidates[0]

        reason = (
            f"Cast '{primary.id}' ({primary.model}) for role '{role.name}'. "
            f"Effective cost: ${primary.effective_cost(curr_time):.4f}/M, "
            f"Reset in: {primary.time_to_reset(curr_time):.0f}s"
        )

        return CastingDecision(
            role=role,
            primary_seat=primary,
            candidates=top_candidates,
            reason=reason,
        )
