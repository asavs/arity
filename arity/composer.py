"""Arity composer — casting engine and multi-seat decision maker.

One engine answering two questions with different domains:

- Question A, "who is good at this?", ranges over *models*. Inferred from scorecard
  evidence, carries uncertainty, needs exploration to stay fresh.
- Question B, "whose tokens should I spend?", ranges over *seats* — quota window, reset
  deadline, warm cache, presence lock. Measured, not inferred.

B filters, A orders. The two are never summed: summing them requires an exchange rate
between a ten-point standing and dollars-per-million, and no such rate exists. B may veto
A; A may never veto B. A mode selects which question *orders*, not how much each counts,
so there are no weights and no coefficients here to tune.

Axiom 3: The model behind a bot is chosen per prompt, on evidence (Provider, Model, Effort).
Axiom 3 Corollary: Many kernels per task (A/B testing candidates on real tasks).
Axiom 7: A conversation expected to go quiet must not be seated where the assured warm
window is shorter than the silence.
Axiom 36: Never choose a seat a human is live on.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .ledger import Seat, SeatLedger
from .roles import Role, VOICE_ROLE

BROKIE = "brokie"
SMART = "smart"
CHAOS = "chaos"
CASTING_MODES = (BROKIE, SMART, CHAOS)

# Signature dimensions a Seat carries. The signature's fourth axis, the tool runner, is
# assigned downstream by the terrarium and is not a property of a seat, so casting cannot
# serve a distinctness request on it.
DISTINCT_DIMENSIONS = ("model", "provider", "harness")

BASELINE_STANDING = 10.0

# Below this the exploration slot would be the whole cast, and smart mode would never
# exploit what it knows.
MIN_COUNT_FOR_EXPLORATION = 2


@dataclass(frozen=True)
class CastingDecision:
    """The result of casting a role to one or more candidate seats."""
    role: Role
    primary_seat: Seat
    candidates: list[Seat] = field(default_factory=list)
    reason: str = ""
    mode: str = SMART
    seed: int = 0
    requested_count: int = 1
    distinct_on: Optional[str] = None
    exploration_seat: Optional[Seat] = None
    shortfall: Optional[str] = None

    @property
    def satisfied_count(self) -> int:
        return len(self.candidates)

    @property
    def fully_satisfied(self) -> bool:
        return self.shortfall is None


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

    # -- Question A: who is good at this? ------------------------------------------------

    def _aptitude(self, role: Role, seat: Seat) -> float:
        """Evidence for this model in this role, plus the role's skill deltas. Inferred.

        Under Axiom 3 / A3-2, aptitude ranks by average delta per observation rather than
        accumulated running total, so a long-running incumbent does not permanently lock out
        newer, better models.
        """
        if not self.scorecard:
            return BASELINE_STANDING
        if hasattr(self.scorecard, "get_average_delta"):
            delta = self.scorecard.get_average_delta(role.name, seat.model)
            total_delta = delta if delta is not None else 0.0
            for sk in getattr(role, "skills", ()) or ():
                sk_delta = self.scorecard.get_average_delta(f"skill:{sk}", seat.model)
                if sk_delta is not None:
                    total_delta += sk_delta
            return BASELINE_STANDING + total_delta
        if hasattr(self.scorecard, "get_standing"):
            standing = self.scorecard.get_standing(role.name, seat.model)
            for sk in getattr(role, "skills", ()) or ():
                standing += self.scorecard.get_standing(f"skill:{sk}", seat.model) - BASELINE_STANDING
            return standing
        return BASELINE_STANDING

    def _observations(self, role: Role, seat: Seat) -> int:
        """How many verdicts have scored this model in this role (confidence tiebreak)."""
        if not (self.scorecard and hasattr(self.scorecard, "get_observations")):
            return 0
        return self.scorecard.get_observations(role.name, seat.model)
    def _evidence_key(self, role: Role, seat: Seat) -> str:
        """The scorecard key whose observation count says how much is known about this seat."""
        key_name = getattr(role, "key_name", None) or role.name.replace(":", ".")
        return f"{key_name}:{seat.model}".lower()

    # -- Question B: whose tokens should I spend? -----------------------------------------

    def _estimate_task_tokens(self, task_key: Optional[str] = None) -> float:
        """Return average recorded token use for a task key, or a conservative default.

        A named task uses its stable task-bank name; an ad hoc task uses its exact brief. New
        trial axes persist that key, while older records fall back to their opaque ``task_id``.
        Terrarium trial records are canonical; older imported evidence may only contain
        ``trial_axes``. Malformed and non-finite measurements do not influence a cast.
        """
        store = getattr(self.scorecard, "store", None)
        query = getattr(store, "query", None)
        if not callable(query):
            return 5_000.0

        for kind, token_field in (
            ("terrarium_trial", "tokens_used"),
            ("trial_axes", "tokens"),
        ):
            try:
                records = query(kind)
            except Exception:
                continue

            total = 0.0
            count = 0
            for record in records:
                if not isinstance(record, dict):
                    continue
                record_task_key = record.get("task_key", record.get("task_id"))
                if task_key is not None and record_task_key != task_key:
                    continue
                tokens = record.get(token_field)
                if (
                    isinstance(tokens, (int, float))
                    and not isinstance(tokens, bool)
                    and math.isfinite(tokens)
                    and tokens >= 0
                ):
                    total += float(tokens)
                    count += 1
            if count:
                return total / count
        return 5_000.0

    def _affordable(
        self,
        curr_time: float,
        expected_idle_seconds: Optional[float],
        min_tokens: Optional[float] = None,
    ) -> tuple[list[Seat], list[str]]:
        """Seats with available quota and the reasons any were removed."""
        pool = self.ledger.list_available(now=curr_time, exclude_presence=True)
        removals: list[str] = []
        if not pool:
            return [], ["no seat is both un-exhausted and free of a presence lock"]

        if min_tokens is not None:
            kept: list[Seat] = []
            dropped: list[Seat] = []
            for seat in pool:
                if (
                    seat.kind == "quota"
                    and seat.reset_deadline > curr_time
                    and seat.remaining < min_tokens
                ):
                    dropped.append(seat)
                else:
                    kept.append(seat)
            if dropped:
                removals.append(
                    f"{len(dropped)} seat(s) with insufficient quota for estimated "
                    f"{min_tokens:.0f} tokens"
                )
            pool = kept

        if expected_idle_seconds is not None:
            # A zero window is not a violation: a provider that assures nothing has no warm
            # state to forfeit, so silence costs it nothing extra.
            kept = [
                s
                for s in pool
                if not (0.0 < s.warm_window_seconds < expected_idle_seconds)
            ]
            dropped = len(pool) - len(kept)
            if dropped:
                removals.append(
                    f"{dropped} seat(s) whose assured warm window is shorter than the "
                    f"{expected_idle_seconds:.0f}s expected idle"
                )
            pool = kept
        return pool, removals

    def _economic_order(self, pool: list[Seat], curr_time: float) -> list[Seat]:
        """Spend what is about to evaporate: quota before metered, soonest reset first."""
        return self.ledger.dying_soonest(candidates=pool, now=curr_time)

    # -- Selection -------------------------------------------------------------------------

    @staticmethod
    def _pick(
        ordered: list[Seat],
        limit: int,
        distinct_on: Optional[str],
        chosen: list[Seat],
    ) -> list[Seat]:
        """Extend `chosen` up to `limit` seats, honoring the caller's distinctness request."""
        taken = {s.id for s in chosen}
        seen = {getattr(s, distinct_on) for s in chosen} if distinct_on else set()
        picked = list(chosen)
        for seat in ordered:
            if len(picked) >= limit:
                break
            if seat.id in taken:
                continue
            if distinct_on:
                value = getattr(seat, distinct_on)
                if value in seen:
                    continue
                seen.add(value)
            taken.add(seat.id)
            picked.append(seat)
        return picked

    def _explore(
        self,
        role: Role,
        ordered: list[Seat],
        distinct_on: Optional[str],
        chosen: list[Seat],
    ) -> Optional[Seat]:
        """The least-observed seat still eligible, so the leaders are not the only ones asked."""
        taken = {s.id for s in chosen}
        seen = {getattr(s, distinct_on) for s in chosen} if distinct_on else set()
        eligible = [
            s
            for s in ordered
            if s.id not in taken and not (distinct_on and getattr(s, distinct_on) in seen)
        ]
        if not eligible:
            return None
        if not (self.scorecard and hasattr(self.scorecard, "least_observed")):
            return eligible[0]
        by_key: dict[str, Seat] = {}
        for seat in eligible:
            by_key.setdefault(self._evidence_key(role, seat), seat)
        key = self.scorecard.least_observed(list(by_key))
        return by_key.get(key) if key else eligible[0]

    def cast(
        self,
        role: Role,
        task: str,
        candidates_count: int = 1,
        now: Optional[float] = None,
        *,
        mode: str = SMART,
        seed: Optional[int] = None,
        distinct_on: Optional[str] = None,
        expected_idle_seconds: Optional[float] = None,
    ) -> CastingDecision:
        """Cast a role: economics filters the seats, the mode decides what orders them."""
        if mode not in CASTING_MODES:
            raise ValueError(f"Unknown casting mode {mode!r}; expected one of {CASTING_MODES}.")
        if candidates_count < 1:
            raise ValueError(f"candidates_count must be at least 1, got {candidates_count}.")
        if distinct_on is not None and distinct_on not in DISTINCT_DIMENSIONS:
            hint = (
                " The tool runner is chosen by the terrarium, not carried by a seat."
                if distinct_on == "tools"
                else ""
            )
            raise ValueError(
                f"Cannot cast distinct on {distinct_on!r}; a seat carries "
                f"{DISTINCT_DIMENSIONS}.{hint}"
            )

        curr_time = now if now is not None else time.time()
        if seed is None:
            seed = random.SystemRandom().getrandbits(32)
        rng = random.Random(seed)

        estimated_tokens = self._estimate_task_tokens(task)
        pool, removals = self._affordable(
            curr_time, expected_idle_seconds, min_tokens=estimated_tokens
        )
        if not pool:
            raise RuntimeError(f"No castable seats: {'; '.join(removals)}.")

        # Every ordering starts from the same deterministic floor, so a tie the mode does not
        # break falls to seat id rather than to ledger insertion order.
        base = sorted(pool, key=lambda s: s.id or "")
        aptitude = {s.id: self._aptitude(role, s) for s in base}
        observations = {s.id: self._observations(role, s) for s in base}

        if mode == SMART:
            # High average delta first; ties broken by observation count (confidence),
            # then by economics (because sorted() is stable).
            ordered = sorted(
                self._economic_order(base, curr_time),
                key=lambda s: (-aptitude[s.id], -observations[s.id]),
            )
        elif mode == BROKIE:
            ordered = self._economic_order(
                sorted(base, key=lambda s: (-aptitude[s.id], -observations[s.id])), curr_time
            )
        else:
            ordered = list(base)
            rng.shuffle(ordered)

        explores = mode == SMART and candidates_count >= MIN_COUNT_FOR_EXPLORATION
        exploit_slots = candidates_count - 1 if explores else candidates_count
        chosen = self._pick(ordered, exploit_slots, distinct_on, [])

        exploration_seat: Optional[Seat] = None
        if explores:
            exploration_seat = self._explore(role, ordered, distinct_on, chosen)
            if exploration_seat is not None:
                chosen.append(exploration_seat)

        shortfall = None
        if len(chosen) < candidates_count:
            detail = f"{len(base)} seat(s) survived question B"
            if distinct_on:
                distinct_available = len({getattr(s, distinct_on) for s in base})
                detail += f", holding {distinct_available} distinct {distinct_on} value(s)"
            shortfall = (
                f"requested {candidates_count}, satisfied {len(chosen)}: {detail}"
            )
            if removals:
                shortfall += f" after removing {'; '.join(removals)}"

        primary = chosen[0]
        reason = (
            f"Cast '{primary.id}' ({primary.model}) for role '{role.name}' in {mode} mode "
            f"(seed {seed}). Aptitude: {aptitude[primary.id]:.1f} pts, "
            f"effective cost: ${primary.effective_cost(curr_time):.4f}/M, "
            f"expiring in: {primary.time_to_reset(curr_time):.0f}s. "
            f"{len(chosen)}/{candidates_count} slots filled"
            + (f", distinct on {distinct_on}" if distinct_on else "")
            + (f". Filtered: {'; '.join(removals)}" if removals else ".")
        )

        return CastingDecision(
            role=role,
            primary_seat=primary,
            candidates=chosen,
            reason=reason,
            mode=mode,
            seed=seed,
            requested_count=candidates_count,
            distinct_on=distinct_on,
            exploration_seat=exploration_seat,
            shortfall=shortfall,
        )
