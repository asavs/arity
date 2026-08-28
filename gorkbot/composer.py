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
    "secretary": ["gemini-3.6-flash", "gpt-4o", "claude-3-5-sonnet", "llama"],
    "voice": ["gemini-3.6-flash", "gpt-4o", "claude-3-5-sonnet", "llama"],
    "engineer": ["claude-3-7-sonnet", "claude-3-5-sonnet", "gpt-5.6-sol", "gemini-3.1-pro"],
    "python_developer": ["gpt-5.6-sol", "gemini-3.6-flash", "nemotron", "llama"],
    "builder": ["gpt-5.6-sol", "gemini-3.6-flash", "nemotron", "llama"],
    "reviewer": ["claude-3-7-sonnet", "claude-3-5-sonnet", "gpt-5.6-sol", "gemini-3.6-flash"],
    "scout": ["gemini-3.6-flash", "grok-4.5", "gpt-4o"],
}


class CastingComposer:
    """Composes seat availability, quota expiration, scorecard standings, and skills to cast seats."""

    def __init__(
        self,
        ledger: SeatLedger,
        scorecard: Optional[Any] = None,
        aptitude_matrix: Optional[dict[str, list[str]]] = None,
    ):
        self.ledger = ledger
        self.scorecard = scorecard
        self.aptitudes = aptitude_matrix or APTITUDE_MATRIX

    def cast(
        self,
        role: Role,
        task: str,
        candidates_count: int = 1,
        now: Optional[float] = None,
    ) -> CastingDecision:
        """Select the best candidate seat(s) for a role and task based on empirical evidence and quota math."""
        curr_time = now if now is not None else time.time()
        available_seats = self.ledger.list_available(now=curr_time, exclude_presence=True)

        if not available_seats:
            raise RuntimeError("No available seats found in ledger (all exhausted or presence-locked).")

        def evaluate_seat(seat: Seat) -> float:
            """Higher score is better."""
            # 1. Empirical Scorecard Standing (Axiom 9 & 3)
            standing_score = 10.0
            if self.scorecard and hasattr(self.scorecard, "get_standing"):
                standing_score = self.scorecard.get_standing(role.name, seat.model)
                if hasattr(role, "skills") and role.skills:
                    for sk in role.skills:
                        standing_score += (self.scorecard.get_standing(f"skill:{sk}", seat.model) - 10.0)

            # 2. Economic Opportunity ($C_eff near 0 gives bonus for expiring subscriptions)
            eff_cost = seat.effective_cost(curr_time)
            cost_penalty = eff_cost * 2.0  # Penalize high metered dollar cost

            # 3. Urgency bonus for expiring subscription quota
            urgency_bonus = 0.0
            if seat.kind == "quota":
                time_left = seat.time_to_reset(curr_time)
                if time_left < 3600:  # < 1 hour left
                    urgency_bonus = 3.0
                elif time_left < 14400:  # < 4 hours left
                    urgency_bonus = 1.5

            return standing_score - cost_penalty + urgency_bonus

        # Sort all available seats by score descending
        ranked_seats = sorted(available_seats, key=evaluate_seat, reverse=True)

        # Select candidates, prioritizing provider diversity for A/B trials
        top_candidates: list[Seat] = []
        seen_providers: set[str] = set()

        # First pass: pick best seat per distinct provider
        for seat in ranked_seats:
            if seat.provider not in seen_providers:
                top_candidates.append(seat)
                seen_providers.add(seat.provider)
                if len(top_candidates) >= candidates_count:
                    break

        # Second pass: fill remaining candidate slots if needed
        if len(top_candidates) < candidates_count:
            for seat in ranked_seats:
                if seat not in top_candidates:
                    top_candidates.append(seat)
                    if len(top_candidates) >= candidates_count:
                        break

        primary = top_candidates[0]
        reason = (
            f"Cast '{primary.id}' ({primary.model}) for role '{role.name}'. "
            f"Scorecard standing: {self.scorecard.get_standing(role.name, primary.model) if self.scorecard else 10.0:.1f} pts, "
            f"Effective cost: ${primary.effective_cost(curr_time):.4f}/M, "
            f"Expiring in: {primary.time_to_reset(curr_time):.0f}s"
        )

        return CastingDecision(
            role=role,
            primary_seat=primary,
            candidates=top_candidates,
            reason=reason,
        )
