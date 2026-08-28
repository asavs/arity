"""Seats, their clocks, and the cache arithmetic casting needs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


CACHE_TABLE: dict[str, dict[str, Any]] = {
    "anthropic": {"window_s": 300, "long_window_s": 3600, "read_x": .10,
                  "write_x": 1.25, "long_write_x": 2.0, "price_in_m": 10.0,
                  "storage_per_100k_hr": 0.0, "penalty_100k": .90},
    "openai": {"window_s": 1800, "read_x": .10, "write_x": 1.0,
               "price_in_m": 4.0, "storage_per_100k_hr": 0.0, "penalty_100k": .36},
    "gemini": {"window_s": 3600, "implicit_opportunistic": True, "read_x": .10,
               "write_x": 1.0, "price_in_m": 2.0, "storage_per_100k_hr": .45,
               "penalty_100k": .18},
    "xai": {"window_s": 0, "evictable": True, "read_x": .25, "write_x": 1.0,
            "price_in_m": 2.0, "storage_per_100k_hr": 0.0, "penalty_100k": .15},
    "nim": {"window_s": 0, "unverified": True, "read_x": 1.0, "write_x": 1.0,
            "price_in_m": 0.0, "storage_per_100k_hr": 0.0, "penalty_100k": 0.0},
}


@dataclass
class Seat:
    id: str
    provider: str
    endpoint: str
    model: str
    kind: str
    remaining: int
    reset_at: datetime
    expires_at: datetime
    cache_boundary: str
    presence: bool = False

    @property
    def cache_window(self) -> int:
        return int(CACHE_TABLE[self.provider]["window_s"])


class Ledger:
    def __init__(self, seats: list[Seat], keys: dict[str, str]):
        self.seats, self.__keys = seats, keys
        self.tokens = 0

    @classmethod
    def from_env(cls) -> "Ledger":
        now = datetime.now(timezone.utc)
        seats: list[Seat] = []
        keys: dict[str, str] = {}
        specs = []
        if key := os.getenv("GEMINI_API_KEY"):
            specs += [("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", m,
                       "gemini-project-env", key, 1) for m in ("gemini-3.6-flash", "gemini-3.5-flash-lite")]
        if key := os.getenv("NVIDIA_NIM_API_KEY"):
            specs += [("nim", "https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-nano-30b-a3b",
                       "nim-account-env", key, 2)]
        if key := os.getenv("OPENROUTER_API_KEY"):
            models = tuple(filter(None, os.getenv("OPENROUTER_MODELS", "openai/gpt-5-mini").split(",")))
            specs += [("openai", "https://openrouter.ai/api/v1", m.strip(), "openrouter-account-env", key, 3)
                      for m in models]
        for index, (provider, endpoint, model, boundary, key, reset_hours) in enumerate(specs):
            seat_id = f"{provider}-{index}"
            kind = "api_cheap" if provider == "nim" else "api"
            seats.append(Seat(seat_id, provider, endpoint, model, kind, 1_000_000,
                              now + timedelta(hours=reset_hours), now + timedelta(days=30), boundary))
            keys[seat_id] = key
        return cls(seats, keys)

    def credential_for_proxy(self, seat_id: str) -> str:
        """Only the HTTP plug calls this; Kernel has no credential field."""
        return self.__keys[seat_id]

    def candidates(self, models: list[str], predicted_gap: float) -> list[Seat]:
        rank = {model: n for n, model in enumerate(models)}
        out = [s for s in self.seats if s.model in rank and s.remaining > 0 and not s.presence]
        out = [s for s in out if s.cache_window >= predicted_gap or s.kind == "api_cheap"]
        return sorted(out, key=lambda s: (rank[s.model], min(s.reset_at, s.expires_at)))

    def mark_boundary_presence(self, boundary: str, live: bool) -> None:
        for seat in self.seats:
            if seat.cache_boundary == boundary:
                seat.presence = live

    def reserve(self, seat: Seat, amount: int) -> bool:
        if seat.remaining < amount:
            return False
        seat.remaining -= amount
        return True

    def meter(self, seat: Seat, tokens: int) -> None:
        seat.remaining = max(0, seat.remaining - tokens)
        self.tokens += tokens

    def cold_cost(self, provider: str, prefix_tokens: int, warm: bool = True) -> dict[str, float]:
        row = CACHE_TABLE[provider]
        cold = prefix_tokens / 1_000_000 * row["price_in_m"]
        hot = cold * row["read_x"] if warm else cold
        return {"cold": cold, "warm": hot, "penalty": cold - hot}
