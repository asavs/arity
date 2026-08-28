"""Tests for arity pulse engine, economic keepalives, and quota harvesting."""
from __future__ import annotations

import unittest

from arity.ledger import Seat, SeatLedger
from arity.pulse import CacheEconomics, CadenceModel, PulseEngine


class TestPulseEngine(unittest.TestCase):
    def setUp(self):
        self.pulse = PulseEngine(keepalive_text="hi luv u")
        self.seat_openai = Seat(
            id="gpt-seat",
            provider="openai",
            endpoint="https://api.openai.com/v1",
            model="gpt-4o",
            warm_window_seconds=1800,  # 30 minutes
        )

    def test_cache_economics_calculations(self):
        # 100k prefix on OpenAI ($2.50/M, 1.25x write, 0.10x read)
        cold_cost = CacheEconomics.cold_cost("openai", 100_000)
        # Cold cost = (100k/1M) * 2.50 * (1.25 - 0.10) = 0.1 * 2.50 * 1.15 = 0.2875
        self.assertAlmostEqual(cold_cost, 0.2875, places=4)

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


if __name__ == "__main__":
    unittest.main()
