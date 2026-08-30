"""Tests for the race runner: context axis, hidden tests, tester harvest, tie detection, task bank, presets."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gorkbot.archivist import ImpartialArchivist
from gorkbot.handlers import JsonlRecordStore
from gorkbot.ledger import Seat, SeatLedger
from gorkbot.race import (
    GOOD_LRU,
    OWN_TEST,
    RaceConfig,
    ScriptedProvider,
    attach_mocks,
    placeholder_seats,
    resolve_candidates,
    run_race,
)
from gorkbot.roles import BUILDER_ROLE, TESTER_ROLE, RoleRegistry
from gorkbot.scorecard import Scorecard
from gorkbot.tasks import TaskBank, load_task_dir
from gorkbot.terrarium import (
    HIDDEN_TESTS_DIR,
    CandidateSpec,
    TaskRecord,
    TerrariumDispatcher,
    normalize_harness,
    normalize_tool_runner,
    run_sandbox_verification,
)
from gorkbot.tools import SandboxToolRunner
from gorkbot.types import CallModel, ModelCompleted


class RecordingProvider:
    """Captures the messages it was called with, then reports without touching disk."""

    def __init__(self):
        self.seen: list[list[dict]] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.seen.append(list(effect.messages))
        return ModelCompleted(content="Done. No files written.", tool_calls=[], usage={"prompt_tokens": 1, "completion_tokens": 1})


class TestNormalizationAndSignature(unittest.TestCase):
    def test_one_normalizer_for_every_spelling(self):
        for raw in ("sandbox", "ast", "native", SandboxToolRunner):
            self.assertEqual(normalize_tool_runner(raw), "ast_tools")
        self.assertEqual(normalize_tool_runner("mcp"), "mcp_tools")
        self.assertEqual(normalize_tool_runner("local"), "shell_tools")
        self.assertEqual(normalize_harness("codex"), "cli")
        self.assertEqual(normalize_harness("wire"), "wire")

    def test_signature_and_display_agree(self):
        spec = CandidateSpec(seat=Seat(provider="google", model="gemini-3.6-flash"), role=BUILDER_ROLE,
                             tool_runner_type="sandbox", skills=["pytest-tdd"])
        self.assertEqual(spec.signature(), "developer.python:gemini-3.6-flash:wire:ast_tools:pytest-tdd")
        self.assertEqual(spec.display_tuple()[2], "ast_tools")

    def test_context_axis_only_appears_when_non_default(self):
        seat = Seat(provider="google", model="m")
        self.assertNotIn("ctx=", CandidateSpec(seat=seat, role=BUILDER_ROLE).signature())
        self.assertTrue(CandidateSpec(seat=seat, role=BUILDER_ROLE, context="fork").signature().endswith(":ctx=fork"))
        with self.assertRaises(ValueError):
            CandidateSpec(seat=seat, context="telepathy")


class TestHiddenTestVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hidden_tests_run_after_and_apart_from_own_tests(self):
        (self.ws / "lru_cache.py").write_text(GOOD_LRU, encoding="utf-8")
        (self.ws / "test_lru_cache.py").write_text(OWN_TEST, encoding="utf-8")
        hidden = {"test_h.py": "from lru_cache import LRUCache\n\ndef test_len():\n    c = LRUCache(1)\n    c.put(1, 1)\n    assert len(c) == 1\n"}
        res = run_sandbox_verification(self.ws, hidden_tests=hidden)
        self.assertEqual((res["own"]["passed"], res["hidden"]["passed"]), (1, 1))
        self.assertEqual(res["total"], 2)
        self.assertTrue((self.ws / HIDDEN_TESTS_DIR / "test_h.py").exists())

    def test_hidden_failure_fails_the_whole_verification(self):
        (self.ws / "lru_cache.py").write_text(GOOD_LRU, encoding="utf-8")
        hidden = {"test_h.py": "def test_nope():\n    assert False\n"}
        res = run_sandbox_verification(self.ws, hidden_tests=hidden)
        self.assertFalse(res["own"]["has_tests"])
        self.assertEqual(res["hidden"]["failed"], 1)
        self.assertNotEqual(res["exit_code"], 0)


class TestArchivistJudgement(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = JsonlRecordStore(self.base / "records")
        self.ledger = SeatLedger(initial_seats=placeholder_seats(), auto_seed=False)
        self.dispatcher = TerrariumDispatcher(ledger=self.ledger, store=self.store, base_workspace=self.base / "t")

    def tearDown(self):
        self.tmp.cleanup()

    def _spec(self, name, provider):
        return CandidateSpec(seat=placeholder_seats()[0], name=name, role=BUILDER_ROLE, custom_model_provider=provider)

    def test_verification_side_effects_are_not_artifacts(self):
        spec = self._spec("good", ScriptedProvider({"lru_cache.py": GOOD_LRU, "test_lru_cache.py": OWN_TEST}, "Created lru_cache.py.", "g"))
        _, results, entries = self.dispatcher.race(task="lru", candidates=[spec, self._spec("x", RecordingProvider())],
                                                   archivist=ImpartialArchivist(store=self.store))
        good = next(e for e in entries if e.candidate_id == results[[r.spec.name for r in results].index("good")].candidate_id)
        self.assertEqual(sorted(good.verified_artifacts), ["lru_cache.py", "test_lru_cache.py"])
        self.assertFalse(any("__pycache__" in a or HIDDEN_TESTS_DIR in a for a in good.verified_artifacts))

    def test_identical_candidates_are_reported_as_a_tie(self):
        def good(tag):
            return ScriptedProvider({"lru_cache.py": GOOD_LRU, "test_lru_cache.py": OWN_TEST}, "Created lru_cache.py.", tag)
        specs = [self._spec("a", good("a")), self._spec("b", good("b")), self._spec("c", good("c"))]
        winner, results, entries = self.dispatcher.race(task="lru", candidates=specs, archivist=ImpartialArchivist(store=self.store))
        self.assertIsNotNone(winner)
        top = next(e for e in entries if e.rank == 1)
        self.assertEqual(len(top.tied_with), 2)
        self.assertIsNotNone(top.tie_break)
        self.assertEqual(len({(e.axes["tier"], e.axes["hidden_rate"], e.axes["own_rate"]) for e in entries}), 1)

    def test_hidden_tests_outweigh_own_tests_and_catch_the_slow_build(self):
        from gorkbot.race import SLOW_LRU
        hidden = TaskBank().get("lru_cache").hidden_tests
        task = TaskRecord(brief="lru", hidden_tests=hidden)
        specs = [
            self._spec("good", ScriptedProvider({"lru_cache.py": GOOD_LRU, "test_lru_cache.py": OWN_TEST}, "Created lru_cache.py.", "g")),
            self._spec("slow", ScriptedProvider({"lru_cache.py": SLOW_LRU, "test_lru_cache.py": OWN_TEST}, "Created lru_cache.py.", "s")),
        ]
        winner, results, entries = self.dispatcher.race(task=task, candidates=specs, archivist=ImpartialArchivist(store=self.store))
        self.assertEqual(winner.spec.name, "good")
        slow = next(r for r in results if r.spec.name == "slow")
        self.assertEqual(slow.test_results["own"]["failed"], 0)       # its own test passes
        self.assertGreater(slow.test_results["hidden"]["failed"], 0)  # the benchmark does not
        self.assertEqual(next(e for e in entries if e.candidate_id == slow.candidate_id).verdict, "failed")

    def test_a_slow_pass_still_outranks_a_fast_failure(self):
        """Facts are tiers; cost only orders inside a tier. A full pass can never score below zero."""
        from gorkbot.terrarium import TerrariumCandidateResult, State, Status
        from gorkbot.archivist import ArchivistEntry
        def fake(name, verdict, hidden_pass, seconds, tokens):
            r = TerrariumCandidateResult(candidate_id=name, task_id="t", seat=placeholder_seats()[0], role=BUILDER_ROLE,
                                         final_state=State(session_id=name), output="", self_report="x", tokens_used=tokens,
                                         duration_seconds=seconds, workspace_path=self.base,
                                         test_results={"has_tests": True, "own": {"has_tests": False, "passed": 0, "total": 0},
                                                       "hidden": {"has_tests": True, "passed": hidden_pass, "total": 7}})
            e = ArchivistEntry(task_id="t", candidate_id=name, model="m", role="r", self_report_present=True, self_report="x", verdict=verdict)
            return r, e
        slow_pass = fake("slow", "success", 7, 600.0, 900_000)
        fast_fail = fake("fast", "failed", 6, 1.0, 100)
        a_slow, a_fast = ImpartialArchivist.axes(*slow_pass), ImpartialArchivist.axes(*fast_fail)
        self.assertGreater(a_slow["tier"], a_fast["tier"])
        self.assertGreater(ImpartialArchivist.composite_score(*slow_pass), 0)
        self.assertGreater(ImpartialArchivist.composite_score(*slow_pass), ImpartialArchivist.composite_score(*fast_fail))

    def test_teardown_removes_sandboxes(self):
        spec = self._spec("good", ScriptedProvider({"lru_cache.py": GOOD_LRU}, "Created lru_cache.py.", "g"))
        _, results, _ = self.dispatcher.race(task="lru", candidates=[spec, self._spec("x", RecordingProvider())],
                                             archivist=ImpartialArchivist(store=self.store), teardown=True)
        self.assertTrue(all(not r.workspace_path.exists() for r in results))


class TestContextAxisAndTester(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = JsonlRecordStore(self.base / "records")
        self.dispatcher = TerrariumDispatcher(ledger=SeatLedger(initial_seats=placeholder_seats(), auto_seed=False),
                                              store=self.store, base_workspace=self.base / "t")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fork_replays_parent_prefix_and_fresh_does_not(self):
        parent_msgs = [{"role": "user", "content": "PARENT-TURN"}, {"role": "assistant", "content": "PARENT-REPLY"}]
        task = TaskRecord(brief="child brief", parent_system_prompt="PARENT-SYS", parent_messages=parent_msgs)
        fork, fresh = RecordingProvider(), RecordingProvider()
        self.dispatcher.dispatch_single(task, CandidateSpec(seat=placeholder_seats()[0], role=BUILDER_ROLE, context="fork", custom_model_provider=fork), run_verification=False)
        self.dispatcher.dispatch_single(task, CandidateSpec(seat=placeholder_seats()[0], role=BUILDER_ROLE, context="fresh", custom_model_provider=fresh), run_verification=False)
        fork_first = fork.seen[0]
        self.assertEqual(fork_first[0], {"role": "system", "content": "PARENT-SYS"})
        self.assertEqual(fork_first[1]["content"], "PARENT-TURN")
        self.assertEqual(fork_first[-1]["content"], "child brief")
        fresh_first = fresh.seen[0]
        self.assertNotIn("PARENT", json.dumps(fresh_first))
        self.assertEqual(fresh_first[-1]["content"], "child brief")

    def test_tester_authors_hidden_tests_before_the_race(self):
        tester_src = "from lru_cache import LRUCache\n\ndef test_contains():\n    c = LRUCache(1)\n    c.put('a', 1)\n    assert 'a' in c\n"
        tester = CandidateSpec(seat=placeholder_seats()[0], role=TESTER_ROLE, name="tester",
                               custom_model_provider=ScriptedProvider({"test_by_tester.py": tester_src}, "Wrote tests.", "t"))
        builder = CandidateSpec(seat=placeholder_seats()[0], role=BUILDER_ROLE, name="builder",
                                custom_model_provider=ScriptedProvider({"lru_cache.py": GOOD_LRU}, "Created lru_cache.py.", "b"))
        task = TaskRecord(brief="lru")
        winner, results, _ = self.dispatcher.race(task=task, candidates=[builder], archivist=ImpartialArchivist(store=self.store), tester=tester)
        self.assertIn("test_by_tester.py", task.hidden_tests)
        self.assertEqual(results[0].test_results["hidden"]["passed"], 1)
        self.assertIsNotNone(results[0].tester_result)
        self.assertEqual(results[0].tester_result.role.name, "tester")

    def test_tester_is_a_real_role_not_a_reviewer_alias(self):
        reg = RoleRegistry()
        self.assertEqual(reg.get("tester").name, "tester")
        self.assertIn("test-engineering", reg.get("tester").skills)
        self.assertEqual(reg.get("test_engineer").name, "tester")


class TestTaskBankAndPresets(unittest.TestCase):
    def test_packaged_bank_loads_briefs_and_hidden_tests(self):
        bank = TaskBank()
        names = [t.name for t in bank.list_tasks()]
        self.assertEqual(names, ["lru_cache", "rate_limiter", "sqlite_cache"])
        lru = bank.get("lru_cache")
        self.assertEqual((lru.module, lru.entrypoint), ("lru_cache", "LRUCache"))
        self.assertIn("performance", lru.tags)
        self.assertTrue(lru.has_hidden_tests)
        self.assertNotIn("---", lru.brief)

    def test_hidden_suites_pass_against_a_known_good_implementation(self):
        with TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "lru_cache.py").write_text(GOOD_LRU, encoding="utf-8")
            res = run_sandbox_verification(ws, hidden_tests=TaskBank().get("lru_cache").hidden_tests)
            self.assertEqual(res["hidden"]["failed"], 0, res["output"])
            self.assertEqual(res["hidden"]["passed"], 7)

    def test_presets_vary_exactly_one_axis(self):
        seats = placeholder_seats()
        for preset, axis in (("harness", "harness_name"), ("tools", "tool_runner_name"), ("context", "context")):
            specs, _ = resolve_candidates(preset, BUILDER_ROLE, seats)
            varied = {getattr(s, axis) for s in specs}
            self.assertEqual(len(varied), len(specs), preset)
            for other in ("harness_name", "tool_runner_name", "context"):
                if other != axis:
                    self.assertEqual(len({getattr(s, other) for s in specs}), 1, f"{preset} leaks into {other}")
            self.assertEqual(len({s.seat.model for s in specs}), 1)
        models, _ = resolve_candidates("models", BUILDER_ROLE, seats)
        self.assertEqual([s.seat.model for s in models], [s.model for s in seats])
        self.assertEqual(len({s.harness_name for s in models}), 1)

    def test_custom_variant_grammar(self):
        specs, _ = resolve_candidates("model=gpt-5.6-sol+harness=cli+tools=mcp+skills=pytest-tdd/firecrawl-developer-index+ctx=fork,model=gemini-3.6-flash", BUILDER_ROLE, placeholder_seats())
        self.assertEqual(specs[0].seat.model, "gpt-5.6-sol")
        self.assertEqual(specs[0].signature(), "developer.python:gpt-5.6-sol:cli:mcp_tools:firecrawl-developer-index,pytest-tdd:ctx=fork")
        self.assertEqual(specs[1].seat.model, "gemini-3.6-flash")

    def test_review_phase_runs_only_on_a_facts_tie_and_maps_letters_back(self):
        from gorkbot.race import blind_bundle, parse_judgement
        # good/slow/liar are separated by facts -> review skipped even with judges named
        with TemporaryDirectory() as d:
            rep = run_race(RaceConfig(task_name="lru_cache", mock=True, judges=["gpt-5.6-sol"], workspace_root=Path(d) / "ws"))
        self.assertEqual(rep.judgements, [])
        self.assertTrue(any("review skipped" in n for n in rep.notes))
        # --review always forces it; the canned judge's letters come back as candidate ids
        with TemporaryDirectory() as d:
            rep = run_race(RaceConfig(task_name="lru_cache", mock=True, judges=["gpt-5.6-sol", "claude-3-7-sonnet"], review="always", workspace_root=Path(d) / "ws"))
        self.assertEqual(len(rep.judgements), 2)
        ids = {r.candidate_id for r in rep.results}
        for j in rep.judgements:
            self.assertTrue(j["parsed"])
            self.assertEqual(set(j["order"]), ids)
            self.assertTrue(set(j["cherry_picks"]) <= ids)
            self.assertIn("ranked_own_model_first", j)
            self.assertEqual(set(j["citations"]), {"checked", "true", "false"})
        # the bundle tells judges what is already counted, so they spend tokens on the remainder
        text, _ = blind_bundle(rep)
        self.assertIn("counted already", text)
        self.assertIn("loc=", text)
        # the bundle never truncates and never leaks a model name
        text, key = blind_bundle(rep)
        self.assertEqual(len(key), 3)
        for r in rep.results:
            self.assertNotIn(r.seat.model, text)
        self.assertEqual(parse_judgement("garbage", key)["parsed"], False)

    def test_mock_race_is_ephemeral_and_ranks_good_slow_liar(self):
        with TemporaryDirectory() as d:
            rep = run_race(RaceConfig(task_name="lru_cache", mock=True, workspace_root=Path(d) / "ws"))
        self.assertTrue(rep.ephemeral)
        by_rank = sorted(rep.results, key=lambda r: rep.entry_for(r).rank)
        self.assertTrue(by_rank[0].spec.name.endswith("[good]"))
        self.assertEqual({rep.entry_for(r).verdict for r in by_rank[1:]}, {"failed", "discrepancy"})
        # The mock race wrote its scorecard into a throwaway root, not .gorkbot/records
        self.assertIn("gorkbot_race_", str(rep.archivist.store.root))
        self.assertNotEqual(Path(rep.archivist.store.root).resolve(), Path(".gorkbot/records").resolve())


class TestTypePacks(unittest.TestCase):
    def test_role_plus_type_composes_skills_prompt_and_verify(self):
        reg = RoleRegistry()
        dev = reg.get("developer:python")
        self.assertEqual(dev.name, "developer:python")
        self.assertEqual(dev.base_name, "developer")
        self.assertEqual(dev.key_name, "developer.python")
        self.assertIn("pytest-tdd", dev.skills)
        self.assertIn("# Type: python", dev.system_prompt)
        self.assertTrue(dev.verify["test_command"].startswith("python -m pytest"))
        self.assertEqual(reg.get("developer:python"), dev)  # cached, same object
        self.assertIsNone(reg.get("developer:cobol"))

    def test_same_pack_attaches_to_tester_and_reviewer(self):
        reg = RoleRegistry()
        self.assertEqual(reg.get("reviewer:python").skills, reg.get("developer:python").skills)
        self.assertEqual(reg.get("tester:python").type_name, "python")
        self.assertEqual(reg.with_type(reg.get("reviewer"), "rust").name, "reviewer:rust")
        self.assertEqual(reg.with_type(reg.get("reviewer"), None).name, "reviewer")

    def test_task_tags_pick_the_type_and_aliases_default_to_python(self):
        reg = RoleRegistry()
        self.assertEqual(reg.type_for_tags(TaskBank().get("lru_cache").tags).name, "python")
        self.assertEqual(reg.get("builder").name, "developer:python")
        self.assertEqual(reg.get("python_developer").name, "developer:python")
        self.assertEqual(reg.resolve("build a rust crate for parsing").name, "developer:rust")

    def test_rust_stub_verify_block_is_wired_but_untested(self):
        rust = RoleRegistry().get("developer:rust")
        self.assertEqual(rust.verify["hidden_dir"], "tests")
        self.assertTrue(rust.verify["test_command"].startswith("cargo test"))

    def test_race_on_a_task_uses_typed_roles_for_builder_tester_and_judge(self):
        with TemporaryDirectory() as d:
            rep = run_race(RaceConfig(task_name="lru_cache", mock=True, role="developer", tester=True,
                                      judges=["gpt-5.6-sol"], review="always", workspace_root=Path(d) / "ws"))
        self.assertEqual({c.role.name for c in rep.candidates}, {"developer:python"})
        self.assertEqual(rep.results[0].tester_result.role.name, "tester:python")
        self.assertTrue(any("type 'python' from task tags" in n for n in rep.notes))


if __name__ == "__main__":
    unittest.main()
