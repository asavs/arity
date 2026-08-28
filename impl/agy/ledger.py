"""ledger.py - seat registry, axiom-7 cache metrics, and human presence tracking."""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Any

AXIOM7_CACHE: dict[str, dict[str, Any]] = {
    "gemini": {
        "window_tokens": 1048576,
        "cache_read_multiplier": 0.25,
        "cache_write_multiplier": 1.00,
        "input_cost_per_m": 0.075,
        "output_cost_per_m": 0.300,
        "cache_read_per_m": 0.01875,
        "ping_cost": 0.00005,
        "cold_cost": 0.00200,
    },
    "nim": {
        "window_tokens": 131072,
        "cache_read_multiplier": 0.50,
        "cache_write_multiplier": 1.00,
        "input_cost_per_m": 0.100,
        "output_cost_per_m": 0.400,
        "cache_read_per_m": 0.05000,
        "ping_cost": 0.00008,
        "cold_cost": 0.00300,
    },
    "openai": {
        "window_tokens": 128000,
        "cache_read_multiplier": 0.50,
        "cache_write_multiplier": 1.25,
        "input_cost_per_m": 0.150,
        "output_cost_per_m": 0.600,
        "cache_read_per_m": 0.07500,
        "ping_cost": 0.00010,
        "cold_cost": 0.00400,
    },
}


@dataclass
class Seat:
    seat_id: str
    provider: str
    endpoint: str
    model: str
    _api_key: str = field(repr=False)
    presence: bool = False
    active_sessions: int = 0
    total_calls: int = 0
    total_tokens: int = 0

    @property
    def key_preview(self) -> str:
        if not self._api_key:
            return "none"
        return f"{self._api_key[:4]}...{self._api_key[-2:]}" if len(self._api_key) > 6 else "***"


@dataclass
class Ledger:
    seats: dict[str, Seat] = field(default_factory=dict)
    total_calls: int = 0
    total_tokens: int = 0

    def register_seat(
        self,
        seat_id: str,
        provider: str,
        endpoint: str,
        model: str,
        api_key: str,
        presence: bool = False,
    ) -> Seat:
        seat = Seat(
            seat_id=seat_id,
            provider=provider,
            endpoint=endpoint,
            model=model,
            _api_key=api_key,
            presence=presence,
        )
        self.seats[seat_id] = seat
        return seat

    def seed_from_env(self) -> int:
        count = 0
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            self.register_seat(
                "gemini-flash-1",
                "gemini",
                "https://generativelanguage.googleapis.com/v1beta/openai",
                "gemini-3.6-flash",
                gemini_key,
            )
            self.register_seat(
                "gemini-lite-1",
                "gemini",
                "https://generativelanguage.googleapis.com/v1beta/openai",
                "gemini-3.5-flash-lite",
                gemini_key,
            )
            count += 2

        nim_key = os.getenv("NVIDIA_NIM_API_KEY")
        if nim_key:
            self.register_seat(
                "nim-nano-1",
                "nim",
                "https://integrate.api.nvidia.com/v1",
                "nvidia/nemotron-3-nano-30b-a3b",
                nim_key,
            )
            count += 1

        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            self.register_seat(
                "openrouter-main-1",
                "openai",
                "https://openrouter.ai/api/v1",
                "meta-llama/llama-3.3-70b-instruct",
                openrouter_key,
            )
            count += 1
        return count

    def mark_presence(self, seat_id: str, live: bool) -> None:
        if seat_id in self.seats:
            self.seats[seat_id].presence = live

    def get_available_seats(self, provider: str | None = None) -> list[Seat]:
        return [
            seat
            for seat in self.seats.values()
            if not seat.presence and (provider is None or seat.provider == provider)
        ]

    def record_usage(self, seat_id: str, prompt_tokens: int, completion_tokens: int) -> None:
        tokens = prompt_tokens + completion_tokens
        self.total_calls += 1
        self.total_tokens += tokens
        if seat_id in self.seats:
            seat = self.seats[seat_id]
            seat.total_calls += 1
            seat.total_tokens += tokens
