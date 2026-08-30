"""Frozen trial evidence, evaluator results, and explicit resolution decisions.

These contracts sit between candidate execution and winner delivery.  Candidate
harnesses may be expensive and stateful; evaluators should be able to inspect a
stable evidence bundle repeatedly without asking those harnesses to run again.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable


EVIDENCE_SCHEMA_VERSION = 1
EVALUATION_SCHEMA_VERSION = 1
RESOLUTION_SCHEMA_VERSION = 1


def _freeze_json(value: Any) -> Any:
    """Copy JSON-like data into recursively immutable containers."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"evidence values must be JSON-compatible, got {type(value).__name__}")


def _thaw_json(value: Any) -> Any:
    """Return independent JSON-compatible containers from frozen data."""
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactEvidence:
    """A workspace artifact captured without retaining an absolute host path."""

    path: str
    sha256: str
    size: int
    text: Optional[str] = None

    @classmethod
    def from_bytes(cls, path: str, content: bytes) -> "ArtifactEvidence":
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return cls(
            path=path.replace("\\", "/"),
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            text=text,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactEvidence":
        return cls(
            path=str(value["path"]),
            sha256=str(value["sha256"]),
            size=int(value["size"]),
            text=None if value.get("text") is None else str(value["text"]),
        )


@dataclass(frozen=True)
class CandidateEvidence:
    """One candidate's resolved stack, factual audit, and captured outputs."""

    candidate_id: str
    name: str
    signature: str
    model: str
    provider: str
    role: str
    harness: str
    tool_runner: str
    skills: tuple[str, ...]
    context: str
    status: str
    verdict: str
    rank: int
    tied_with: tuple[str, ...]
    tokens_used: int
    duration_seconds: float
    fallbacks: int
    test_results: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    axes: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    artifacts: tuple[ArtifactEvidence, ...] = ()
    output: Optional[str] = None

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        name: str,
        signature: str,
        model: str,
        provider: str,
        role: str,
        harness: str,
        tool_runner: str,
        skills: Sequence[str] = (),
        context: str = "accounts",
        status: str = "completed",
        verdict: str = "",
        rank: int = 0,
        tied_with: Sequence[str] = (),
        tokens_used: int = 0,
        duration_seconds: float = 0.0,
        fallbacks: int = 0,
        test_results: Optional[Mapping[str, Any]] = None,
        axes: Optional[Mapping[str, Any]] = None,
        artifacts: Sequence[ArtifactEvidence] = (),
        output: Optional[str] = None,
    ) -> "CandidateEvidence":
        return cls(
            candidate_id=str(candidate_id),
            name=str(name),
            signature=str(signature),
            model=str(model),
            provider=str(provider),
            role=str(role),
            harness=str(harness),
            tool_runner=str(tool_runner),
            skills=tuple(str(skill) for skill in skills),
            context=str(context),
            status=str(status),
            verdict=str(verdict),
            rank=int(rank),
            tied_with=tuple(str(item) for item in tied_with),
            tokens_used=int(tokens_used),
            duration_seconds=float(duration_seconds),
            fallbacks=int(fallbacks),
            test_results=_freeze_json(test_results or {}),
            axes=_freeze_json(axes or {}),
            artifacts=tuple(artifacts),
            output=None if output is None else str(output),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "signature": self.signature,
            "model": self.model,
            "provider": self.provider,
            "role": self.role,
            "harness": self.harness,
            "tool_runner": self.tool_runner,
            "skills": list(self.skills),
            "context": self.context,
            "status": self.status,
            "verdict": self.verdict,
            "rank": self.rank,
            "tied_with": list(self.tied_with),
            "tokens_used": self.tokens_used,
            "duration_seconds": self.duration_seconds,
            "fallbacks": self.fallbacks,
            "test_results": _thaw_json(self.test_results),
            "axes": _thaw_json(self.axes),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "output": self.output,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateEvidence":
        return cls.create(
            candidate_id=str(value["candidate_id"]),
            name=str(value.get("name", value["candidate_id"])),
            signature=str(value.get("signature", "")),
            model=str(value.get("model", "")),
            provider=str(value.get("provider", "")),
            role=str(value.get("role", "")),
            harness=str(value.get("harness", "")),
            tool_runner=str(value.get("tool_runner", "")),
            skills=value.get("skills") or (),
            context=str(value.get("context", "accounts")),
            status=str(value.get("status", "completed")),
            verdict=str(value.get("verdict", "")),
            rank=int(value.get("rank", 0)),
            tied_with=value.get("tied_with") or (),
            tokens_used=int(value.get("tokens_used", 0)),
            duration_seconds=float(value.get("duration_seconds", 0.0)),
            fallbacks=int(value.get("fallbacks", 0)),
            test_results=value.get("test_results") or {},
            axes=value.get("axes") or {},
            artifacts=tuple(ArtifactEvidence.from_dict(item) for item in value.get("artifacts") or ()),
            output=value.get("output"),
        )


@dataclass(frozen=True)
class EvidenceBundle:
    """Content-addressed evidence that can be evaluated without live workspaces."""

    trial_id: str
    task_id: str
    task_name: Optional[str]
    brief: str
    candidates: tuple[CandidateEvidence, ...]
    hidden_test_hashes: Mapping[str, str]
    metadata: Mapping[str, Any]
    evidence_hash: str
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        trial_id: str,
        task_id: str,
        task_name: Optional[str],
        brief: str,
        candidates: Sequence[CandidateEvidence],
        hidden_test_hashes: Optional[Mapping[str, str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "EvidenceBundle":
        frozen_hashes = _freeze_json(hidden_test_hashes or {})
        frozen_metadata = _freeze_json(metadata or {})
        candidate_tuple = tuple(candidates)
        if not candidate_tuple:
            raise ValueError("an evidence bundle requires at least one candidate")
        ids = [candidate.candidate_id for candidate in candidate_tuple]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate ids must be unique within an evidence bundle")
        body = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "trial_id": str(trial_id),
            "task_id": str(task_id),
            "task_name": None if task_name is None else str(task_name),
            "brief": str(brief),
            "hidden_test_hashes": _thaw_json(frozen_hashes),
            "metadata": _thaw_json(frozen_metadata),
            "candidates": [candidate.to_dict() for candidate in candidate_tuple],
        }
        return cls(
            trial_id=str(trial_id),
            task_id=str(task_id),
            task_name=None if task_name is None else str(task_name),
            brief=str(brief),
            candidates=candidate_tuple,
            hidden_test_hashes=frozen_hashes,
            metadata=frozen_metadata,
            evidence_hash=_content_hash(body),
        )

    def candidate(self, candidate_id: str) -> CandidateEvidence:
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise KeyError(candidate_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "brief": self.brief,
            "hidden_test_hashes": _thaw_json(self.hidden_test_hashes),
            "metadata": _thaw_json(self.metadata),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "evidence_hash": self.evidence_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceBundle":
        schema_version = int(value.get("schema_version", 0))
        if schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError(f"unsupported evidence schema version {schema_version}")
        bundle = cls.create(
            trial_id=str(value["trial_id"]),
            task_id=str(value["task_id"]),
            task_name=value.get("task_name"),
            brief=str(value.get("brief", "")),
            candidates=tuple(CandidateEvidence.from_dict(item) for item in value.get("candidates") or ()),
            hidden_test_hashes=value.get("hidden_test_hashes") or {},
            metadata=value.get("metadata") or {},
        )
        claimed_hash = str(value.get("evidence_hash", ""))
        if claimed_hash and claimed_hash != bundle.evidence_hash:
            raise ValueError("evidence hash does not match bundle contents")
        return bundle


@dataclass(frozen=True)
class Evaluation:
    """A validated evaluator ranking bound to one immutable evidence bundle."""

    evaluation_id: str
    evaluator_id: str
    evidence_hash: str
    order: tuple[str, ...]
    ties: tuple[tuple[str, ...], ...]
    reason: str
    metadata: Mapping[str, Any]
    schema_version: int = EVALUATION_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        bundle: EvidenceBundle,
        *,
        evaluator_id: str,
        order: Sequence[str],
        ties: Sequence[Sequence[str]] = (),
        reason: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "Evaluation":
        expected = {candidate.candidate_id for candidate in bundle.candidates}
        ordered = tuple(str(candidate_id) for candidate_id in order)
        if len(ordered) != len(expected) or len(set(ordered)) != len(ordered) or set(ordered) != expected:
            raise ValueError("evaluation order must contain every candidate exactly once")
        frozen_ties = tuple(tuple(str(candidate_id) for candidate_id in group) for group in ties)
        for group in frozen_ties:
            if len(group) < 2 or len(set(group)) != len(group) or not set(group) <= expected:
                raise ValueError("evaluation ties must contain distinct known candidates")
        frozen_metadata = _freeze_json(metadata or {})
        body = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "evaluator_id": str(evaluator_id),
            "evidence_hash": bundle.evidence_hash,
            "order": list(ordered),
            "ties": [list(group) for group in frozen_ties],
            "reason": str(reason),
            "metadata": _thaw_json(frozen_metadata),
        }
        return cls(
            evaluation_id=_content_hash(body),
            evaluator_id=str(evaluator_id),
            evidence_hash=bundle.evidence_hash,
            order=ordered,
            ties=frozen_ties,
            reason=str(reason),
            metadata=frozen_metadata,
        )

    def validate(self, bundle: EvidenceBundle) -> None:
        if self.evidence_hash != bundle.evidence_hash:
            raise ValueError("evaluation was produced for a different evidence bundle")
        expected = {candidate.candidate_id for candidate in bundle.candidates}
        if len(self.order) != len(expected) or len(set(self.order)) != len(self.order) or set(self.order) != expected:
            raise ValueError("evaluation order is not an exact candidate permutation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluation_id": self.evaluation_id,
            "evaluator_id": self.evaluator_id,
            "evidence_hash": self.evidence_hash,
            "order": list(self.order),
            "ties": [list(group) for group in self.ties],
            "reason": self.reason,
            "metadata": _thaw_json(self.metadata),
        }


@runtime_checkable
class TrialEvaluator(Protocol):
    """Evaluate a frozen bundle without invoking its candidate harnesses."""

    evaluator_id: str

    def evaluate(self, bundle: EvidenceBundle) -> Evaluation:
        ...


def evaluate_bundle(bundle: EvidenceBundle, evaluator: TrialEvaluator) -> Evaluation:
    evaluation = evaluator.evaluate(bundle)
    if not isinstance(evaluation, Evaluation):
        raise TypeError("TrialEvaluator.evaluate() must return Evaluation")
    evaluation.validate(bundle)
    if evaluation.evaluator_id != str(evaluator.evaluator_id):
        raise ValueError("evaluation identity does not match the evaluator")
    return evaluation


class ResolutionKind(str, Enum):
    FACTS_WINNER = "facts_winner"
    JUDGE_CONSENSUS = "judge_consensus"
    HUMAN_PICK = "human_pick"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Resolution:
    """The explicit, attributable decision controlling delivery."""

    kind: ResolutionKind
    candidate_id: Optional[str]
    evidence_hash: str
    reason: str
    eligible_candidate_ids: tuple[str, ...] = ()
    evaluator_ids: tuple[str, ...] = ()
    evaluation_ids: tuple[str, ...] = ()
    schema_version: int = RESOLUTION_SCHEMA_VERSION

    @property
    def resolved(self) -> bool:
        return self.kind is not ResolutionKind.UNRESOLVED and self.candidate_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": "resolved" if self.resolved else "unresolved",
            "source": self.kind.value,
            "candidate_id": self.candidate_id,
            "evidence_hash": self.evidence_hash,
            "reason": self.reason,
            "eligible_candidate_ids": list(self.eligible_candidate_ids),
            "evaluator_ids": list(self.evaluator_ids),
            "evaluation_ids": list(self.evaluation_ids),
        }


def resolve_bundle(
    bundle: EvidenceBundle,
    *,
    facts_candidate_id: Optional[str],
    facts_tied_with: Sequence[str] = (),
    facts_supported: bool = True,
    evaluations: Sequence[Evaluation] = (),
    expected_evaluator_ids: Sequence[str] = (),
    human_candidate_id: Optional[str] = None,
) -> Resolution:
    """Resolve facts first, then evaluator consensus, then an explicit human pick."""
    candidate_ids = {candidate.candidate_id for candidate in bundle.candidates}
    if facts_candidate_id is not None and facts_candidate_id not in candidate_ids:
        raise ValueError("facts candidate is not present in the evidence bundle")
    checked_evaluations = tuple(evaluations)
    for evaluation in checked_evaluations:
        evaluation.validate(bundle)

    tied = (
        {facts_candidate_id, *(str(candidate_id) for candidate_id in facts_tied_with)}
        if facts_candidate_id is not None
        else set(candidate_ids)
    )
    if not tied <= candidate_ids:
        raise ValueError("facts tie references a candidate outside the evidence bundle")
    eligible = tuple(candidate.candidate_id for candidate in bundle.candidates if candidate.candidate_id in tied)
    if human_candidate_id is not None:
        if human_candidate_id not in tied:
            raise ValueError("human-picked candidate is not eligible under the factual evidence")
        return Resolution(
            kind=ResolutionKind.HUMAN_PICK,
            candidate_id=human_candidate_id,
            evidence_hash=bundle.evidence_hash,
            reason="human selected a candidate after reviewing the recorded evidence",
            eligible_candidate_ids=eligible,
        )

    if facts_candidate_id is None or not facts_supported:
        return Resolution(
            kind=ResolutionKind.UNRESOLVED,
            candidate_id=None,
            evidence_hash=bundle.evidence_hash,
            reason="no candidate had sufficient factual evidence for resolution",
            eligible_candidate_ids=eligible,
            evaluator_ids=tuple(evaluation.evaluator_id for evaluation in checked_evaluations),
            evaluation_ids=tuple(evaluation.evaluation_id for evaluation in checked_evaluations),
        )

    if len(tied) == 1:
        return Resolution(
            kind=ResolutionKind.FACTS_WINNER,
            candidate_id=facts_candidate_id,
            evidence_hash=bundle.evidence_hash,
            reason="verified facts uniquely ranked this candidate first",
            eligible_candidate_ids=eligible,
        )

    expected = tuple(str(evaluator_id) for evaluator_id in expected_evaluator_ids)
    actual = tuple(evaluation.evaluator_id for evaluation in checked_evaluations)
    complete_panel = (
        len(actual) == len(set(actual))
        and (not expected or len(expected) == len(set(expected)) and set(actual) == set(expected))
    )
    first_choices = [evaluation.order[0] for evaluation in checked_evaluations if evaluation.order]
    unique_top = all(
        not any(
            evaluation.order[0] in group and len(set(group) & tied) > 1
            for group in evaluation.ties
        )
        for evaluation in checked_evaluations
        if evaluation.order
    )
    eligible_tops = all(candidate_id in tied for candidate_id in first_choices)
    consensus = (
        first_choices[0]
        if complete_panel and unique_top and eligible_tops and first_choices and len(set(first_choices)) == 1
        else None
    )
    if consensus is not None and consensus in tied:
        return Resolution(
            kind=ResolutionKind.JUDGE_CONSENSUS,
            candidate_id=consensus,
            evidence_hash=bundle.evidence_hash,
            reason="all recorded evaluators selected the same candidate within the factual tie",
            eligible_candidate_ids=eligible,
            evaluator_ids=tuple(evaluation.evaluator_id for evaluation in checked_evaluations),
            evaluation_ids=tuple(evaluation.evaluation_id for evaluation in checked_evaluations),
        )

    return Resolution(
        kind=ResolutionKind.UNRESOLVED,
        candidate_id=None,
        evidence_hash=bundle.evidence_hash,
        reason=(
            "the expected evaluator panel was incomplete"
            if expected and not complete_panel
            else "an evaluator did not select a unique candidate within the factual tie"
            if not unique_top
            else "an evaluator selected a candidate outside the factual tie"
            if not eligible_tops
            else "evaluators disagreed within the factual tie"
            if first_choices
            else "verified facts tied and no valid evaluator decision was recorded"
        ),
        eligible_candidate_ids=eligible,
        evaluator_ids=tuple(evaluation.evaluator_id for evaluation in checked_evaluations),
        evaluation_ids=tuple(evaluation.evaluation_id for evaluation in checked_evaluations),
    )
