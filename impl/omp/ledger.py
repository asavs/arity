"""Seats, their clocks, and the cache arithmetic casting needs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

CACHE_TABLE: dict[str, dict[str, Any]] = {
    "anthropic": {"window_s": 300, "long_window_s": 3600, "read_x": 0.10, "write_x": 1.25,
                  "long_write_x": 2.0, "price_in_m": 10.0, "storage_per_100k_hr": 0.0, "penalty_100k": 0.90},
    "openai": {"window_s": 1800, "read_x": 0.10, "write_x": 1.0, "price_in_m": 4.0,
               "storage_per_100k_hr": 0.0, "penalty_100k": 0.36},
    "gemini": {"window_s": 3600, "implicit_opportunistic": True, "read_x": 0.10, "write_x": 1.0,
               "price_in_m": 2.0, "storage_per_100k_hr": 0.45, "penalty_100k": 0.18},
    "xai": {"window_s": 0, "evictable": True, "read_x": 0.25, "write_x": 1.0, "price_in_m": 2.0,
            "storage_per_100k_hr": 0.0, "penalty_100k": 0.15},
    "nim": {"window_s": 0, "unverified": True, "read_x": 1.0, "write_x": 1.0, "price_in_m": 0.0,
            "storage_per_100k_hr": 0.0, "penalty_100k": 0.0},
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
        return int(CACHE_TABLE.get(self.provider, {}).get("window_s", 0))


class Ledger:
    def __init__(self, seats: list[Seat], keys: dict[str, str]):
        self.seats = seats
        self.__keys = keys
        self.tokens = 0

    @classmethod
    def from_env(cls) -> Ledger:
        now = datetime.now(timezone.utc)
        seats: list[Seat] = []
        keys: dict[str, str] = {}

        if gemini_key := os.environ.get("GEMINI_API_KEY"):
            endpoint = "https://generativelanguage.googleapis.com/v1beta/openai"
            for model in ("gemini-3.5-flash-lite", "gemini-3.6-flash"):
                sid = f"gemini:{model}"
                seats.append(Seat(sid, "gemini", endpoint, model, "quota", 500_000,
                                  now + timedelta(hours=24), now + timedelta(days=30),
                                  "gemini-project-default", False))
                keys[sid] = gemini_key

        if nim_key := os.environ.get("NVIDIA_NIM_API_KEY"):
            sid = "nim:nvidia/nemotron-3-nano-30b-a3b"
            seats.append(Seat(sid, "nim", "https://integrate.api.nvidia.com/v1",
                              "nvidia/nemotron-3-nano-30b-a3b", "quota", 500_000,
                              now + timedelta(hours=12), now + timedelta(days=7),
                              "nim-org-default", False))
            keys[sid] = nim_key

        if openrouter_key := os.environ.get("OPENROUTER_API_KEY"):
            sid = "openrouter:openai"
            seats.append(Seat(sid, "openai", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini",
                              "api", 1_000_000, now + timedelta(hours=1),
                              now + timedelta(days=365), "openrouter-default", False))
            keys[sid] = openrouter_key

        return cls(seats, keys)

    def credential_for_proxy(self, seat_id: str) -> str:
        return self.__keys[seat_id]

    def candidates(self, models: list[str], predicted_gap: float) -> list[Seat]:
        rank = {model: n for n, model in enumerate(models)}
        out = [s for s in self.seats if not s.presence and s.model in rank and
               (s.cache_window >= predicted_gap or s.kind == "api" or s.provider == "nim")]
        return sorted(out, key=lambda s: (rank[s.model], min(s.reset_at, s.expires_at)))

    def mark_boundary_presence(self, boundary: str, live: bool) -> None:
        for s in self.seats:
            if s.cache_boundary == boundary:
                s.presence = live

    def reserve(self, seat: Seat, amount: int) -> bool:
        return seat.remaining >= amount

    def meter(self, seat: Seat, tokens: int) -> None:
        seat.remaining = max(0, seat.remaining - tokens)
        self.tokens += tokens

    def cold_cost(self, provider: str, prefix_tokens: int, warm: bool = True) -> dict[str, float]:
        row = CACHE_TABLE.get(provider, {"window_s": 0, "read_x": 1.0, "write_x": 1.0, "price_in_m": 1.0})
        cold = (prefix_tokens / 1e6) * row["price_in_m"]
        hot = cold * row["read_x"] if warm else cold
        return {"cold": cold, "warm": hot, "penalty": cold - hot}
