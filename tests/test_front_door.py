"""Tests for gorkbot run: seat picking, delivery, the secretary's question on a judge split."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gorkbot.ledger import Seat
from gorkbot.race import deliver, judges_split, pick_seats, run_front_door


class TestPickSeats(unittest.TestCase):
    def test_one_seat_per_model_fullest_first_capped(self):
        seats = [
            Seat(provider="google", model="claude-opus-4.6", account="a@x", remaining=2_000_000),
            Seat(provider="google", model="claude-opus-4.6", account="b@x", remaining=1_000_000),
            Seat(provider="openai", model="gpt-5.6-sol", remaining=2_000_000),
            Seat(provider="xai", model="grok-4.5", remaining=0),                 # empty: skipped
            Seat(provider="google", model="gemini-3.6-flash", remaining=500_000),
        ]
        wire = lambda s: s.provider != "anthropic"   # deterministic stand-in for "has a tool-capable wire"
        picked = pick_seats(seats, 3, wire_capable=wire)
        # a CLI-only seat can only narrate work it cannot do, so it fills gaps last
        cli_only = Seat(provider="anthropic", model="claude-3-7-sonnet", remaining=2_000_000)
        ordered = pick_seats([cli_only] + seats, 4, wire_capable=wire)
        self.assertEqual(ordered[-1].model, "claude-3-7-sonnet")
        self.assertEqual([s.model for s in picked], ["claude-opus-4.6", "gpt-5.6-sol", "gemini-3.6-flash"])
        self.assertEqual(picked[0].account, "a@x")
        self.assertEqual(len(pick_seats(seats, 2, wire_capable=wire)), 2)


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

    def test_answer_only_delivery_writes_answer_md(self):
        from gorkbot.race import RaceReport
        from gorkbot.terrarium import TaskRecord, TerrariumCandidateResult, State
        from gorkbot.archivist import ImpartialArchivist
        from gorkbot.handlers import JsonlRecordStore
        from gorkbot.roles import BUILDER_ROLE
        from gorkbot.race import placeholder_seats, CandidateSpec
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
        from gorkbot.evidence import ResolutionKind
        from gorkbot.race import RaceConfig, run_race, ScriptedProvider
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
