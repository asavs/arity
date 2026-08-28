"""gorkbot ledger — Seat registry, quota reset management, and presence tracking.

Axiom 3: Provider quota remainders — use seats about to reset first.
Axiom 36: Presence — a seat a human is live on is never chosen for a fresh cast.
Axiom 7: Prompt cache boundary preservation.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Seat:
    """A model endpoint capacity slice with quota, pricing, and presence state."""
    id: str
    provider: str
    endpoint: str
    model: str
    kind: str = "quota"  # "quota" (subscription window) | "metered_api" (pay-per-token)
    total_allowance: float = 1_000_000.0  # Total tokens per reset cycle
    remaining: float = 1_000_000.0
    cycle_seconds: float = 86400.0  # 24h cycle or 5h sliding window
    reset_deadline: float = 0.0  # Unix timestamp when quota resets
    base_price_per_m: float = 1.0  # Reference price in USD per 1M tokens
    warm_window_seconds: float = 300.0  # Assured warm cache TTL
    presence: bool = False  # True if human or active session is currently typing here
    workspace_boundary: str = "default"
    api_key: Optional[str] = None

    def time_to_reset(self, now: float) -> float:
        """Seconds remaining until quota reset deadline."""
        return max(0.0, self.reset_deadline - now)

    def effective_cost(self, now: float) -> float:
        """Calculate dynamic effective cost weight (Axiom 3 / Survey).

        If subscription tokens are about to reset and remain unused,
        their opportunity cost drops to 0 ("use it or lose it").
        """
        if self.kind == "metered_api":
            return self.base_price_per_m

        time_left = self.time_to_reset(now)
        if time_left <= 0:
            return 0.0001

        fraction_time_left = max(0.01, time_left / self.cycle_seconds)
        fraction_quota_remaining = max(0.0, self.remaining / max(1.0, self.total_allowance))

        # Urgency ratio: if remaining quota exceeds remaining time ratio, cost decays to 0
        urgency = fraction_quota_remaining / fraction_time_left
        decay_factor = min(0.999, urgency * 0.5)
        return max(0.0001, self.base_price_per_m * (1.0 - decay_factor))


class SeatLedger:
    """Manages the pool of model seats, quota balances, and presence locks."""

    def __init__(self, initial_seats: Optional[list[Seat]] = None, auto_seed: bool = True):
        self._seats: dict[str, Seat] = {}
        if initial_seats:
            for s in initial_seats:
                self.register(s)
        elif auto_seed:
            self._seed_from_env()

    def register(self, seat: Seat) -> None:
        self._seats[seat.id] = seat

    def get(self, seat_id: str) -> Optional[Seat]:
        return self._seats.get(seat_id)

    def list_seats(self) -> list[Seat]:
        return list(self._seats.values())

    def list_available(self, now: Optional[float] = None, exclude_presence: bool = True) -> list[Seat]:
        """List seats with remaining capacity that are not presence-locked."""
        curr_time = now if now is not None else time.time()
        available = []
        for s in self._seats.values():
            if exclude_presence and s.presence:
                continue
            if s.kind == "quota" and s.remaining <= 0 and s.time_to_reset(curr_time) > 0:
                continue
            available.append(s)
        return available

    def dying_soonest(self, candidates: Optional[list[Seat]] = None, now: Optional[float] = None) -> list[Seat]:
        """Rank seats by which quota window expires first (Axiom 3)."""
        curr_time = now if now is not None else time.time()
        pool = candidates if candidates is not None else self.list_available(now=curr_time)

        def sort_key(s: Seat) -> tuple[int, float, float]:
            # Priority 1: Quota seats (0) before metered API seats (1)
            # Priority 2: Earliest reset deadline
            # Priority 3: Lowest effective cost
            kind_order = 0 if s.kind == "quota" else 1
            reset_in = s.time_to_reset(curr_time) if s.kind == "quota" else 999999.0
            return (kind_order, reset_in, s.effective_cost(curr_time))

        return sorted(pool, key=sort_key)

    def meter(self, seat_id: str, tokens: float) -> None:
        """Deduct token usage from seat balance."""
        seat = self._seats.get(seat_id)
        if seat and seat.kind == "quota":
            seat.remaining = max(0.0, seat.remaining - tokens)

    def set_presence(self, seat_id: str, is_present: bool) -> None:
        """Set or release human presence on a seat."""
        seat = self._seats.get(seat_id)
        if seat:
            seat.presence = is_present

    def _seed_from_env(self) -> None:
        now = time.time()
        default_reset = now + 86400.0  # 24h from now

        # 0. Direct OAuth Subscription Wire Seats (ChatGPT Codex & SuperGrok)
        try:
            from .auth import TokenStore
            store = TokenStore()
            agy_accounts = store.get_all_for_provider("google-antigravity")
            if agy_accounts:
                # Primary default seats
                self.register(
                    Seat(
                        id="gemini-wire",
                        provider="antigravity-wire",
                        endpoint="wire://google/antigravity/gemini",
                        model="gemini-3.6-flash",
                        kind="quota",
                        total_allowance=2_000_000 * len(agy_accounts),
                        remaining=2_000_000 * len(agy_accounts),
                        reset_deadline=default_reset,
                        base_price_per_m=0.0001,
                    )
                )
                self.register(
                    Seat(
                        id="claude-wire",
                        provider="claude-wire",
                        endpoint="wire://google/antigravity/claude",
                        model="claude-sonnet-4-6",
                        kind="quota",
                        total_allowance=2_000_000 * len(agy_accounts),
                        remaining=2_000_000 * len(agy_accounts),
                        reset_deadline=default_reset,
                        base_price_per_m=0.0001,
                    )
                )
                # Account-specific seats for parallel routing / A-B testing
                for key, acc in agy_accounts:
                    email = acc.get("email", "")
                    slug = email.split("@")[0] if "@" in email else (acc.get("projectId") or key)
                    slug_clean = "".join(c for c in slug if c.isalnum() or c in "-_")
                    self.register(
                        Seat(
                            id=f"gemini-wire-{slug_clean}",
                            provider=f"antigravity-wire:{key}",
                            endpoint=f"wire://google/antigravity/{slug_clean}",
                            model="gemini-3.6-flash",
                            kind="quota",
                            total_allowance=2_000_000,
                            remaining=2_000_000,
                            reset_deadline=default_reset,
                            base_price_per_m=0.0001,
                        )
                    )
                    self.register(
                        Seat(
                            id=f"claude-wire-{slug_clean}",
                            provider=f"claude-wire:{key}",
                            endpoint=f"wire://google/antigravity/{slug_clean}/claude",
                            model="claude-sonnet-4-6",
                            kind="quota",
                            total_allowance=2_000_000,
                            remaining=2_000_000,
                            reset_deadline=default_reset,
                            base_price_per_m=0.0001,
                        )
                    )
            if "openai-codex" in creds:
                self.register(
                    Seat(
                        id="codex-wire",
                        provider="codex-wire",
                        endpoint="wire://chatgpt/codex",
                        model="gpt-5.6-sol",
                        kind="quota",
                        total_allowance=2_000_000,
                        remaining=2_000_000,
                        reset_deadline=default_reset,
                        base_price_per_m=0.0001,  # Fast direct wire
                    )
                )
            if "xai-oauth" in creds:
                self.register(
                    Seat(
                        id="grok-wire",
                        provider="grok-wire",
                        endpoint="wire://xai/grok",
                        model="grok-4.5",
                        kind="quota",
                        total_allowance=2_000_000,
                        remaining=2_000_000,
                        reset_deadline=default_reset,
                        base_price_per_m=0.0001,  # Fast direct wire
                    )
                )
        except Exception:
            pass

        # 1. Codex CLI Harness Seat
        if shutil.which("codex"):
            self.register(
                Seat(
                    id="codex-sol",
                    provider="codex",
                    endpoint="cli://codex",
                    model="gpt-5.6-sol",
                    kind="quota",
                    total_allowance=2_000_000,
                    remaining=2_000_000,
                    reset_deadline=default_reset,
                    base_price_per_m=0.001,
                )
            )

        # 2. Claude Code CLI Harness Seat
        if shutil.which("claude"):
            self.register(
                Seat(
                    id="claude-sonnet",
                    provider="claude",
                    endpoint="cli://claude",
                    model="claude-3-7-sonnet",
                    kind="quota",
                    total_allowance=2_000_000,
                    remaining=2_000_000,
                    reset_deadline=default_reset,
                    base_price_per_m=0.001,
                )
            )
        # 3. Gemini API
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if gemini_key:
            self.register(
                Seat(
                    id="gemini-flash",
                    provider="gemini",
                    endpoint="https://generativelanguage.googleapis.com/v1beta",
                    model="gemini-3.6-flash",
                    kind="quota",
                    total_allowance=1_000_000,
                    remaining=1_000_000,
                    reset_deadline=default_reset,
                    base_price_per_m=0.10,
                    api_key=gemini_key,
                )
            )

        # 4. NVIDIA NIM
        nim_key = os.environ.get("NVIDIA_NIM_API_KEY")
        if nim_key:
            self.register(
                Seat(
                    id="nim-nemotron",
                    provider="nim",
                    endpoint="https://integrate.api.nvidia.com/v1",
                    model="nvidia/nemotron-3-nano-30b-a3b",
                    kind="quota",
                    total_allowance=500_000,
                    remaining=500_000,
                    reset_deadline=default_reset,
                    base_price_per_m=0.05,
                    api_key=nim_key,
                )
            )

        # 5. OpenRouter
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            self.register(
                Seat(
                    id="openrouter-llama",
                    provider="openrouter",
                    endpoint="https://openrouter.ai/api/v1",
                    model="meta-llama/llama-3.3-70b-instruct",
                    kind="metered_api",
                    base_price_per_m=0.40,
                    api_key=openrouter_key,
                )
            )

        # 6. OpenAI API Key (Metered)
        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            self.register(
                Seat(
                    id="openai-gpt4o",
                    provider="openai",
                    endpoint="https://api.openai.com/v1",
                    model="gpt-4o",
                    kind="metered_api",
                    base_price_per_m=2.50,
                    api_key=openai_key,
                )
            )
