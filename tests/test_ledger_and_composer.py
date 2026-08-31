"""Tests for the Arity seat ledger and casting composer."""
from __future__ import annotations

import unittest

from arity.composer import CastingComposer
from arity.ledger import Seat, SeatLedger
from arity.roles import BUILDER_ROLE, VOICE_ROLE


class TestSeatLedgerAndComposer(unittest.TestCase):
    def setUp(self):
        self.now = 10000.0
        # Seat 1: Expiring in 1 hour (high urgency)
        self.seat_expiring_soon = Seat(
            id="gemini-expiring",
            provider="gemini",
            endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini-3.6-flash",
            kind="quota",
            total_allowance=1_000_000,
            remaining=800_000,
            cycle_seconds=86400,
            reset_deadline=self.now + 3600,  # 1 hour left
            base_price_per_m=0.10,
        )

        # Seat 2: Expiring in 20 hours (low urgency)
        self.seat_expiring_late = Seat(
            id="gpt-late",
            provider="openai",
            endpoint="https://api.openai.com/v1",
            model="gpt-4o",
            kind="quota",
            total_allowance=1_000_000,
            remaining=800_000,
            cycle_seconds=86400,
            reset_deadline=self.now + 72000,  # 20 hours left
            base_price_per_m=2.50,
        )

        # Seat 3: Pay-per-token API (no deadline)
        self.seat_metered = Seat(
            id="openrouter-metered",
            provider="openrouter",
            endpoint="https://openrouter.ai/api/v1",
            model="meta-llama/llama-3.3-70b-instruct",
            kind="metered_api",
            base_price_per_m=0.40,
        )

        self.ledger = SeatLedger(
            initial_seats=[self.seat_expiring_soon, self.seat_expiring_late, self.seat_metered],
            auto_seed=False,
        )
        self.composer = CastingComposer(ledger=self.ledger)

    def test_effective_cost_decay_on_expiring_quota(self):
        # The seat expiring in 1 hour has high urgency, so effective cost drops substantially
        cost_soon = self.seat_expiring_soon.effective_cost(self.now)
        cost_late = self.seat_expiring_late.effective_cost(self.now)

        self.assertLess(cost_soon, self.seat_expiring_soon.base_price_per_m)
        self.assertLess(cost_soon, cost_late)

        # Metered seat cost remains constant
        self.assertEqual(self.seat_metered.effective_cost(self.now), 0.40)

    def test_dying_soonest_ranking(self):
        ranked = self.ledger.dying_soonest(now=self.now)
        # Quota seats come before metered, and 1h seat comes before 20h seat
        self.assertEqual(ranked[0].id, "gemini-expiring")
        self.assertEqual(ranked[1].id, "gpt-late")
        self.assertEqual(ranked[2].id, "openrouter-metered")

    def test_presence_locking_prevents_casting(self):
        # Mark gemini-expiring as presence=True (Asa is typing there)
        self.ledger.set_presence("gemini-expiring", True)

        decision = self.composer.cast(BUILDER_ROLE, "Build a tool", now=self.now)
        # Must NOT pick gemini-expiring
        self.assertNotEqual(decision.primary_seat.id, "gemini-expiring")

        # Unlock presence
        self.ledger.set_presence("gemini-expiring", False)
        decision_unlocked = self.composer.cast(BUILDER_ROLE, "Build a tool", now=self.now)
        self.assertEqual(decision_unlocked.primary_seat.id, "gemini-expiring")

    def test_multi_candidate_selection_for_terrarium(self):
        decision = self.composer.cast(
            BUILDER_ROLE,
            "Build a scraper",
            candidates_count=2,
            now=self.now,
        )
        self.assertEqual(len(decision.candidates), 2)
        self.assertEqual(decision.candidates[0].id, "gemini-expiring")


if __name__ == "__main__":
    unittest.main()
