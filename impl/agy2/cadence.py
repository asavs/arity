"""cadence.py - Axiom 7 cache economics, predicted gaps, and cold costs."""

from __future__ import annotations
import time
from statistics import median
from typing import Any

CACHE_TABLE: dict[str, dict[str, float]] = {
    "anthropic": {
        "window_s": 300.0,
        "read_x": 0.10,
        "write_x": 1.0,
        "storage_per_hr": 0.0,
        "price_in_per_M": 3.0,
    },
    "openai": {
        "window_s": 1800.0,
        "read_x": 0.10,
        "write_x": 1.0,
        "storage_per_hr": 0.0,
        "price_in_per_M": 2.5,
    },
    "gemini": {
        "window_s": 300.0,
        "read_x": 0.10,
        "write_x": 1.0,
        "storage_per_hr": 4.5,
        "price_in_per_M": 2.0,
    },
    "nim": {
        "window_s": 300.0,
        "read_x": 0.10,
        "write_x": 1.0,
        "storage_per_hr": 0.0,
        "price_in_per_M": 1.0,
    },
    "xai": {
        "window_s": 0.0,
        "read_x": 0.25,
        "write_x": 1.0,
        "storage_per_hr": 0.0,
        "price_in_per_M": 2.0,
    },
}


def cold_cost(kernel: Any) -> dict[str, float]:
    """Calculate cold restart cost vs warm cache read penalty."""
    provider = getattr(kernel.seat, "provider", "gemini")
    t = CACHE_TABLE.get(provider, CACHE_TABLE["gemini"])
    prefix_tokens = getattr(kernel, "prefix_tokens", 10_000)
    prefix_M = prefix_tokens / 1e6

    cold = prefix_M * t["price_in_per_M"]
    now = time.time()
    if now > getattr(kernel, "cache_expires_at", now):
        warm = cold
    else:
        warm = prefix_M * t["price_in_per_M"] * t["read_x"]

    penalty = max(0.0, cold - warm)
    expires_in = max(0.0, getattr(kernel, "cache_expires_at", now) - now)
    return {
        "cold": cold,
        "warm": warm,
        "penalty": penalty,
        "expires_in": expires_in,
    }


def predict(convo: Any) -> float:
    """Predict inter-message gap in seconds (p50)."""
    gaps = getattr(convo, "recent_gaps", [])
    if not gaps:
        kind = getattr(convo, "kind", "dm")
        prior = {"call": 5.0, "dm": 600.0, "project": 3600.0}
        return prior.get(kind, 600.0)
    return float(median(gaps))


def p_return(cache_expires_at: float, recent_gaps: list[float]) -> float:
    """Empirical probability that the user speaks before cache expires."""
    now = time.time()
    remaining_window = cache_expires_at - now
    if remaining_window <= 0:
        return 0.0
    if not recent_gaps:
        return 0.7
    hits = sum(1 for g in recent_gaps if g <= remaining_window)
    return hits / len(recent_gaps)
