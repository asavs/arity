"""Frozen trial evidence, evaluator results, and explicit resolution decisions.

These contracts sit between candidate execution and winner delivery.  Candidate
harnesses may be expensive and stateful; evaluators should be able to inspect a
stable evidence bundle repeatedly without asking those harnesses to run again.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable


EVIDENCE_SCHEMA_VERSION = 2
EVALUATION_SCHEMA_VERSION = 1
RESOLUTION_SCHEMA_VERSION = 2


class UnsupportedEvidenceContractSchema(ValueError):
    """Base class for a future version of a nested evidence contract."""

    document_type = "evidence contract"

    def __init__(self, schema_version: int) -> None:
        super().__init__(
            f"unsupported {self.document_type} schema version {schema_version}"
        )
        self.schema_version = schema_version


class UnsupportedEvidenceSchema(UnsupportedEvidenceContractSchema):
    document_type = "evidence"


class UnsupportedEvaluationSchema(UnsupportedEvidenceContractSchema):
    document_type = "evaluation"


class UnsupportedResolutionSchema(UnsupportedEvidenceContractSchema):
    document_type = "resolution"


def _require_schema_version(
    value: Mapping[str, Any],
    expected: int,
    error_type: type[UnsupportedEvidenceContractSchema],
) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{error_type.document_type} must be a JSON object")
    schema_version = value.get("schema_version")
    if type(schema_version) is not int:
        raise TypeError(f"{error_type.document_type} schema_version must be an integer")
    if schema_version != expected:
        raise error_type(schema_version)


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _require_array(value: Any, label: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{label} must be a JSON array")
    return tuple(value)


def _require_string(value: Any, label: str, *, nullable: bool = False) -> Optional[str]:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value


def _require_integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    return value


def _require_number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    try:
        finite = math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not finite:
        raise ValueError(f"{label} must be finite")
    return value


def _require_strings(value: Any, label: str) -> tuple[str, ...]:
    items = _require_array(value, label)
    if any(not isinstance(item, str) for item in items):
        raise TypeError(f"{label} must contain only strings")
    return items  # type: ignore[return-value]


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
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("evidence text must contain valid Unicode") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ArtifactEvidence:
    """A workspace artifact captured without retaining an absolute host path."""

    path: str
    sha256: str
    size: int
    text: Optional[str] = None
    content_base64: Optional[str] = None

    def __post_init__(self) -> None:
        path = self.path
        parsed = PurePosixPath(path)
        if (
            not path
            or path != path.replace("\\", "/")
            or parsed.is_absolute()
            or parsed.as_posix() != path
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or (parsed.parts and ":" in parsed.parts[0])
        ):
            raise ValueError("artifact evidence paths must be safe relative POSIX paths")
        if (self.text is None) == (self.content_base64 is None):
            raise ValueError("artifact evidence must contain exactly one encoded representation")
        content = self.content_bytes()
        if len(content) != self.size or hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("artifact evidence content does not match its size and hash")

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
            content_base64=(
                base64.b64encode(content).decode("ascii") if text is None else None
            ),
        )

    def content_bytes(self) -> bytes:
        if self.text is not None:
            return self.text.encode("utf-8")
        try:
            return base64.b64decode(self.content_base64 or "", validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("artifact evidence contains invalid base64") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "text": self.text,
            "content_base64": self.content_base64,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactEvidence":
        value = _require_mapping(value, "artifact evidence")
        return cls(
            path=_require_string(value["path"], "artifact path") or "",
            sha256=_require_string(value["sha256"], "artifact sha256") or "",
            size=_require_integer(value["size"], "artifact size"),
            text=_require_string(value.get("text"), "artifact text", nullable=True),
            content_base64=_require_string(
                value.get("content_base64"), "artifact content_base64", nullable=True
            ),
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
    arm_id: str = ""
    arm_ordinal: int = 0
    context_adapter: Optional[str] = None

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
        arm_id: Optional[str] = None,
        arm_ordinal: int = 0,
        context_adapter: Optional[str] = None,
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
            arm_id=str(arm_id or candidate_id),
            arm_ordinal=int(arm_ordinal),
            context_adapter=None if context_adapter is None else str(context_adapter),
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
            "arm_id": self.arm_id,
            "arm_ordinal": self.arm_ordinal,
            "context_adapter": self.context_adapter,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateEvidence":
        value = _require_mapping(value, "candidate evidence")
        candidate_id = _require_string(value["candidate_id"], "candidate id") or ""
        artifacts = _require_array(value.get("artifacts", ()), "candidate artifacts")
        return cls.create(
            candidate_id=candidate_id,
            name=_require_string(value.get("name", candidate_id), "candidate name") or "",
            signature=_require_string(value.get("signature", ""), "candidate signature") or "",
            model=_require_string(value.get("model", ""), "candidate model") or "",
            provider=_require_string(value.get("provider", ""), "candidate provider") or "",
            role=_require_string(value.get("role", ""), "candidate role") or "",
            harness=_require_string(value.get("harness", ""), "candidate harness") or "",
            tool_runner=_require_string(
                value.get("tool_runner", ""), "candidate tool runner"
            ) or "",
            skills=_require_strings(value.get("skills", ()), "candidate skills"),
            context=_require_string(value.get("context", "accounts"), "candidate context") or "",
            status=_require_string(value.get("status", "completed"), "candidate status") or "",
            verdict=_require_string(value.get("verdict", ""), "candidate verdict") or "",
            rank=_require_integer(value.get("rank", 0), "candidate rank"),
            tied_with=_require_strings(value.get("tied_with", ()), "candidate ties"),
            tokens_used=_require_integer(value.get("tokens_used", 0), "candidate tokens"),
            duration_seconds=_require_number(
                value.get("duration_seconds", 0.0), "candidate duration"
            ),
            fallbacks=_require_integer(value.get("fallbacks", 0), "candidate fallbacks"),
            test_results=_require_mapping(
                value.get("test_results", {}), "candidate test results"
            ),
            axes=_require_mapping(value.get("axes", {}), "candidate axes"),
            artifacts=tuple(ArtifactEvidence.from_dict(item) for item in artifacts),
            output=_require_string(value.get("output"), "candidate output", nullable=True),
            arm_id=_require_string(value.get("arm_id"), "candidate arm id", nullable=True),
            arm_ordinal=_require_integer(value.get("arm_ordinal", 0), "candidate arm ordinal"),
            context_adapter=_require_string(
                value.get("context_adapter"), "candidate context adapter", nullable=True
            ),
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
        candidate_tuple = tuple(sorted(candidates, key=lambda candidate: candidate.arm_ordinal))
        if not candidate_tuple:
            raise ValueError("an evidence bundle requires at least one candidate")
        ids = [candidate.candidate_id for candidate in candidate_tuple]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate ids must be unique within an evidence bundle")
        arm_ids = [candidate.arm_id for candidate in candidate_tuple]
        ordinals = [candidate.arm_ordinal for candidate in candidate_tuple]
        if len(arm_ids) != len(set(arm_ids)) or len(ordinals) != len(set(ordinals)):
            raise ValueError("arm ids and ordinals must be unique within an evidence bundle")
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
        _require_schema_version(value, EVIDENCE_SCHEMA_VERSION, UnsupportedEvidenceSchema)
        candidates = _require_array(value.get("candidates", ()), "evidence candidates")
        hidden_test_hashes = _require_mapping(
            value.get("hidden_test_hashes", {}), "evidence hidden test hashes"
        )
        if any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in hidden_test_hashes.items()
        ):
            raise TypeError("evidence hidden test hashes must map strings to strings")
        bundle = cls.create(
            trial_id=_require_string(value["trial_id"], "evidence trial id") or "",
            task_id=_require_string(value["task_id"], "evidence task id") or "",
            task_name=_require_string(
                value.get("task_name"), "evidence task name", nullable=True
            ),
            brief=_require_string(value.get("brief", ""), "evidence brief") or "",
            candidates=tuple(CandidateEvidence.from_dict(item) for item in candidates),
            hidden_test_hashes=hidden_test_hashes,
            metadata=_require_mapping(value.get("metadata", {}), "evidence metadata"),
        )
        claimed_hash = _require_string(
            value.get("evidence_hash", ""), "evidence content hash"
        ) or ""
        if not claimed_hash:
            raise ValueError("evidence bundle is missing its content hash")
        if claimed_hash != bundle.evidence_hash:
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

    @classmethod
    def from_dict(cls, bundle: EvidenceBundle, value: Mapping[str, Any]) -> "Evaluation":
        _require_schema_version(value, EVALUATION_SCHEMA_VERSION, UnsupportedEvaluationSchema)
        ties = _require_array(value.get("ties", ()), "evaluation ties")
        validated_ties = tuple(
            _require_strings(group, "evaluation tie group") for group in ties
        )
        evaluation = cls.create(
            bundle,
            evaluator_id=_require_string(value["evaluator_id"], "evaluation evaluator id") or "",
            order=_require_strings(value.get("order", ()), "evaluation order"),
            ties=validated_ties,
            reason=_require_string(value.get("reason", ""), "evaluation reason") or "",
            metadata=_require_mapping(value.get("metadata", {}), "evaluation metadata"),
        )
        if _require_string(
            value.get("evidence_hash", ""), "evaluation evidence hash"
        ) != bundle.evidence_hash:
            raise ValueError("evaluation was produced for a different evidence bundle")
        claimed_id = _require_string(
            value.get("evaluation_id", ""), "evaluation content id"
        ) or ""
        if not claimed_id:
            raise ValueError("evaluation is missing its content id")
        if claimed_id != evaluation.evaluation_id:
            raise ValueError("evaluation id does not match evaluation contents")
        return evaluation


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
    expected_evaluator_ids: tuple[str, ...] = ()
    evaluator_ids: tuple[str, ...] = ()
    evaluation_ids: tuple[str, ...] = ()
    resolution_id: str = ""
    schema_version: int = RESOLUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.expected_evaluator_ids != tuple(sorted(set(self.expected_evaluator_ids))):
            raise ValueError("expected evaluator ids must be unique and canonical")
        if len(self.evaluator_ids) != len(self.evaluation_ids):
            raise ValueError("each resolution evaluator must have one evaluation id")
        pairs = tuple(zip(self.evaluator_ids, self.evaluation_ids))
        if pairs != tuple(sorted(pairs)):
            raise ValueError("resolution evaluation inputs must be canonical")
        expected_id = _content_hash(self._identity_body())
        if self.resolution_id and self.resolution_id != expected_id:
            raise ValueError("resolution id does not match resolution contents")
        object.__setattr__(self, "resolution_id", expected_id)

    def _identity_body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.kind.value,
            "candidate_id": self.candidate_id,
            "evidence_hash": self.evidence_hash,
            "reason": self.reason,
            "eligible_candidate_ids": list(self.eligible_candidate_ids),
            "expected_evaluator_ids": list(self.expected_evaluator_ids),
            "evaluator_ids": list(self.evaluator_ids),
            "evaluation_ids": list(self.evaluation_ids),
        }

    @property
    def resolved(self) -> bool:
        return self.kind is not ResolutionKind.UNRESOLVED and self.candidate_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": "resolved" if self.resolved else "unresolved",
            "resolution_id": self.resolution_id,
            "source": self.kind.value,
            "candidate_id": self.candidate_id,
            "evidence_hash": self.evidence_hash,
            "reason": self.reason,
            "eligible_candidate_ids": list(self.eligible_candidate_ids),
            "expected_evaluator_ids": list(self.expected_evaluator_ids),
            "evaluator_ids": list(self.evaluator_ids),
            "evaluation_ids": list(self.evaluation_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Resolution":
        _require_schema_version(value, RESOLUTION_SCHEMA_VERSION, UnsupportedResolutionSchema)
        resolution = cls(
            kind=ResolutionKind(
                _require_string(value.get("source", "unresolved"), "resolution source")
                or "unresolved"
            ),
            candidate_id=_require_string(
                value.get("candidate_id"), "resolution candidate id", nullable=True
            ),
            evidence_hash=_require_string(
                value["evidence_hash"], "resolution evidence hash"
            ) or "",
            reason=_require_string(value.get("reason", ""), "resolution reason") or "",
            eligible_candidate_ids=_require_strings(
                value.get("eligible_candidate_ids", ()), "resolution eligible candidates"
            ),
            expected_evaluator_ids=_require_strings(
                value.get("expected_evaluator_ids", ()), "resolution expected evaluators"
            ),
            evaluator_ids=_require_strings(
                value.get("evaluator_ids", ()), "resolution evaluators"
            ),
            evaluation_ids=_require_strings(
                value.get("evaluation_ids", ()), "resolution evaluations"
            ),
            resolution_id=_require_string(
                value.get("resolution_id", ""), "resolution content id"
            ) or "",
        )
        if not value.get("resolution_id"):
            raise ValueError("resolution is missing its content id")
        claimed_status = _require_string(
            value.get("status", ""), "resolution status"
        ) or ""
        if claimed_status and claimed_status != ("resolved" if resolution.resolved else "unresolved"):
            raise ValueError("resolution status contradicts its source and candidate")
        return resolution

    def validate(
        self,
        bundle: EvidenceBundle,
        evaluations: Sequence[Evaluation] = (),
    ) -> None:
        if self.evidence_hash != bundle.evidence_hash:
            raise ValueError("resolution references a different evidence bundle")
        candidate_ids = {candidate.candidate_id for candidate in bundle.candidates}
        if self.kind is ResolutionKind.UNRESOLVED:
            if self.candidate_id is not None:
                raise ValueError("an unresolved resolution cannot select a candidate")
        elif self.candidate_id is None:
            raise ValueError("a resolved resolution must select a candidate")
        eligible = set(self.eligible_candidate_ids)
        if not eligible or len(eligible) != len(self.eligible_candidate_ids):
            raise ValueError("resolution eligibility must contain distinct candidates")
        if not eligible <= candidate_ids:
            raise ValueError("resolution eligibility references an unknown candidate")
        if self.candidate_id is not None and self.candidate_id not in eligible:
            raise ValueError("resolved candidate is not factually eligible")
        factual_eligible, facts_supported = factual_eligibility(bundle)
        if self.eligible_candidate_ids != factual_eligible:
            raise ValueError("resolution eligibility does not match the frozen factual evidence")
        if self.resolved and not facts_supported:
            raise ValueError("a resolution cannot select a candidate without positive factual evidence")
        for evaluation in evaluations:
            evaluation.validate(bundle)
        known_evaluations = {evaluation.evaluation_id: evaluation for evaluation in evaluations}
        if not set(self.evaluation_ids) <= set(known_evaluations):
            raise ValueError("resolution references an unknown evaluation")
        referenced = tuple(known_evaluations[evaluation_id] for evaluation_id in self.evaluation_ids)
        if tuple(evaluation.evaluator_id for evaluation in referenced) != self.evaluator_ids:
            raise ValueError("resolution evaluator identities do not match its evaluations")
        if self.kind is ResolutionKind.JUDGE_CONSENSUS:
            if (
                not referenced
                or len(set(self.evaluator_ids)) != len(self.evaluator_ids)
                or set(self.evaluator_ids) != set(self.expected_evaluator_ids)
            ):
                raise ValueError("judge consensus requires a complete distinct evaluator panel")
            for evaluation in referenced:
                if not evaluation.order or evaluation.order[0] != self.candidate_id:
                    raise ValueError("judge consensus does not match the recorded rankings")
                if any(
                    self.candidate_id in group and len(set(group) & eligible) > 1
                    for group in evaluation.ties
                ):
                    raise ValueError("judge consensus cannot rely on an explicitly tied first choice")
        elif self.kind is ResolutionKind.FACTS_WINNER and eligible != {self.candidate_id}:
            raise ValueError("a facts winner must be the sole factually eligible candidate")


def factual_eligibility(bundle: EvidenceBundle) -> tuple[tuple[str, ...], bool]:
    """Derive the eligible factual tier from the immutable archivist axes."""
    def fact_key(candidate: CandidateEvidence) -> tuple[int, float, float]:
        return (
            int(candidate.axes.get("tier", 0)),
            float(candidate.axes.get("hidden_rate", 0.0)),
            float(candidate.axes.get("own_rate", 0.0)),
        )

    def has_support(candidate: CandidateEvidence) -> bool:
        test_results = candidate.test_results
        has_tests = bool(test_results.get("has_tests")) or any(
            bool(layer.get("has_tests"))
            for layer in (test_results.get("own") or {}, test_results.get("hidden") or {})
            if hasattr(layer, "get")
        )
        return bool(candidate.artifacts or has_tests)

    best_key = max(fact_key(candidate) for candidate in bundle.candidates)
    best = tuple(candidate for candidate in bundle.candidates if fact_key(candidate) == best_key)
    supported = tuple(candidate for candidate in best if best_key[0] > 0 and has_support(candidate))
    eligible = supported or best
    return tuple(candidate.candidate_id for candidate in eligible), bool(supported)


def _canonical_panel(
    evaluations: Sequence[Evaluation],
    expected_evaluator_ids: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    pairs = tuple(sorted((evaluation.evaluator_id, evaluation.evaluation_id) for evaluation in evaluations))
    raw_expected = tuple(str(item) for item in expected_evaluator_ids)
    if len(raw_expected) != len(set(raw_expected)):
        raise ValueError("expected evaluator ids must be unique")
    expected = tuple(sorted(raw_expected))
    if not expected:
        expected = tuple(sorted({evaluator_id for evaluator_id, _ in pairs}))
    return expected, tuple(item[0] for item in pairs), tuple(item[1] for item in pairs)


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
    panel_expected, panel_evaluators, panel_evaluations = _canonical_panel(
        checked_evaluations, expected_evaluator_ids,
    )

    tied = (
        {facts_candidate_id, *(str(candidate_id) for candidate_id in facts_tied_with)}
        if facts_candidate_id is not None
        else set(candidate_ids)
    )
    if not tied <= candidate_ids:
        raise ValueError("facts tie references a candidate outside the evidence bundle")
    eligible = tuple(candidate.candidate_id for candidate in bundle.candidates if candidate.candidate_id in tied)
    frozen_eligible, frozen_supported = factual_eligibility(bundle)
    if eligible != frozen_eligible:
        raise ValueError("reported factual eligibility does not match the frozen evidence")
    if (facts_candidate_id is not None and facts_supported) != frozen_supported:
        raise ValueError("reported factual support does not match the frozen evidence")
    if human_candidate_id is not None:
        if human_candidate_id not in tied:
            raise ValueError("human-picked candidate is not eligible under the factual evidence")
        if not frozen_supported:
            return Resolution(
                kind=ResolutionKind.UNRESOLVED,
                candidate_id=None,
                evidence_hash=bundle.evidence_hash,
                reason="human preference was recorded but no candidate had positive factual evidence",
                eligible_candidate_ids=eligible,
                expected_evaluator_ids=panel_expected,
                evaluator_ids=panel_evaluators,
                evaluation_ids=panel_evaluations,
            )
        return Resolution(
            kind=ResolutionKind.HUMAN_PICK,
            candidate_id=human_candidate_id,
            evidence_hash=bundle.evidence_hash,
            reason="human selected a candidate after reviewing the recorded evidence",
            eligible_candidate_ids=eligible,
            expected_evaluator_ids=panel_expected,
            evaluator_ids=panel_evaluators,
            evaluation_ids=panel_evaluations,
        )

    if facts_candidate_id is None or not facts_supported:
        return Resolution(
            kind=ResolutionKind.UNRESOLVED,
            candidate_id=None,
            evidence_hash=bundle.evidence_hash,
            reason="no candidate had sufficient factual evidence for resolution",
            eligible_candidate_ids=eligible,
            expected_evaluator_ids=panel_expected,
            evaluator_ids=panel_evaluators,
            evaluation_ids=panel_evaluations,
        )

    if len(tied) == 1:
        return Resolution(
            kind=ResolutionKind.FACTS_WINNER,
            candidate_id=facts_candidate_id,
            evidence_hash=bundle.evidence_hash,
            reason="verified facts uniquely ranked this candidate first",
            eligible_candidate_ids=eligible,
        )

    expected = panel_expected
    actual = panel_evaluators
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
            expected_evaluator_ids=panel_expected,
            evaluator_ids=panel_evaluators,
            evaluation_ids=panel_evaluations,
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
        expected_evaluator_ids=panel_expected,
        evaluator_ids=panel_evaluators,
        evaluation_ids=panel_evaluations,
    )
