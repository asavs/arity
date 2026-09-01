"""Tests for Arity quiet-failure and data-loss policy (Axiom 12, A12-2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.archivist import ImpartialArchivist
from arity.diagnostics import get_data_loss_count, get_data_loss_reasons, record_data_loss, reset_data_loss_count
from arity.ledger import Seat
from arity.handlers import JsonlRecordStore
from arity.roles import BUILDER_ROLE, RoleRegistry
from arity.runtime import Runtime
from arity.scorecard import Scorecard
from arity.seams import Observer, RecordStore
from arity.tasks import TaskBank
from arity.terrarium import TerrariumCandidateResult
from arity.transports import RedphoneInbox
from arity.types import EmitMessage, Event, State, Status, StoreRecord, UserMessage


class _FailingStore:
    def append(self, effect: StoreRecord) -> None:
        raise OSError("Simulated disk full failure")

    def query(self, kind: str, **filters) -> list[dict]:
        raise OSError("Simulated disk read failure")


class _BrokenObserver(Observer):
    def on_event(self, state: State, event: Event) -> None:
        raise TypeError("Simulated observer type mismatch")

    def on_effect(self, state: State, effect: Any) -> None:
        raise AttributeError("Simulated observer attribute error")


class TestQuietFailuresAndDataLoss(unittest.TestCase):
    def setUp(self):
        reset_data_loss_count()

    def tearDown(self):
        reset_data_loss_count()

    def test_record_data_loss_increments_count_and_stores_reasons(self):
        self.assertEqual(get_data_loss_count(), 0)
        record_data_loss("TestDrop", ValueError("Disk full"))
        self.assertEqual(get_data_loss_count(), 1)
        reasons = get_data_loss_reasons()
        self.assertEqual(len(reasons), 1)
        self.assertIn("TestDrop", reasons[0])
        self.assertIn("Disk full", reasons[0])

        reset_data_loss_count()
        self.assertEqual(get_data_loss_count(), 0)
        self.assertEqual(get_data_loss_reasons(), [])

    def test_runtime_tracks_data_loss_on_store_append_failure(self):
        rt = Runtime(store=_FailingStore())
        state = State(session_id="s1", status=Status.IDLE)
        # UserMessage generates StoreRecord(kind="message") and CallModel
        rt.step(state, UserMessage(text="hello", sender="user"))
        self.assertGreater(get_data_loss_count(), 0)
        self.assertTrue(any("StoreRecord" in r for r in get_data_loss_reasons()))

    def test_runtime_raises_on_observer_type_or_attribute_error(self):
        rt = Runtime(observers=[_BrokenObserver()])
        state = State(session_id="s1", status=Status.IDLE)
        with self.assertRaises(TypeError):
            rt.step(state, UserMessage(text="hello", sender="user"))

    def test_scorecard_tracks_data_loss_on_store_append_failure(self):
        card = Scorecard(store=_FailingStore())
        rec = card.record_verdict("builder", "gemini-3.6-flash", "t1", "success")
        self.assertIsNotNone(rec)
        self.assertGreater(get_data_loss_count(), 0)
        self.assertTrue(any("ScorecardRecord" in r for r in get_data_loss_reasons()))

    def test_archivist_tracks_data_loss_on_store_append_failure(self):
        arch = ImpartialArchivist(store=_FailingStore())
        reset_data_loss_count()
        with TemporaryDirectory() as d:
            res = TerrariumCandidateResult(
                candidate_id="c1",
                task_id="t1",
                seat=Seat(provider="google", model="gemini-3.6-flash"),
                role=BUILDER_ROLE,
                final_state=State(session_id="c1", status=Status.IDLE),
                output="done",
                self_report="Created app.py",
                tokens_used=100,
                duration_seconds=1.0,
                workspace_path=Path(d),
                status="completed",
            )
            entry = arch.audit(res)
            self.assertGreater(get_data_loss_count(), 0)
            self.assertTrue(any("ArchivistEntry" in r for r in get_data_loss_reasons()))
    def test_redphone_inbox_tracks_data_loss_on_store_append_failure(self):
        inbox = RedphoneInbox(store=_FailingStore())
        inbox.post(channel="general", sender="alice", text="hi")
        self.assertGreater(get_data_loss_count(), 0)
        self.assertTrue(any("RedphoneMessage" in r for r in get_data_loss_reasons()))

    def test_jsonl_store_tracks_corrupted_lines_as_data_loss(self):
        with TemporaryDirectory() as d:
            store = JsonlRecordStore(root=Path(d))
            p = store._path("scorecard")
            p.write_text('{"valid": 1}\n{not valid json\n{"valid": 2}\n', encoding="utf-8")

            results = store.query("scorecard")
            self.assertEqual(len(results), 2)
            self.assertEqual(get_data_loss_count(), 1)
            self.assertTrue(any("JsonlCorruptLine" in r for r in get_data_loss_reasons()))


if __name__ == "__main__":
    unittest.main()
