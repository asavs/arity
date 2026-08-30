"""Tests for the conference phase: candidates woken up together, peers staged, notes queued between rounds."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.archivist import ImpartialArchivist
from arity.handlers import JsonlRecordStore
from arity.ledger import SeatLedger
from arity.race import GOOD_LRU, OWN_TEST, RaceConfig, ScriptedProvider, placeholder_seats, run_race
from arity.roles import BUILDER_ROLE
from arity.tasks import TaskBank
from arity.terrarium import PEERS_DIR, CandidateSpec, TaskRecord, TerrariumDispatcher
from arity.types import CallModel, ModelCompleted


class ConferenceMock:
    """Round 1: sends a note to the other peer and writes a merged file. Round 2: records notes received."""

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.received: list[str] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(list(effect.messages))
        last_user = next((m.get("content") or "" for m in reversed(effect.messages) if m.get("role") == "user"), "")
        just_used_tools = any(m.get("role") == "tool" for m in effect.messages[-2:])
        if "Conference round 1/" in last_user and not just_used_tools:
            me = re.search(r"You are candidate (\w)", last_user).group(1)
            other = "B" if me == "A" else "A"
            merged = f"# merged by {me}, borrowed from peers/{other}\nX = 1\n"
            return ModelCompleted(
                content="Borrowing.",
                tool_calls=[
                    {"id": f"m_{me}", "type": "function",
                     "function": {"name": "message", "arguments": json.dumps({"to": f"peer:{other}", "text": f"hi from {me}"})}},
                    {"id": f"w_{me}", "type": "function",
                     "function": {"name": "write_file", "arguments": json.dumps({"path": "merged.py", "content": merged})}},
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 10},
                finish_reason="tool_calls",
            )
        if "Conference round 2/" in last_user:
            self.received = re.findall(r"\[from \w\] [^\n]+", last_user)
        return ModelCompleted(content="Final draft in merged.py; kept lru_cache.py.", tool_calls=[],
                              usage={"prompt_tokens": 10, "completion_tokens": 5})


class TestConference(unittest.TestCase):
    def test_conference_wakes_candidates_with_peers_and_queued_notes(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            store = JsonlRecordStore(base / "records")
            disp = TerrariumDispatcher(ledger=SeatLedger(initial_seats=placeholder_seats(), auto_seed=False),
                                       store=store, base_workspace=base / "t")
            seats = placeholder_seats()
            specs = [
                CandidateSpec(seat=seats[0], name="one", role=BUILDER_ROLE,
                              custom_model_provider=ScriptedProvider({"lru_cache.py": GOOD_LRU, "test_lru_cache.py": OWN_TEST}, "Created lru_cache.py.", "1")),
                CandidateSpec(seat=seats[1], name="two", role=BUILDER_ROLE,
                              custom_model_provider=ScriptedProvider({"lru_cache.py": GOOD_LRU}, "Created lru_cache.py.", "2")),
            ]
            task = TaskRecord(brief="lru", hidden_tests=TaskBank().get("lru_cache").hidden_tests)
            archivist = ImpartialArchivist(store=store)
            _, results, entries = disp.race(task=task, candidates=specs, archivist=archivist)

            mocks = {r.spec.name: ConferenceMock() for r in results}
            for r in results:
                r.spec.custom_model_provider = mocks[r.spec.name]

            phase2 = disp.conference(task, results, entries=entries, rounds=2)
            self.assertEqual(len(phase2), 2)
            for r in phase2:
                self.assertTrue(r.candidate_id.endswith("_c2"))
                self.assertTrue((Path(r.workspace_path) / "merged.py").exists())
                self.assertFalse((Path(r.workspace_path) / PEERS_DIR).exists())   # staged copies cleaned up
                self.assertTrue(r.test_results["hidden"]["has_tests"])             # re-verified after the last round
                self.assertEqual(r.test_results["hidden"]["failed"], 0)

            # Round 1 saw the peer's files staged, and the phase-1 transcript replayed (fork context)
            first_call = next(iter(mocks.values())).calls[0]
            self.assertTrue(any(f"{PEERS_DIR}/" in (m.get("content") or "") for m in first_call))
            self.assertGreater(len(first_call), 2)
            # Notes sent in round 1 were delivered in round 2
            for m in mocks.values():
                self.assertTrue(m.received and m.received[0].startswith("[from "), m.received)
            # Phase 2 is audited on its own and peers/ never counts as an artifact
            c_winner, c_entries = archivist.evaluate_trial(phase2)
            self.assertEqual(len(c_entries), 2)
            self.assertIsNotNone(c_winner)
            for e in c_entries:
                self.assertFalse(any(a.startswith(f"{PEERS_DIR}/") for a in e.verified_artifacts))
                self.assertIn("merged.py", e.verified_artifacts)

    def test_peer_message_routing_rules(self):
        with TemporaryDirectory() as d:
            base = Path(d)
            disp = TerrariumDispatcher(ledger=SeatLedger(initial_seats=placeholder_seats(), auto_seed=False),
                                       store=JsonlRecordStore(base / "r"), base_workspace=base / "t")

            class Talker:
                def __init__(self):
                    self.turn = 0

                def call(self, effect: CallModel) -> ModelCompleted:
                    self.turn += 1
                    if self.turn == 1:
                        return ModelCompleted(content="", tool_calls=[
                            {"id": "1", "type": "function", "function": {"name": "message", "arguments": json.dumps({"to": "peer:A", "text": "self"})}},
                            {"id": "2", "type": "function", "function": {"name": "message", "arguments": json.dumps({"to": "peer:Z", "text": "nobody"})}},
                            {"id": "3", "type": "function", "function": {"name": "message", "arguments": json.dumps({"to": "peer:b", "text": "lowercase ok"})}},
                        ], usage={}, finish_reason="tool_calls")
                    return ModelCompleted(content="done", tool_calls=[], usage={})

            mailbox = {"A": [], "B": []}
            spec = CandidateSpec(seat=placeholder_seats()[0], role=BUILDER_ROLE, custom_model_provider=Talker())
            res = disp.dispatch_single(TaskRecord(brief="x"), spec, run_verification=False, mailbox=mailbox, peer_letter="A")
            outputs = [e.get("output_preview", "") for e in res.tool_events]
            self.assertTrue(any("That is you" in o for o in outputs))
            self.assertTrue(any("No peer 'Z'" in o for o in outputs))
            self.assertEqual(mailbox["B"], ["[from A] lowercase ok"])
            self.assertEqual(mailbox["A"], [])

    def test_race_cli_config_runs_conference_and_reports_phase_two(self):
        with TemporaryDirectory() as d:
            rep = run_race(RaceConfig(task_name="lru_cache", mock=True, conference=1, workspace_root=Path(d) / "ws"))
        self.assertEqual(len(rep.conference_results), 3)
        self.assertEqual(len(rep.conference_entries), 3)
        self.assertTrue(all(r.candidate_id.endswith("_c1") for r in rep.conference_results))
        d2 = rep.to_dict()
        self.assertTrue(d2["conference"]["rounds_run"])
        self.assertEqual(len(d2["conference"]["results"]), 3)


if __name__ == "__main__":
    unittest.main()
