"""Tests for arity terrarium and multi-kernel parallel execution."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity import terrarium
from arity.handlers import JsonlRecordStore
from arity.ledger import Seat, SeatLedger
from arity.roles import BUILDER_ROLE
from arity.terrarium import (
    CandidateSpec,
    TaskRecord,
    TerrariumCandidateResult,
    TerrariumDispatcher,
)
from arity.tools import USER_DELIVERY_MARKER
from arity.types import CallModel, ExecuteTool, ModelCompleted, ToolCompleted


class AlienToolRunner:
    """A third-party ToolRunner: satisfies the Protocol structurally, related to nothing built in."""

    instances: list["AlienToolRunner"] = []

    def __init__(self, workspace_root=None, role=None, message_router=None):
        self.workspace_root = workspace_root
        self.role = role
        self.message_router = message_router
        self.calls: list[str] = []
        AlienToolRunner.instances.append(self)

    def get_schemas(self) -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": "speak",
                "description": "Say something to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        }]

    def execute(self, effect: ExecuteTool) -> ToolCompleted:
        self.calls.append(effect.name)
        return ToolCompleted(
            call_id=effect.call_id,
            tool_name=effect.name,
            output=f"{USER_DELIVERY_MARKER}: {effect.arguments.get('text', '')}",
            is_error=False,
        )


def speaking_model_factory(spoken: str, final_content):
    """A provider that calls speak(text=spoken) once, then ends its turn with final_content."""

    def factory(seat: Seat):
        class SpeakThenStop:
            def __init__(self):
                self.turn = 0

            def call(self, effect: CallModel) -> ModelCompleted:
                self.turn += 1
                if self.turn == 1:
                    return ModelCompleted(
                        content=None,
                        tool_calls=[{
                            "id": "call_speak",
                            "type": "function",
                            "function": {
                                "name": "speak",
                                "arguments": json.dumps({"text": spoken}),
                            },
                        }],
                        usage={"prompt_tokens": 10, "completion_tokens": 5},
                    )
                return ModelCompleted(
                    content=final_content,
                    tool_calls=[],
                    usage={"prompt_tokens": 10, "completion_tokens": 5},
                )

        return SpeakThenStop()

    return factory


class TestTerrariumDispatcher(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.base_ws = Path(self.tmpdir.name)
        # Without an explicit store the dispatcher defaults to the real cwd-relative
        # .arity/records, and running this file would write into the developer's own store.
        self.store = JsonlRecordStore(self.base_ws / "records")

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
        dispatcher = TerrariumDispatcher(ledger=self.ledger, base_workspace=self.base_ws, store=self.store)

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
            store=self.store,
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

    def test_tool_runner_class_resolves_to_a_working_instance(self):
        AlienToolRunner.instances.clear()
        dispatcher = TerrariumDispatcher(
            ledger=self.ledger,
            base_workspace=self.base_ws,
            model_factory=speaking_model_factory("hi", "done"),
            store=self.store,
            quiet=True,
        )
        spec = CandidateSpec(
            seat=self.seat_a,
            role=BUILDER_ROLE,
            harness="wire",
            tool_runner_type=AlienToolRunner,
        )

        res = dispatcher.dispatch_single(
            TaskRecord(brief="Say hi"), spec, run_verification=False
        )

        self.assertEqual(res.status, "completed")
        self.assertEqual(len(AlienToolRunner.instances), 1)
        runner = AlienToolRunner.instances[0]
        self.assertEqual(Path(runner.workspace_root), res.workspace_path)
        self.assertIs(runner.role, BUILDER_ROLE)
        self.assertEqual(runner.calls, ["speak"])

    def test_tool_runner_instance_is_used_as_supplied(self):
        AlienToolRunner.instances.clear()
        supplied = AlienToolRunner(workspace_root=self.base_ws)
        dispatcher = TerrariumDispatcher(
            ledger=self.ledger,
            base_workspace=self.base_ws,
            model_factory=speaking_model_factory("hi", "done"),
            store=self.store,
            quiet=True,
        )
        spec = CandidateSpec(
            seat=self.seat_a,
            role=BUILDER_ROLE,
            harness="wire",
            tool_runner_type=supplied,
        )

        dispatcher.dispatch_single(TaskRecord(brief="Say hi"), spec, run_verification=False)

        self.assertEqual(len(AlienToolRunner.instances), 1)
        self.assertEqual(supplied.calls, ["speak"])

    def test_delivered_output_is_recovered_via_the_shared_marker(self):
        # The dispatcher must key on the constant, not on a literal of its own, so an alien
        # runner that imports it is recovered too.
        self.assertEqual(terrarium.USER_DELIVERY_MARKER, USER_DELIVERY_MARKER)

        AlienToolRunner.instances.clear()
        dispatcher = TerrariumDispatcher(
            ledger=self.ledger,
            base_workspace=self.base_ws,
            # A kernel that speaks through the tool and then stops with empty content.
            model_factory=speaking_model_factory("A beats B on hidden tests", ""),
            store=self.store,
            quiet=True,
        )
        spec = CandidateSpec(
            seat=self.seat_a,
            role=BUILDER_ROLE,
            harness="wire",
            tool_runner_type=AlienToolRunner(workspace_root=self.base_ws),
        )

        res = dispatcher.dispatch_single(
            TaskRecord(brief="Rank the candidates"), spec, run_verification=False
        )

        self.assertEqual(res.output, "A beats B on hidden tests")
        self.assertIn("A beats B on hidden tests", res.self_report or "")


if __name__ == "__main__":
    unittest.main()
