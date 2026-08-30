"""arity race — the `arity race` runner: one task, N candidates, one impartial judge.

Everything the CLI needs to turn flags into CandidateSpecs, run them through the
TerrariumDispatcher, and render the side-by-side table. The kernel is untouched.

Presets vary exactly one axis at a time so a result is attributable:

    models    one candidate per live seat            (harness/tools/skills held fixed)
    harness   wire | cli | omp                        on the first live seat
    tools     ast_tools | mcp_tools | shell_tools     on the first live seat
    skills    pytest-tdd | baseline                   on the first live seat
    context   fresh | accounts | fork                 on the first live seat

A custom variant list is `key=value` pairs joined by `+`, comma-separated per candidate;
a skills list uses `/` (keys: model, harness, tools, skills, ctx, name):

    --variants "model=gemini-3.6-flash+harness=wire,model=gpt-5.6-sol+harness=cli+skills=pytest-tdd/scout-recon"

Mock mode swaps the model for canned providers with deliberately different behaviour
(a correct build, a slow build that fails the benchmark, and a liar) so the judge's
verdicts are visible without spending tokens. Mock runs never touch the real scorecard.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .archivist import ArchivistEntry, ImpartialArchivist
from .handlers import JsonlRecordStore
from .ledger import Seat, SeatLedger
from .roles import BUILDER_ROLE, TESTER_ROLE, Role, RoleRegistry
from .tasks import RaceTask, TaskBank
from .terrarium import CONTEXT_MODES, CandidateSpec, TaskRecord, TerrariumCandidateResult, TerrariumDispatcher
from .types import CallModel, ModelCompleted

PRESETS = ("models", "harness", "tools", "skills", "context")


@dataclass
class RaceConfig:
    prompt: str = ""
    task_name: Optional[str] = None
    variants: str = "models"
    role: str = "developer:python"
    test_command: Optional[str] = None
    workers: int = 4
    mock: bool = False
    as_json: bool = False
    tester: bool = False
    teardown: Optional[bool] = None  # None -> mock tears down, live keeps
    store_root: Optional[Path] = None
    workspace_root: Optional[Path] = None
    # Review phase: the reviewer role reads a blind bundle of the candidates and ranks them.
    judges: list[str] = field(default_factory=list)  # model names to seat as judges
    review: str = "tie"  # "tie" (only when facts tie) | "always" | "never"
    judge_provider: Optional[Callable[[str], Any]] = None  # tests: model name -> ModelProvider
    # Conference phase: wake the candidates up together for N rounds, then re-verify and re-audit.
    conference: int = 0


@dataclass
class RaceReport:
    task: TaskRecord
    race_task: Optional[RaceTask]
    candidates: list[CandidateSpec]
    winner: Optional[TerrariumCandidateResult]
    results: list[TerrariumCandidateResult]
    entries: list[ArchivistEntry]
    archivist: ImpartialArchivist
    ephemeral: bool
    notes: list[str] = field(default_factory=list)
    judgements: list[dict[str, Any]] = field(default_factory=list)
    # Phase 2 (conference): the same candidates after talking; audited separately.
    conference_results: list[TerrariumCandidateResult] = field(default_factory=list)
    conference_entries: list[ArchivistEntry] = field(default_factory=list)
    conference_winner: Optional[TerrariumCandidateResult] = None

    def entry_for(self, r: TerrariumCandidateResult) -> Optional[ArchivistEntry]:
        return next((e for e in self.entries + self.conference_entries if e.candidate_id == r.candidate_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.brief,
            "task_name": self.race_task.name if self.race_task else None,
            "hidden_tests": sorted(self.task.hidden_tests),
            "ephemeral": self.ephemeral,
            "winner": self.winner.spec.name if self.winner and self.winner.spec else None,
            "winner_signature": self.winner.signature if self.winner else None,
            "notes": self.notes,
            "judgements": self.judgements,
            "conference": {
                "rounds_run": bool(self.conference_results),
                "winner": (self.conference_winner.spec.name if self.conference_winner and self.conference_winner.spec else None),
                "results": [self._result_dict(r) for r in self.conference_results],
            },
            "results": [self._result_dict(r) for r in self.results],
        }

    def _result_dict(self, r: TerrariumCandidateResult) -> dict[str, Any]:
        return {
            "name": r.spec.name if r.spec else r.candidate_id,
            "candidate_id": r.candidate_id,
            "signature": r.signature,
            "status": r.status,
            "error": r.error,
            "harness_actual": r.harness,
            "fallbacks": r.fallbacks,
            "verdict": (self.entry_for(r).verdict if self.entry_for(r) else None),
            "score": (self.entry_for(r).score if self.entry_for(r) else None),
            "axes": (self.entry_for(r).axes if self.entry_for(r) else {}),
            "rank": (self.entry_for(r).rank if self.entry_for(r) else None),
            "tied_with": (self.entry_for(r).tied_with if self.entry_for(r) else []),
            "duration_seconds": r.duration_seconds,
            "tokens_used": r.tokens_used,
            "test_results": r.test_results,
            "artifacts": (self.entry_for(r).verified_artifacts if self.entry_for(r) else []),
            "output": r.output,
        }



# -----------------------------------------------------------------------------
# Candidate resolution
# -----------------------------------------------------------------------------

def placeholder_seats() -> list[Seat]:
    """Seats used only when no authenticated seat exists (mock mode, or a dry run that will fail on the wire)."""
    return [
        Seat(id="gemini-flash", provider="google", model="gemini-3.6-flash"),
        Seat(id="gpt-5.6-sol", provider="openai", model="gpt-5.6-sol"),
        Seat(id="claude-sonnet", provider="anthropic", model="claude-3-7-sonnet"),
    ]


def live_seats(ledger: Optional[SeatLedger] = None) -> list[Seat]:
    """Authenticated, unlocked seats, fullest quota first (so a model name resolves to the account that can still pay)."""
    ledger = ledger or SeatLedger()
    seats = [s for s in ledger.list_seats() if not s.presence]
    return sorted(seats, key=lambda s: -s.remaining / max(1.0, s.total_allowance))


def _parse_custom_variant(spec_str: str, seats: list[Seat], role: Role, idx: int) -> CandidateSpec:
    kv: dict[str, str] = {}
    for part in spec_str.split("+"):
        if "=" in part:
            k, v = part.split("=", 1)
            kv[k.strip().lower()] = v.strip()
        elif part.strip():
            kv.setdefault("model", part.strip())
    model = kv.get("model")
    seat = next((s for s in seats if model and (s.model == model or s.id == model)), None)
    if seat is None:
        seat = seats[idx % len(seats)] if not model else Seat(provider="custom", model=model)
    skills = [s for s in kv["skills"].split("/") if s] if "skills" in kv else ["pytest-tdd"]
    return CandidateSpec(
        seat=seat,
        name=kv.get("name", spec_str),
        role=role,
        harness=kv.get("harness", "wire"),
        tool_runner_type=kv.get("tools", "sandbox"),
        skills=skills,
        context=kv.get("ctx", kv.get("context", "accounts")),
    )


def resolve_candidates(variants: str, role: Role, seats: list[Seat]) -> tuple[list[CandidateSpec], list[str]]:
    """Turn a preset name or a custom variant list into CandidateSpecs. Returns (specs, notes)."""
    notes: list[str] = []
    v = variants.strip().lower()
    if not seats:
        seats = placeholder_seats()
        notes.append("no authenticated seats in the ledger; using placeholder seats (run `arity auth login`)")
    first = seats[0]
    fixed = dict(role=role, harness="wire", tool_runner_type="sandbox", skills=["pytest-tdd"])

    if v == "models":
        specs = [CandidateSpec(seat=s, name=s.model, **fixed) for s in seats]
    elif v == "harness":
        specs = [CandidateSpec(seat=first, name=f"{first.model} / {h}", **{**fixed, "harness": h}) for h in ("wire", "cli", "omp")]
    elif v == "tools":
        specs = [CandidateSpec(seat=first, name=f"{first.model} / {t}", **{**fixed, "tool_runner_type": t}) for t in ("sandbox", "mcp", "shell")]
    elif v == "skills":
        specs = [
            CandidateSpec(seat=first, name=f"{first.model} / pytest-tdd", **fixed),
            CandidateSpec(seat=first, name=f"{first.model} / baseline", **{**fixed, "skills": []}),
        ]
    elif v == "context":
        specs = [CandidateSpec(seat=first, name=f"{first.model} / ctx={c}", context=c, **fixed) for c in CONTEXT_MODES]
    else:
        parts = [p.strip() for p in variants.split(",") if p.strip()]
        specs = [_parse_custom_variant(p, seats, role, i) for i, p in enumerate(parts)]
    if len(specs) < 2:
        notes.append(f"only {len(specs)} candidate resolved; a race needs at least two to say anything")
    return specs, notes


# -----------------------------------------------------------------------------
# Mock providers: three personalities so the judge has something to judge
# -----------------------------------------------------------------------------

GOOD_LRU = textwrap.dedent('''
    from collections import OrderedDict


    class LRUCache:
        def __init__(self, capacity: int = 128):
            if not isinstance(capacity, int) or capacity <= 0:
                raise ValueError("capacity must be a positive integer")
            self.capacity = capacity
            self._d: OrderedDict = OrderedDict()

        def get(self, key):
            if key not in self._d:
                return None
            self._d.move_to_end(key)
            return self._d[key]

        def put(self, key, value) -> None:
            if key in self._d:
                self._d.move_to_end(key)
            self._d[key] = value
            if len(self._d) > self.capacity:
                self._d.popitem(last=False)

        def __len__(self) -> int:
            return len(self._d)

        def __contains__(self, key) -> bool:
            return key in self._d
''').lstrip()

SLOW_LRU = textwrap.dedent('''
    class LRUCache:
        """Correct but O(n): a list of (key, value) pairs scanned on every call."""

        def __init__(self, capacity: int = 128):
            if not isinstance(capacity, int) or capacity <= 0:
                raise ValueError("capacity must be a positive integer")
            self.capacity = capacity
            self._items = []

        def _find(self, key):
            for i, (k, _) in enumerate(self._items):
                if k == key:
                    return i
            return -1

        def get(self, key):
            i = self._find(key)
            if i < 0:
                return None
            k, v = self._items.pop(i)
            self._items.append((k, v))
            return v

        def put(self, key, value) -> None:
            i = self._find(key)
            if i >= 0:
                self._items.pop(i)
            elif len(self._items) >= self.capacity:
                self._items.pop(0)
            self._items.append((key, value))

        def __len__(self) -> int:
            return len(self._items)

        def __contains__(self, key) -> bool:
            return self._find(key) >= 0
''').lstrip()

OWN_TEST = textwrap.dedent('''
    from lru_cache import LRUCache


    def test_put_get_and_evict():
        c = LRUCache(2)
        c.put("a", 1)
        c.put("b", 2)
        assert c.get("a") == 1
        c.put("c", 3)
        assert c.get("b") is None
''').lstrip()


class ScriptedProvider:
    """A ModelProvider that plays a fixed script of tool calls, then reports."""

    def __init__(self, files: dict[str, str], report: str, tag: str):
        self.files = files
        self.report = report
        self.tag = tag
        self.turn = 0

    def call(self, effect: CallModel) -> ModelCompleted:
        self.turn += 1
        if self.turn == 1 and self.files:
            calls = [
                {
                    "id": f"{self.tag}_w{i}",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": json.dumps({"path": path, "content": src})},
                }
                for i, (path, src) in enumerate(self.files.items())
            ]
            return ModelCompleted(content="Writing files.", tool_calls=calls,
                                  usage={"prompt_tokens": 150, "completion_tokens": 120}, finish_reason="tool_calls")
        return ModelCompleted(content=self.report, tool_calls=[],
                              usage={"prompt_tokens": 200, "completion_tokens": 50}, finish_reason="stop")


MOCK_PERSONALITIES: list[tuple[str, Callable[[], ScriptedProvider]]] = [
    ("good", lambda: ScriptedProvider({"lru_cache.py": GOOD_LRU, "test_lru_cache.py": OWN_TEST},
                                      "Created lru_cache.py and test_lru_cache.py; all tests pass.", "good")),
    ("slow", lambda: ScriptedProvider({"lru_cache.py": SLOW_LRU, "test_lru_cache.py": OWN_TEST},
                                      "Created lru_cache.py and test_lru_cache.py; all tests pass.", "slow")),
    ("liar", lambda: ScriptedProvider({}, "Created lru_cache.py and test_lru_cache.py; all tests pass.", "liar")),
]


def attach_mocks(candidates: list[CandidateSpec]) -> None:
    for i, cand in enumerate(candidates):
        label, factory = MOCK_PERSONALITIES[i % len(MOCK_PERSONALITIES)]
        cand.custom_model_provider = factory()
        cand.name = f"{cand.name} [{label}]"


# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------

def run_race(cfg: RaceConfig) -> RaceReport:
    notes: list[str] = []
    roles = RoleRegistry()
    role = roles.get(cfg.role) or BUILDER_ROLE

    race_task: Optional[RaceTask] = None
    if cfg.task_name:
        race_task = TaskBank().get(cfg.task_name)
        if race_task is None:
            raise SystemExit(f"unknown task '{cfg.task_name}'; see `arity tasks`")
        # A task's tags pick the type (python, rust, ...) unless the role already names one.
        pack = roles.type_for_tags(race_task.tags)
        if pack and not role.type_name:
            role = roles.with_type(role, pack.name)
            notes.append(f"type '{pack.name}' from task tags -> {role.name}")
    type_name = role.type_name or None
    brief = cfg.prompt or (race_task.brief if race_task else "")
    if not brief:
        raise SystemExit("a prompt or --task is required")

    seats = placeholder_seats() if cfg.mock else live_seats()
    candidates, c_notes = resolve_candidates(cfg.variants, role, seats)
    notes += c_notes
    if cfg.mock:
        attach_mocks(candidates)
        notes.append("mock mode: canned providers, ephemeral store, sandboxes torn down")
        if cfg.judges and not cfg.judge_provider:
            # Canned judge: ranks by letter and cherry-picks from B, so the review phase is visible offline.
            cfg.judge_provider = lambda model: ScriptedProvider(
                {}, '1. A - fewest lines (lru_cache.py).\n2. B - broader tests.\n3. C - no artifacts.\n'
                    '{"order": ["A", "B", "C"], "ties": [], "cherry_picks": {"B": "its eviction test"}}', f"judge-{model}")

    # Mock runs never write to the real scorecard; live runs are the scorecard's whole purpose.
    ephemeral = cfg.mock
    tmp_root = Path(tempfile.mkdtemp(prefix="arity_race_")) if ephemeral else None
    store = JsonlRecordStore(root=cfg.store_root or (tmp_root / "records" if tmp_root else None))
    workspace = cfg.workspace_root or (tmp_root / "terrarium" if tmp_root else Path(".terrarium"))
    ledger = SeatLedger(initial_seats=[c.seat for c in candidates], auto_seed=False)
    dispatcher = TerrariumDispatcher(ledger=ledger, store=store, base_workspace=workspace, quiet=cfg.as_json)
    archivist = ImpartialArchivist(store=store)

    task = TaskRecord(
        brief=brief,
        from_role="Asa",
        to_role=role.name,
        hidden_tests=dict(race_task.hidden_tests) if race_task else {},
        metadata={"module": race_task.module, "entrypoint": race_task.entrypoint} if race_task else {},
    )
    tester_spec: Optional[CandidateSpec] = None
    if cfg.tester:
        tester_seat = candidates[0].seat
        tester_spec = CandidateSpec(seat=tester_seat, name="tester", role=roles.with_type(TESTER_ROLE, type_name),
                                    harness="wire", tool_runner_type="sandbox", skills=["test-engineering"])
        if cfg.mock:
            tester_spec.custom_model_provider = ScriptedProvider(
                {"test_hidden_by_tester.py": OWN_TEST}, "Wrote test_hidden_by_tester.py.", "tester")
    elif not task.hidden_tests:
        notes.append("no hidden tests: candidates are graded only on tests they wrote themselves (use --task or --tester)")

    teardown = cfg.teardown if cfg.teardown is not None else ephemeral
    # Review must read the sandboxes, so it runs before any teardown.
    winner, results, entries = dispatcher.race(
        task=task, candidates=candidates, test_command=cfg.test_command, max_workers=cfg.workers,
        archivist=archivist, tester=tester_spec, teardown=False,
    )
    report = RaceReport(task=task, race_task=race_task, candidates=candidates, winner=winner, results=results,
                        entries=entries, archivist=archivist, ephemeral=ephemeral, notes=notes)

    top = report.entry_for(winner) if winner else None
    facts_tie = bool(top and top.tied_with)
    if cfg.judges and cfg.review != "never" and (cfg.review == "always" or facts_tie):
        report.judgements = run_review(report, cfg, dispatcher, seats)
    elif cfg.judges and not facts_tie:
        notes.append("review skipped: the facts already separated the candidates (use --review always to force)")

    if cfg.conference > 0 and results:
        phase2 = dispatcher.conference(task, results, entries=entries, rounds=cfg.conference,
                                       max_workers=cfg.workers, test_command=cfg.test_command)
        c_winner, c_entries = archivist.evaluate_trial(phase2)
        report.conference_results, report.conference_entries, report.conference_winner = phase2, c_entries, c_winner

    if teardown:
        dispatcher.teardown(results + ([results[0].tester_result] if results and results[0].tester_result else []))
    if tmp_root and teardown:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return report


# -----------------------------------------------------------------------------
# Review phase: blind bundle -> reviewer role on each judge seat -> one order each
# -----------------------------------------------------------------------------

def blind_bundle(rep: RaceReport) -> tuple[str, dict[str, str]]:
    """The evidence every judge sees, with candidate identities scrubbed to letters.

    Full files, never truncated: a judge that says 'truncated, cannot verify' is right, and
    a bundle that forces it to say so is a bug in the bundle. Returns (text, letter -> candidate_id).
    """
    import random
    from .terrarium import ARTIFACT_IGNORE_PARTS
    ordered = list(rep.results)
    random.Random(rep.task.id).shuffle(ordered)
    letters = [chr(ord("A") + i) for i in range(len(ordered))]
    key = {L: r.candidate_id for L, r in zip(letters, ordered)}
    parts = [f"# Brief\n{rep.task.brief.strip()}\n"]
    for L, r in zip(letters, ordered):
        e = rep.entry_for(r)
        parts.append(f"\n\n# Candidate {L}")
        ws = Path(r.workspace_path)
        if ws.is_dir():
            for p in sorted(ws.rglob("*")):
                if p.is_file() and not any(seg in ARTIFACT_IGNORE_PARTS for seg in p.relative_to(ws).parts):
                    try:
                        body = p.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    parts.append(f"\n## {p.relative_to(ws).as_posix()}\n```\n{body}\n```")
        tr = r.test_results or {}
        own, hidden = tr.get("own") or {}, tr.get("hidden") or {}
        a = (e.axes if e else {}) or {}
        counted = ", ".join(f"{k}={a[k]}" for k in (
            "loc", "test_count", "type_ignores", "bare_asserts", "compile_ok", "tool_calls", "tool_errors",
            "model_turns", "module_present", "entrypoint_present", "brief_numbers_in_own_tests") if k in a)
        parts.append(
            f"\n## Archivist facts for {L}\n"
            f"- verdict: {e.verdict if e else r.status}\n"
            f"- own tests: {own.get('passed', 0)}/{own.get('total', 0)} | hidden tests: {hidden.get('passed', 0)}/{hidden.get('total', 0)}\n"
            f"- counted already (do not re-count): {counted}\n"
            f"- candidate's own closing report: {(r.output or '').strip()}"  # not self_report: that wrapper names the model
        )
    parts.append(
        f"\n\n# Your task\nRank candidates {', '.join(letters)}. The counts above are settled; spend your reasoning on what "
        "counting cannot see: idiom, intent, maintenance risk, honesty of the closing report. One cited reason per rank "
        "(a file, a line, a test name). Say 'tie' where evidence cannot separate two. Name anything worth cherry-picking "
        "from a candidate that did not win. Do not re-run anything. End with one JSON line: "
        '{"order": ["A", ...], "ties": [["A","B"], ...], "cherry_picks": {"A": "...", ...}}'
    )
    return "".join(parts), key


def parse_judgement(text: str, key: dict[str, str]) -> dict[str, Any]:
    """Pull the trailing JSON line out of a judge's reply and map letters back to candidate ids."""
    out: dict[str, Any] = {"order": [], "ties": [], "cherry_picks": {}, "parsed": False}
    decoder = json.JSONDecoder()
    text = text or ""
    # Try every '{' from the end backwards; the ranking object may nest (cherry_picks is a dict).
    for start in reversed([i for i, ch in enumerate(text) if ch == "{"]):
        try:
            data, _ = decoder.raw_decode(text[start:])
            if not isinstance(data, dict) or "order" not in data:
                continue
            out["order"] = [key.get(L, L) for L in data.get("order", [])]
            out["ties"] = [[key.get(L, L) for L in t] for t in data.get("ties", [])]
            out["cherry_picks"] = {key.get(L, L): v for L, v in (data.get("cherry_picks") or {}).items()}
            out["parsed"] = True
            break
        except Exception:
            continue
    return out


def check_citations(text: str, key: dict[str, str], rep: RaceReport) -> dict[str, Any]:
    """For each 'Candidate X' paragraph, every backticked identifier must appear in X's sandbox.

    A judge is required to cite; this checks the citations are real. It is a fact printed next to
    the judgement, not a score.
    """
    import re
    from .terrarium import ARTIFACT_IGNORE_PARTS
    by_id = {r.candidate_id: r for r in rep.results}
    corpus: dict[str, str] = {}
    for L, cid in key.items():
        ws = Path(by_id[cid].workspace_path) if cid in by_id else None
        blob = ""
        if ws and ws.is_dir():
            for p in ws.rglob("*"):
                if p.is_file() and not any(seg in ARTIFACT_IGNORE_PARTS for seg in p.relative_to(ws).parts):
                    try:
                        blob += p.relative_to(ws).as_posix() + "\n" + p.read_text(encoding="utf-8", errors="replace") + "\n"
                    except Exception:
                        pass
        corpus[L] = blob
    checked = true = 0
    false_cites: list[str] = []
    # Attribute each backticked token to the most recent candidate letter mentioned before it.
    current: Optional[str] = None
    for m in re.finditer(r"(?:\*\*|\b)([A-Z])(?:\*\*|\b)(?=\s*[-:—–]|\s*\))|`([^`\n]{2,80})`", text or ""):
        if m.group(1) and m.group(1) in key:
            current = m.group(1)
            continue
        token = m.group(2)
        if not token or current is None:
            continue
        # "B has no `__repr__`" is a claim of absence; only positive citations are checkable here.
        lead = (text[max(0, m.start() - 60):m.start()]).lower()
        if re.search(r"\b(no|not|lacks?|lacking|without|omits?|omitted|missing|never|instead of|rather than)\b[^`]*$", lead):
            continue
        ident = token.split("(")[0].split("::")[-1].split(".")[-1].strip()
        if not re.fullmatch(r"[\w/]+", ident) or ident in ("None", "True", "False", "int", "str", "bool", "in", "OrderedDict", "dict", "self"):
            continue
        checked += 1
        if ident in corpus.get(current, ""):
            true += 1
        else:
            false_cites.append(f"{current}:{ident}")
    return {"checked": checked, "true": true, "false": false_cites[:10]}


def run_review(rep: RaceReport, cfg: RaceConfig, dispatcher: TerrariumDispatcher, seats: list[Seat]) -> list[dict[str, Any]]:
    from .roles import RoleRegistry
    from .types import StoreRecord
    roles = RoleRegistry()
    # The judge takes the same type as the builders: reviewer:python judges a python race.
    reviewer = roles.with_type(roles.get("reviewer"), (rep.candidates[0].role.type_name if rep.candidates and rep.candidates[0].role else None))
    text, key = blind_bundle(rep)
    review_task = TaskRecord(brief=text, from_role="Asa", to_role="reviewer", metadata={"reviews": rep.task.id})
    judge_specs: list[CandidateSpec] = []
    for model in cfg.judges:
        seat = next((s for s in seats if s.model == model or s.id == model), None)
        if seat is None:
            rep.notes.append(f"no seat for judge '{model}'")
            continue
        spec = CandidateSpec(seat=seat, name=f"judge:{model}", role=reviewer, harness="wire", tool_runner_type="sandbox", skills=[])
        if cfg.judge_provider:
            spec.custom_model_provider = cfg.judge_provider(model)
        judge_specs.append(spec)
    if not judge_specs:
        return []
    results = dispatcher.dispatch_candidates(task=review_task, candidates=judge_specs, max_workers=cfg.workers, run_verification=False)
    judgements: list[dict[str, Any]] = []
    model_of = {c.candidate_id: c.seat.model for c in rep.results}
    for r in results:
        j = parse_judgement(r.output or "", key)
        # Two facts about the judgement itself, reported not scored: did it rank its own model first,
        # and do the identifiers it cites (files, tests, names) actually exist in the sandbox it cites them for.
        j["ranked_own_model_first"] = bool(j["order"]) and model_of.get(j["order"][0]) == r.seat.model
        j["citations"] = check_citations(r.output or "", key, rep)
        j.update({
            "task_id": rep.task.id, "judge": r.seat.model, "judge_seat": r.seat.id, "harness": r.harness,
            "status": r.status, "tokens_used": r.tokens_used, "duration_seconds": round(r.duration_seconds, 2),
            "key": key, "text": r.output or r.error or "",
        })
        judgements.append(j)
        try:
            dispatcher.store.append(StoreRecord(kind="judgement", record=j))
        except Exception:
            pass
    return judgements


# -----------------------------------------------------------------------------
# Rendering
# -----------------------------------------------------------------------------

def _fmt_tests(tr: Optional[dict[str, Any]]) -> tuple[str, str]:
    if not tr or not tr.get("has_tests"):
        return "-", "-"
    own = tr.get("own") or tr
    hidden = tr.get("hidden") or {}

    def cell(res: dict[str, Any]) -> str:
        if not res.get("has_tests"):
            return "-"
        mark = "ok" if res.get("failed", 0) == 0 and res.get("exit_code") == 0 else "FAIL"
        return f"{res.get('passed', 0)}/{res.get('total', 0)} {mark}"

    return cell(own), cell(hidden)


def render_report(rep: RaceReport, printer: Callable[..., None] = print) -> None:
    p = printer
    bold, dim, green, red, yellow, cyan, reset = "\033[1m", "\033[2m", "\033[1;32m", "\033[1;31m", "\033[1;33m", "\033[1;36m", "\033[0m"

    p(f"\n{cyan}{'=' * 100}{reset}")
    p(f"{bold}arity race{reset}  {rep.race_task.name if rep.race_task else 'ad-hoc'}"
      f"  |  {len(rep.candidates)} candidates  |  hidden tests: {len(rep.task.hidden_tests) or 'none'}"
      f"  |  {'ephemeral' if rep.ephemeral else 'scorecard: live'}")
    p(f"{dim}{rep.task.brief.strip().splitlines()[0][:96]}{reset}")
    for n in rep.notes:
        p(f"{yellow}note:{reset} {n}")
    p(f"{cyan}{'=' * 100}{reset}\n")

    cols = [("#", 2), ("candidate", 30), ("model", 18), ("harness", 7), ("tools", 11), ("skills", 12), ("ctx", 8),
            ("verdict", 11), ("own", 9), ("hidden", 9), ("time", 7), ("tokens", 7), ("score", 7), ("standing", 8)]
    head = " | ".join(f"{name:<{w}}" for name, w in cols)
    p(f"{bold}{head}{reset}")
    p("-+-".join("-" * w for _, w in cols))

    ordered = sorted(rep.results, key=lambda r: (rep.entry_for(r).rank if rep.entry_for(r) else 99))
    for r in ordered:
        e = rep.entry_for(r)
        spec = r.spec
        own, hidden = _fmt_tests(r.test_results)
        verdict = (e.verdict if e else r.status).upper()
        colour = green if verdict == "SUCCESS" else red
        tie = "=" if e and e.tied_with else " "
        standing = rep.archivist.scorecard.get_standing(r.signature or r.seat.model)
        row = [
            f"{(e.rank if e else 0)}{tie}", (spec.name if spec else r.candidate_id), r.seat.model, r.harness,
            r.tool_runner_name, (",".join(r.skills_used) or "baseline"), (spec.context if spec else "-"),
            verdict, own, hidden, f"{r.duration_seconds:.2f}s", f"{r.tokens_used:,}",
            f"{(e.score if e else 0):.1f}", f"{standing:.1f}",
        ]
        cells = []
        for (name, w), val in zip(cols, row):
            cell = f"{str(val)[:w]:<{w}}"
            cells.append(f"{colour}{cell}{reset}" if name == "verdict" else cell)
        p(" | ".join(cells))
    p()

    if rep.judgements:
        names = {r.candidate_id: (r.spec.name if r.spec else r.candidate_id) for r in rep.results}
        p(f"{bold}review{reset} (facts tied; blind bundle, letters shuffled)")
        for j in rep.judgements:
            if not j.get("parsed"):
                p(f"  {j['judge']:20} {red}no parseable ranking{reset} ({j['status']})")
                continue
            order = " > ".join(names.get(c, c) for c in j["order"])
            ties = f"  ties: {[[names.get(c, c) for c in t] for t in j['ties']]}" if j["ties"] else ""
            cit = j.get("citations") or {}
            flags = []
            if cit.get("checked"):
                flags.append(f"cited {cit['true']}/{cit['checked']} true" + (f" (false: {', '.join(cit['false'])})" if cit.get("false") else ""))
            if j.get("ranked_own_model_first"):
                flags.append("ranked its own model first")
            p(f"  {j['judge']:20} {order}{ties}" + (f"   {dim}[{'; '.join(flags)}]{reset}" if flags else ""))
            for cid, note in (j.get("cherry_picks") or {}).items():
                if note:
                    p(f"      {dim}cherry-pick from {names.get(cid, cid)}: {note}{reset}")
        p()

    if rep.conference_results:
        p(f"{bold}conference{reset} (same candidates, woken up together; final drafts re-verified)")
        for r in sorted(rep.conference_results, key=lambda r: (rep.entry_for(r).rank if rep.entry_for(r) else 99)):
            e = rep.entry_for(r)
            own, hidden = _fmt_tests(r.test_results)
            a = (e.axes if e else {}) or {}
            p(f"  {(e.rank if e else 0)}{'=' if e and e.tied_with else ' '} {(r.spec.name if r.spec else r.candidate_id):28} "
              f"{(e.verdict if e else r.status):11} own={own:9} hidden={hidden:9} loc={a.get('loc', '-')} "
              f"tests={a.get('test_count', '-')} {r.duration_seconds:.0f}s {r.tokens_used:,} tok")
        if rep.conference_winner and rep.conference_winner.spec:
            p(f"  {green}final draft:{reset} {bold}{rep.conference_winner.spec.name}{reset}")
        p()

    if rep.winner and rep.winner.spec:
        e = rep.entry_for(rep.winner)
        if e and e.tied_with:
            p(f"{yellow}TIE{reset} between {len(e.tied_with) + 1} candidates (scores within judge epsilon); "
              f"broke on {e.tie_break}. Treat the winner as provisional.")
        p(f"{green}winner:{reset} {bold}{rep.winner.spec.name}{reset}  {dim}{rep.winner.signature}{reset}")
        if e:
            p(f"  artifacts: {', '.join(e.verified_artifacts) or 'none'}")
            p(f"  findings:  {e.entry_text.splitlines()[-1].replace('- **Findings**: ', '')}")
    else:
        p(f"{red}no winner:{reset} every candidate scored <= 0 (failed, lied, or produced nothing verifiable)")

    moved = [r for r in rep.results if r.fallbacks]
    for r in moved:
        p(f"{yellow}harness moved:{reset} {(r.spec.name if r.spec else r.candidate_id)} ran as {r.harness} "
          f"({r.fallbacks} fallback{'s' if r.fallbacks != 1 else ''}); do not attribute this result to the wire.")

    losers = [r for r in rep.results if rep.winner is None or r.candidate_id != rep.winner.candidate_id]
    for r in losers:
        e = rep.entry_for(r)
        if e and e.verdict != "success":
            p(f"  {dim}{(r.spec.name if r.spec else r.candidate_id)}: {e.verdict} - "
              f"{e.entry_text.splitlines()[-1].replace('- **Findings**: ', '')}{reset}")
    p(f"{cyan}{'=' * 100}{reset}\n")
