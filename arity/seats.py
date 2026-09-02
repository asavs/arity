"""Seats: providers, keys, and how much quota is left. Keyed by seat id.

Two readers:
    cast   asks "who has quota for this model?" when choosing a spec
    wire   asks "what URL and key do I use for this seat?" when sending

A seat is either subscription quota (resets on a clock) or API dollars
(never resets). The shape of the routing decision is the same for both,
but the unit differs, so the seat says which kind it is.

Naive version: a table in a JSON file, refreshed by hand or by one call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from . import paths


@dataclass(frozen=True)
class Seat:
    id: str                 # "anthropic-max", "openai-pro", "xai-super"
    provider: str           # "anthropic" | "openai" | "xai" | "gemini"
    kind: str               # "subscription" | "api"
    models: tuple[str, ...] # model ids this seat can serve
    url: str
    key_env: str            # name of the environment variable holding the key
    remaining: float        # fraction of quota left (subscription) or dollars (api)
    resets_at: str | None   # ISO timestamp, subscription only
    warm_window: int = 0    # seconds the provider keeps a cached prefix warm after a
                            # call; 0 means unknown or none, and no keepalive is sent


def all_seats() -> list[Seat]:
    rows = json.loads(paths.seats().read_text())
    return [Seat(**row) for row in rows]


def lookup(seat_id: str) -> Seat:
    """The wire's question."""
    return next(s for s in all_seats() if s.id == seat_id)


def with_quota(model: str) -> list[Seat]:
    """Cast's question: seats that can serve this model and still have something left.

    Ordered so the seat closest to its reset comes first. Quota that is about
    to reset is quota that would otherwise be wasted, so spend it first.
    """
    able = [s for s in all_seats() if model in s.models and s.remaining > 0]
    return sorted(able, key=lambda s: (s.resets_at or "9999", -s.remaining))


def spend(seat_id: str, amount: float) -> None:
    """Record that a call was made. Naive: subtract and rewrite the table."""
    rows = json.loads(paths.seats().read_text())
    for row in rows:
        if row["id"] == seat_id:
            row["remaining"] -= amount
    paths.seats().write_text(json.dumps(rows, indent=2))
