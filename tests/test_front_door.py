"""Tests for arity run: casting the seats, delivery, the secretary's question on a judge split."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import arity.race as race
from arity.composer import CHAOS, SMART, CastingDecision
from arity.ledger import Seat, SeatLedger
from arity.roles import BUILDER_ROLE
from arity.race import cast_seats, deliver, judges_split, run_front_door


class TestCastSeats(unittest.TestCase):
    # A deterministic stand-in for "this seat has a tool-capable wire".
    WIRE = staticmethod(lambda s: s.provider != "anthropic")

    def _ledger(self, *seats: Seat) -> SeatLedger:
        return SeatLedger(initial_seats=list(seats), auto_seed=False)

    def _pool(self) -> list[Seat]:
        later = time.time() + 3600.0
        return [
            Seat(provider="google", model="claude-opus-4.6", account="a@x", remaining=2_000_000, reset_deadline=later),
            Seat(provider="google", model="claude-opus-4.6", account="b@x", remaining=1_000_000, reset_deadline=later),
            Seat(provider="openai", model="gpt-5.6-sol", remaining=2_000_000, reset_deadline=later),
            Seat(provider="xai", model="grok-4.5", remaining=0, reset_deadline=later),   # exhausted
            Seat(provider="google", model="gemini-3.6-flash", remaining=500_000, reset_deadline=later),
        ]

    def test_one_seat_per_model_and_no_seat_that_cannot_pay(self):
        decision, pool, notes = cast_seats(BUILDER_ROLE, "brief", 3, ledger=self._ledger(*self._pool()),
                                           wire_capable=self.WIRE, seed=1)
        models = [s.model for s in decision.candidates]
        self.assertEqual(len(models), 3)
        self.assertEqual(len(set(models)), 3)                       # one seat per model
        self.assertNotIn("grok-4.5", models)                        # exhausted quota never casts
        self.assertNotIn("grok-4.5", [s.model for s in pool])
        self.assertEqual(decision.distinct_on, "model")
        self.assertIsNone(decision.shortfall)
        self.assertEqual(notes, [])

    def test_a_cli_only_seat_only_fills_a_seat_the_wire_cannot(self):
        cli_only = Seat(provider="anthropic", model="claude-3-7-sonnet", remaining=2_000_000,
                        reset_deadline=time.time() + 3600.0)
        ledger = self._ledger(cli_only, *self._pool())
        three, _, _ = cast_seats(BUILDER_ROLE, "brief", 3, ledger=ledger, wire_capable=self.WIRE, seed=1)
        four, _, notes = cast_seats(BUILDER_ROLE, "brief", 4, ledger=ledger, wire_capable=self.WIRE, seed=1)
        self.assertNotIn("claude-3-7-sonnet", [s.model for s in three.candidates])
        self.assertIn("claude-3-7-sonnet", [s.model for s in four.candidates])
        self.assertTrue(any("widened to CLI-only" in n for n in notes))

    def test_a_wire_seat_is_never_displaced_by_a_cli_only_seat(self):
        # P1: two wire-capable models cannot fill a request for three. The old rule swapped the
        # whole pool for the mode to order freely, which let CLI-only seats outrank a wire seat
        # that was sitting right there.
        later = time.time() + 3600.0
        wire = [Seat(provider="openai", model=f"wire-{n}", remaining=2_000_000, reset_deadline=later)
                for n in ("a", "b")]
        cli = [Seat(provider="anthropic", model=f"cli-{n}", remaining=2_000_000, reset_deadline=later)
               for n in ("c", "d")]
        decision, _, notes = cast_seats(BUILDER_ROLE, "brief", 3, ledger=self._ledger(*cli, *wire),
                                        wire_capable=self.WIRE, seed=1)
        models = [s.model for s in decision.candidates]
        self.assertEqual(len(models), 3)
        self.assertEqual(set(models[:2]), {"wire-a", "wire-b"})     # every wire seat, ahead of the fill
        self.assertEqual(len([m for m in models if m.startswith("cli-")]), 1)
        self.assertEqual(decision.primary_seat.model[:4], "wire")
        self.assertTrue(any("widened to CLI-only" in n for n in notes))

    def test_a_pool_with_no_wire_seat_at_all_casts_and_says_so(self):
        cli = [Seat(provider="anthropic", model=f"cli-{n}", remaining=2_000_000,
                    reset_deadline=time.time() + 3600.0) for n in ("a", "b")]
        decision, _, notes = cast_seats(BUILDER_ROLE, "brief", 2, ledger=self._ledger(*cli),
                                        wire_capable=self.WIRE, seed=1)
        self.assertEqual(len(decision.candidates), 2)
        self.assertTrue(any("no wire-capable seat" in n for n in notes))

    def test_presence_locked_seats_are_never_cast(self):
        ledger = self._ledger(*self._pool())
        for seat in ledger.list_seats():
            if seat.model != "gpt-5.6-sol":
                seat.presence = True
        decision, _, _ = cast_seats(BUILDER_ROLE, "brief", 3, ledger=ledger, wire_capable=self.WIRE, seed=1)
        self.assertEqual([s.model for s in decision.candidates], ["gpt-5.6-sol"])

    def test_a_short_cast_is_reported_not_padded(self):
        decision, _, _ = cast_seats(BUILDER_ROLE, "brief", 9, ledger=self._ledger(*self._pool()),
                                    wire_capable=self.WIRE, seed=1)
        self.assertEqual(decision.requested_count, 9)
        self.assertEqual(decision.satisfied_count, 3)
        self.assertFalse(decision.fully_satisfied)
        self.assertIn("requested 9, satisfied 3", decision.shortfall)

    def test_a_widened_cast_still_reports_what_it_could_not_fill(self):
        later = time.time() + 3600.0
        seats = [Seat(provider="openai", model="wire-a", remaining=2_000_000, reset_deadline=later),
                 Seat(provider="anthropic", model="cli-b", remaining=2_000_000, reset_deadline=later)]
        decision, _, notes = cast_seats(BUILDER_ROLE, "brief", 5, ledger=self._ledger(*seats),
                                        wire_capable=self.WIRE, seed=1)
        self.assertEqual(decision.satisfied_count, 2)
        self.assertIn("requested 5, satisfied 2", decision.shortfall)
        self.assertIn("2 distinct model value(s)", decision.shortfall)
        self.assertTrue(any("widened to CLI-only" in n for n in notes))

    def test_a_seed_reproduces_a_chaos_cast(self):
        ledger = self._ledger(*self._pool())
        first, _, _ = cast_seats(BUILDER_ROLE, "brief", 3, ledger=ledger, wire_capable=self.WIRE, mode=CHAOS, seed=99)
        again, _, _ = cast_seats(BUILDER_ROLE, "brief", 3, ledger=ledger, wire_capable=self.WIRE, mode=CHAOS, seed=99)
        self.assertEqual([s.id for s in first.candidates], [s.id for s in again.candidates])
        self.assertEqual(first.mode, CHAOS)

    def test_a_rolled_over_quota_seat_is_not_cast_ahead_of_a_live_one(self):
        # P3: an elapsed deadline floors both time_to_reset and effective_cost, so the seat
        # likeliest to answer 429 used to sort first among quota seats.
        now = 10_000.0
        stale = Seat(id="stale", provider="openai", model="stale-model", remaining=0.0,
                     reset_deadline=now - 60.0)
        live = Seat(id="live", provider="openai", model="live-model", remaining=2_000_000,
                    reset_deadline=now + 3600.0)
        decision, pool, _ = cast_seats(BUILDER_ROLE, "brief", 2, ledger=self._ledger(stale, live),
                                       wire_capable=self.WIRE, seed=1, now=now)
        self.assertIn("stale", [s.id for s in pool])          # still eligible: quota presumed restored
        self.assertEqual(decision.primary_seat.id, "live")
        self.assertEqual([s.id for s in decision.candidates], ["live", "stale"])


class TestRunFrontDoor(unittest.TestCase):
    def test_mock_run_delivers_the_winners_files_with_a_receipt(self):
        with TemporaryDirectory() as d:
            out = Path(d) / "out"
            rep, delivery = run_front_door(
                "", task_name="lru_cache", candidates=5, mock=True, out_dir=out, interactive=False,
            )
        self.assertTrue(delivery.winner_name.endswith("[good]"))
        self.assertEqual(sorted(delivery.files), ["lru_cache.py", "test_lru_cache.py"])
        self.assertIsNone(delivery.answer)
        self.assertIn("hidden 7/7", delivery.receipt)
        self.assertIn("[good]", delivery.receipt)
        self.assertFalse(delivery.asked_human)
        self.assertFalse(any("__pycache__" in f or ".hidden_tests" in f for f in delivery.files))
        self.assertEqual(rep.to_dict()["arity"], {"requested_max": 5, "resolved": 3})
        self.assertIn("arity requested max 5; resolved 3 unique candidates", rep.notes)
        self.assertTrue(delivery.receipt.startswith("arity 3/5 resolved"))

    def test_the_cast_mode_and_seed_reach_the_trial_record(self):
        with TemporaryDirectory() as d:
            rep, _ = run_front_door(
                "", task_name="lru_cache", candidates=2, mock=True, out_dir=Path(d) / "out",
                interactive=False, cast_mode=CHAOS, cast_seed=4242,
            )
        self.assertEqual((rep.casting["mode"], rep.casting["seed"]), (CHAOS, 4242))
        self.assertEqual(rep.casting["requested_count"], 2)
        self.assertEqual(rep.casting["distinct_on"], "model")
        self.assertEqual(rep.to_dict()["casting"], rep.casting)
        started = next(e for e in rep.journal.events if e.event_type == "trial.started")
        self.assertEqual(started.payload["casting"]["seed"], 4242)
        self.assertEqual(list(started.payload["casting"]["seats"]), [c.seat.id for c in rep.candidates])

    def test_the_default_cast_explores_as_well_as_exploits(self):
        with TemporaryDirectory() as d:
            rep, _ = run_front_door(
                "", task_name="lru_cache", candidates=3, mock=True, out_dir=Path(d) / "out",
                interactive=False,
            )
        self.assertEqual(rep.casting["mode"], SMART)
        self.assertIsNotNone(rep.casting["exploration_seat"])
        self.assertIn(rep.casting["exploration_seat"], rep.casting["seats"])

    def test_the_cast_reads_the_role_key_the_trial_writes(self):
        # P4: the task's type pack lands on the role inside run_race, so a cast resolved from
        # the bare `--role developer` used to read `developer:<model>` while the trial wrote
        # `developer.python:<model>` — a key question A could never accumulate evidence under.
        seen: dict[str, object] = {}
        real = race.cast_seats

        def spy(role, *args, **kwargs):
            seen["role"] = role
            return real(role, *args, **kwargs)

        with TemporaryDirectory() as d, patch.object(race, "cast_seats", spy):
            rep, _ = run_front_door(
                "", task_name="lru_cache", role="developer", candidates=2, mock=True,
                out_dir=Path(d) / "out", interactive=False,
            )
        self.assertEqual(seen["role"].key_name, "developer.python")
        self.assertEqual(seen["role"].key_name, rep.candidates[0].role.key_name)
        self.assertIn("type 'python' from task tags -> developer:python", rep.notes)

    def test_an_unknown_task_is_refused_before_anything_is_cast(self):
        called: list[str] = []
        with patch.object(race, "cast_seats", lambda *a, **kw: called.append("cast")):
            with self.assertRaises(SystemExit) as ctx:
                run_front_door("", task_name="no-such-task", mock=True, interactive=False)
        self.assertIn("unknown task 'no-such-task'", str(ctx.exception))
        self.assertEqual(called, [])

    def test_the_cast_reads_the_store_the_run_grades_into(self):
        # P5: a caller with a private store used to cast on the global record and grade into
        # its own, and a live run replayed the store twice to do it.
        seats = [Seat(id="s1", provider="openai", model="m1"),
                 Seat(id="s2", provider="openai", model="m2")]
        seen: dict[str, object] = {}

        def fake_cast(role, brief, requested, **kwargs):
            seen["scorecard"] = kwargs.get("scorecard")
            return (
                CastingDecision(role=role, primary_seat=seats[0], candidates=list(seats),
                                requested_count=requested, distinct_on="model"),
                list(seats),
                [],
            )

        report = SimpleNamespace(judgements=[], conference_winner=None, winner=None,
                                 candidates=list(seats), notes=[], requested_arity=None)
        delivery = SimpleNamespace(asked_human=False, receipt="done")
        with TemporaryDirectory() as d:
            root = Path(d) / "records"
            with (
                patch.object(race, "cast_seats", fake_cast),
                patch.object(race, "run_race", return_value=report) as run_race,
                patch.object(race, "deliver", return_value=delivery),
            ):
                run_front_door("brief", candidates=2, store_root=root, interactive=False)
            cfg = run_race.call_args.args[0]
        self.assertEqual(Path(cfg.record_store.root), root)
        self.assertIs(cfg.scorecard.store, cfg.record_store)
        self.assertIs(seen["scorecard"], cfg.scorecard)

    def test_the_trial_reuses_the_scorecard_the_cast_already_replayed(self):
        from arity.handlers import JsonlRecordStore
        from arity.race import RaceConfig, run_race
        from arity.scorecard import Scorecard
        with TemporaryDirectory() as d:
            root = Path(d)
            card = Scorecard(store=JsonlRecordStore(root=root / "records"))
            rep = run_race(RaceConfig(task_name="lru_cache", mock=True, workspace_root=root / "ws",
                                      store_root=root / "records", scorecard=card))
        # The archivist grades through the caller's scorecard, so the store is replayed once.
        self.assertIs(rep.archivist.scorecard, card)

    def test_a_mock_run_still_reads_no_store_and_no_scorecard(self):
        report = SimpleNamespace(judgements=[], conference_winner=None, winner=None,
                                 candidates=[object(), object(), object()], notes=[], requested_arity=None)
        delivery = SimpleNamespace(asked_human=False, receipt="done")
        with (
            patch.object(race, "run_race", return_value=report) as run_race,
            patch.object(race, "deliver", return_value=delivery),
        ):
            run_front_door("brief", candidates=3, mock=True, interactive=False)
        cfg = run_race.call_args.args[0]
        self.assertIsNone(cfg.record_store)
        self.assertIsNone(cfg.scorecard)

    def test_cast_seed_and_mode_reach_the_front_door_from_the_cli(self):
        # P6: chaos records a seed so the cast can be replayed; without a flag it could not be.
        import arity.cli as cli
        rep = SimpleNamespace(judgements=[], notes=[], to_dict=lambda: {})
        delivery = SimpleNamespace(answer=None, files=[], receipt="r", to_dict=lambda: {})
        argv = ["arity", "run", "brief", "--mock", "--cast", "chaos", "--cast-seed", "4242"]
        with (
            patch.object(race, "run_front_door", return_value=(rep, delivery)) as front,
            patch.object(sys, "argv", argv),
        ):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(front.call_args.kwargs["cast_seed"], 4242)
        self.assertEqual(front.call_args.kwargs["cast_mode"], CHAOS)

    def test_an_unseeded_run_leaves_the_cast_seed_unset(self):
        import arity.cli as cli
        rep = SimpleNamespace(judgements=[], notes=[], to_dict=lambda: {})
        delivery = SimpleNamespace(answer=None, files=[], receipt="r", to_dict=lambda: {})
        with (
            patch.object(race, "run_front_door", return_value=(rep, delivery)) as front,
            patch.object(sys, "argv", ["arity", "run", "brief", "--mock"]),
        ):
            cli.main()
        self.assertIsNone(front.call_args.kwargs["cast_seed"])
        self.assertEqual(front.call_args.kwargs["cast_mode"], SMART)

    def test_answer_only_delivery_writes_answer_md(self):
        from arity.race import RaceReport
        from arity.terrarium import TaskRecord, TerrariumCandidateResult, State
        from arity.archivist import ImpartialArchivist
        from arity.handlers import JsonlRecordStore
        from arity.roles import BUILDER_ROLE
        from arity.race import placeholder_seats, CandidateSpec
        with TemporaryDirectory() as d:
            ws = Path(d) / "empty"; ws.mkdir()
            spec = CandidateSpec(seat=placeholder_seats()[0], name="scout-a", role=BUILDER_ROLE)
            r = TerrariumCandidateResult(candidate_id="c", task_id="t1", seat=spec.seat, role=BUILDER_ROLE, final_state=State(session_id="c"),
                                         output="Prices: $20/mo.", self_report="x", tokens_used=5, duration_seconds=1.0, workspace_path=ws, spec=spec)
            rep = RaceReport(task=TaskRecord(id="t1", brief="prices"), race_task=None, candidates=[spec], winner=r, results=[r], entries=[],
                             archivist=ImpartialArchivist(store=JsonlRecordStore(Path(d) / "r")), ephemeral=True)
            delivery = deliver(rep, out_dir=Path(d) / "out")
            self.assertEqual(delivery.files, [])
            self.assertEqual(delivery.answer, "Prices: $20/mo.")
            self.assertEqual((Path(d) / "out" / "answer.md").read_text(encoding="utf-8").strip(), "Prices: $20/mo.")
            self.assertIn("no hidden tests", delivery.receipt)

    def test_review_always_cannot_override_unique_facts(self):
        from arity.evidence import ResolutionKind
        from arity.race import RaceConfig, run_race, ScriptedProvider
        # Two judges that disagree: one says A, the other says B.
        answers = iter(['{"order": ["A", "B", "C"], "ties": []}', '{"order": ["B", "A", "C"], "ties": []}'])
        cfg = RaceConfig(task_name="lru_cache", mock=True, judges=["gpt-5.6-sol", "claude-3-7-sonnet"], review="always",
                         judge_provider=lambda model: ScriptedProvider({}, next(answers), f"j-{model}"))
        with TemporaryDirectory() as d:
            cfg.workspace_root = Path(d) / "ws"
            cfg.teardown = False
            rep = run_race(cfg)
            self.assertTrue(judges_split(rep))
            self.assertEqual(rep.resolution.kind, ResolutionKind.FACTS_WINNER)
            self.assertEqual(rep.resolution.candidate_id, rep.winner.candidate_id)
            self.assertEqual(rep.archivist.store.query("human_pick"), [])


if __name__ == "__main__":
    unittest.main()
