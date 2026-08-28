"""Tests for arity terrarium and multi-kernel parallel execution."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.ledger import Seat, SeatLedger
from arity.roles import BUILDER_ROLE
from arity.terrarium import TaskRecord, TerrariumCandidateResult, TerrariumDispatcher
from arity.types import CallModel, ModelCompleted


class TestTerrariumDispatcher(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.base_ws = Path(self.tmpdir.name)

        self.seat_a = Seat(
            id="gemini-candidate",
            provider="gemini",
            endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini-3.6-flash",
            total_allowance=1_000_000,
            remaining=1_000_000,
        )
        self.seat_b = Seat(
            id="nemotron-candidate",
            provider="nim",
            endpoint="https://integrate.api.nvidia.com/v1",
            model="nvidia/nemotron-3-nano-30b-a3b",
            total_allowance=500_000,
            remaining=500_000,
        )

        self.ledger = SeatLedger(initial_seats=[self.seat_a, self.seat_b], auto_seed=False)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_depth_exceeded_check(self):
        task = TaskRecord(brief="Too deep", depth=3, max_depth=3)
        dispatcher = TerrariumDispatcher(ledger=self.ledger, base_workspace=self.base_ws)

        res = dispatcher.dispatch_single(task, self.seat_a, BUILDER_ROLE)
        self.assertEqual(res.status, "depth_exceeded")
        self.assertIn("exceeded", res.output or "")

    def test_parallel_execution_and_workspace_isolation(self):
        # Create mock model factory simulating tool execution for both candidates
        def mock_model_factory(seat: Seat):
            class SequenceMockProvider:
                def __init__(self, s_name: str):
                    self.s_name = s_name
                    self.turn = 0

                def call(self, effect: CallModel) -> ModelCompleted:
                    self.turn += 1
                    if self.turn == 1:
                        # Write a candidate-specific file in sandbox
                        return ModelCompleted(
                            content=None,
                            tool_calls=[
                                {
                                    "id": f"write_{self.s_name}",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": f'{{"path": "result.txt", "content": "Built by {self.s_name}"}}',
                                    },
                                }
                            ],
                            usage={"prompt_tokens": 100, "completion_tokens": 30},
                        )
                    return ModelCompleted(
                        content=f"Finished by {self.s_name}",
                        tool_calls=[],
                        usage={"prompt_tokens": 150, "completion_tokens": 20},
                    )

            return SequenceMockProvider(seat.id)

        dispatcher = TerrariumDispatcher(
            ledger=self.ledger,
            base_workspace=self.base_ws,
            model_factory=mock_model_factory,
        )

        task = TaskRecord(brief="Write result.txt with your signature")
        results = dispatcher.dispatch_parallel(task, [self.seat_a, self.seat_b], BUILDER_ROLE)

        self.assertEqual(len(results), 2)

        # Check results from both candidates
        res_a = next(r for r in results if r.seat.id == "gemini-candidate")
        res_b = next(r for r in results if r.seat.id == "nemotron-candidate")

        self.assertEqual(res_a.status, "completed")
        self.assertEqual(res_b.status, "completed")
        self.assertIn("gemini-candidate", res_a.output or "")
        self.assertIn("nemotron-candidate", res_b.output or "")

        # Verify isolated workspaces have isolated files
        file_a = res_a.workspace_path / "result.txt"
        file_b = res_b.workspace_path / "result.txt"

        self.assertTrue(file_a.exists())
        self.assertTrue(file_b.exists())
        self.assertEqual(file_a.read_text(), "Built by gemini-candidate")
        self.assertEqual(file_b.read_text(), "Built by nemotron-candidate")

        # Verify token metering deducted tokens in ledger
        self.assertLess(self.seat_a.remaining, 1_000_000)
        self.assertLess(self.seat_b.remaining, 500_000)


if __name__ == "__main__":
    unittest.main()
