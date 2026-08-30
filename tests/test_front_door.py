"""Tests for arity run: seat picking, delivery, the secretary's question on a judge split."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.ledger import Seat
from arity.race import deliver, judges_split, pick_seats, run_front_door


class TestPickSeats(unittest.TestCase):
    def test_one_seat_per_model_fullest_first_capped(self):
        seats = [
            Seat(provider="google", model="claude-opus-4.6", account="a@x", remaining=2_000_000),
            Seat(provider="google", model="claude-opus-4.6", account="b@x", remaining=1_000_000),
            Seat(provider="openai", model="gpt-5.6-sol", remaining=2_000_000),
            Seat(provider="xai", model="grok-4.5", remaining=0),                 # empty: skipped
            Seat(provider="google", model="gemini-3.6-flash", remaining=500_000),
        ]
        picked = pick_seats(seats, 3)
        self.assertEqual([s.model for s in picked], ["claude-opus-4.6", "gpt-5.6-sol", "gemini-3.6-flash"])
        self.assertEqual(picked[0].account, "a@x")
        self.assertEqual(len(pick_seats(seats, 2)), 2)


class TestRunFrontDoor(unittest.TestCase):
    def test_mock_run_delivers_the_winners_files_with_a_receipt(self):
        with TemporaryDirectory() as d:
            out = Path(d) / "out"
            rep, delivery = run_front_door("", task_name="lru_cache", mock=True, out_dir=out, interactive=False)
        self.assertTrue(delivery.winner_name.endswith("[good]"))
        self.assertEqual(sorted(delivery.files), ["lru_cache.py", "test_lru_cache.py"])
        self.assertIsNone(delivery.answer)
        self.assertIn("hidden 7/7", delivery.receipt)
        self.assertIn("[good]", delivery.receipt)
        self.assertFalse(delivery.asked_human)
        self.assertFalse(any("__pycache__" in f or ".hidden_tests" in f for f in delivery.files))

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

    def test_judge_split_asks_the_human_and_records_the_pick(self):
        from arity.race import RaceConfig, run_race, human_pick, ScriptedProvider
        from arity.handlers import JsonlRecordStore
        # Two judges that disagree: one says A, the other says B.
        answers = iter(['{"order": ["A", "B", "C"], "ties": []}', '{"order": ["B", "A", "C"], "ties": []}'])
        cfg = RaceConfig(task_name="lru_cache", mock=True, judges=["gpt-5.6-sol", "claude-3-7-sonnet"], review="always",
                         judge_provider=lambda model: ScriptedProvider({}, next(answers), f"j-{model}"))
        with TemporaryDirectory() as d:
            cfg.workspace_root = Path(d) / "ws"
            cfg.teardown = False
            rep = run_race(cfg)
            self.assertTrue(judges_split(rep))
            printed: list[str] = []
            pick = human_pick(rep, ask=lambda prompt: "2", printer=lambda *a, **k: printed.append(" ".join(map(str, a))))
            self.assertIsNotNone(pick)
            self.assertTrue(any("judges disagree" in line for line in printed))
            picks = rep.archivist.store.query("human_pick")
            self.assertEqual(len(picks), 1)
            self.assertEqual(picks[0]["picked"], pick.candidate_id)


if __name__ == "__main__":
    unittest.main()
