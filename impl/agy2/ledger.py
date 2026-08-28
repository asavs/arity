"""ledger.py - Seat ledger, presence tracking, quota reserve, and provider endpoints."""

from __future__ import annotations
import os
import time
import itertools
from dataclasses import dataclass, field
from typing import Any

_seat_counter = itertools.count(1)


@dataclass
class Seat:
    provider: str
    endpoint: str
    model: str
    api_key: str
    kind: str = "quota"  # quota | api
    remaining: int = 1_000_000
    reset_at: float = field(default_factory=lambda: time.time() + 86400)
    expires_at: float = field(default_factory=lambda: time.time() + 86400 * 30)
    cache_boundary: str = "default_boundary"
    cache_window: float = 300.0  # seconds
    presence: bool = False  # True if human is live on this seat
    price_in_per_M: float = 2.0
    id: str = field(default_factory=lambda: f"seat_{next(_seat_counter):04d}")


class SeatLedger:
    """Tracks provider seats, quota resets, and human presence."""

    def __init__(self) -> None:
        self.seats: list[Seat] = []
        self._forced_quota_wall = False
        self.seed_from_env()

    def seed_from_env(self) -> None:
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if gemini_key:
            self.seats.append(Seat(
                provider="gemini",
                endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
                model="gemini-3.6-flash",
                api_key=gemini_key,
                kind="quota",
                cache_boundary="proj-gemini-main",
                cache_window=300.0,
                price_in_per_M=2.0,
            ))
            self.seats.append(Seat(
                provider="gemini",
                endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
                model="gemini-3.5-flash-lite",
                api_key=gemini_key,
                kind="quota",
                cache_boundary="proj-gemini-main",
                cache_window=300.0,
                price_in_per_M=1.0,
            ))

        nim_key = os.environ.get("NVIDIA_NIM_API_KEY")
        if nim_key:
            self.seats.append(Seat(
                provider="nim",
                endpoint="https://integrate.api.nvidia.com/v1",
                model="nvidia/nemotron-3-nano-30b-a3b",
                api_key=nim_key,
                kind="quota",
                cache_boundary="nim-workspace",
                cache_window=300.0,
                price_in_per_M=1.0,
            ))

        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            self.seats.append(Seat(
                provider="openai",
                endpoint="https://openrouter.ai/api/v1",
                model="openai/gpt-4o-mini",
                api_key=openrouter_key,
                kind="api",
                cache_boundary="openrouter-main",
                cache_window=1800.0,
                price_in_per_M=2.5,
            ))

        if not self.seats:
            # Standby seats for local test execution
            self.seats.append(Seat(
                provider="gemini",
                endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
                model="gemini-3.6-flash",
                api_key=gemini_key or "standby_key",
                kind="quota",
                cache_boundary="proj-gemini-local",
                cache_window=300.0,
                price_in_per_M=2.0,
            ))
            self.seats.append(Seat(
                provider="gemini",
                endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
                model="gemini-3.5-flash-lite",
                api_key=gemini_key or "standby_key",
                kind="quota",
                cache_boundary="proj-gemini-local",
                cache_window=300.0,
                price_in_per_M=1.0,
            ))

    def seats_for(self, models: list[str]) -> list[Seat]:
        if not models:
            return list(self.seats)
        matched = [s for s in self.seats if s.model in models]
        return matched if matched else list(self.seats)

    def reserve(self, seat: Seat, purpose: str = "report_turn") -> bool:
        if self._forced_quota_wall:
            return False
        if seat.remaining < 50:
            return False
        seat.remaining -= 50
        return True

    def meter(self, seat: Seat, prompt_tokens: int, completion_tokens: int) -> None:
        used = prompt_tokens + completion_tokens
        seat.remaining = max(0, seat.remaining - used)

    def probe(self, seat: Seat) -> dict[str, Any]:
        return {
            "seat_id": seat.id,
            "provider": seat.provider,
            "remaining": seat.remaining,
            "presence": seat.presence,
            "reset_at": seat.reset_at,
            "expires_at": seat.expires_at,
        }
