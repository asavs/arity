"""Tests for arity pulse engine, economic keepalives, and quota harvesting."""
from __future__ import annotations

import unittest

from arity import cache_economics
from arity.ledger import Seat, SeatLedger
from arity.pulse import CacheEconomics, CadenceModel, PulseEngine


class TestPulseEngine(unittest.TestCase):
    def setUp(self):
        self.pulse = PulseEngine(keepalive_text="hi luv u")
        self.seat_openai = Seat(
            id="gpt-seat",
            provider="openai",
            endpoint="https://api.openai.com/v1",
            model="gpt-5.6-sol",
            warm_window_seconds=1800,  # 30 minutes
        )

    def test_cache_economics_calculations(self):
        # 100k prefix on OpenAI ($4/M input, 0.10x read)
        cold_cost = CacheEconomics.cold_cost("openai", 100_000)
        # Cold cost = (100k/1M) * 4.00 * (1 - 0.10) = 0.1 * 4.00 * 0.90 = 0.36
        self.assertAlmostEqual(cold_cost, 0.36, places=4)

        # Ping cost is tiny (5 tokens)
        ping_cost = CacheEconomics.ping_cost("openai")
        self.assertLess(ping_cost, 0.0001)

    def test_cadence_probability_decay(self):
        # Immediate return
        self.assertEqual(CadenceModel.predict_p_return(0, 1800), 1.0)

        # Midway through window
        p_mid = CadenceModel.predict_p_return(900, 1800)
        self.assertGreater(p_mid, 0.0)
        self.assertLess(p_mid, 1.0)

        # Expired window
        self.assertEqual(CadenceModel.predict_p_return(1800, 1800), 0.0)

    def test_keepalive_vs_let_die_decision(self):
        # Scenario 1: Warm 100k prefix, 2 minutes idle -> Keepalive Ping "hi luv u"
        action_keep = self.pulse.evaluate_session(
            session_id="sess_warm",
            seat=self.seat_openai,
            seconds_idle=120.0,
            prefix_tokens=100_000,
        )
        self.assertEqual(action_keep.kind, "keepalive_ping")
        self.assertEqual(action_keep.message, "hi luv u")

        # Scenario 2: Tiny 10-token prefix, 25 minutes idle -> Let Die
        action_die = self.pulse.evaluate_session(
            session_id="sess_cold",
            seat=self.seat_openai,
            seconds_idle=1500.0,
            prefix_tokens=10,
        )
        self.assertEqual(action_die.kind, "let_die")

    def test_harvest_expiring_quota(self):
        now = 50000.0
        seat_expiring = Seat(
            id="gemini-expiring-soon",
            provider="gemini",
            endpoint="https://api.google.com",
            model="gemini-flash",
            kind="quota",
            total_allowance=1_000_000,
            remaining=400_000,
            reset_deadline=now + 1200,  # 20 minutes left
        )
        seat_safe = Seat(
            id="gemini-safe",
            provider="gemini",
            endpoint="https://api.google.com",
            model="gemini-flash",
            kind="quota",
            total_allowance=1_000_000,
            remaining=400_000,
            reset_deadline=now + 86400,  # 24 hours left
        )

        ledger = SeatLedger(initial_seats=[seat_expiring, seat_safe], auto_seed=False)
        actions = self.pulse.scan_expiring_seats(ledger, now=now, harvest_threshold_seconds=1800)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "harvest_quota")
        self.assertEqual(actions[0].target_id, "gemini-expiring-soon")


class TestAxiom7Table(unittest.TestCase):
    """The wiki's Axiom 7 table, held to by the code that prices from it."""

    def test_cold_cost_reproduces_the_published_penalty_column(self):
        # arity/.wiki/axioms.md, Axiom 7: cold-vs-warm penalty on a 100k prefix.
        published = {
            "anthropic": 0.90,
            "openai": 0.36,
            "google": 0.18,
            "xai": 0.15,
        }
        for provider, penalty in published.items():
            with self.subTest(provider=provider):
                self.assertAlmostEqual(CacheEconomics.cold_cost(provider, 100_000), penalty, places=6)

    def test_assured_warm_windows_match_the_published_column(self):
        # 5 min sliding / at least 30 min / opportunistic only / none guaranteed.
        self.assertEqual(cache_economics.profile("anthropic").warm_window_seconds, 300.0)
        self.assertEqual(cache_economics.profile("openai").warm_window_seconds, 1800.0)
        self.assertEqual(cache_economics.profile("google").warm_window_seconds, 0.0)
        self.assertEqual(cache_economics.profile("xai").warm_window_seconds, 0.0)

    def test_every_seeded_provider_resolves(self):
        # Provider strings SeatLedger._seed_from_env registers seats under; each has to find a
        # row, or that seat silently keeps a warm window nobody sourced.
        for provider in ("google", "openai", "xai", "anthropic", "google-api", "nvidia"):
            with self.subTest(provider=provider):
                self.assertIsNotNone(cache_economics.lookup(provider))

    def test_unknown_provider_assumes_nothing(self):
        self.assertIsNone(cache_economics.lookup("brand-new-lab"))
        self.assertEqual(cache_economics.profile("brand-new-lab").warm_window_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
