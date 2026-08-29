"""Tests for arity master orchestrator and end-to-end multi-part integration."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.handlers import JsonlRecordStore
from arity.ledger import Seat, SeatLedger
from arity.orchestrator import ArityOrchestrator
from arity.types import CallModel, ModelCompleted


class TestArityOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.ws = Path(self.tmpdir.name)
        self.store = JsonlRecordStore(root=self.ws / "records")

        # Candidate seats
        self.seat_gemini = Seat(
            id="gemini-fast",
            provider="gemini",
            endpoint="https://api.google.com",
            model="gemini-3.6-flash",
            kind="quota",
            total_allowance=1_000_000,
            remaining=800_000,
            reset_deadline=10000.0 + 1800,  # 30m left
            base_price_per_m=0.10,
        )
        self.seat_gpt = Seat(
            id="gpt-voice",
            provider="openai",
            endpoint="https://api.openai.com",
            model="gpt-4o",
            kind="quota",
            total_allowance=1_000_000,
            remaining=800_000,
            reset_deadline=10000.0 + 86400,
            base_price_per_m=2.50,
        )

        self.ledger = SeatLedger(initial_seats=[self.seat_gemini, self.seat_gpt], auto_seed=False)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_full_delegation_pipeline(self):
        # Mock model factory simulating tool execution for the builder
        def mock_model_factory(seat: Seat):
            class MockBuilderProvider:
                def __init__(self):
                    self.turn = 0

                def call(self, effect: CallModel) -> ModelCompleted:
                    self.turn += 1
                    if self.turn == 1:
                        # Write the requested schema file
                        return ModelCompleted(
                            content="Writing the schema",
                            tool_calls=[
                                {
                                    "id": "tc_write",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": '{"path": "brokie/schema.sql", "content": "CREATE TABLE deals (id INT);"}',
                                    },
                                }
                            ],
                            usage={"prompt_tokens": 80, "completion_tokens": 30},
                        )
                    return ModelCompleted(
                        content="Successfully created brokie/schema.sql with deals table.",
                        tool_calls=[],
                        usage={"prompt_tokens": 120, "completion_tokens": 20},
                    )

            return MockBuilderProvider()

        orchestrator = ArityOrchestrator(
            ledger=self.ledger,
            store=self.store,
            base_workspace=self.ws / "terrarium",
            model_factory=mock_model_factory,
        )

        # User asks for a builder task
        response = orchestrator.handle_message(
            user_text="implement a new table schema in brokie/schema.sql",
            sender="Asa",
            now=10000.0,
        )

        # 1. Delegation occurred
        self.assertIsNotNone(response.delegated_task)
        self.assertEqual(response.delegated_task.to_role, "developer:python")

        # 2. Winning candidate finished successfully
        self.assertIsNotNone(response.winning_candidate)
        self.assertEqual(response.winning_candidate.status, "completed")

        # 3. Archivist audited physical artifact
        self.assertEqual(len(response.archivist_entries), 1)
        entry = response.archivist_entries[0]
        self.assertEqual(entry.verdict, "success")
        self.assertIn("brokie/schema.sql", entry.verified_artifacts)

        # 4. Verified file exists in winning candidate sandbox
        created_file = response.winning_candidate.workspace_path / "brokie/schema.sql"
        self.assertTrue(created_file.exists())
        self.assertEqual(created_file.read_text(), "CREATE TABLE deals (id INT);")

        # 5. Scorecard standing increased
        self.assertGreater(orchestrator.scorecard.get_standing("developer:python", "gemini-3.6-flash"), 10.0)

        # 6. Pulse tick discovers expiring seat
        pulse_actions = orchestrator.tick_pulse(now=10000.0)
        self.assertTrue(any(a.kind == "harvest_quota" for a in pulse_actions))


if __name__ == "__main__":
    unittest.main()
