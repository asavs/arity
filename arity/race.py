"""Arity trial runner: one task, N candidates, and evidence before opinion.

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

`arity race` runs the arms the caller names — a preset or a variant list — and never casts.
`arity run` names none, so it asks `CastingComposer` for up to `--arity` seats on distinct
models under `--cast smart|brokie|chaos`, and records the mode and seed with the trial so
`--cast-seed` can replay it.

Mock mode swaps the model for canned providers with deliberately different behaviour
(a correct build, a slow build that fails the benchmark, and a liar) so the judge's
verdicts are visible without spending tokens. Mock runs never touch the real scorecard.
"""
from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
import textwrap
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .archivist import ArchivistEntry, ImpartialArchivist
from .composer import SMART, CastingComposer, CastingDecision
from .evidence import (
    ArtifactEvidence,
    CandidateEvidence,
    Evaluation,
    EvidenceBundle,
    Resolution,
    ResolutionKind,
    TrialEvaluator,
    evaluate_bundle,
    factual_eligibility,
    resolve_bundle,
)
from .handlers import JsonlRecordStore, default_record_store
from .ledger import Seat, SeatLedger
from .roles import BUILDER_ROLE, TESTER_ROLE, Role, RoleRegistry
from .scorecard import Scorecard
from .seams import RecordStore
from .tasks import RaceTask, TaskBank
from .terrarium import CONTEXT_MODES, CandidateSpec, TaskRecord, TerrariumCandidateResult, TerrariumDispatcher
from .transports import RedphoneInbox
from .trial_events import TrialJournal
from .tools import resolve_arity
from .types import CallModel, ModelCompleted, StoreRecord

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
    record_store: Optional[RecordStore] = None
    # The scorecard the caller already replayed from that same store. Reused by the archivist so
    # a run that casts on the record does not load and replay it a second time to grade into it.
    scorecard: Optional[Scorecard] = None
    workspace_root: Optional[Path] = None
    # Review phase: the reviewer role reads a blind bundle of the candidates and ranks them.
    judges: list[str] = field(default_factory=list)  # model names to seat as judges
    review: str = "tie"  # "tie" (only when facts tie) | "always" | "never"
    judge_provider: Optional[Callable[[str], Any]] = None  # tests: model name -> ModelProvider
    # Conference phase: wake the candidates up together for N rounds, then re-verify and re-audit.
    conference: int = 0
    quiet: bool = False  # suppress per-candidate console chatter (run does this unless --verbose)
    # Set by the front door. ``--arity`` is a requested maximum; seat availability may resolve fewer.
    requested_arity: Optional[int] = None
    # Seats the caller already resolved. ``variants`` still names the arms; a model named there
    # binds to the first matching seat, so a cast seat must precede other accounts of its model.
    seats: Optional[list[Seat]] = None
    # The casting decision behind those seats, recorded so the cast can be replayed.
    casting: Optional[dict[str, Any]] = None
    # Programmatic callers may supply exact arms; the CLI continues to resolve ``variants``.
    candidate_specs: Optional[list[CandidateSpec]] = None
    # First-class evaluators consume one frozen EvidenceBundle.  Legacy blind reviewer
    # candidates remain available through ``judges`` and are normalized into Evaluations.
    evaluators: list[TrialEvaluator] = field(default_factory=list)


def resolve_record_store(
    record_store: Optional[RecordStore] = None,
    store_root: Optional[Path] = None,
    tmp_root: Optional[Path] = None,
) -> RecordStore:
    """Resolve one writer through the same configured store seam used by readers.

    The front door resolves it before casting so the evidence the cast reads and the evidence
    the trial writes are the same store.
    """
    if record_store is not None:
        return record_store
    if store_root is not None:
        return JsonlRecordStore(root=store_root)
    if tmp_root is not None:
        return JsonlRecordStore(root=tmp_root / "records")
    return default_record_store()


def _record_store_for_run(cfg: RaceConfig, tmp_root: Optional[Path]) -> RecordStore:
    return resolve_record_store(cfg.record_store, cfg.store_root, tmp_root)


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
    requested_arity: Optional[int] = None
    casting: Optional[dict[str, Any]] = None
    notes: list[str] = field(default_factory=list)
    judgements: list[dict[str, Any]] = field(default_factory=list)
    # Phase 2 (conference): the same candidates after talking; audited separately.
    conference_results: list[TerrariumCandidateResult] = field(default_factory=list)
    conference_entries: list[ArchivistEntry] = field(default_factory=list)
    conference_winner: Optional[TerrariumCandidateResult] = None
    evidence: Optional[EvidenceBundle] = None
    evidence_history: list[EvidenceBundle] = field(default_factory=list)
    evaluations: list[Evaluation] = field(default_factory=list)
    resolution: Optional[Resolution] = None
    journal: Optional[TrialJournal] = field(default=None, repr=False, compare=False)
    resolution_event_sequence: Optional[int] = None

    def entry_for(self, r: TerrariumCandidateResult) -> Optional[ArchivistEntry]:
        conference_result = any(candidate is r for candidate in self.conference_results)
        entries = self.conference_entries + self.entries if conference_result else self.entries + self.conference_entries
        return next((e for e in entries if e.candidate_id == r.candidate_id), None)

    @property
    def active_results(self) -> list[TerrariumCandidateResult]:
        return self.conference_results or self.results

    @property
    def active_entries(self) -> list[ArchivistEntry]:
        return self.conference_entries or self.entries

    @property
    def provisional_winner(self) -> Optional[TerrariumCandidateResult]:
        return self.conference_winner or self.winner

    @property
    def resolved_candidate(self) -> Optional[TerrariumCandidateResult]:
        if self.resolution is None or not self.resolution.resolved:
            return None
        return next(
            (result for result in self.active_results if result.candidate_id == self.resolution.candidate_id),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.brief,
            "trial_id": self.task.id,
            "task_name": self.race_task.name if self.race_task else None,
            "hidden_tests": sorted(self.task.hidden_tests),
            "ephemeral": self.ephemeral,
            "arity": {
                "requested_max": self.requested_arity,
                "resolved": len(self.candidates),
            },
            "casting": self.casting,
            "winner": self.winner.spec.name if self.winner and self.winner.spec else None,
            "winner_signature": self.winner.signature if self.winner else None,
            "winner_is_provisional": bool(
                self.winner and self.entry_for(self.winner) and self.entry_for(self.winner).tied_with
            ),
            "evidence_hash": self.evidence.evidence_hash if self.evidence else None,
            "evidence_history": [bundle.evidence_hash for bundle in self.evidence_history],
            "evaluations": [evaluation.to_dict() for evaluation in self.evaluations],
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "resolved_winner": (
                self.resolved_candidate.spec.name
                if self.resolved_candidate and self.resolved_candidate.spec
                else None
            ),
            "event_count": len(self.journal.events) if self.journal else 0,
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
    """Authenticated, unlocked seats, fullest quota first (so a model name resolves to the account that can still pay).

    Presence is the only filter, because this serves variant resolution rather than casting: a
    model the caller names by hand must bind to the seat that really provides it. An exhausted
    seat kept here fails honestly on a 429; dropped, the name falls through to an invented
    ``provider="custom"`` seat and resolves to whatever the default wire is. Casting does its
    own quota filtering, in ``cast_seats``.
    """
    ledger = ledger or SeatLedger()
    seats = [s for s in ledger.list_seats() if not s.presence]
    return sorted(seats, key=lambda s: -s.remaining / max(1.0, s.total_allowance))


def _parse_custom_variant(spec_str: str, seats: list[Seat], role: Role, idx: int) -> tuple[CandidateSpec, list[str]]:
    """One arm from one ``key=value`` group, plus notes about anything it had to invent."""
    kv: dict[str, str] = {}
    notes: list[str] = []
    for part in spec_str.split("+"):
        if "=" in part:
            k, v = part.split("=", 1)
            kv[k.strip().lower()] = v.strip()
        elif part.strip():
            kv.setdefault("model", part.strip())
    model = kv.get("model")
    seat = next((s for s in seats if model and (s.model == model or s.id == model)), None)
    if seat is None and not model:
        seat = seats[idx % len(seats)]
    elif seat is None:
        # A model no seat provides still has to run somewhere, but the invented seat names no
        # provider, so the wire resolves it to its default backend on an ambient key. Say so:
        # a silent rebinding is indistinguishable from having raced the model that was asked for.
        seat = Seat(provider="custom", model=model)
        notes.append(
            f"no ledger seat provides model '{model}'; racing it on an invented seat with no "
            "provider binding, which resolves to the default wire backend"
        )
    skills = [s for s in kv["skills"].split("/") if s] if "skills" in kv else ["pytest-tdd"]
    spec = CandidateSpec(
        seat=seat,
        name=kv.get("name", spec_str),
        role=role,
        harness=kv.get("harness", "wire"),
        tool_runner_type=kv.get("tools", "sandbox"),
        skills=skills,
        context=kv.get("ctx", kv.get("context", "accounts")),
    )
    return spec, notes


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
        specs = []
        for i, part in enumerate(parts):
            spec, spec_notes = _parse_custom_variant(part, seats, role, i)
            specs.append(spec)
            notes += spec_notes
    if len(specs) < 2:
        notes.append(f"unary trial: {len(specs)} candidate resolved; comparison requires at least two")
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
        if cand.custom_model_provider is None:
            cand.custom_model_provider = factory()
            cand.name = f"{cand.name} [{label}]"


def canned_mock_judgement(candidate_count: int) -> str:
    """Build a valid canned blind ranking for exactly the candidates in a mock trial."""
    labels = [chr(ord("A") + i) for i in range(candidate_count)]
    reasons = [f"{i}. {label} - canned mock preference." for i, label in enumerate(labels, 1)]
    cherry_picks = {"B": "its eviction test"} if "B" in labels else {}
    payload = {"order": labels, "ties": [], "cherry_picks": cherry_picks}
    return "\n".join([*reasons, json.dumps(payload)])


def _snapshot_artifacts(result: TerrariumCandidateResult) -> tuple[ArtifactEvidence, ...]:
    """Capture candidate-created files before teardown without retaining host paths."""
    from .terrarium import ARTIFACT_IGNORE_PARTS

    workspace = Path(result.workspace_path)
    if not workspace.is_dir():
        return ()
    artifacts: list[ArtifactEvidence] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if any(part in ARTIFACT_IGNORE_PARTS for part in relative.parts):
            continue
        artifacts.append(ArtifactEvidence.from_bytes(relative.as_posix(), path.read_bytes()))
    return tuple(artifacts)


def _arm_declaration(candidate: CandidateSpec) -> dict[str, Any]:
    return {
        "arm_id": str(candidate.metadata["arm_id"]),
        "arm_ordinal": int(candidate.metadata["arm_ordinal"]),
        "name": candidate.name,
        "signature": candidate.signature(default_role=candidate.role.name if candidate.role else "builder"),
        "model": candidate.seat.model,
        "provider": candidate.seat.provider,
        "role": candidate.role.name if candidate.role else "builder",
        "harness": candidate.harness_name,
        "tool_runner": candidate.tool_runner_name,
        "skills": candidate.skill_names,
        "context": candidate.context,
        "context_adapter": candidate.context_adapter_id,
    }


def _record_completed_arms(report: RaceReport, phase: str) -> None:
    if report.journal is None:
        return
    results = report.conference_results if phase == "conference" else report.results
    for result in results:
        metadata = result.spec.metadata if result.spec else {}
        spec = result.spec
        report.journal.append(
            "arm.completed",
            {
                "phase": phase,
                "arm_id": str(metadata.get("arm_id", result.candidate_id)),
                "arm_ordinal": int(metadata.get("arm_ordinal", 0)),
                "candidate_id": result.candidate_id,
                "name": spec.name if spec else result.candidate_id,
                "status": result.status,
                "signature": result.signature,
                "model": result.seat.model,
                "provider": result.seat.provider,
                "role": result.role.name,
                "harness": result.harness,
                "tool_runner": result.tool_runner_name,
                "skills": result.skills_used,
                "context": spec.context if spec else "accounts",
                "context_adapter": spec.context_adapter_id if spec else None,
                "tokens_used": result.tokens_used,
                "duration_seconds": result.duration_seconds,
                "fallbacks": result.fallbacks,
            },
            idempotency_key=f"arm.completed:{phase}:{metadata.get('arm_id', result.candidate_id)}",
        )


def freeze_report_evidence(
    report: RaceReport,
    *,
    parent_evidence_hash: Optional[str] = None,
) -> EvidenceBundle:
    """Snapshot the report's final factual phase into a content-addressed bundle."""
    entries = {entry.candidate_id: entry for entry in report.active_entries}
    ordered_results = sorted(
        report.active_results,
        key=lambda result: int((result.spec.metadata if result.spec else {}).get("arm_ordinal", 0)),
    )
    candidates: list[CandidateEvidence] = []
    for result in ordered_results:
        entry = entries.get(result.candidate_id)
        spec = result.spec
        spec_metadata = spec.metadata if spec else {}
        candidates.append(
            CandidateEvidence.create(
                candidate_id=result.candidate_id,
                name=spec.name if spec else result.candidate_id,
                signature=result.signature,
                model=result.seat.model,
                provider=result.seat.provider,
                role=result.role.name,
                harness=result.harness,
                tool_runner=result.tool_runner_name,
                skills=result.skills_used,
                context=spec.context if spec else "accounts",
                status=result.status,
                verdict=entry.verdict if entry else result.status,
                rank=entry.rank if entry else 0,
                tied_with=entry.tied_with if entry else (),
                tokens_used=result.tokens_used,
                duration_seconds=result.duration_seconds,
                fallbacks=result.fallbacks,
                test_results=result.test_results or {},
                axes=entry.axes if entry else {},
                artifacts=_snapshot_artifacts(result),
                output=result.output,
                arm_id=str(spec_metadata.get("arm_id", result.candidate_id)),
                arm_ordinal=int(spec_metadata.get("arm_ordinal", len(candidates))),
                context_adapter=spec.context_adapter_id if spec else None,
            )
        )
    hidden_test_hashes = {
        name: hashlib.sha256(source.encode("utf-8")).hexdigest()
        for name, source in sorted(report.task.hidden_tests.items())
    }
    phase = "conference" if report.conference_results else "trial"
    bundle = EvidenceBundle.create(
        trial_id=report.task.id,
        task_id=report.task.id,
        task_name=report.race_task.name if report.race_task else None,
        brief=report.task.brief,
        candidates=candidates,
        hidden_test_hashes=hidden_test_hashes,
        metadata={
            "phase": phase,
            "parent_evidence_hash": parent_evidence_hash,
            "requested_arity": report.requested_arity,
            "resolved_arity": len(candidates),
            "task": report.task.metadata,
        },
    )
    report.evidence = bundle
    report.evidence_history.append(bundle)
    report.archivist.store.append(
        StoreRecord(kind="evidence_bundle", record=bundle.to_dict())
    )
    if report.journal:
        report.journal.append(
            "evidence.frozen",
            {"bundle": bundle.to_dict()},
            idempotency_key=f"evidence.frozen:{bundle.evidence_hash}",
        )
    return bundle


def _facts_context(report: RaceReport) -> tuple[Optional[str], tuple[str, ...], bool]:
    """Return the evidence-derived factual leader, peers, and support."""
    if report.evidence is None:
        return None, (), False
    eligible, supported = factual_eligibility(report.evidence)
    if not eligible:
        return None, (), False
    provisional = min(
        (report.evidence.candidate(candidate_id) for candidate_id in eligible),
        key=lambda candidate: (candidate.rank if candidate.rank > 0 else 10**9, candidate.arm_ordinal),
    )
    tied_with = tuple(candidate_id for candidate_id in eligible if candidate_id != provisional.candidate_id)
    return provisional.candidate_id, tied_with, supported


def _evaluation_from_judgement(bundle: EvidenceBundle, judgement: dict[str, Any]) -> Optional[Evaluation]:
    if not judgement.get("parsed"):
        return None
    evaluator_id = str(judgement.get("evaluator_id") or f"judge:{judgement.get('judge', 'unknown')}")
    try:
        return Evaluation.create(
            bundle,
            evaluator_id=evaluator_id,
            order=judgement.get("order") or (),
            ties=judgement.get("ties") or (),
            reason=str(judgement.get("text") or ""),
            metadata={
                "citations": judgement.get("citations") or {},
                "ranked_own_model_first": bool(judgement.get("ranked_own_model_first")),
                "judge_seat": judgement.get("judge_seat"),
            },
        )
    except ValueError:
        return None


def _record_review_attempt(
    report: RaceReport,
    *,
    evaluator_id: str,
    status: str,
    evaluation: Optional[Evaluation] = None,
    error: Optional[str] = None,
    raw: Optional[dict[str, Any]] = None,
) -> None:
    if evaluation is not None:
        report.evaluations.append(evaluation)
        report.archivist.store.append(
            StoreRecord(
                kind="evaluation",
                record={"task_id": report.task.id, **evaluation.to_dict()},
            )
        )
    if report.journal:
        review_key = (
            f"review.recorded:{evaluation.evaluation_id}"
            if evaluation is not None
            else f"review.attempt:{report.evidence.evidence_hash if report.evidence else 'none'}:{evaluator_id}:{status}"
        )
        report.journal.append(
            "review.recorded",
            {
                "evaluator_id": evaluator_id,
                "evidence_hash": report.evidence.evidence_hash if report.evidence else None,
                "status": status,
                "error": error,
                "evaluation": evaluation.to_dict() if evaluation else None,
                "raw": raw,
            },
            idempotency_key=review_key,
        )


def evaluate_report(report: RaceReport, evaluator: TrialEvaluator) -> Evaluation:
    """Evaluate and record an already-frozen report without rerunning its candidates."""
    if report.evidence is None:
        raise ValueError("report evidence must be frozen before evaluation")
    evaluation = evaluate_bundle(report.evidence, evaluator)
    return record_evaluation(report, evaluation)


def record_evaluation(report: RaceReport, evaluation: Evaluation) -> Evaluation:
    """Attach a precomputed evaluation of the report's frozen bundle to its journal."""
    if report.evidence is None:
        raise ValueError("report evidence must be frozen before recording an evaluation")
    evaluation.validate(report.evidence)
    _record_review_attempt(
        report,
        evaluator_id=evaluation.evaluator_id,
        status="completed",
        evaluation=evaluation,
    )
    return evaluation


def resolve_report(
    report: RaceReport,
    *,
    expected_evaluator_ids: tuple[str, ...] = (),
    human_candidate_id: Optional[str] = None,
) -> Resolution:
    """Resolve and persist one report from its frozen evidence and evaluations."""
    if report.evidence is None:
        raise ValueError("report evidence must be frozen before resolution")
    facts_candidate_id, facts_tied_with, facts_supported = _facts_context(report)
    expected_panel = tuple(expected_evaluator_ids)
    if human_candidate_id is not None and not expected_panel and report.resolution is not None:
        expected_panel = report.resolution.expected_evaluator_ids
    resolution = resolve_bundle(
        report.evidence,
        facts_candidate_id=facts_candidate_id,
        facts_tied_with=facts_tied_with,
        facts_supported=facts_supported,
        evaluations=report.evaluations,
        expected_evaluator_ids=expected_panel,
        human_candidate_id=human_candidate_id,
    )
    resolution.validate(report.evidence, report.evaluations)
    report.resolution = resolution
    report.archivist.store.append(
        StoreRecord(
            kind="resolution",
            record={"task_id": report.task.id, **resolution.to_dict()},
        )
    )
    if report.journal:
        event = report.journal.append(
            "resolution.recorded",
            {"resolution": resolution.to_dict()},
            idempotency_key=f"resolution.recorded:{resolution.resolution_id}",
        )
        report.resolution_event_sequence = event.sequence
    return resolution


# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------

def named_task(task_name: Optional[str]) -> Optional[RaceTask]:
    """The task the caller named, or None when none was. An unknown name is refused, not guessed."""
    if not task_name:
        return None
    race_task = TaskBank().get(task_name)
    if race_task is None:
        raise SystemExit(f"unknown task '{task_name}'; see `arity tasks`")
    return race_task


def role_for_trial(
    role_name: str, race_task: Optional[RaceTask], roles: Optional[RoleRegistry] = None
) -> tuple[Role, list[str]]:
    """The role a trial actually scores under: the named role, plus the type its task's tags select.

    The front door casts against this and ``run_race`` grades against it, so the scorecard key
    the cast reads is the key the trial writes: ``--role developer --task lru_cache`` is
    ``developer.python:<model>`` on both sides, not ``developer:<model>`` on one.
    """
    registry = roles if roles is not None else RoleRegistry()
    role = registry.get(role_name) or BUILDER_ROLE
    notes: list[str] = []
    if race_task is not None:
        # A task's tags pick the type (python, rust, ...) unless the role already names one.
        pack = registry.type_for_tags(race_task.tags)
        if pack and not role.type_name:
            role = registry.with_type(role, pack.name)
            notes.append(f"type '{pack.name}' from task tags -> {role.name}")
    return role, notes


def run_race(cfg: RaceConfig) -> RaceReport:
    notes: list[str] = []
    roles = RoleRegistry()

    race_task = named_task(cfg.task_name)
    role, role_notes = role_for_trial(cfg.role, race_task, roles)
    notes += role_notes
    type_name = role.type_name or None
    brief = cfg.prompt or (race_task.brief if race_task else "")
    if not brief:
        raise SystemExit("a prompt or --task is required")

    if cfg.candidate_specs is not None:
        candidates = list(cfg.candidate_specs)
        seats = [candidate.seat for candidate in candidates]
        if not candidates:
            raise ValueError("candidate_specs must contain at least one arm")
        if len(candidates) < 2:
            notes.append("unary trial: 1 candidate resolved; comparison requires at least two")
    else:
        if cfg.seats is not None:
            seats = list(cfg.seats)
        else:
            seats = placeholder_seats() if cfg.mock else live_seats()
        candidates, c_notes = resolve_candidates(cfg.variants, role, seats)
        notes += c_notes
    if cfg.mock:
        attach_mocks(candidates)
        notes.append("mock mode: canned providers, ephemeral store, sandboxes torn down")
        if cfg.judges and not cfg.judge_provider:
            # Match the resolved blind bundle exactly; smaller arities must not invent phantom labels.
            canned_review = canned_mock_judgement(len(candidates))
            cfg.judge_provider = lambda model: ScriptedProvider(
                {}, canned_review, f"judge-{model}"
            )

    for ordinal, candidate in enumerate(candidates):
        candidate.metadata.setdefault("arm_ordinal", ordinal)
        candidate.metadata.setdefault("arm_id", f"arm-{ordinal + 1}")

    # Mock runs never write to the real scorecard; live runs are the scorecard's whole purpose.
    ephemeral = cfg.mock
    needs_temp_root = ephemeral and (
        (cfg.record_store is None and cfg.store_root is None) or cfg.workspace_root is None
    )
    tmp_root = Path(tempfile.mkdtemp(prefix="arity_trial_")) if needs_temp_root else None
    store = _record_store_for_run(cfg, tmp_root)
    workspace = cfg.workspace_root or (tmp_root / "terrarium" if tmp_root else Path(".terrarium"))
    ledger = SeatLedger(initial_seats=[c.seat for c in candidates], auto_seed=False)
    dispatcher = TerrariumDispatcher(ledger=ledger, store=store, base_workspace=workspace, quiet=cfg.as_json or cfg.quiet)
    archivist = ImpartialArchivist(scorecard=cfg.scorecard, store=store)

    task = TaskRecord(
        brief=brief,
        from_role="Asa",
        to_role=role.name,
        hidden_tests=dict(race_task.hidden_tests) if race_task else {},
        metadata={
            "module": race_task.module,
            "entrypoint": race_task.entrypoint,
            "task_key": race_task.name,
        } if race_task else {"task_key": brief},
    )
    journal = TrialJournal(store, task.id)
    journal.append(
        "trial.started",
        {
            "task_id": task.id,
            "task_name": race_task.name if race_task else None,
            "brief": task.brief,
            "role": task.to_role,
            "hidden_test_hashes": {
                name: hashlib.sha256(source.encode("utf-8")).hexdigest()
                for name, source in sorted(task.hidden_tests.items())
            },
            "requested_arity": cfg.requested_arity,
            "resolved_arity": len(candidates),
            "casting": cfg.casting,
            "arms": [_arm_declaration(candidate) for candidate in candidates],
            "evaluator_ids": [
                str(getattr(evaluator, "evaluator_id", evaluator.__class__.__name__))
                for evaluator in cfg.evaluators
            ] + [f"judge:{model}" for model in cfg.judges],
        },
        idempotency_key="trial.started",
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
    report = RaceReport(
        task=task,
        race_task=race_task,
        candidates=candidates,
        winner=winner,
        results=results,
        entries=entries,
        archivist=archivist,
        ephemeral=ephemeral,
        requested_arity=cfg.requested_arity,
        casting=cfg.casting,
        notes=notes,
        journal=journal,
    )

    _record_completed_arms(report, "trial")
    initial_bundle = freeze_report_evidence(report)

    if cfg.conference > 0 and results:
        phase2 = dispatcher.conference(task, results, entries=entries, rounds=cfg.conference,
                                       max_workers=cfg.workers, test_command=cfg.test_command)
        c_winner, c_entries = archivist.evaluate_trial(phase2)
        report.conference_results, report.conference_entries, report.conference_winner = phase2, c_entries, c_winner
        _record_completed_arms(report, "conference")
        bundle = freeze_report_evidence(report, parent_evidence_hash=initial_bundle.evidence_hash)
    else:
        bundle = initial_bundle
    provisional = report.provisional_winner
    top = next(
        (entry for entry in report.active_entries if provisional and entry.candidate_id == provisional.candidate_id),
        None,
    )
    facts_tie = bool(top and top.tied_with)
    has_evaluators = bool(cfg.evaluators or cfg.judges)
    should_review = has_evaluators and cfg.review != "never" and (cfg.review == "always" or facts_tie)
    expected_evaluator_ids: list[str] = []
    if should_review:
        for evaluator in cfg.evaluators:
            evaluator_id = str(getattr(evaluator, "evaluator_id", evaluator.__class__.__name__))
            expected_evaluator_ids.append(evaluator_id)
            try:
                evaluate_report(report, evaluator)
            except Exception as exc:
                notes.append(f"evaluator '{evaluator_id}' failed: {exc}")
                _record_review_attempt(
                    report,
                    evaluator_id=evaluator_id,
                    status="failed",
                    error=str(exc),
                )
                continue
        if cfg.judges:
            expected_evaluator_ids.extend(f"judge:{model}" for model in cfg.judges)
            report.judgements = run_review(report, cfg, dispatcher, seats)
            for judgement in report.judgements:
                evaluation = _evaluation_from_judgement(bundle, judgement)
                _record_review_attempt(
                    report,
                    evaluator_id=str(judgement.get("evaluator_id", "unknown")),
                    status="completed" if evaluation else "invalid",
                    evaluation=evaluation,
                    raw=judgement,
                )
            recorded_judges = {
                str(judgement.get("evaluator_id")) for judgement in report.judgements
            }
            for evaluator_id in (f"judge:{model}" for model in cfg.judges):
                if evaluator_id not in recorded_judges:
                    _record_review_attempt(
                        report,
                        evaluator_id=evaluator_id,
                        status="missing",
                        error="no matching reviewer seat completed",
                    )
    elif has_evaluators and not facts_tie:
        notes.append("review skipped: the facts already separated the candidates (use --review always to force)")

    resolve_report(report, expected_evaluator_ids=tuple(expected_evaluator_ids))

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
    bundle = rep.evidence or freeze_report_evidence(rep)
    ordered = sorted(
        bundle.candidates,
        key=lambda candidate: hashlib.sha256(
            f"{bundle.evidence_hash}:{candidate.candidate_id}".encode("utf-8")
        ).hexdigest(),
    )
    letters = [chr(ord("A") + i) for i in range(len(ordered))]
    key = {letter: candidate.candidate_id for letter, candidate in zip(letters, ordered)}
    parts = [
        f"# Evidence bundle\n{bundle.evidence_hash}\n\n# Brief\n{bundle.brief.strip()}\n"
    ]
    for letter, candidate in zip(letters, ordered):
        L = letter
        parts.append(f"\n\n# Candidate {L}")
        for artifact in candidate.artifacts:
            if artifact.text is not None:
                parts.append(f"\n## {artifact.path}\n```\n{artifact.text}\n```")
            else:
                parts.append(
                    f"\n## {artifact.path}\n(binary, {artifact.size} bytes, sha256 {artifact.sha256})"
                )
        tr = candidate.test_results
        own, hidden = tr.get("own") or {}, tr.get("hidden") or {}
        axes = candidate.axes
        counted = ", ".join(f"{key_name}={axes[key_name]}" for key_name in (
            "loc", "test_count", "type_ignores", "bare_asserts", "compile_ok", "tool_calls", "tool_errors",
            "model_turns", "module_present", "entrypoint_present", "brief_numbers_in_own_tests")
            if key_name in axes)
        parts.append(
            f"\n## Archivist facts for {L}\n"
            f"- verdict: {candidate.verdict or candidate.status}\n"
            f"- own tests: {own.get('passed', 0)}/{own.get('total', 0)} | hidden tests: {hidden.get('passed', 0)}/{hidden.get('total', 0)}\n"
            f"- counted already (do not re-count): {counted}\n"
            f"- candidate's own closing report: {(candidate.output or '').strip()}"
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
    """Parse a complete blind-label permutation and map it back to candidate ids.

    A ranking is accepted only when it contains every current label exactly once and no
    tie or cherry-pick references an unknown label. Invalid model output remains recorded
    as text by the caller, but cannot leak phantom candidates into resolved evidence.
    """
    out: dict[str, Any] = {"order": [], "ties": [], "cherry_picks": {}, "parsed": False}
    if not key:
        return out
    expected = set(key)
    decoder = json.JSONDecoder()
    text = text or ""
    # Try every '{' from the end backwards; the ranking object may nest (cherry_picks is a dict).
    for start in reversed([i for i, ch in enumerate(text) if ch == "{"]):
        try:
            data, _ = decoder.raw_decode(text[start:])
            if not isinstance(data, dict) or "order" not in data:
                continue

            order = data.get("order")
            if (
                not isinstance(order, list)
                or len(order) != len(key)
                or any(not isinstance(label, str) for label in order)
                or len(set(order)) != len(order)
                or set(order) != expected
            ):
                continue

            ties = data.get("ties", [])
            if ties is None:
                ties = []
            if not isinstance(ties, list):
                continue
            valid_ties = True
            for tied in ties:
                if (
                    not isinstance(tied, list)
                    or len(tied) < 2
                    or any(not isinstance(label, str) or label not in expected for label in tied)
                    or len(set(tied)) != len(tied)
                ):
                    valid_ties = False
                    break
            if not valid_ties:
                continue

            cherry_picks = data.get("cherry_picks", {})
            if cherry_picks is None:
                cherry_picks = {}
            if not isinstance(cherry_picks, dict) or any(label not in expected for label in cherry_picks):
                continue

            out["order"] = [key[label] for label in order]
            out["ties"] = [[key[label] for label in tied] for tied in ties]
            out["cherry_picks"] = {key[label]: value for label, value in cherry_picks.items()}
            out["parsed"] = True
            break
        except (json.JSONDecodeError, ValueError, KeyError):
            continue
    return out
def check_citations(text: str, key: dict[str, str], rep: RaceReport) -> dict[str, Any]:
    """For each 'Candidate X' paragraph, check identifiers against frozen artifacts.

    A judge is required to cite; this checks the citations are real. It is a fact printed next to
    the judgement, not a score.
    """
    import re
    bundle = rep.evidence or freeze_report_evidence(rep)
    by_id = {candidate.candidate_id: candidate for candidate in bundle.candidates}
    corpus: dict[str, str] = {}
    for L, cid in key.items():
        blob = ""
        candidate = by_id.get(cid)
        if candidate:
            for artifact in candidate.artifacts:
                blob += artifact.path + "\n" + (artifact.text or "") + "\n"
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
    roles = RoleRegistry()
    # The judge takes the same type as the builders: reviewer:python judges a python race.
    reviewer = roles.with_type(roles.get("reviewer"), (rep.candidates[0].role.type_name if rep.candidates and rep.candidates[0].role else None))
    text, key = blind_bundle(rep)
    review_task = TaskRecord(
        brief=text,
        from_role="Asa",
        to_role="reviewer",
        metadata={
            "reviews": rep.task.id,
            "evidence_hash": rep.evidence.evidence_hash if rep.evidence else None,
        },
    )
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
    model_of = {
        candidate.candidate_id: candidate.model
        for candidate in (rep.evidence.candidates if rep.evidence else ())
    }
    for r in results:
        j = parse_judgement(r.output or "", key)
        # Two facts about the judgement itself, reported not scored: did it rank its own model first,
        # and do the identifiers it cites (files, tests, names) actually exist in the sandbox it cites them for.
        j["ranked_own_model_first"] = bool(j["order"]) and model_of.get(j["order"][0]) == r.seat.model
        j["citations"] = check_citations(r.output or "", key, rep)
        j.update({
            "task_id": rep.task.id, "judge": r.seat.model, "judge_seat": r.seat.id, "harness": r.harness,
            "evaluator_id": f"judge:{r.seat.model}",
            "evidence_hash": rep.evidence.evidence_hash if rep.evidence else None,
            "status": r.status, "tokens_used": r.tokens_used, "duration_seconds": round(r.duration_seconds, 2),
            "key": key, "text": r.output or r.error or "",
        })
        judgements.append(j)
        dispatcher.store.append(StoreRecord(kind="judgement", record=j))
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


# -----------------------------------------------------------------------------
# Front door: arity run "<brief>" -> cast -> trial with chosen axes -> delivery
# -----------------------------------------------------------------------------

def default_wire_capable(s: Seat) -> bool:
    """A seat whose provider can call tools. A bare CLI seat can only narrate work it cannot do."""
    from .handlers import CLIModelProvider
    try:
        from .wire import create_wire_model_provider
        return not isinstance(create_wire_model_provider(s), CLIModelProvider)
    except Exception:
        return False


def cast_seats(
    role: Role,
    brief: str,
    requested: int,
    *,
    task_key: Optional[str] = None,
    ledger: Optional[SeatLedger] = None,
    scorecard: Optional[Any] = None,
    mode: str = SMART,
    seed: Optional[int] = None,
    wire_capable: Optional[Callable[[Seat], bool]] = default_wire_capable,
    now: Optional[float] = None,
) -> tuple[CastingDecision, list[Seat], list[str]]:
    """Cast up to `requested` distinct models for one trial.

    Returns the decision, the pool it drew from, and notes about anything the cast gave up.

    Different models, not different accounts of one model: a run compares as many distinct minds
    as are available, up to the requested maximum.

    Wire capability is an eligibility filter that degrades, not an all-or-nothing pool swap, so
    it belongs with question B's filtering rather than with the mode's ordering. The cast is
    made from wire-capable seats; CLI-only seats fill slots that would otherwise go unfilled,
    and never displace an available wire seat, because a seat that cannot use tools is not
    interchangeable with one that can.
    """
    ledger = ledger if ledger is not None else SeatLedger()
    pool = ledger.list_available(now=now)
    notes: list[str] = []

    def cast_from(seats: list[Seat], count: int) -> CastingDecision:
        composer = CastingComposer(
            ledger=SeatLedger(initial_seats=seats, auto_seed=False), scorecard=scorecard
        )
        return composer.cast(
            role, task_key or brief, candidates_count=count, now=now,
            mode=mode, seed=seed, distinct_on="model",
        )

    if wire_capable is None:
        return cast_from(pool, requested), pool, notes

    wired = [seat for seat in pool if wire_capable(seat)]
    wired_ids = {seat.id for seat in wired}
    cli_only = [seat for seat in pool if seat.id not in wired_ids]
    if not wired:
        if cli_only:
            notes.append(
                "casting: no wire-capable seat is available, so the whole cast is CLI-only, "
                "which can only narrate work it cannot do"
            )
        return cast_from(pool, requested), pool, notes

    decision = cast_from(wired, requested)
    filled = len(decision.candidates)
    seated_models = {seat.model for seat in decision.candidates}
    fill = [seat for seat in cli_only if seat.model not in seated_models]
    if filled >= requested or not fill:
        return decision, pool, notes

    widened = cast_from(fill, requested - filled)
    candidates = [*decision.candidates, *widened.candidates]
    notes.append(
        "casting: widened to CLI-only seat(s) "
        f"({', '.join(seat.model for seat in widened.candidates)}) for {len(widened.candidates)} "
        f"slot(s) the {len(seated_models)} wire-capable model(s) could not fill; a CLI-only seat "
        "can only narrate work it cannot do"
    )
    shortfall = None
    if len(candidates) < requested:
        shortfall = (
            f"requested {requested}, satisfied {len(candidates)}: {len(pool)} seat(s) survived "
            f"question B, holding {len({seat.model for seat in pool})} distinct model value(s)"
        )
    return (
        replace(
            decision,
            candidates=candidates,
            shortfall=shortfall,
            reason=f"{decision.reason} Widened by {len(widened.candidates)} CLI-only seat(s).",
        ),
        pool,
        notes,
    )


def casting_record(decision: CastingDecision) -> dict[str, Any]:
    """The cast as a record: what was asked for, what was seated, under which mode and seed."""
    return {
        "mode": decision.mode,
        "seed": decision.seed,
        "distinct_on": decision.distinct_on,
        "requested_count": decision.requested_count,
        "satisfied_count": decision.satisfied_count,
        "shortfall": decision.shortfall,
        "exploration_seat": decision.exploration_seat.id if decision.exploration_seat else None,
        "seats": [seat.id for seat in decision.candidates],
        "reason": decision.reason,
    }


@dataclass
class Delivery:
    task_id: str
    out_dir: Path
    files: list[str]
    answer: Optional[str]
    winner_name: str
    signature: str
    receipt: str
    asked_human: bool = False
    delivered: bool = True
    resolution_source: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "out_dir": str(self.out_dir),
            "files": list(self.files),
            "answer": self.answer,
            "winner_name": self.winner_name,
            "signature": self.signature,
            "receipt": self.receipt,
            "asked_human": self.asked_human,
            "delivered": self.delivered,
            "resolution_source": self.resolution_source,
        }


def judges_split(rep: RaceReport) -> bool:
    """True when the parsed judgements do not share a first place."""
    firsts = {j["order"][0] for j in rep.judgements if j.get("parsed") and j.get("order")}
    return len(firsts) > 1


def human_pick(rep: RaceReport, ask: Callable[[str], str] = input, printer: Callable[..., None] = print) -> Optional[TerrariumCandidateResult]:
    """The secretary's question: facts tied and the judges disagree, so Asa picks. Records the pick."""
    names = {r.candidate_id: (r.spec.name if r.spec else r.candidate_id) for r in rep.active_results}
    firsts: dict[str, list[str]] = {}
    for j in rep.judgements:
        if j.get("parsed") and j.get("order"):
            firsts.setdefault(j["order"][0], []).append(j["judge"])
    eligible = set(rep.resolution.eligible_candidate_ids if rep.resolution else firsts)
    options = [result for result in rep.active_results if result.candidate_id in eligible]
    printer("The facts tie and the judges disagree. Two candidates, one difference per line:")
    for i, r in enumerate(options, 1):
        e = rep.entry_for(r)
        a = (e.axes if e else {}) or {}
        printer(f"  [{i}] {names[r.candidate_id]}: loc={a.get('loc', '-')} tests={a.get('test_count', '-')} "
                f"tokens={r.tokens_used:,} {r.duration_seconds:.0f}s  "
                f"(preferred by {', '.join(firsts.get(r.candidate_id, [])) or 'no decisive reviewer'})")
        printer(f"      {(r.output or '').strip().splitlines()[0][:110] if (r.output or '').strip() else '(no closing report)'}")
    try:
        answer = ask("Which do you prefer? [number, or blank to leave unresolved] ").strip()
        idx = int(answer) - 1
        pick = options[idx] if 0 <= idx < len(options) else None
    except Exception:
        pick = None
    if rep.archivist.store:
        rep.archivist.store.append(StoreRecord(kind="human_pick", record={
            "task_id": rep.task.id, "options": [r.candidate_id for r in options],
            "evidence_hash": rep.evidence.evidence_hash if rep.evidence else None,
            "picked": pick.candidate_id if pick else None, "judges": {k: v for k, v in firsts.items()},
        }))
    if pick is not None:
        resolution = resolve_report(rep, human_candidate_id=pick.candidate_id)
        if not resolution.resolved:
            printer("Human preference recorded, but delivery remains unresolved without positive factual evidence.")
            return None
    return pick


_AUTO_FINAL = object()


def deliver(rep: RaceReport, out_dir: Optional[Path] = None, final: Any = _AUTO_FINAL) -> Delivery:
    """Deliver once while serializing journal preflight, filesystem writes, and event append."""
    if rep.journal is None:
        return _deliver_once(rep, out_dir=out_dir, final=final)
    with rep.journal.serialized():
        return _deliver_once(rep, out_dir=out_dir, final=final)


def _deliver_once(rep: RaceReport, out_dir: Optional[Path] = None, final: Any = _AUTO_FINAL) -> Delivery:
    """Copy the final candidate's work to out_dir (default deliveries/<task_id>/); if it wrote no files,
    write its closing report as answer.md. A present unresolved Resolution withholds delivery.

    Explicit ``final=`` remains a compatibility override for legacy reports. Resolution-aware
    reports only accept their resolved candidate and deliver bytes from the frozen evidence.
    """
    from .terrarium import ARTIFACT_IGNORE_PARTS
    out = Path(out_dir) if out_dir else Path("deliveries") / rep.task.id
    resolution_source: Optional[str] = None
    if final is _AUTO_FINAL:
        if rep.resolution is not None:
            final = rep.resolved_candidate
            resolution_source = rep.resolution.kind.value
        else:
            final = rep.provisional_winner
            resolution_source = "legacy_provisional"
    elif rep.resolution is not None and final is not None:
        if (
            not rep.resolution.resolved
            or rep.resolved_candidate is None
            or getattr(final, "candidate_id", None) != rep.resolution.candidate_id
        ):
            raise ValueError("explicit delivery cannot override a recorded resolution")
        final = rep.resolved_candidate
        resolution_source = rep.resolution.kind.value
    if rep.journal is not None and rep.resolution is None:
        raise RuntimeError("a journaled trial cannot deliver without its recorded resolution")
    if final is None:
        source = rep.resolution.kind.value if rep.resolution else "no_winner"
        return Delivery(
            task_id=rep.task.id,
            out_dir=out,
            files=[],
            answer=None,
            winner_name="no resolved winner",
            signature="",
            receipt=f"{source} - nothing delivered -> {out}",
            delivered=False,
            resolution_source=source,
        )
    recorded_delivery = None
    persisted_delivery_evidence: Optional[EvidenceBundle] = None
    persisted_delivery_evaluations: tuple[Evaluation, ...] = ()
    if rep.journal:
        persisted_events = rep.journal.events
        recorded_delivery = next(
            (event for event in persisted_events if event.event_type == "delivery.completed"),
            None,
        )
        if recorded_delivery is not None:
            raise RuntimeError("this trial already has a completed delivery")
        if rep.resolution is not None:
            if rep.resolution_event_sequence is None:
                raise RuntimeError("the delivery resolution was not persisted to the trial journal")
            try:
                persisted_replay = rep.journal.replay()
            except ValueError as exc:
                raise RuntimeError("the persisted trial lifecycle is not replayable") from exc
            if persisted_replay.unhandled_events:
                raise RuntimeError("the persisted trial lifecycle contains unhandled events")
            if (
                persisted_replay.latest_resolution != rep.resolution
                or not persisted_replay.resolution_sequences
                or persisted_replay.resolution_sequences[-1] != rep.resolution_event_sequence
            ):
                raise RuntimeError("the replayed resolution does not match this delivery")
            persisted_delivery_evidence = persisted_replay.evidence(rep.resolution.evidence_hash)
            persisted_delivery_evaluations = persisted_replay.evaluations
            persisted_resolutions = tuple(
                event for event in persisted_events if event.event_type == "resolution.recorded"
            )
            persisted_resolution = persisted_resolutions[-1] if persisted_resolutions else None
            encoded_resolution = (
                persisted_resolution.payload.get("resolution") if persisted_resolution else None
            )
            if (
                persisted_resolution is None
                or persisted_resolution.sequence != rep.resolution_event_sequence
                or not isinstance(encoded_resolution, Mapping)
                or encoded_resolution.get("resolution_id") != rep.resolution.resolution_id
                or encoded_resolution.get("evidence_hash") != rep.resolution.evidence_hash
            ):
                raise RuntimeError("the latest persisted resolution does not match this delivery")
    frozen_candidate = None
    if rep.resolution is not None:
        if rep.evidence is None:
            raise ValueError("a recorded resolution requires frozen evidence for delivery")
        delivery_evidence = persisted_delivery_evidence or rep.evidence
        if (
            rep.evidence != delivery_evidence
            or delivery_evidence.evidence_hash != rep.resolution.evidence_hash
        ):
            raise ValueError("delivery evidence does not match the recorded resolution")
        rep.resolution.validate(
            delivery_evidence,
            (
                persisted_delivery_evaluations
                if persisted_delivery_evidence is not None
                else tuple(rep.evaluations)
            ),
        )
        frozen_candidate = delivery_evidence.candidate(final.candidate_id)
        if out.exists():
            raise FileExistsError("resolution-controlled delivery requires a new output directory")
    out.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    if frozen_candidate is not None:
        for artifact in frozen_candidate.artifacts:
            dst = out.joinpath(*artifact.path.split("/"))
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(artifact.content_bytes())
            files.append(artifact.path)
    else:
        ws = Path(final.workspace_path)
        if ws.is_dir():
            for p in sorted(ws.rglob("*")):
                rel = p.relative_to(ws)
                if p.is_file() and not any(seg in ARTIFACT_IGNORE_PARTS for seg in rel.parts):
                    dst = out / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dst)
                    files.append(rel.as_posix())
    answer = None
    if not files:
        frozen_output = frozen_candidate.output if frozen_candidate is not None else final.output
        answer = (frozen_output or "").strip() or None
        if answer:
            (out / "answer.md").write_text(answer + "\n", encoding="utf-8")
    e = rep.entry_for(final)
    tr = frozen_candidate.test_results if frozen_candidate is not None else (final.test_results or {})
    hidden = tr.get("hidden") or {}
    winner_name = frozen_candidate.name if frozen_candidate is not None else (
        final.spec.name if final.spec else final.candidate_id
    )
    winner_signature = frozen_candidate.signature if frozen_candidate is not None else final.signature
    verdict = frozen_candidate.verdict if frozen_candidate is not None else (e.verdict if e else "-")
    duration_seconds = frozen_candidate.duration_seconds if frozen_candidate is not None else final.duration_seconds
    tokens_used = frozen_candidate.tokens_used if frozen_candidate is not None else final.tokens_used
    parts = [
        winner_name,
        verdict,
        (f"hidden {hidden.get('passed', 0)}/{hidden.get('total', 0)}" if hidden.get("has_tests") else "no hidden tests"),
        f"{duration_seconds:.0f}s",
        f"{tokens_used:,} tok",
        f"resolved by {resolution_source or 'explicit override'}",
        f"-> {out}",
    ]
    delivery = Delivery(task_id=rep.task.id, out_dir=out, files=files, answer=answer,
                        winner_name=winner_name, signature=winner_signature,
                        receipt=" · ".join(p for p in parts if p), delivered=True,
                        asked_human=bool(rep.resolution and rep.resolution.kind is ResolutionKind.HUMAN_PICK),
                        resolution_source=resolution_source or "explicit_override")
    if (
        rep.journal
        and rep.resolution
        and rep.resolution.resolved
        and rep.resolution.candidate_id == final.candidate_id
        and rep.resolution_event_sequence is not None
    ):
        event_delivery = delivery.to_dict()
        # The journal is portable trial state, so keep machine-local output paths
        # in the user-facing receipt rather than in the persisted event payload.
        event_delivery.pop("out_dir", None)
        event_delivery["receipt"] = " · ".join(p for p in parts[:-1] if p)
        if recorded_delivery is None:
            rep.journal.append(
                "delivery.completed",
                {
                    "candidate_id": final.candidate_id,
                    "resolution_sequence": rep.resolution_event_sequence,
                    "resolution_id": rep.resolution.resolution_id,
                    "evidence_hash": rep.resolution.evidence_hash,
                    "delivery": event_delivery,
                },
                idempotency_key=f"delivery.completed:{rep.resolution.resolution_id}",
            )
    return delivery


def run_front_door(brief: str, *, task_name: Optional[str] = None, role: str = "developer:python",
                   candidates: Optional[int] = None, judges: Optional[list[str]] = None, conference: int = 0,
                   tester: bool = False, out_dir: Optional[Path] = None, mock: bool = False, ask: Callable[[str], str] = input,
                   printer: Callable[..., None] = print, interactive: bool = True, quiet: bool = True,
                   evaluators: Optional[list[TrialEvaluator]] = None, store_root: Optional[Path] = None,
                   workspace_root: Optional[Path] = None, cast_mode: str = SMART,
                   cast_seed: Optional[int] = None) -> tuple[RaceReport, Delivery]:
    """Cast up to the requested number of unique candidates, then deliver the selected result."""
    requested_arity = resolve_arity(candidates, default=3)
    # The trial attaches the task's type pack before it grades, so the cast has to resolve the
    # same role: otherwise `--role developer --task lru_cache` casts on `developer:<model>`,
    # which nothing ever writes, and question A stays pinned at the baseline forever.
    cast_role, _ = role_for_trial(role, named_task(task_name))
    cast_notes: list[str] = []
    # A mock run spends no tokens and must not read the operator's record: no credential probe,
    # no scorecard, no store, and placeholder seats in place of the ledger.
    placeholder_ledger = SeatLedger(initial_seats=placeholder_seats(), auto_seed=False)
    # One store per run: the cast reads the evidence the trial is about to write into, and the
    # archivist reuses this scorecard rather than replaying the same store a second time.
    record_store = None if mock else resolve_record_store(store_root=store_root)
    scorecard = None if mock else Scorecard(store=record_store)
    try:
        decision, pool, pool_notes = cast_seats(
            cast_role, brief, requested_arity,
            task_key=task_name or brief,
            ledger=placeholder_ledger if mock else None,
            scorecard=scorecard,
            mode=cast_mode, seed=cast_seed,
            wire_capable=None if mock else default_wire_capable,
        )
    except RuntimeError as exc:
        cast_notes.append(f"{exc} Using placeholder seats (run `arity auth login`).")
        decision, pool, pool_notes = cast_seats(
            cast_role, brief, requested_arity,
            task_key=task_name or brief,
            ledger=placeholder_ledger,
            mode=cast_mode, seed=cast_seed, wire_capable=None,
        )
    cast_notes += pool_notes
    if decision.shortfall:
        cast_notes.append(f"casting: {decision.shortfall}")
    cast = decision.candidates
    seated = {seat.id for seat in cast}
    variants = ",".join(f"model={seat.model}" for seat in cast)
    resolved_arity = len(cast)
    cfg = RaceConfig(
        prompt=brief,
        task_name=task_name,
        variants=variants,
        role=role,
        mock=mock,
        workers=resolved_arity,
        judges=judges if judges is not None else [seat.model for seat in cast],
        review="tie",
        conference=conference,
        teardown=False,
        quiet=quiet,
        tester=tester,
        requested_arity=requested_arity,
        # Un-cast seats stay reachable so a named judge need not have raced.
        seats=[*cast, *(seat for seat in pool if seat.id not in seated)],
        casting=casting_record(decision),
        evaluators=list(evaluators or ()),
        store_root=store_root,
        record_store=record_store,
        scorecard=scorecard,
        workspace_root=workspace_root,
    )
    rep = run_race(cfg)
    actual_arity = len(rep.candidates)
    rep.requested_arity = requested_arity
    for note in reversed(cast_notes):
        rep.notes.insert(0, note)
    underfilled = actual_arity < requested_arity
    if underfilled:
        rep.notes.insert(
            0,
            f"arity requested max {requested_arity}; resolved {actual_arity} unique candidates",
        )
    asked = False
    resolution = getattr(rep, "resolution", None)
    if resolution and not resolution.resolved and rep.judgements and judges_split(rep):
        if interactive:
            human_pick(rep, ask=ask, printer=printer)
            asked = True
        else:
            candidate_letters = {
                letter: candidate_id
                for judgement in rep.judgements
                for letter, candidate_id in (judgement.get("key") or {}).items()
            }
            letters_by_candidate = {
                candidate_id: letter for letter, candidate_id in candidate_letters.items()
            }
            split_details = [
                {
                    "judge": judgement.get("judge"),
                    "first_choice": judgement["order"][0],
                    "first_choice_letter": letters_by_candidate.get(judgement["order"][0]),
                    "order": list(judgement["order"]),
                }
                for judgement in rep.judgements
                if judgement.get("parsed") and judgement.get("order")
            ]
            RedphoneInbox(store=record_store).post(
                channel="review",
                sender="arity",
                text=f"Judge review split on trial {rep.task.id}: {rep.judgements}",
                kind="alert",
                metadata={
                    "candidate_letters": candidate_letters,
                    "split_details": split_details,
                },
            )
            rep.notes.append("review split recorded in redphone review inbox")
    delivery = deliver(rep, out_dir=out_dir)
    if underfilled:
        delivery.receipt = f"arity {actual_arity}/{requested_arity} resolved · {delivery.receipt}"
    delivery.asked_human = asked
    return rep, delivery


def render_report(rep: RaceReport, printer: Callable[..., None] = print) -> None:
    p = printer
    bold, dim, green, red, yellow, cyan, reset = "\033[1m", "\033[2m", "\033[1;32m", "\033[1;31m", "\033[1;33m", "\033[1;36m", "\033[0m"

    arity = (
        f"{len(rep.candidates)}/{rep.requested_arity} candidates"
        if rep.requested_arity
        else f"{len(rep.candidates)} candidates"
    )
    cast = rep.casting or {}
    p(f"\n{cyan}{'=' * 100}{reset}")
    p(f"{bold}Arity trial{reset}  {rep.race_task.name if rep.race_task else 'ad-hoc'}"
      f"  |  {arity}  |  hidden tests: {len(rep.task.hidden_tests) or 'none'}"
      f"  |  {'ephemeral' if rep.ephemeral else 'scorecard: live'}"
      + (f"  |  cast: {cast['mode']} seed {cast['seed']}" if cast else ""))
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
              f"tests={a.get('test_count', '-')} changed={a.get('changed_files', '-')} "
              f"{r.duration_seconds:.0f}s {r.tokens_used:,} tok total ({r.phase_tokens:,} this phase)")
        if rep.conference_winner and rep.conference_winner.spec:
            p(f"  {green}final draft:{reset} {bold}{rep.conference_winner.spec.name}{reset}")
        p()

    if rep.winner and rep.winner.spec:
        e = rep.entry_for(rep.winner)
        if e and e.tied_with:
            p(f"{yellow}TIE{reset} between {len(e.tied_with) + 1} candidates (scores within judge epsilon); "
              f"broke on {e.tie_break}. Treat the winner as provisional.")
        p(f"{green}archivist order:{reset} {bold}{rep.winner.spec.name}{reset}  {dim}{rep.winner.signature}{reset}")
        if e:
            p(f"  artifacts: {', '.join(e.verified_artifacts) or 'none'}")
            p(f"  findings:  {e.entry_text.splitlines()[-1].replace('- **Findings**: ', '')}")
    else:
        p(f"{red}no winner:{reset} every candidate scored <= 0 (failed, lied, or produced nothing verifiable)")

    if rep.resolution:
        if rep.resolution.resolved and rep.resolved_candidate and rep.resolved_candidate.spec:
            p(f"{green}resolved:{reset} {bold}{rep.resolved_candidate.spec.name}{reset}  "
              f"{dim}{rep.resolution.kind.value}{reset}")
        else:
            p(f"{yellow}unresolved:{reset} delivery is withheld until the evidence has a decisive resolution")

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
