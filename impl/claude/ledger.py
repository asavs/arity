"""Seats, and what a cache is worth.

Every account is a row, and a row is always a best guess with a confidence stapled to it —
providers don't say. The axiom 7 table lives here too, because the table is the thing that
changes when a provider moves its prices, and nothing else should have to care.
"""

import json
import os
import time
import urllib.request

# The axiom 7 table as data. price_in_per_M is back-derived from the wiki's cold-vs-warm penalty
# on a 100k prefix: penalty = 0.1M * price_in * (1 - read_x); gemini's 0.18 = 0.1 * 2.00 * 0.9.
# verified=False means the wiki says unverified and so do we.
CACHE_TABLE = {
    "anthropic": dict(window_s=300, read_x=0.10, write_x=1.25, storage_per_M_hr=0.0,
                      price_in_per_M=10.00, price_out_per_M=50.00, verified=True),
    "openai": dict(window_s=1800, read_x=0.10, write_x=1.00, storage_per_M_hr=0.0,
                   price_in_per_M=4.00, price_out_per_M=16.00, verified=False),
    "gemini": dict(window_s=0, read_x=0.10, write_x=1.00, storage_per_M_hr=4.50,
                   price_in_per_M=2.00, price_out_per_M=8.00, verified=True),
    "xai": dict(window_s=0, read_x=0.25, write_x=1.00, storage_per_M_hr=0.0,
                price_in_per_M=2.00, price_out_per_M=10.00, verified=True),
    # not in the wiki table: a cheap open-weights seat with no cache discount we've confirmed
    "nim": dict(window_s=0, read_x=1.00, write_x=1.00, storage_per_M_hr=0.0,
                price_in_per_M=0.20, price_out_per_M=0.60, verified=False),
}
# window_s=0 means nothing is assured. Cast still needs a number to compare a gap against, so
# these are ours, made up, and flagged as such.
WORKING_WINDOW = {"gemini": 3600, "xai": 600, "nim": 3600}

SEEDS = [
    ("gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai",
     ["gemini-3.6-flash", "gemini-3.5-flash-lite"], "quota", "gemini:project:default"),
    ("nim", "NVIDIA_NIM_API_KEY", "https://integrate.api.nvidia.com/v1",
     ["nvidia/nemotron-3-nano-30b-a3b"], "quota", "nim:account"),
    ("openai", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
     None, "api", "openrouter:account"),
]
DEFAULT_BUDGET = int(os.environ.get("ARITY_SEAT_BUDGET", "400000"))

def window_s(provider):
    return (CACHE_TABLE.get(provider) or CACHE_TABLE["nim"])["window_s"] \
        or WORKING_WINDOW.get(provider, 600)

class Seat:
    """One account by one model. The unit casting picks from."""

    def __init__(self, provider, endpoint, model, kind, cache_boundary, remaining):
        self.id = "%s/%s" % (provider, model)
        self.provider, self.endpoint, self.model = provider, endpoint.rstrip("/"), model
        self.kind, self.cache_boundary = kind, cache_boundary  # quota|api ; warm-swap group
        self.remaining = remaining             # tokens, our unit for both kinds of seat
        self.reset_at, self.expires_at = time.time() + 3600, time.time() + 86400
        self.presence = False                  # is a human live on this seat right now
        self.reserved = self.used = self.calls = self.recent_429s = 0
        self.last_headers, self.confidence, self.source = {}, 0.2, "seed"

    @property
    def free(self):
        return self.remaining - self.reserved

    @property
    def cache_window(self):
        return window_s(self.provider)

    def price(self):
        return CACHE_TABLE.get(self.provider, CACHE_TABLE["nim"])

class Proxy:
    """The key never enters a kernel. Kernels hand over a seat; the proxy hands back headers."""

    def __init__(self):
        self._keys = {}

    def register(self, seat_id, key):
        self._keys[seat_id] = key

    def copy(self, from_id, to_id):
        self._keys[to_id] = self._keys[from_id]

    def headers(self, seat):
        h = {"Content-Type": "application/json", "Authorization": "Bearer " + self._keys[seat.id]}
        if "openrouter" in seat.endpoint:
            h["HTTP-Referer"], h["X-Title"] = "https://redphone.example/asas", "arity"
        return h

def _openrouter_models(key):
    """OpenRouter's roster moves weekly, so ask it rather than hardcoding a guess."""
    env = os.environ.get("ARITY_OPENROUTER_MODELS")
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/models",
                                     headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=20) as r:
            ids = [m["id"] for m in json.loads(r.read().decode("utf-8")).get("data", [])
                   if isinstance(m.get("id"), str)]
        return [i for i in ids if i.endswith(":free")][:2] or ids[:1]
    except Exception:
        return []

class Ledger:
    RL_KEYS = ("x-ratelimit-remaining-tokens", "x-ratelimit-remaining-requests",
               "x-ratelimit-remaining", "ratelimit-remaining")

    def __init__(self, store):
        self.store, self.seats, self.proxy = store, [], Proxy()

    def seed_from_env(self):
        for provider, envvar, endpoint, models, kind, boundary in SEEDS:
            key = os.environ.get(envvar)
            if not key:
                continue
            for m in (models if models is not None else _openrouter_models(key)):
                s = Seat(provider, endpoint, m, kind, boundary, DEFAULT_BUDGET)
                self.seats.append(s)
                self.proxy.register(s.id, key)
        return self.seats

    def seats_for(self, models):
        out = []
        for m in models:
            out += [s for s in self.seats if s.model == m and s not in out]
        return out

    def dying_soonest(self, seats):
        return sorted(seats, key=lambda s: min(s.reset_at, s.expires_at))

    def clone_seat(self, seat, remaining, suffix="tight"):
        """Same account, same model, less left on it — for standing a kernel next to a wall."""
        s = Seat(seat.provider, seat.endpoint, seat.model, seat.kind,
                 seat.cache_boundary, remaining)
        s.id = seat.id + "#" + suffix
        s.reset_at = time.time() + 1           # dies soonest, so casting reaches for it first
        self.seats.append(s)
        self.proxy.copy(seat.id, s.id)
        return s

    def reserve(self, seat, amount):
        """Hold quota back — usually the one turn a kernel gets to speak last."""
        if seat.free < amount:
            return False
        seat.reserved += amount
        return True

    def release(self, seat, amount):
        seat.reserved = max(0, seat.reserved - amount)

    def meter(self, seat, usage):
        n = int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))
        seat.remaining = max(0, seat.remaining - n)
        seat.used, seat.calls = seat.used + n, seat.calls + 1
        return n

    def probe(self, seat):
        """Learn what leaks: headers, then throttle shape, then our own decayed belief."""
        h = {k.lower(): v for k, v in (seat.last_headers or {}).items()}
        hit = next((k for k in self.RL_KEYS if k in h), None)
        if hit:
            src, conf = "headers:" + hit, 0.7
        elif seat.recent_429s:
            src, conf = "throttle", 0.5
        else:
            src, conf = "decayed:" + seat.source, max(0.05, seat.confidence * 0.9)
        seat.source, seat.confidence = src, conf
        obs = {"seat": seat.id, "at": time.time(), "presence": seat.presence, "source": src,
               "confidence": conf, "remaining_believed": seat.remaining,
               "reported": h.get(hit) if hit else None}
        self.store.append("ledger_probes.jsonl", obs)
        return obs

def cold_cost(kernel, now=None):
    """What it costs if this kernel dies now and the next one has to re-read everything."""
    now = now or time.time()
    t = kernel.seat.price()
    prefix_M = kernel.prefix_tokens / 1e6
    cold = prefix_M * t["price_in_per_M"]
    warm = cold * t["read_x"]
    if now > kernel.cache_expires_at:
        warm = cold                                    # already gone; no discount left to lose
    if t["storage_per_M_hr"]:
        warm += prefix_M * t["storage_per_M_hr"] * 0.25            # gemini charges to hold it
    return {"cold": cold, "warm": warm, "penalty": cold - warm,
            "expires_in": kernel.cache_expires_at - now}
