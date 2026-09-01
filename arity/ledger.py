"""Arity ledger — seat registry, quota reset management, and presence tracking.

Axiom 3: Provider quota remainders — use seats about to reset first.
Axiom 36: Presence — a seat a human is live on is never chosen for a fresh cast.
Axiom 7: Prompt cache boundary preservation.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import cache_economics

# A lock is held by a person, and people close laptops. Without an expiry, a session that dies
# before releasing its seat would withhold that seat from casting forever.
PRESENCE_TTL_SECONDS = 12 * 3600.0


@dataclass
class Seat:
    """A model capacity slice: provider, model, harness, and quota state."""
    provider: str                      # "google", "openai", "xai", "anthropic", "nvidia"
    model: str                         # "gemini-3.6-flash", "gpt-5.6-sol", "grok-4.5", "claude-3-7-sonnet"
    harness: str = "arity"             # "arity" remains accepted as the legacy Arity harness ID
    account: Optional[str] = None      # Optional account email (e.g. for multi-account Google)
    endpoint: str = ""                 # Optional base URL / endpoint
    kind: str = "quota"                # "quota" (subscription window) | "metered_api" (pay-per-token)
    total_allowance: float = 2_000_000.0
    remaining: float = 2_000_000.0
    cycle_seconds: float = 86400.0     # 24h cycle
    reset_deadline: float = 0.0        # Unix timestamp when quota resets
    base_price_per_m: float = 0.0001   # Reference cost per 1M tokens
    warm_window_seconds: float = 300.0 # Assured warm cache TTL (seeded seats: cache_economics)
    presence: bool = False             # True if human or active session is currently typing here
    workspace_boundary: str = "default"
    api_key: Optional[str] = None
    id: Optional[str] = None

    def __post_init__(self):
        if not self.id:
            if self.account:
                slug = self.account.split("@")[0] if "@" in self.account else self.account
                slug_clean = "".join(c for c in slug if c.isalnum() or c in "-_")
                self.id = f"{self.provider}:{slug_clean}:{self.model}"
            else:
                self.id = f"{self.provider}:{self.model}"
    def time_to_reset(self, now: float) -> float:
        """Seconds remaining until quota reset deadline."""
        return max(0.0, self.reset_deadline - now)

    def effective_cost(self, now: float) -> float:
        """Calculate dynamic effective cost weight (Axiom 3).

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

        urgency = fraction_quota_remaining / fraction_time_left
        decay_factor = min(0.999, urgency * 0.5)
        return max(0.0001, self.base_price_per_m * (1.0 - decay_factor))


ANTIGRAVITY_MODELS = {
    # ledger model name -> name the quota endpoint reports
    "gemini-3.6-flash": "gemini-3-flash-agent",
    "claude-opus-4.6": "claude-opus-4-6-thinking",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "gpt-oss-120b": "gpt-oss-120b-medium",
}


def _parse_iso(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


class SeatLedger:
    """Manages the pool of model seats, quota balances, and presence locks."""

    @staticmethod
    def _antigravity_quota(store: Any, key: str, acc: dict[str, Any]) -> dict[str, Any]:
        """Live per-model quota for one account; {} when unreachable (seats then keep defaults)."""
        if os.environ.get("ARITY_SKIP_QUOTA", os.environ.get("ARITY_SKIP_QUOTA")):
            return {}
        try:
            from .auth import fetch_antigravity_quota
            fresh = store.refresh_if_needed(key) or acc
            if fresh.get("access") and fresh.get("projectId"):
                return fetch_antigravity_quota(fresh["access"], fresh["projectId"]) or {}
        except Exception:
            pass
        return {}

    def __init__(
        self,
        initial_seats: Optional[list[Seat]] = None,
        auto_seed: bool = True,
        presence_path: Optional[Path] = None,
    ):
        self._seats: dict[str, Seat] = {}
        self.presence_path = Path(presence_path) if presence_path else (Path.home() / ".arity" / "presence.json")
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
            kind_order = 0 if s.kind == "quota" else 1
            reset_in = s.time_to_reset(curr_time) if s.kind == "quota" else 999999.0
            return (kind_order, reset_in, s.effective_cost(curr_time))

        return sorted(pool, key=sort_key)

    def meter(self, seat_id: str, tokens: float) -> None:
        """Deduct token usage from seat balance."""
        seat = self._seats.get(seat_id)
        if seat and seat.kind == "quota":
            seat.remaining = max(0.0, seat.remaining - tokens)

    def read_presence_locks(self, now: Optional[float] = None) -> dict[str, float]:
        """Seat id -> lock expiry, for locks that have not aged out."""
        curr_time = now if now is not None else time.time()
        try:
            raw = json.loads(self.presence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        locks: dict[str, float] = {}
        for seat_id, expires in raw.items():
            try:
                expiry = float(expires)
            except (TypeError, ValueError):
                continue
            if expiry > curr_time:
                locks[str(seat_id)] = expiry
        return locks

    def set_presence(self, seat_id: str, is_present: bool, now: Optional[float] = None) -> bool:
        """Set or release human presence on a seat, and record it for other processes.

        A lock has to outlive the process that took it: `arity lock` exits immediately, and
        every later process re-seeds its ledger from scratch. Returns False when this ledger
        has no such seat, so a caller can say so rather than reporting a lock it never took.
        """
        seat = self._seats.get(seat_id)
        if not seat:
            return False
        seat.presence = is_present
        curr_time = now if now is not None else time.time()
        locks = self.read_presence_locks(curr_time)
        if is_present:
            locks[seat_id] = curr_time + PRESENCE_TTL_SECONDS
        else:
            locks.pop(seat_id, None)
        self.presence_path.parent.mkdir(parents=True, exist_ok=True)
        self.presence_path.write_text(json.dumps(locks, indent=2), encoding="utf-8")
        return True

    def _apply_persisted_presence(self, now: Optional[float] = None) -> None:
        """Re-apply locks taken by other processes, so every seeded ledger honors them."""
        for seat_id in self.read_presence_locks(now):
            seat = self._seats.get(seat_id)
            if seat:
                seat.presence = True

    def _seed_from_env(self) -> None:
        now = time.time()
        default_reset = now + 86400.0  # 24h from now

        try:
            from .auth import TokenStore
            store = TokenStore()

            # 1. Google Antigravity: one seat per (account, model). The backend keeps two
            #    separate quotas - Gemini, and Claude+GPT-OSS - so a Gemini 429 says nothing
            #    about Claude. Seed each seat's `remaining` from the live quota when reachable.
            agy_accounts = store.get_all_for_provider("google-antigravity")
            for key, acc in agy_accounts:
                email = acc.get("email", "")
                quota = self._antigravity_quota(store, key, acc)
                for model, wire_name in ANTIGRAVITY_MODELS.items():
                    q = quota.get(wire_name) or {}
                    fraction = q.get("remainingFraction")
                    reset = _parse_iso(q.get("resetTime")) or default_reset
                    seat = Seat(
                        provider="google",
                        model=model,
                        harness="omp",
                        account=email,
                        reset_deadline=reset,
                    )
                    if fraction is not None:
                        seat.remaining = seat.total_allowance * float(fraction)
                    elif quota:
                        # Endpoint answered but omitted this model: it is exhausted.
                        seat.remaining = 0.0
                    self.register(seat)

            # 2. OpenAI (ChatGPT backend with Codex CLI fallback)
            codex_creds = store.get_credential("openai-codex")
            if codex_creds:
                self.register(
                    Seat(
                        provider="openai",
                        model="gpt-5.6-sol",
                        harness="codex",
                        reset_deadline=default_reset,
                    )
                )

            # 3. xAI (Grok backend with Grok build fallback)
            xai_creds = store.get_credential("xai-oauth")
            if xai_creds:
                self.register(
                    Seat(
                        provider="xai",
                        model="grok-4.5",
                        harness="grok",
                        reset_deadline=default_reset,
                    )
                )

            # 4. Anthropic (Claude Code harness)
            if shutil.which("claude") or store.get_credential("anthropic"):
                self.register(
                    Seat(
                        provider="anthropic",
                        model="claude-3-7-sonnet",
                        harness="claude",
                        reset_deadline=default_reset,
                    )
                )
        except Exception:
            pass

        # 5. Fallback CLI Harnesses if not already mounted
        if shutil.which("codex") and "openai:gpt-5.6-sol" not in self._seats:
            self.register(
                Seat(
                    provider="openai",
                    model="gpt-5.6-sol",
                    harness="codex",
                    reset_deadline=default_reset,
                )
            )

        if shutil.which("omp") and not any(s.provider == "google" for s in self._seats.values()):
            self.register(
                Seat(
                    provider="google",
                    model="gemini-3.6-flash",
                    harness="omp",
                    reset_deadline=default_reset,
                )
            )

        # 6. Metered APIs
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if gemini_key:
            self.register(
                Seat(
                    provider="google-api",
                    model="gemini-3.6-flash",
                    harness="arity",
                    kind="metered_api",
                    base_price_per_m=0.10,
                    api_key=gemini_key,
                )
            )

        nim_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_API_KEY")
        if nim_key:
            self.register(
                Seat(
                    provider="nvidia",
                    model="nvidia/nemotron-3-nano-30b-a3b",
                    harness="arity",
                    kind="metered_api",
                    base_price_per_m=0.05,
                    api_key=nim_key,
                )
            )

        # A seeded seat takes its provider's assured warm window (Axiom 7). Seats a caller
        # hands to __init__ are left alone: only this path invents the value.
        for seat in self._seats.values():
            terms = cache_economics.lookup(seat.provider)
            if terms is not None:
                seat.warm_window_seconds = terms.warm_window_seconds

        # Last, so it covers every seat any branch above registered.
        self._apply_persisted_presence()
