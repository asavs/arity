"""Tests for the Multi-Dimensional A/B/C Matrix, CandidateSpec, and the arity race runner."""
from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.archivist import ArchivistEntry, ImpartialArchivist
from arity.handlers import JsonlRecordStore, LocalToolRunner
from arity.ledger import Seat, SeatLedger
from arity.roles import Role, BUILDER_ROLE, PYTHON_DEVELOPER_ROLE, REVIEWER_ROLE
from arity.scorecard import Scorecard, ScorecardRecord
from arity.skills import FIRECRAWL_SKILL, PYTEST_TDD_SKILL, Skill
from arity.terrarium import (
    CandidateSpec,
    TaskRecord,
    TerrariumCandidateResult,
    TerrariumDispatcher,
    run_sandbox_verification,
)
from arity.tools import SandboxToolRunner, create_mcp_tool_runner
from arity.types import CallModel, ModelCompleted


class TestCandidateSpecAndSignatures(unittest.TestCase):
    """Test CandidateSpec multidimensional signatures across stack axes."""

    def test_signature_combinations(self):
        seat_gemini = Seat(id="gemini-flash", provider="gemini", model="gemini-3.6-flash")
        seat_gpt = Seat(id="gpt-sol", provider="codex", model="gpt-5.6-sol")
        seat_claude = Seat(id="claude-sonnet", provider="omp", model="claude-3-7-sonnet")

        builder_role = Role(name="builder", description="Builder", system_prompt="Build")

        # 1. Wire + AST Tools + pytest-tdd
        cand_a = CandidateSpec(
            seat=seat_gemini,
            role=builder_role,
            harness="wire",
            tool_runner_type="sandbox",
            skills=["pytest-tdd"],
        )
        self.assertEqual(
            cand_a.signature(),
            "builder:gemini-3.6-flash:wire:ast_tools:pytest-tdd",
        )

        # 2. CLI + MCP Tools + pytest-tdd
        cand_b = CandidateSpec(
            seat=seat_gpt,
            role=builder_role,
            harness="cli",
            tool_runner_type="mcp",
            skills=["pytest-tdd"],
        )
        self.assertEqual(
            cand_b.signature(),
            "builder:gpt-5.6-sol:cli:mcp_tools:pytest-tdd",
        )

        # 3. OMP + Shell Tools + No skill (baseline)
        cand_c = CandidateSpec(
            seat=seat_claude,
            role=builder_role,
            harness="omp",
            tool_runner_type="shell",
            skills=[],
        )
        self.assertEqual(
            cand_c.signature(),
            "builder:claude-3-7-sonnet:omp:shell_tools",
        )

        # 4. Custom Skill objects
        cand_d = CandidateSpec(
            seat=seat_gemini,
            role=PYTHON_DEVELOPER_ROLE,
            harness="wire",
            tool_runner_type="ast",
            skills=[PYTEST_TDD_SKILL, FIRECRAWL_SKILL],
        )
        self.assertIn("pytest-tdd", cand_d.signature())
        self.assertIn("firecrawl-developer-index", cand_d.signature())
        self.assertTrue(cand_d.signature().startswith("python_developer:gemini-3.6-flash:wire:ast_tools:"))


class TestMultidimensionalScorecard(unittest.TestCase):
    """Test Scorecard combination standings tracking and ranking."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.store_path = Path(self.tmpdir.name) / "test_store.jsonl"
        self.store = JsonlRecordStore(self.store_path)
        self.scorecard = Scorecard(store=self.store)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_record_verdict_updates_combination_signatures(self):
        # Default baseline standing = 10.0 pts
        sig_a = "builder:gemini-3.6-flash:wire:ast_tools:pytest-tdd"
        sig_b = "builder:gpt-5.6-sol:cli:mcp_tools:pytest-tdd"

        self.assertEqual(self.scorecard.get_standing(sig_a), 10.0)
        self.assertEqual(self.scorecard.get_standing(sig_b), 10.0)

        # Success on sig_a (+1.0 pt)
        self.scorecard.record_verdict(
            role="builder",
            model="gemini-3.6-flash",
            task_id="t1",
            verdict="success",
            signature=sig_a,
            harness="wire",
            tool_runner="ast_tools",
            skills=["pytest-tdd"],
        )
        self.assertEqual(self.scorecard.get_standing(sig_a), 11.0)
        self.assertEqual(self.scorecard.get_standing("harness:wire:gemini-3.6-flash"), 11.0)
        self.assertEqual(self.scorecard.get_standing("tools:ast_tools:gemini-3.6-flash"), 11.0)
        self.assertEqual(self.scorecard.get_standing("skill:pytest-tdd:gemini-3.6-flash"), 11.0)

        # Discrepancy on sig_b (-2.5 pts Axiom 9 penalty)
        self.scorecard.record_verdict(
            role="builder",
            model="gpt-5.6-sol",
            task_id="t2",
            verdict="discrepancy",
            signature=sig_b,
            harness="cli",
            tool_runner="mcp_tools",
            skills=["pytest-tdd"],
        )
        self.assertEqual(self.scorecard.get_standing(sig_b), 7.5)

        # Combination ranking
        combos = self.scorecard.rank_combinations()
        self.assertEqual(len(combos), 2)
        self.assertEqual(combos[0][0], sig_a)
        self.assertEqual(combos[0][1], 11.0)
        self.assertEqual(combos[1][0], sig_b)
        self.assertEqual(combos[1][1], 7.5)

    def test_scorecard_replays_combinations_from_store(self):
        sig = "builder:gemini-3.6-flash:wire:ast_tools:pytest-tdd"
        self.scorecard.record_verdict(
            role="builder",
            model="gemini-3.6-flash",
            task_id="t1",
            verdict="success",
            signature=sig,
        )
        self.scorecard.record_verdict(
            role="builder",
            model="gemini-3.6-flash",
            task_id="t2",
            verdict="success",
            signature=sig,
        )
        self.assertEqual(self.scorecard.get_standing(sig), 12.0)

        # Re-instantiate from store
        restored = Scorecard(store=self.store)
        self.assertEqual(restored.get_standing(sig), 12.0)


class TestSandboxVerification(unittest.TestCase):
    """Test in-sandbox unit test runner and verification."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.ws = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_run_verification_on_passing_tests(self):
        # Write module and passing test file
        (self.ws / "calc.py").write_text("def add(a, b): return a + b\n")
        (self.ws / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n"
            "    def test_add_neg(self):\n"
            "        self.assertEqual(add(-1, 1), 0)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        )

        res = run_sandbox_verification(self.ws)
        self.assertTrue(res["has_tests"])
        self.assertEqual(res["passed"], 2)
        self.assertEqual(res["failed"], 0)
        self.assertEqual(res["exit_code"], 0)

    def test_run_verification_on_failing_tests(self):
        (self.ws / "test_fail.py").write_text(
            "import unittest\n\n"
            "class TestFail(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertEqual(1, 1)\n"
            "    def test_bad(self):\n"
            "        self.assertEqual(1, 2)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        )
        res = run_sandbox_verification(self.ws)
        self.assertTrue(res["has_tests"])
        self.assertEqual(res["failed"], 1)
        self.assertNotEqual(res["exit_code"], 0)


class TestTerrariumRaceExecution(unittest.TestCase):
    """Test multi-candidate parallel race execution, trial evaluation, and winner picking."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.base_ws = Path(self.tmpdir.name)
        self.store = JsonlRecordStore(self.base_ws / "records.jsonl")

        self.seat_wire = Seat(id="gemini-cand", provider="gemini", model="gemini-3.6-flash")
        self.seat_cli = Seat(id="codex-cand", provider="codex", model="gpt-5.6-sol")
        self.seat_omp = Seat(id="omp-cand", provider="omp", model="claude-3-7-sonnet")

        self.ledger = SeatLedger(
            initial_seats=[self.seat_wire, self.seat_cli, self.seat_omp],
            auto_seed=False,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_race_with_three_variants_and_impartial_judge(self):
        # Create Mock providers for 3 candidates with different outcomes:
        # Candidate 1: Writes code + passing tests (Winner)
        # Candidate 2: Writes code + failing tests
        # Candidate 3: Claims changes but writes no files (Discrepancy)

        class WinningMockProvider:
            def __init__(self):
                self.turn = 0

            def call(self, effect: CallModel) -> ModelCompleted:
                self.turn += 1
                if self.turn == 1:
                    return ModelCompleted(
                        content="Writing cache implementation and tests",
                        tool_calls=[
                            {
                                "id": "c1_w1",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({
                                        "path": "cache.py",
                                        "content": "class Cache:\n    def get(self, k): return 42\n",
                                    }),
                                },
                            },
                            {
                                "id": "c1_w2",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({
                                        "path": "test_cache.py",
                                        "content": "import unittest\nfrom cache import Cache\nclass TC(unittest.TestCase):\n    def test_g(self): self.assertEqual(Cache().get('x'), 42)\nif __name__=='__main__': unittest.main()\n",
                                    }),
                                },
                            },
                        ],
                        usage={"prompt_tokens": 100, "completion_tokens": 80},
                        finish_reason="tool_calls",
                        seat_id="cand_1",
                    )
                return ModelCompleted(
                    content="Completed cache and verified all tests pass.",
                    tool_calls=[],
                    usage={"prompt_tokens": 120, "completion_tokens": 20},
                    finish_reason="stop",
                    seat_id="cand_1",
                )

        class FailingTestMockProvider:
            def __init__(self):
                self.turn = 0

            def call(self, effect: CallModel) -> ModelCompleted:
                self.turn += 1
                if self.turn == 1:
                    return ModelCompleted(
                        content="Writing broken tests",
                        tool_calls=[
                            {
                                "id": "c2_w1",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({
                                        "path": "test_broken.py",
                                        "content": "import unittest\nclass TB(unittest.TestCase):\n    def test_b(self): self.assertEqual(1, 99)\nif __name__=='__main__': unittest.main()\n",
                                    }),
                                },
                            },
                        ],
                        usage={"prompt_tokens": 100, "completion_tokens": 80},
                        finish_reason="tool_calls",
                        seat_id="cand_2",
                    )
                return ModelCompleted(
                    content="Tests written.",
                    tool_calls=[],
                    usage={"prompt_tokens": 120, "completion_tokens": 20},
                    finish_reason="stop",
                    seat_id="cand_2",
                )

        class HallucinatingMockProvider:
            def call(self, effect: CallModel) -> ModelCompleted:
                return ModelCompleted(
                    content="I created file database.py and wrote tests.",
                    tool_calls=[],
                    usage={"prompt_tokens": 100, "completion_tokens": 30},
                    finish_reason="stop",
                    seat_id="cand_3",
                )

        cand_1 = CandidateSpec(
            seat=self.seat_wire,
            name="Wire + AST Tools (Winner)",
            role=BUILDER_ROLE,
            harness="wire",
            tool_runner_type="sandbox",
            skills=["pytest-tdd"],
            custom_model_provider=WinningMockProvider(),
        )

        cand_2 = CandidateSpec(
            seat=self.seat_cli,
            name="CLI + MCP (Failing Tests)",
            role=BUILDER_ROLE,
            harness="cli",
            tool_runner_type="mcp",
            skills=["pytest-tdd"],
            custom_model_provider=FailingTestMockProvider(),
        )

        cand_3 = CandidateSpec(
            seat=self.seat_omp,
            name="OMP (Discrepancy)",
            role=BUILDER_ROLE,
            harness="omp",
            tool_runner_type="shell",
            skills=[],
            custom_model_provider=HallucinatingMockProvider(),
        )

        dispatcher = TerrariumDispatcher(
            ledger=self.ledger,
            store=self.store,
            base_workspace=self.base_ws,
        )

        task = TaskRecord(brief="Build cache with unit tests")
        scorecard = Scorecard(store=self.store)
        archivist = ImpartialArchivist(scorecard=scorecard, store=self.store)

        winner, results, entries = dispatcher.race(
            task=task,
            candidates=[cand_1, cand_2, cand_3],
            archivist=archivist,
        )

        self.assertEqual(len(results), 3)
        self.assertEqual(len(entries), 3)

        # Verify Candidate 1 is chosen as Winner
        self.assertIsNotNone(winner)
        self.assertEqual(winner.spec.name, "Wire + AST Tools (Winner)")

        # Verify entry verdicts
        entry_1 = next(e for e in entries if e.candidate_id == winner.candidate_id)
        self.assertEqual(entry_1.verdict, "success")
        self.assertIn("cache.py", entry_1.verified_artifacts)
        self.assertIn("test_cache.py", entry_1.verified_artifacts)

        entry_2 = next(e for e in entries if e.candidate_id == next(r for r in results if r.spec.name == "CLI + MCP (Failing Tests)").candidate_id)
        self.assertEqual(entry_2.verdict, "failed")

        entry_3 = next(e for e in entries if e.candidate_id == next(r for r in results if r.spec.name == "OMP (Discrepancy)").candidate_id)
        self.assertEqual(entry_3.verdict, "discrepancy")

        # Verify Scorecard Combination Standings
        self.assertGreater(scorecard.get_standing(cand_1.signature()), 10.0)
        self.assertLess(scorecard.get_standing(cand_3.signature()), 10.0)


if __name__ == "__main__":
    unittest.main()
