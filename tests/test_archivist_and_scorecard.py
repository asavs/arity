"""Tests for arity archivist, evidence auditing, and scorecard."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.handlers import JsonlRecordStore
from arity.archivist import ArchivistEntry, ImpartialArchivist
from arity.ledger import Seat
from arity.roles import BUILDER_ROLE
from arity.scorecard import Scorecard
from arity.terrarium import TerrariumCandidateResult
from arity.types import State, Status


class TestArchivistAndScorecard(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.ws = Path(self.tmpdir.name)
        self.store = JsonlRecordStore(root=self.ws / "records")
        self.scorecard = Scorecard(store=self.store)
        self.archivist = ImpartialArchivist(scorecard=self.scorecard, store=self.store)

        self.seat_gemini = Seat(
            id="gemini",
            provider="gemini",
            endpoint="https://api.google.com",
            model="gemini-3.6-flash",
        )
        self.seat_nemotron = Seat(
            id="nemotron",
            provider="nim",
            endpoint="https://api.nvidia.com",
            model="nvidia/nemotron",
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_scorecard_verdicts_and_discrepancy_penalty(self):
        initial_standing = self.scorecard.get_standing("builder", "gemini-3.6-flash")
        self.assertEqual(initial_standing, 10.0)

        # 1. Success increases standing (+1.0)
        self.scorecard.record_verdict("builder", "gemini-3.6-flash", "task_1", "success")
        self.assertEqual(self.scorecard.get_standing("builder", "gemini-3.6-flash"), 11.0)

        # 2. Discrepancy heavily penalizes standing (-2.5) (Axiom 9)
        self.scorecard.record_verdict("builder", "gemini-3.6-flash", "task_2", "discrepancy")
        self.assertEqual(self.scorecard.get_standing("builder", "gemini-3.6-flash"), 8.5)

    def test_archivist_verified_success(self):
        # Create an actual file in candidate's workspace
        cand_ws = self.ws / "cand_1"
        cand_ws.mkdir(parents=True, exist_ok=True)
        (cand_ws / "schema.sql").write_text("CREATE TABLE deals (id INT);")

        res = TerrariumCandidateResult(
            candidate_id="cand_1",
            task_id="task_1",
            seat=self.seat_gemini,
            role=BUILDER_ROLE,
            final_state=State(session_id="cand_1", status=Status.IDLE),
            output="Done! I created schema.sql.",
            self_report="Wrote file schema.sql with deals table.",
            tokens_used=120,
            duration_seconds=1.5,
            workspace_path=cand_ws,
            status="completed",
        )

        entry = self.archivist.audit(res)
        self.assertEqual(entry.verdict, "success")
        self.assertFalse(entry.discrepancy)
        self.assertIn("schema.sql", entry.verified_artifacts)

    def test_archivist_catches_discrepancy(self):
        # Candidate claims it wrote 'missing.sql', but never created the file
        cand_ws = self.ws / "cand_2"
        cand_ws.mkdir(parents=True, exist_ok=True)

        res = TerrariumCandidateResult(
            candidate_id="cand_2",
            task_id="task_2",
            seat=self.seat_nemotron,
            role=BUILDER_ROLE,
            final_state=State(session_id="cand_2", status=Status.IDLE),
            output="Done! Created missing.sql",
            self_report="Created file missing.sql with 10 tables.",
            tokens_used=200,
            duration_seconds=2.0,
            workspace_path=cand_ws,
            status="completed",
        )

        entry = self.archivist.audit(res)
        self.assertEqual(entry.verdict, "discrepancy")
        self.assertTrue(entry.discrepancy)
        self.assertIn("missing.sql", entry.discrepancy_details or "")

        # Scorecard standing should have dropped
        self.assertLess(self.scorecard.get_standing(res.role.name, "nvidia/nemotron"), 10.0)

    def test_evaluate_trial_picks_verified_winner(self):
        # Candidate A: Created verified file
        ws_a = self.ws / "cand_a"
        ws_a.mkdir(parents=True, exist_ok=True)
        (ws_a / "app.py").write_text("print('hello')")
        res_a = TerrariumCandidateResult(
            candidate_id="cand_a",
            task_id="trial_1",
            seat=self.seat_gemini,
            role=BUILDER_ROLE,
            final_state=State(session_id="cand_a", status=Status.IDLE),
            output="Done app.py",
            self_report="Created file app.py",
            tokens_used=100,
            duration_seconds=1.0,
            workspace_path=ws_a,
            status="completed",
        )

        # Candidate B: Hallucinated file
        ws_b = self.ws / "cand_b"
        ws_b.mkdir(parents=True, exist_ok=True)
        res_b = TerrariumCandidateResult(
            candidate_id="cand_b",
            task_id="trial_1",
            seat=self.seat_nemotron,
            role=BUILDER_ROLE,
            final_state=State(session_id="cand_b", status=Status.IDLE),
            output="Done app.py",
            self_report="Created file app.py",
            tokens_used=100,
            duration_seconds=1.0,
            workspace_path=ws_b,
            status="completed",
        )

        winner, entries = self.archivist.evaluate_trial([res_a, res_b])
        self.assertIsNotNone(winner)
        self.assertEqual(winner.candidate_id, "cand_a")
        self.assertEqual(len(entries), 2)


if __name__ == "__main__":
    unittest.main()
