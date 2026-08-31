---
name: rate_limiter
description: Token-bucket rate limiter with an injectable clock and per-key buckets.
module: rate_limiter
entrypoint: TokenBucket
tags: [python, concurrency, algorithms]
---

Build a token-bucket rate limiter in `rate_limiter.py` exposing `TokenBucket(rate: float, capacity: int, clock=time.monotonic)`.

- `allow(key: str, cost: int = 1) -> bool` consumes `cost` tokens from `key`'s bucket if available, else returns `False` without consuming.
- Each key has its own bucket, starting full at `capacity`.
- Tokens refill continuously at `rate` per second, never exceeding `capacity`.
- `tokens(key) -> float` reports the current balance (after refill) without consuming.
- `rate` and `capacity` must be positive; raise `ValueError` otherwise.
- `clock` is injectable so tests never sleep.

Write your own unit tests in `test_rate_limiter.py`.
