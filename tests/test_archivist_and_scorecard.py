"""Tests for arity archivist, evidence auditing, and scorecard."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.handlers import JsonlRecordStore
from arity.archivist import ArchivistEntry, ImpartialArchivist, extract_structured_file_declaration
from arity.ledger import Seat
from arity.roles import BUILDER_ROLE
from arity.scorecard import Scorecard
from arity.terrarium import TerrariumCandidateResult
from arity.types import State, Status, StoreRecord


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

    def test_structured_file_declarations_are_parsed_exactly(self):
        self.assertEqual(
            extract_structured_file_declaration('{"files": ["src/app.py", "tests/test_app.py"]}'),
            ["src/app.py", "tests/test_app.py"],
        )
        self.assertEqual(
            extract_structured_file_declaration("files:\n- src/app.py\n- tests/test_app.py"),
            ["src/app.py", "tests/test_app.py"],
        )
        self.assertEqual(
            extract_structured_file_declaration("[files]\nsrc/app.py\ntests/test_app.py"),
            ["src/app.py", "tests/test_app.py"],
        )

    def test_structured_missing_file_triggers_discrepancy_penalty(self):
        cand_ws = self.ws / "cand_structured"
        cand_ws.mkdir(parents=True, exist_ok=True)
        (cand_ws / "present.py").write_text("value = 1\n")
        res = TerrariumCandidateResult(
            candidate_id="cand_structured",
            task_id="task_structured",
            seat=self.seat_nemotron,
            role=BUILDER_ROLE,
            final_state=State(session_id="cand_structured", status=Status.IDLE),
            output='{"files": ["present.py", "missing.py"]}',
            self_report="Created unrelated_claim.py.",
            tokens_used=100,
            duration_seconds=1.0,
            workspace_path=cand_ws,
            status="completed",
        )

        entry = self.archivist.audit(res)

        self.assertEqual(entry.false_claims, ["missing.py"])
        self.assertIn("missing.py", entry.discrepancy_details or "")
        self.assertTrue(entry.discrepancy)
        self.assertEqual(entry.verdict, "discrepancy")
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

    def test_reload_restores_every_axis_not_just_role_model(self):
        model = "gemini-3.6-flash"
        sig_a = f"builder:{model}:wire:ast_tools"
        sig_b = f"builder:{model}:socket:shell_tools"

        def under_a(task_id, verdict):
            self.scorecard.record_verdict(
                "builder", model, task_id, verdict,
                signature=sig_a, harness="wire", tool_runner="ast_tools", skills=["sql"],
            )

        def under_b(task_id, verdict):
            self.scorecard.record_verdict(
                "builder", model, task_id, verdict,
                signature=sig_b, harness="socket", tool_runner="shell_tools", skills=["sql", "docs"],
            )

        under_a("t1", "success")
        under_a("t2", "success")
        under_b("t3", "discrepancy")
        under_a("t4", "discrepancy")
        under_b("t5", "success")

        live = dict(self.scorecard._standings)
        live_observations = dict(self.scorecard._observations)

        # The two signatures must have diverged from each other and from the aggregate,
        # or the reload check below would pass even while conflating them.
        self.assertNotEqual(live[sig_a], live[sig_b])
        self.assertNotEqual(live[sig_a], live[self.scorecard._key("builder", model)])
        self.assertNotEqual(live[sig_b], live[self.scorecard._key("builder", model)])

        # Same for the counts: a uniform count across keys would make the equality check below
        # pass on a scorecard that counted nothing per-axis.
        self.assertEqual(self.scorecard.get_observations("builder", model), 5)
        self.assertEqual(self.scorecard.get_observations(sig_a), 3)
        self.assertEqual(self.scorecard.get_observations(sig_b), 2)

        reloaded = Scorecard(store=self.store)
        self.assertEqual(reloaded._standings, live)
        self.assertEqual(reloaded._observations, live_observations)

        self.assertEqual(reloaded.get_standing("builder", model),
                         self.scorecard.get_standing("builder", model))
        self.assertEqual(reloaded.get_standing(sig_a), self.scorecard.get_standing(sig_a))
        self.assertEqual(reloaded.get_standing(sig_b), self.scorecard.get_standing(sig_b))

        for key in (f"harness:wire:{model}", f"harness:socket:{model}",
                    f"tools:ast_tools:{model}", f"tools:shell_tools:{model}",
                    f"skill:sql:{model}", f"skill:docs:{model}"):
            self.assertNotEqual(reloaded.get_standing(key), 10.0, key)
            self.assertEqual(reloaded.get_standing(key), self.scorecard.get_standing(key), key)

    def test_reload_falls_back_to_standing_after_for_record_without_delta(self):
        self.store.append(
            StoreRecord(
                kind="scorecard",
                record={
                    "role": "reviewer",
                    "model": "nvidia/nemotron",
                    "task_id": "legacy_1",
                    "verdict": "success",
                    "standing_after": 13.5,
                },
            )
        )

        reloaded = Scorecard(store=self.store)
        self.assertEqual(reloaded.get_standing("reviewer", "nvidia/nemotron"), 13.5)
        self.assertEqual(reloaded.get_observations("reviewer", "nvidia/nemotron"), 1)

    def test_observations_count_every_derived_key_family(self):
        model = "gemini-3.6-flash"
        sig = f"builder:{model}:wire:ast_tools"
        for i, verdict in enumerate(("success", "discrepancy", "success")):
            self.scorecard.record_verdict(
                "builder", model, f"t{i}", verdict,
                signature=sig, harness="wire", tool_runner="ast_tools", skills=["sql", "docs"],
            )

        for key in (sig, f"harness:wire:{model}", f"tools:ast_tools:{model}",
                    f"skill:sql:{model}", f"skill:docs:{model}"):
            self.assertEqual(self.scorecard.get_observations(key), 3, key)
        self.assertEqual(self.scorecard.get_observations("builder", model), 3)

        # An unseen key is 0 observations, not a missing-key error.
        self.assertEqual(self.scorecard.get_observations("builder", "nvidia/nemotron"), 0)
        self.assertEqual(self.scorecard.get_observations(f"harness:socket:{model}"), 0)

    def test_equal_standings_are_distinguished_by_observation_count(self):
        self.scorecard.record_verdict("builder", "one-trial", "t1", "success")
        for i, verdict in enumerate(("success", "success", "failed")):
            self.scorecard.record_verdict("builder", "fifty-trials", f"t{i}", verdict)

        self.assertEqual(self.scorecard.get_standing("builder", "one-trial"),
                         self.scorecard.get_standing("builder", "fifty-trials"))
        self.assertEqual(self.scorecard.get_observations("builder", "one-trial"), 1)
        self.assertEqual(self.scorecard.get_observations("builder", "fifty-trials"), 3)

    def test_legacy_record_counts_once_and_invents_no_derived_observations(self):
        model = "nvidia/nemotron"
        self.scorecard.record_verdict(
            "reviewer", model, "t1", "success", harness="wire", skills=["sql"],
        )
        self.store.append(
            StoreRecord(
                kind="scorecard",
                record={
                    "role": "reviewer",
                    "model": model,
                    "task_id": "legacy_1",
                    "verdict": "success",
                    "standing_after": 13.5,
                    "harness": "wire",
                    "skills": ["sql"],
                },
            )
        )

        reloaded = Scorecard(store=self.store)
        self.assertEqual(reloaded.get_standing("reviewer", model), 13.5)
        self.assertEqual(reloaded.get_observations("reviewer", model), 2)
        # The delta-less record moves no derived key, so it must not claim evidence on one.
        self.assertEqual(reloaded.get_observations(f"harness:wire:{model}"), 1)
        self.assertEqual(reloaded.get_observations(f"skill:sql:{model}"), 1)

    def test_least_observed_is_deterministic_under_ties(self):
        model = "gemini-3.6-flash"
        wire = f"harness:wire:{model}"
        socket = f"harness:socket:{model}"
        tmux = f"harness:tmux:{model}"

        self.assertIsNone(self.scorecard.least_observed([]))

        # All three unseen: sorted key order decides, whatever order the caller passes them in.
        self.assertEqual(self.scorecard.least_observed([wire, socket, tmux]), socket)
        self.assertEqual(self.scorecard.least_observed([tmux, wire, socket]), socket)

        self.scorecard.record_verdict("builder", model, "t1", "success", harness="socket")
        self.assertEqual(self.scorecard.least_observed([wire, socket, tmux]), tmux)

        self.scorecard.record_verdict("builder", model, "t2", "success", harness="tmux")
        self.scorecard.record_verdict("builder", model, "t3", "failed", harness="tmux")
        self.scorecard.record_verdict("builder", model, "t4", "failed", harness="wire")
        # socket 1, wire 1, tmux 2 -> tie at 1 broken by sorted order, not by insertion order.
        self.assertEqual(self.scorecard.least_observed([wire, socket, tmux]), socket)
        self.assertEqual(self.scorecard.least_observed([tmux, wire, socket]), socket)

        self.scorecard.record_verdict("builder", model, "t5", "success", harness="socket")
        self.assertEqual(self.scorecard.least_observed([wire, socket, tmux]), wire)


    def test_average_delta_ranks_performance_over_accumulated_incumbency(self):
        # Axiom 3 / A3-2:
        # Model A: 50 trials, 50 successes -> avg delta +1.0, n=50
        # Model B: 3 trials, 3 successes -> avg delta +1.0, n=3
        # Model C: 50 trials, 30 successes, 20 failures -> avg delta +0.2, n=50
        # Model D: 0 trials -> avg delta None (unknown)
        # Model E: 10 trials, 10 failures -> avg delta -1.0, n=10
        for i in range(50):
            self.scorecard.record_verdict("builder", "model-a", f"t_a_{i}", "success")
        for i in range(3):
            self.scorecard.record_verdict("builder", "model-b", f"t_b_{i}", "success")
        for i in range(30):
            self.scorecard.record_verdict("builder", "model-c", f"t_c_win_{i}", "success")
        for i in range(20):
            self.scorecard.record_verdict("builder", "model-c", f"t_c_loss_{i}", "failed")
        for i in range(10):
            self.scorecard.record_verdict("builder", "model-e", f"t_e_{i}", "failed")

        self.assertAlmostEqual(self.scorecard.get_average_delta("builder", "model-a"), 1.0)
        self.assertAlmostEqual(self.scorecard.get_average_delta("builder", "model-b"), 1.0)
        self.assertAlmostEqual(self.scorecard.get_average_delta("builder", "model-c"), 0.2)
        self.assertIsNone(self.scorecard.get_average_delta("builder", "model-d"))
        self.assertAlmostEqual(self.scorecard.get_average_delta("builder", "model-e"), -1.0)

        # Ranking: Model B (+1.0 avg, n=3) beats Model C (+0.2 avg, n=50) despite Model C
        # having a higher running total (20.0 pts vs 13.0 pts).
        ranked = self.scorecard.rank_models("builder")
        models_order = [m[0] for m in ranked]
        self.assertEqual(models_order, ["model-a", "model-b", "model-c", "model-e"])

if __name__ == "__main__":
    unittest.main()
