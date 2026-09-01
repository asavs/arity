"""Arity cache economics — the one Axiom 7 table, and the only one.

Source of record: the wiki's Axiom 7, "How long a kernel wants to live", at
`arity/.wiki/axioms.md` (checked out here as `C:/Users/asas/Projects/arity/.wiki/axioms.md`),
summarising `.wiki/research/2026-08-27-prompt-cache-economics-codex.md`. Every number below
comes from those two documents. When they change, this file changes; nothing else in Arity
carries a second copy of them.

The wiki's cold-vs-warm penalty column is *derived*, not stored: a cold turn pays
`input_price_per_m`, the warm read it replaces pays `input_price_per_m *
cache_read_multiplier`, and the difference over a 100k prefix is the wiki's
$0.90 / $0.36 / $0.18 / $0.15 for Anthropic / OpenAI / Gemini / xAI. `CacheEconomics.cold_cost`
computes it and `tests/test_pulse.py` holds it to the published column.

Cache *write* multipliers (1.25x for a 5-minute Anthropic or 30-minute OpenAI write, 2.00x for
Anthropic's 1-hour window) are in the research doc and deliberately absent here: nothing in
Arity prices a write yet, and unread numbers are how the previous copies of this table drifted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProviderCache:
    """One provider's assured cache terms, priced on the flagship the wiki priced."""

    warm_window_seconds: float    # Assured window only; 0.0 means the provider guarantees none
    cache_read_multiplier: float  # Warm read price as a fraction of normal input
    input_price_per_m: float      # Reference dollars per 1M normal input tokens
    flagship: str                 # The model those prices are quoted on
    window_note: str              # What the wiki's "warm window" column actually says


# Keyed by the provider strings seats are registered under; see ALIASES for the rest.
PROVIDER_CACHE: dict[str, ProviderCache] = {
    "anthropic": ProviderCache(
        warm_window_seconds=300.0,
        cache_read_multiplier=0.10,
        input_price_per_m=10.00,
        flagship="claude-fable-5",
        window_note="5 min sliding, refreshed by every read; 1 h opt-in at a 2x write",
    ),
    "openai": ProviderCache(
        warm_window_seconds=1800.0,
        cache_read_multiplier=0.10,
        input_price_per_m=4.00,
        flagship="gpt-5.6-sol",
        window_note="at least 30 min guaranteed; whether a read refreshes it is unverified",
    ),
    "google": ProviderCache(
        warm_window_seconds=0.0,
        cache_read_multiplier=0.10,
        input_price_per_m=2.00,
        flagship="gemini-3.1-pro",
        window_note="implicit caching is opportunistic; an explicit cache is a TTL you buy at $0.45/h per 100k",
    ),
    "xai": ProviderCache(
        warm_window_seconds=0.0,
        cache_read_multiplier=0.25,
        input_price_per_m=2.00,
        flagship="grok-4.6",
        window_note="none guaranteed; entries are evictable at any time",
    ),
    "openrouter": ProviderCache(
        warm_window_seconds=0.0,
        cache_read_multiplier=0.10,
        input_price_per_m=4.00,
        flagship="openai/gpt-5.6-sol",
        window_note="the routed provider's window; priced on the wiki's routed example",
    ),
    "nvidia": ProviderCache(
        warm_window_seconds=0.0,
        cache_read_multiplier=1.00,
        input_price_per_m=0.05,
        flagship="nvidia/nemotron-3-nano-30b-a3b",
        window_note="outside the wiki: NIM bills no separate cached-input rate, so a lost prefix costs latency, not dollars",
    ),
}

# A provider the table does not carry promises nothing and is priced at a round reference rate.
UNKNOWN = ProviderCache(
    warm_window_seconds=0.0,
    cache_read_multiplier=0.10,
    input_price_per_m=1.00,
    flagship="(unknown)",
    window_note="provider absent from the Axiom 7 table; assume nothing is assured",
)

ALIASES: dict[str, str] = {
    "claude": "anthropic",
    "chatgpt": "openai",
    "codex": "openai",
    "gemini": "google",
    "google-api": "google",
    "google-antigravity": "google",
    "grok": "xai",
    "nim": "nvidia",
}


def lookup(provider: str) -> Optional[ProviderCache]:
    """The wiki's terms for a provider, or None when it names one the table does not carry."""
    key = (provider or "").strip().lower()
    return PROVIDER_CACHE.get(ALIASES.get(key, key))


def profile(provider: str) -> ProviderCache:
    """Terms to price with: the provider's own, or the assume-nothing UNKNOWN row."""
    return lookup(provider) or UNKNOWN
