"""Tests for arity seat ledger and casting composer."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from arity import cache_economics
from arity.composer import BROKIE, CASTING_MODES, CHAOS, SMART, CastingComposer
from arity.ledger import PRESENCE_TTL_SECONDS, Seat, SeatLedger
from arity.roles import BUILDER_ROLE, VOICE_ROLE
from arity.scorecard import Scorecard


class _NullStore:
    """A store with no `query`, so Scorecard skips replay and starts empty."""

    def append(self, record) -> None:
        return None


class _MemoryStore:
    """In-memory record store for task-cost estimation tests."""

    def __init__(self, records_by_kind: dict[str, list[dict]]):
        self.records_by_kind = records_by_kind

    def query(self, kind: str, **filters) -> list[dict]:
        """Return records of one kind matching every requested top-level field."""
        return [
            record for record in self.records_by_kind.get(kind, [])
            if all(record.get(key) == value for key, value in filters.items())
        ]


class _ScorecardWithStore:
    """Scorecard-shaped evidence holder for casting tests."""

    def __init__(self, store: _MemoryStore):
        self.store = store


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

        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, True)
        self.ledger = SeatLedger(
            initial_seats=[self.seat_expiring_soon, self.seat_expiring_late, self.seat_metered],
            auto_seed=False,
            presence_path=Path(tmp_dir) / "presence.json",
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
        # Both quota deadlines are ahead of `now`: the ranking above is about time left in a
        # live window, and says nothing about a window that already rolled over.
        for seat in (self.seat_expiring_soon, self.seat_expiring_late):
            self.assertGreater(seat.time_to_reset(self.now), 0.0)

    def test_a_rolled_over_deadline_ranks_last_among_quota_seats(self):
        # An elapsed deadline floors time_to_reset at 0.0 and effective_cost at its minimum, so
        # without an explicit rank the stalest seat looks like the most urgent and the cheapest.
        rolled_over = Seat(
            id="gemini-rolled-over",
            provider="gemini",
            model="gemini-3.6-flash-b",
            kind="quota",
            total_allowance=1_000_000,
            remaining=0.0,
            cycle_seconds=86400,
            reset_deadline=self.now - 60,
        )
        self.ledger.register(rolled_over)
        self.assertEqual(rolled_over.time_to_reset(self.now), 0.0)
        self.assertEqual(rolled_over.effective_cost(self.now), 0.0001)

        # `list_available` deliberately keeps it: past its deadline, quota is presumed restored.
        self.assertIn("gemini-rolled-over", [s.id for s in self.ledger.list_available(now=self.now)])
        ranked = [s.id for s in self.ledger.dying_soonest(now=self.now)]
        self.assertEqual(ranked, ["gemini-expiring", "gpt-late", "gemini-rolled-over", "openrouter-metered"])

    def test_a_rolled_over_seat_is_not_cast_primary_over_a_live_one(self):
        self.ledger.register(Seat(
            id="rolled-over", provider="gemini", model="stale-model", kind="quota",
            remaining=0.0, reset_deadline=self.now - 60,
        ))
        # Chaos is excluded on purpose: it is seeded random, so it may seat anything eligible.
        for mode in (SMART, BROKIE):
            with self.subTest(mode=mode):
                decision = self.composer.cast(
                    BUILDER_ROLE, "Build", candidates_count=1, now=self.now, mode=mode,
                )
                self.assertEqual(decision.primary_seat.id, "gemini-expiring")
        ordered = [s.id for s in self.ledger.dying_soonest(now=self.now)]
        self.assertGreater(ordered.index("rolled-over"), ordered.index("gpt-late"))

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


class CastingEngineTestCase(unittest.TestCase):
    """Three seats with a deliberate conflict between economics and evidence.

    Economically `gemini-expiring` is the obvious pick: it is the cheapest and its quota
    evaporates in an hour. On the evidence `gpt-late` is the obvious pick: it has the best
    standing and it is the most observed. Every ordering test below turns on which of those
    two questions the mode says should decide.
    """

    def setUp(self):
        self.now = 10000.0
        self.gemini = Seat(
            id="gemini-expiring",
            provider="google",
            model="gemini-3.6-flash",
            kind="quota",
            total_allowance=1_000_000,
            remaining=800_000,
            cycle_seconds=86400,
            reset_deadline=self.now + 3600,
            base_price_per_m=0.10,
        )
        self.gpt = Seat(
            id="gpt-late",
            provider="openai",
            model="gpt-4o",
            kind="quota",
            total_allowance=1_000_000,
            remaining=800_000,
            cycle_seconds=86400,
            reset_deadline=self.now + 72000,
            base_price_per_m=2.50,
        )
        self.llama = Seat(
            id="openrouter-metered",
            provider="openrouter",
            model="meta-llama/llama-3.3-70b-instruct",
            kind="metered_api",
            base_price_per_m=0.40,
        )

        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, True)
        self.ledger = SeatLedger(
            initial_seats=[self.gemini, self.gpt, self.llama],
            auto_seed=False,
            presence_path=Path(tmp_dir) / "presence.json",
        )

        # gpt-4o: 3 successes -> standing 13.0, n=3. gemini: 2 -> 12.0, n=2. llama: none.
        self.scorecard = Scorecard(store=_NullStore())
        for _ in range(3):
            self.scorecard.record_verdict(
                role=BUILDER_ROLE.name, model=self.gpt.model, task_id="t", verdict="success"
            )
        for _ in range(2):
            self.scorecard.record_verdict(
                role=BUILDER_ROLE.name, model=self.gemini.model, task_id="t", verdict="success"
            )
        self.composer = CastingComposer(ledger=self.ledger, scorecard=self.scorecard)

    def ids(self, decision) -> list[str]:
        return [s.id for s in decision.candidates]

    def cast(self, **kwargs):
        kwargs.setdefault("now", self.now)
        return self.composer.cast(BUILDER_ROLE, "Build a scraper", **kwargs)


class TestFixturePremises(CastingEngineTestCase):
    def test_the_two_questions_disagree_on_this_fixture(self):
        self.assertEqual(self.scorecard.get_standing(BUILDER_ROLE.name, self.gpt.model), 13.0)
        self.assertEqual(self.scorecard.get_standing(BUILDER_ROLE.name, self.gemini.model), 12.0)
        self.assertEqual(self.scorecard.get_standing(BUILDER_ROLE.name, self.llama.model), 10.0)
        self.assertEqual(self.scorecard.get_observations(BUILDER_ROLE.name, self.gpt.model), 3)
        self.assertEqual(self.scorecard.get_observations(BUILDER_ROLE.name, self.gemini.model), 2)
        self.assertEqual(self.scorecard.get_observations(BUILDER_ROLE.name, self.llama.model), 0)

        # Economics says the opposite of the evidence.
        self.assertLess(
            self.gemini.effective_cost(self.now), self.gpt.effective_cost(self.now)
        )
        self.assertEqual(
            [s.id for s in self.ledger.dying_soonest(now=self.now)],
            ["gemini-expiring", "gpt-late", "openrouter-metered"],
        )


class TestModeOrdering(CastingEngineTestCase):
    def test_smart_orders_by_aptitude_not_by_expiring_quota(self):
        # The blend this replaces gave a sub-hour quota seat +3.0 and charged 2x its dollar
        # cost, which put gemini at 15.0 against gpt-4o's 10.4 on exactly this fixture.
        decision = self.cast(mode=SMART)
        self.assertEqual(decision.primary_seat.id, "gpt-late")
        self.assertEqual(decision.mode, SMART)

    def test_brokie_orders_by_economics_despite_the_evidence(self):
        decision = self.cast(mode=BROKIE, candidates_count=3)
        self.assertEqual(self.ids(decision), ["gemini-expiring", "gpt-late", "openrouter-metered"])

    def test_smart_full_order_is_aptitude_with_economics_only_breaking_ties(self):
        decision = self.cast(mode=SMART, candidates_count=3)
        # gpt(13.0), gemini(12.0), llama(10.0) — strictly the aptitude order here.
        self.assertEqual(decision.candidates[0].id, "gpt-late")
        self.assertIn("gemini-expiring", self.ids(decision))

    def test_economics_breaks_aptitude_ties_in_smart_mode(self):
        # A composer with no scorecard has no evidence at all, so every seat ties on
        # question A and question B decides the whole order.
        blind = CastingComposer(ledger=self.ledger)
        decision = blind.cast(BUILDER_ROLE, "Build", candidates_count=3, now=self.now, mode=SMART)
        self.assertEqual(self.ids(decision), ["gemini-expiring", "gpt-late", "openrouter-metered"])

    def test_aptitude_breaks_economic_ties_in_brokie_mode(self):
        # Two seats identical on every measured axis, different only on evidence.
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, True)
        twins = [
            Seat(
                id=f"twin-{name}",
                provider="openai",
                model=name,
                kind="metered_api",
                base_price_per_m=1.0,
            )
            for name in ("model-a", "model-b")
        ]
        ledger = SeatLedger(
            initial_seats=twins, auto_seed=False, presence_path=Path(tmp_dir) / "p.json"
        )
        card = Scorecard(store=_NullStore())
        card.record_verdict(
            role=BUILDER_ROLE.name, model="model-b", task_id="t", verdict="success"
        )
        composer = CastingComposer(ledger=ledger, scorecard=card)
        decision = composer.cast(BUILDER_ROLE, "Build", candidates_count=2, now=self.now, mode=BROKIE)
        self.assertEqual([s.id for s in decision.candidates], ["twin-model-b", "twin-model-a"])

    def test_unknown_mode_is_refused_rather_than_treated_as_smart(self):
        with self.assertRaises(ValueError) as ctx:
            self.cast(mode="cheapest")
        self.assertIn("cheapest", str(ctx.exception))

    def test_candidates_count_below_one_is_refused(self):
        with self.assertRaises(ValueError):
            self.cast(candidates_count=0)


class TestExplorationSlot(CastingEngineTestCase):
    def test_smart_spends_its_last_slot_on_the_least_observed_seat(self):
        decision = self.cast(mode=SMART, candidates_count=2)
        # Pure aptitude would have taken gemini (12.0) second; the exploration slot takes
        # llama instead, which is the only seat with no evidence behind it at all.
        self.assertEqual(self.ids(decision), ["gpt-late", "openrouter-metered"])
        self.assertIsNotNone(decision.exploration_seat)
        self.assertEqual(decision.exploration_seat.id, "openrouter-metered")

    def test_exploration_slot_does_not_hijack_a_single_candidate_cast(self):
        decision = self.cast(mode=SMART, candidates_count=1)
        self.assertEqual(decision.primary_seat.id, "gpt-late")
        self.assertIsNone(decision.exploration_seat)

    def test_exploration_slot_never_duplicates_an_already_chosen_seat(self):
        decision = self.cast(mode=SMART, candidates_count=3)
        self.assertEqual(len(self.ids(decision)), len(set(self.ids(decision))))
        self.assertEqual(len(decision.candidates), 3)

    def test_brokie_and_chaos_carry_no_exploration_slot(self):
        for mode in (BROKIE, CHAOS):
            with self.subTest(mode=mode):
                decision = self.cast(mode=mode, candidates_count=2, seed=7)
                self.assertIsNone(decision.exploration_seat)

    def test_exploration_follows_the_evidence_when_the_counts_move(self):
        # Give llama more observations than gemini and the exploration slot must switch.
        for _ in range(4):
            self.scorecard.record_verdict(
                role=BUILDER_ROLE.name, model=self.llama.model, task_id="t", verdict="failed"
            )
        decision = self.cast(mode=SMART, candidates_count=2)
        self.assertEqual(decision.exploration_seat.id, "gemini-expiring")


class TestSeeding(CastingEngineTestCase):
    def test_chaos_is_reproducible_under_the_same_seed(self):
        first = self.cast(mode=CHAOS, candidates_count=3, seed=4242)
        second = self.cast(mode=CHAOS, candidates_count=3, seed=4242)
        self.assertEqual(self.ids(first), self.ids(second))
        self.assertEqual(first.seed, 4242)

    def test_chaos_actually_randomizes_across_seeds(self):
        primaries = {self.cast(mode=CHAOS, seed=s).primary_seat.id for s in range(30)}
        self.assertGreater(len(primaries), 1)
        self.assertTrue(primaries <= {"gemini-expiring", "gpt-late", "openrouter-metered"})

    def test_an_unseeded_cast_records_a_seed_that_replays_it(self):
        decision = self.cast(mode=CHAOS, candidates_count=3)
        self.assertIsInstance(decision.seed, int)
        replay = self.cast(mode=CHAOS, candidates_count=3, seed=decision.seed)
        self.assertEqual(self.ids(replay), self.ids(decision))

    def test_every_mode_records_its_seed(self):
        for mode in CASTING_MODES:
            with self.subTest(mode=mode):
                self.assertIsInstance(self.cast(mode=mode).seed, int)

    def test_deterministic_modes_do_not_consume_the_seed(self):
        for mode in (SMART, BROKIE):
            with self.subTest(mode=mode):
                a = self.cast(mode=mode, candidates_count=3, seed=1)
                b = self.cast(mode=mode, candidates_count=3, seed=999999)
                self.assertEqual(self.ids(a), self.ids(b))


class TestRequestedVersusSatisfied(CastingEngineTestCase):
    def _two_provider_ledger(self) -> SeatLedger:
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, True)
        return SeatLedger(
            initial_seats=[
                Seat(id="g-1", provider="google", model="gemini-a", kind="metered_api"),
                Seat(id="g-2", provider="google", model="gemini-b", kind="metered_api"),
                Seat(id="o-1", provider="openai", model="gpt-a", kind="metered_api"),
            ],
            auto_seed=False,
            presence_path=Path(tmp_dir) / "p.json",
        )

    def test_a_satisfied_request_reports_itself_satisfied(self):
        decision = self.cast(candidates_count=3, distinct_on="provider")
        self.assertEqual(decision.requested_count, 3)
        self.assertEqual(decision.satisfied_count, 3)
        self.assertTrue(decision.fully_satisfied)
        self.assertIsNone(decision.shortfall)
        self.assertEqual(decision.distinct_on, "provider")

    def test_an_unsatisfiable_distinctness_request_is_reported_not_padded(self):
        composer = CastingComposer(ledger=self._two_provider_ledger())
        for mode in CASTING_MODES:
            with self.subTest(mode=mode):
                decision = composer.cast(
                    BUILDER_ROLE, "Build", candidates_count=3, now=self.now,
                    mode=mode, seed=3, distinct_on="provider",
                )
                self.assertEqual(decision.requested_count, 3)
                self.assertEqual(decision.satisfied_count, 2)
                self.assertFalse(decision.fully_satisfied)
                # The pool holds a third seat; padding with it would break the request.
                self.assertEqual(
                    len({s.provider for s in decision.candidates}), 2
                )
                self.assertIn("requested 3, satisfied 2", decision.shortfall)
                self.assertIn("distinct provider", decision.shortfall)

    def test_distinctness_on_model_is_served_independently_of_provider(self):
        composer = CastingComposer(ledger=self._two_provider_ledger())
        decision = composer.cast(
            BUILDER_ROLE, "Build", candidates_count=3, now=self.now, distinct_on="model"
        )
        self.assertEqual(decision.satisfied_count, 3)
        self.assertEqual(len({s.model for s in decision.candidates}), 3)
        self.assertIsNone(decision.shortfall)

    def test_no_distinctness_request_means_no_distinctness_policy(self):
        composer = CastingComposer(ledger=self._two_provider_ledger())
        decision = composer.cast(BUILDER_ROLE, "Build", candidates_count=3, now=self.now)
        self.assertEqual(decision.satisfied_count, 3)
        self.assertIsNone(decision.distinct_on)
        self.assertIsNone(decision.shortfall)

    def test_a_short_pool_is_reported_even_with_no_distinctness_request(self):
        decision = self.cast(candidates_count=5)
        self.assertEqual(decision.requested_count, 5)
        self.assertEqual(decision.satisfied_count, 3)
        self.assertIn("requested 5, satisfied 3", decision.shortfall)

    def test_tools_is_not_a_dimension_a_seat_carries(self):
        with self.assertRaises(ValueError) as ctx:
            self.cast(candidates_count=2, distinct_on="tools")
        self.assertIn("terrarium", str(ctx.exception))

    def test_an_unknown_distinctness_dimension_is_refused(self):
        with self.assertRaises(ValueError):
            self.cast(candidates_count=2, distinct_on="temperature")


class TestQuestionBFilters(CastingEngineTestCase):
    def test_a_presence_locked_seat_is_filtered_in_every_mode(self):
        self.ledger.set_presence("gpt-late", True, now=self.now)
        for mode in CASTING_MODES:
            with self.subTest(mode=mode):
                decision = self.cast(mode=mode, candidates_count=3, seed=11)
                self.assertNotIn("gpt-late", self.ids(decision))
                self.assertEqual(decision.satisfied_count, 2)
                self.assertIsNotNone(decision.shortfall)

    def test_an_exhausted_quota_seat_is_filtered_in_every_mode(self):
        self.ledger.meter("gemini-expiring", 800_000)
        self.assertEqual(self.ledger.get("gemini-expiring").remaining, 0.0)
        for mode in CASTING_MODES:
            with self.subTest(mode=mode):
                decision = self.cast(mode=mode, candidates_count=3, seed=11)
                self.assertNotIn("gemini-expiring", self.ids(decision))

    def test_a_fully_blocked_pool_raises_rather_than_casting_nothing(self):
        for seat_id in ("gemini-expiring", "gpt-late", "openrouter-metered"):
            self.ledger.set_presence(seat_id, True, now=self.now)
        with self.assertRaises(RuntimeError):
            self.cast()


class TestTaskQuotaFilter(unittest.TestCase):
    """Historical task cost vetoes quota seats that cannot finish the task."""

    def setUp(self):
        self.now = 10_000.0
        self.ledger = SeatLedger(
            initial_seats=[
                Seat(
                    id="quota-exhausted",
                    provider="quota-exhausted",
                    model="small-quota",
                    kind="quota",
                    remaining=0.0,
                    reset_deadline=self.now + 3600.0,
                ),
                Seat(
                    id="quota-insufficient",
                    provider="quota-insufficient",
                    model="almost-enough",
                    kind="quota",
                    remaining=4_999.0,
                    reset_deadline=self.now + 3600.0,
                ),
                Seat(
                    id="quota-sufficient",
                    provider="quota-sufficient",
                    model="enough",
                    kind="quota",
                    remaining=5_000.0,
                    reset_deadline=self.now + 3600.0,
                ),
                Seat(
                    id="metered",
                    provider="metered",
                    model="payg",
                    kind="metered_api",
                ),
            ],
            auto_seed=False,
        )
        self.store = _MemoryStore({
            "terrarium_trial": [
                {"task_id": "Large migration", "tokens_used": 4_000},
                {"task_id": "Large migration", "tokens_used": 6_000},
                {"task_id": "Unrelated task", "tokens_used": 100_000},
            ],
        })
        self.composer = CastingComposer(
            ledger=self.ledger,
            scorecard=_ScorecardWithStore(self.store),
        )

    def test_estimate_uses_matching_trial_records_or_the_default(self):
        self.assertEqual(self.composer._estimate_task_tokens("Large migration"), 5_000.0)
        self.assertEqual(self.composer._estimate_task_tokens("Unseen task"), 5_000.0)

    def test_trial_axes_are_used_when_terrarium_trials_are_absent(self):
        self.store.records_by_kind["trial_axes"] = [
            {"task_id": "Large migration", "tokens": 4_000},
            {"task_id": "Large migration", "tokens": 8_000},
        ]
        self.store.records_by_kind["terrarium_trial"] = []

        self.assertEqual(self.composer._estimate_task_tokens("Large migration"), 6_000.0)

    def test_cast_excludes_exhausted_and_insufficient_quota_but_keeps_payg(self):
        decision = self.composer.cast(
            BUILDER_ROLE,
            "Large migration",
            candidates_count=4,
            now=self.now,
            mode=BROKIE,
        )

        self.assertEqual(
            {seat.id for seat in decision.candidates},
            {"quota-sufficient", "metered"},
        )
        self.assertIn(
            "1 seat(s) with insufficient quota for estimated 5000 tokens",
            decision.reason,
        )


class TestWarmWindowFilter(CastingEngineTestCase):
    """A7-3: a sporadic conversation must not be seated inside a short assured window."""

    def _windowed_ledger(self, windows: dict[str, float]) -> SeatLedger:
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, True)
        return SeatLedger(
            initial_seats=[
                Seat(
                    id=provider,
                    provider=provider,
                    model=f"{provider}-flagship",
                    kind="metered_api",
                    base_price_per_m=1.0,
                    warm_window_seconds=window,
                )
                for provider, window in windows.items()
            ],
            auto_seed=False,
            presence_path=Path(tmp_dir) / "p.json",
        )

    def test_no_idle_hint_means_no_window_filter(self):
        composer = CastingComposer(
            ledger=self._windowed_ledger({"anthropic": 300.0, "openai": 1800.0, "google": 0.0})
        )
        decision = composer.cast(BUILDER_ROLE, "Chat", candidates_count=3, now=self.now)
        self.assertEqual(decision.satisfied_count, 3)
        self.assertIsNone(decision.shortfall)

    def test_a_window_shorter_than_the_expected_silence_is_filtered(self):
        composer = CastingComposer(
            ledger=self._windowed_ledger({"anthropic": 300.0, "openai": 1800.0, "google": 0.0})
        )
        decision = composer.cast(
            BUILDER_ROLE, "Chat", candidates_count=3, now=self.now, expected_idle_seconds=600.0
        )
        self.assertNotIn("anthropic", [s.id for s in decision.candidates])
        self.assertEqual({s.id for s in decision.candidates}, {"openai", "google"})

    def test_a_provider_assuring_no_window_is_not_filtered(self):
        # Zero means "nothing assured", so there is no warm state to forfeit by going quiet;
        # filtering it would leave the sporadic case with nowhere to sit.
        composer = CastingComposer(
            ledger=self._windowed_ledger({"anthropic": 300.0, "google": 0.0})
        )
        decision = composer.cast(
            BUILDER_ROLE, "Chat", candidates_count=2, now=self.now, expected_idle_seconds=86400.0
        )
        self.assertEqual([s.id for s in decision.candidates], ["google"])

    def test_the_filter_reads_the_wiki_table_the_seats_are_seeded_from(self):
        anthropic = cache_economics.profile("anthropic").warm_window_seconds
        openai = cache_economics.profile("openai").warm_window_seconds
        self.assertLess(anthropic, openai)  # the premise of the wiki's stated policy

        composer = CastingComposer(
            ledger=self._windowed_ledger({"anthropic": anthropic, "openai": openai})
        )
        idle = (anthropic + openai) / 2
        decision = composer.cast(
            BUILDER_ROLE, "Chat", candidates_count=2, now=self.now, expected_idle_seconds=idle
        )
        self.assertEqual([s.id for s in decision.candidates], ["openai"])
        self.assertIn("warm window", decision.shortfall)

    def test_a_filter_that_empties_the_pool_says_which_filter_did_it(self):
        composer = CastingComposer(ledger=self._windowed_ledger({"anthropic": 300.0}))
        with self.assertRaises(RuntimeError) as ctx:
            composer.cast(
                BUILDER_ROLE, "Chat", now=self.now, expected_idle_seconds=3600.0
            )
        self.assertIn("warm window", str(ctx.exception))

    def test_the_window_filter_applies_in_every_mode(self):
        composer = CastingComposer(
            ledger=self._windowed_ledger({"anthropic": 300.0, "openai": 1800.0})
        )
        for mode in CASTING_MODES:
            with self.subTest(mode=mode):
                decision = composer.cast(
                    BUILDER_ROLE, "Chat", candidates_count=2, now=self.now,
                    mode=mode, seed=5, expected_idle_seconds=900.0,
                )
                self.assertEqual([s.id for s in decision.candidates], ["openai"])


class TestCastingDecisionCompatibility(CastingEngineTestCase):
    def test_the_orchestrators_call_signature_still_works(self):
        decision = self.composer.cast(
            role=BUILDER_ROLE, task="Build a scraper", candidates_count=2, now=self.now
        )
        self.assertIs(decision.role, BUILDER_ROLE)
        self.assertIn(decision.primary_seat, decision.candidates)
        self.assertEqual(decision.primary_seat, decision.candidates[0])
        self.assertTrue(decision.reason)
        self.assertEqual(decision.mode, SMART)

    def test_the_voice_role_casts_the_same_way_as_any_other(self):
        decision = self.composer.cast(role=VOICE_ROLE, task="Say hi", now=self.now)
        self.assertIn(decision.primary_seat.id, {s.id for s in self.ledger.list_seats()})


class TestPresenceLockPersistence(unittest.TestCase):
    def setUp(self):
        self.now = 10000.0
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        self.presence_path = Path(tmp_dir) / "presence.json"

    def _new_ledger(self) -> SeatLedger:
        # Fresh Seat objects each time, so presence can only reach a second ledger
        # through the shared presence file and not through a shared in-memory seat.
        return SeatLedger(
            initial_seats=[
                Seat(
                    id="seat-a",
                    provider="gemini",
                    model="gemini-3.6-flash",
                    kind="quota",
                    reset_deadline=self.now + 3600,
                ),
                Seat(
                    id="seat-b",
                    provider="openai",
                    model="gpt-5.6-sol",
                    kind="quota",
                    reset_deadline=self.now + 7200,
                ),
            ],
            auto_seed=False,
            presence_path=self.presence_path,
        )

    def _available_ids(self, ledger: SeatLedger, now: float) -> list[str]:
        return [s.id for s in ledger.list_available(now=now, exclude_presence=True)]

    def test_presence_lock_round_trips_across_ledger_instances(self):
        locker = self._new_ledger()
        self.assertTrue(locker.set_presence("seat-a", True, now=self.now))

        # auto_seed=False skips _seed_from_env, which is where a real process would
        # pick the lock up, so apply it by hand here.
        reader = self._new_ledger()
        reader._apply_persisted_presence(now=self.now)
        self.assertTrue(reader.get("seat-a").presence)
        self.assertNotIn("seat-a", self._available_ids(reader, self.now))
        self.assertIn("seat-b", self._available_ids(reader, self.now))

        locker.set_presence("seat-a", False, now=self.now)

        after_unlock = self._new_ledger()
        after_unlock._apply_persisted_presence(now=self.now)
        self.assertFalse(after_unlock.get("seat-a").presence)
        self.assertIn("seat-a", self._available_ids(after_unlock, self.now))

    def test_expired_presence_lock_is_ignored(self):
        self._new_ledger().set_presence("seat-a", True, now=self.now)

        later = self.now + PRESENCE_TTL_SECONDS + 1
        reader = self._new_ledger()
        reader._apply_persisted_presence(now=later)
        self.assertEqual(reader.read_presence_locks(now=later), {})
        self.assertFalse(reader.get("seat-a").presence)
        self.assertIn("seat-a", self._available_ids(reader, later))

    def test_set_presence_rejects_unknown_seat_without_locking_it(self):
        ledger = self._new_ledger()
        self.assertFalse(ledger.set_presence("seat-does-not-exist", True, now=self.now))
        self.assertEqual(ledger.read_presence_locks(now=self.now), {})


class TestAntiIncumbencyAptitude(unittest.TestCase):
    def test_smart_mode_prefers_high_win_rate_newcomer_over_high_volume_mediocre_incumbent(self):
        # Incumbent: 50 trials, 30 wins, 20 losses -> standing 20.0, avg delta +0.2, n=50
        # Newcomer: 3 trials, 3 wins, 0 losses -> standing 13.0, avg delta +1.0, n=3
        card = Scorecard(store=_NullStore())
        for i in range(30):
            card.record_verdict(role=BUILDER_ROLE.name, model="incumbent-model", task_id=f"win_{i}", verdict="success")
        for i in range(20):
            card.record_verdict(role=BUILDER_ROLE.name, model="incumbent-model", task_id=f"loss_{i}", verdict="failed")
        for i in range(3):
            card.record_verdict(role=BUILDER_ROLE.name, model="newcomer-model", task_id=f"new_{i}", verdict="success")

        seats = [
            Seat(id="seat-incumbent", provider="openai", model="incumbent-model", kind="metered_api", base_price_per_m=1.0),
            Seat(id="seat-newcomer", provider="openai", model="newcomer-model", kind="metered_api", base_price_per_m=1.0),
        ]
        ledger = SeatLedger(initial_seats=seats, auto_seed=False)
        composer = CastingComposer(ledger=ledger, scorecard=card)

        decision = composer.cast(BUILDER_ROLE, "Build an API", candidates_count=1, mode=SMART)
        self.assertEqual(decision.primary_seat.id, "seat-newcomer")

if __name__ == "__main__":
    unittest.main()
