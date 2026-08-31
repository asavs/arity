"""Hidden acceptance tests for the rate_limiter task. Expects `from rate_limiter import TokenBucket`."""
import pytest

from rate_limiter import TokenBucket


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_bucket_starts_full_and_drains():
    tb = TokenBucket(rate=1.0, capacity=3, clock=Clock())
    assert [tb.allow("k") for _ in range(4)] == [True, True, True, False]


def test_refills_at_rate_and_caps_at_capacity():
    clock = Clock()
    tb = TokenBucket(rate=2.0, capacity=4, clock=clock)
    for _ in range(4):
        tb.allow("k")
    clock.t += 1.0
    assert tb.tokens("k") == pytest.approx(2.0)
    clock.t += 100.0
    assert tb.tokens("k") == pytest.approx(4.0)


def test_keys_are_independent():
    tb = TokenBucket(rate=1.0, capacity=1, clock=Clock())
    assert tb.allow("a")
    assert tb.allow("b")
    assert not tb.allow("a")


def test_insufficient_cost_does_not_consume():
    tb = TokenBucket(rate=1.0, capacity=2, clock=Clock())
    assert not tb.allow("k", cost=3)
    assert tb.tokens("k") == pytest.approx(2.0)
    assert tb.allow("k", cost=2)


def test_invalid_parameters_raise():
    with pytest.raises(ValueError):
        TokenBucket(rate=0, capacity=1)
    with pytest.raises(ValueError):
        TokenBucket(rate=1, capacity=0)
