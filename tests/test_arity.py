"""Tests for the original Arity architecture."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.handlers import (
    JsonlRecordStore,
    LocalToolRunner,
    MetricsObserver,
)
from arity.runtime import Runtime
from arity.seams import ModelProvider, Observer, RecordStore, ToolRunner, Transport
from arity.transition import transition
from arity.types import (
    CallModel,
    EmitMessage,
    ExecuteTool,
    Halt,
    HandoffCompleted,
    HandoffRequested,
    Interrupt,
    ModelCompleted,
    ModelFailed,
    PulseTick,
    SpawnHandoff,
    State,
    Status,
    StoreRecord,
    ToolCompleted,
    UserMessage,
)


class TestTransition(unittest.TestCase):
    """Test statechart transitions without I/O."""

    def setUp(self):
        self.state = State(session_id="test_001")

    def test_user_message_initiates_call_model(self):
        new_state, effects = transition(self.state, UserMessage(text="Hello Arity"))
        self.assertEqual(new_state.status, Status.WAITING_MODEL)
        self.assertEqual(len(new_state.messages), 1)
        self.assertEqual(new_state.messages[0]["content"], "Hello Arity")

        # Must generate StoreRecord and CallModel effects
        has_store = any(isinstance(e, StoreRecord) for e in effects)
        has_call = any(isinstance(e, CallModel) for e in effects)
        self.assertTrue(has_store)
        self.assertTrue(has_call)

    def test_model_completed_text_response(self):
        # Set waiting state
        self.state.status = Status.WAITING_MODEL
        self.state.messages.append({"role": "user", "content": "Hi"})

        new_state, effects = transition(
            self.state,
            ModelCompleted(content="Hello there!", usage={"total_tokens": 15}),
        )
        self.assertEqual(new_state.status, Status.IDLE)
        self.assertEqual(new_state.output, "Hello there!")
        self.assertEqual(len(new_state.messages), 2)
        self.assertEqual(new_state.messages[1]["content"], "Hello there!")

        has_emit = any(isinstance(e, EmitMessage) and e.text == "Hello there!" for e in effects)
        self.assertTrue(has_emit)

    def test_model_completed_with_tools_emits_execute_tools(self):
        self.state.status = Status.WAITING_MODEL
        new_state, effects = transition(
            self.state,
            ModelCompleted(
                content="Checking status",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "foo.txt"}'},
                    }
                ],
            ),
        )
        self.assertEqual(new_state.status, Status.WAITING_TOOLS)
        self.assertIn("c1", new_state.pending_tool_calls)

        exec_effects = [e for e in effects if isinstance(e, ExecuteTool)]
        self.assertEqual(len(exec_effects), 1)
        self.assertEqual(exec_effects[0].name, "read_file")
        self.assertEqual(exec_effects[0].arguments, {"path": "foo.txt"})

    def test_tool_completed_resumes_model_call_when_all_resolved(self):
        self.state.status = Status.WAITING_TOOLS
        self.state.pending_tool_calls["c1"] = {"id": "c1", "name": "read_file"}

        new_state, effects = transition(
            self.state,
            ToolCompleted(call_id="c1", tool_name="read_file", output="contents of foo.txt"),
        )
        self.assertEqual(len(new_state.pending_tool_calls), 0)
        self.assertEqual(new_state.status, Status.WAITING_MODEL)
        self.assertEqual(new_state.messages[-1]["role"], "tool")
        self.assertEqual(new_state.messages[-1]["content"], "contents of foo.txt")

        has_call = any(isinstance(e, CallModel) for e in effects)
        self.assertTrue(has_call)

    def test_model_failed_retry_and_halt(self):
        self.state.status = Status.WAITING_MODEL
        self.state.max_errors = 2

        # Error 1: Retry
        new_state, effects = transition(self.state, ModelFailed(error="Rate limit", retryable=True))
        self.assertEqual(new_state.error_count, 1)
        has_call = any(isinstance(e, CallModel) for e in effects)
        self.assertTrue(has_call)

        # Error 2: Retry
        new_state, effects = transition(new_state, ModelFailed(error="Rate limit", retryable=True))
        self.assertEqual(new_state.error_count, 2)
        has_call = any(isinstance(e, CallModel) for e in effects)
        self.assertTrue(has_call)

        # Error 3: Exceeds max_errors -> Halt
        new_state, effects = transition(new_state, ModelFailed(error="Rate limit", retryable=True))
        self.assertEqual(new_state.status, Status.HALTED)
        has_halt = any(isinstance(e, Halt) for e in effects)
        self.assertTrue(has_halt)

    def test_interrupt_halts_session(self):
        new_state, effects = transition(self.state, Interrupt(reason="User clicked cancel"))
        self.assertEqual(new_state.status, Status.HALTED)
        has_halt = any(isinstance(e, Halt) for e in effects)
        self.assertTrue(has_halt)


class TestRuntimeEndToEnd(unittest.TestCase):
    """Test Runtime orchestrating seams with deterministic mocks."""

    def test_multi_turn_tool_execution(self):
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            class SequenceModelProvider:
                def __init__(self):
                    self.turn = 0

                def call(self, effect: CallModel) -> ModelCompleted:
                    self.turn += 1
                    if self.turn == 1:
                        return ModelCompleted(
                            content=None,
                            tool_calls=[
                                {
                                    "id": "write_1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": '{"path": "test.txt", "content": "hello world"}',
                                    },
                                }
                            ],
                            usage={"prompt_tokens": 50, "completion_tokens": 20},
                        )
                    elif self.turn == 2:
                        return ModelCompleted(
                            content="Wrote the file successfully!",
                            tool_calls=[],
                            usage={"prompt_tokens": 80, "completion_tokens": 10},
                        )
                    raise RuntimeError("Unexpected turn")

            tool_runner = LocalToolRunner(workspace_root=tmppath)
            store = JsonlRecordStore(root=tmppath / "records")
            metrics = MetricsObserver()

            runtime = Runtime(
                model_provider=SequenceModelProvider(),
                tool_runner=tool_runner,
                store=store,
                observers=[metrics],
            )

            output, final_state = runtime.chat("Create test.txt please")

            self.assertEqual(output, "Wrote the file successfully!")
            self.assertEqual(final_state.status, Status.IDLE)
            self.assertEqual(metrics.total_tool_calls, 1)
            self.assertEqual(metrics.total_prompt_tokens, 130)
            self.assertEqual(metrics.total_completion_tokens, 30)

            # Confirm file created on disk
            created = tmppath / "test.txt"
            self.assertTrue(created.exists())
            self.assertEqual(created.read_text(), "hello world")

            # Confirm records persisted in JSONL
            records = store.query("tool_result")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["tool_name"], "write_file")


if __name__ == "__main__":
    unittest.main()
