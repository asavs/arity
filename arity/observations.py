"""Strict, attributed observation envelopes for durable trial analytics.

Observations retain independent mechanical, model, and human viewpoints without
turning any of them into a resolution or delivery authority.  They contain only
opaque identifiers and content-addressed references; prompts, outputs, provider
identities, seats, and free-form rationales belong elsewhere.
"""
from __future__ import annotations

import math
import re
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, TypeAlias


OBSERVATION_SCHEMA_VERSION = 1
OBSERVER_KINDS = frozenset({"mechanical", "model", "human"})
OBSERVATION_PHASES = frozenset({"trial", "conference", "review", "resolution"})
OBSERVATION_STATUSES = frozenset({"recorded", "failed", "unavailable", "declined"})
SUBJECT_KINDS = frozenset({"trial", "arm", "evidence", "review", "resolution"})
REVIEW_ATTEMPT_STATUSES = frozenset({"completed", "failed", "invalid", "missing"})
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class UnsupportedObservationSchema(ValueError):
    """An observation uses a newer schema than this reader understands."""

    document_type = "observation"

    def __init__(self, schema_version: int) -> None:
        super().__init__(f"unsupported observation schema version {schema_version}")
        self.schema_version = schema_version


def _exact_keys(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} fields differ: expected {sorted(expected)!r}, got {sorted(actual)!r}"
        )


def _opaque_id(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    if not _OPAQUE_ID.fullmatch(value):
        raise ValueError(f"{label} must be a nonblank opaque identifier")
    return value


def _evidence_hash(value: object) -> str:
    if type(value) is not str:
        raise TypeError("evidence hash must be a string")
    if not _SHA256.fullmatch(value):
        raise ValueError("evidence hash must be a lowercase SHA-256 digest")
    return value


def _finite_time(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("observation time must be a number")
    resolved = float(value)
    if not math.isfinite(resolved):
        raise ValueError("observation time must be finite")
    return resolved


def _content_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ObservationSubject:
    """An opaque, bounded subject identifier; never copied trial content."""

    kind: str
    subject_id: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in SUBJECT_KINDS:
            raise ValueError("observation subject has an unsupported kind")
        object.__setattr__(self, "subject_id", _opaque_id(self.subject_id, label="subject id"))

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "subject_id": self.subject_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservationSubject":
        if not isinstance(value, Mapping):
            raise TypeError("observation subject must be a JSON object")
        _exact_keys(value, {"kind", "subject_id"}, label="observation subject")
        return cls(kind=value["kind"], subject_id=value["subject_id"])


@dataclass(frozen=True)
class MechanicalEvidenceReference:
    """Reference to immutable evidence, optionally narrowed to an arm."""

    evidence_hash: str
    arm_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_hash", _evidence_hash(self.evidence_hash))
        if self.arm_id is not None:
            object.__setattr__(self, "arm_id", _opaque_id(self.arm_id, label="arm id"))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "mechanical_evidence", "evidence_hash": self.evidence_hash, "arm_id": self.arm_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MechanicalEvidenceReference":
        if not isinstance(value, Mapping):
            raise TypeError("mechanical reference must be a JSON object")
        _exact_keys(value, {"kind", "evidence_hash", "arm_id"}, label="mechanical reference")
        if value["kind"] != "mechanical_evidence":
            raise ValueError("mechanical reference kind must be mechanical_evidence")
        return cls(evidence_hash=value["evidence_hash"], arm_id=value["arm_id"])


@dataclass(frozen=True)
class ModelReviewReference:
    """Reference to one review attempt, including a failed or missing attempt."""

    evidence_hash: str
    review_id: str
    attempt_status: str
    evaluation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_hash", _evidence_hash(self.evidence_hash))
        object.__setattr__(self, "review_id", _opaque_id(self.review_id, label="review id"))
        if type(self.attempt_status) is not str or self.attempt_status not in REVIEW_ATTEMPT_STATUSES:
            raise ValueError("model review reference has an unsupported attempt status")
        if self.evaluation_id is not None:
            object.__setattr__(
                self, "evaluation_id", _opaque_id(self.evaluation_id, label="evaluation id")
            )
        if self.attempt_status == "completed" and self.evaluation_id is None:
            raise ValueError("completed model review requires an evaluation id")
        if self.attempt_status != "completed" and self.evaluation_id is not None:
            raise ValueError("failed or missing model review cannot claim an evaluation id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "model_review",
            "evidence_hash": self.evidence_hash,
            "review_id": self.review_id,
            "attempt_status": self.attempt_status,
            "evaluation_id": self.evaluation_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelReviewReference":
        if not isinstance(value, Mapping):
            raise TypeError("model review reference must be a JSON object")
        _exact_keys(
            value,
            {"kind", "evidence_hash", "review_id", "attempt_status", "evaluation_id"},
            label="model review reference",
        )
        if value["kind"] != "model_review":
            raise ValueError("model review reference kind must be model_review")
        return cls(
            evidence_hash=value["evidence_hash"],
            review_id=value["review_id"],
            attempt_status=value["attempt_status"],
            evaluation_id=value["evaluation_id"],
        )


@dataclass(frozen=True)
class HumanDecisionReference:
    """Reference to a human choice, including an explicit declined choice."""

    evidence_hash: str
    decision: str
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_hash", _evidence_hash(self.evidence_hash))
        if type(self.decision) is not str or self.decision not in {"selected", "declined"}:
            raise ValueError("human decision reference must select or decline")
        if self.candidate_id is not None:
            object.__setattr__(
                self, "candidate_id", _opaque_id(self.candidate_id, label="candidate id")
            )
        if self.decision == "selected" and self.candidate_id is None:
            raise ValueError("selected human decision requires a candidate id")
        if self.decision == "declined" and self.candidate_id is not None:
            raise ValueError("declined human decision cannot claim a candidate id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "human_decision",
            "evidence_hash": self.evidence_hash,
            "decision": self.decision,
            "candidate_id": self.candidate_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HumanDecisionReference":
        if not isinstance(value, Mapping):
            raise TypeError("human decision reference must be a JSON object")
        _exact_keys(
            value,
            {"kind", "evidence_hash", "decision", "candidate_id"},
            label="human decision reference",
        )
        if value["kind"] != "human_decision":
            raise ValueError("human decision reference kind must be human_decision")
        return cls(
            evidence_hash=value["evidence_hash"],
            decision=value["decision"],
            candidate_id=value["candidate_id"],
        )


ObservationReference: TypeAlias = (
    MechanicalEvidenceReference | ModelReviewReference | HumanDecisionReference
)


def _reference_from_dict(value: Mapping[str, Any]) -> ObservationReference:
    if not isinstance(value, Mapping):
        raise TypeError("observation reference must be a JSON object")
    kind = value.get("kind")
    if kind == "mechanical_evidence":
        return MechanicalEvidenceReference.from_dict(value)
    if kind == "model_review":
        return ModelReviewReference.from_dict(value)
    if kind == "human_decision":
        return HumanDecisionReference.from_dict(value)
    raise ValueError("observation reference has an unsupported kind")


@dataclass(frozen=True)
class Observation:
    """One durable viewpoint about immutable trial material.

    This is an attribution envelope only.  It has no resolution, winner, or
    delivery fields, and callers must keep those actions in their dedicated
    evidence contracts.
    """

    observer_kind: str
    observer_id: str
    observer_version: str
    observed_at: float
    phase: str
    status: str
    subject: ObservationSubject
    reference: ObservationReference
    observation_id: str = ""
    schema_version: int = OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise TypeError("observation schema_version must be an integer")
        if self.schema_version != OBSERVATION_SCHEMA_VERSION:
            raise UnsupportedObservationSchema(self.schema_version)
        if type(self.observer_kind) is not str or self.observer_kind not in OBSERVER_KINDS:
            raise ValueError("observation has an unsupported observer kind")
        object.__setattr__(self, "observer_id", _opaque_id(self.observer_id, label="observer id"))
        object.__setattr__(
            self, "observer_version", _opaque_id(self.observer_version, label="observer version")
        )
        object.__setattr__(self, "observed_at", _finite_time(self.observed_at))
        if type(self.phase) is not str or self.phase not in OBSERVATION_PHASES:
            raise ValueError("observation has an unsupported phase")
        if type(self.status) is not str or self.status not in OBSERVATION_STATUSES:
            raise ValueError("observation has an unsupported status")
        if type(self.subject) is not ObservationSubject:
            raise TypeError("observation subject must be an exact ObservationSubject")
        if type(self.reference) not in {
            MechanicalEvidenceReference,
            ModelReviewReference,
            HumanDecisionReference,
        }:
            raise TypeError("observation reference must be an exact supported reference")
        self._validate_attribution()
        expected_id = _content_hash(self._body())
        if self.observation_id:
            if type(self.observation_id) is not str or not _SHA256.fullmatch(
                self.observation_id
            ):
                raise ValueError("observation id must be a lowercase SHA-256 digest")
            if self.observation_id != expected_id:
                raise ValueError("observation id does not match observation contents")
        object.__setattr__(self, "observation_id", expected_id)

    def _validate_attribution(self) -> None:
        if self.observer_kind == "mechanical":
            if type(self.reference) is not MechanicalEvidenceReference:
                raise ValueError("mechanical observation requires mechanical evidence reference")
            if self.status not in {"recorded", "unavailable"}:
                raise ValueError("mechanical observation status must be recorded or unavailable")
        elif self.observer_kind == "model":
            if type(self.reference) is not ModelReviewReference:
                raise ValueError("model observation requires model review reference")
            expected = {
                "completed": "recorded",
                "failed": "failed",
                "invalid": "failed",
                "missing": "unavailable",
            }[self.reference.attempt_status]
            if self.status != expected:
                raise ValueError("model observation status must match review attempt status")
        else:
            if type(self.reference) is not HumanDecisionReference:
                raise ValueError("human observation requires human decision reference")
            expected = "recorded" if self.reference.decision == "selected" else "declined"
            if self.status != expected:
                raise ValueError("human observation status must match human decision")

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "observer_kind": self.observer_kind,
            "observer_id": self.observer_id,
            "observer_version": self.observer_version,
            "observed_at": self.observed_at,
            "phase": self.phase,
            "status": self.status,
            "subject": self.subject.to_dict(),
            "reference": self.reference.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._body(),
            "observation_id": self.observation_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Observation":
        if not isinstance(value, Mapping):
            raise TypeError("observation must be a JSON object")
        schema_version = value.get("schema_version")
        if type(schema_version) is not int:
            raise TypeError("observation schema_version must be an integer")
        if schema_version != OBSERVATION_SCHEMA_VERSION:
            raise UnsupportedObservationSchema(schema_version)
        _exact_keys(
            value,
            {
                "schema_version",
                "observation_id",
                "observer_kind",
                "observer_id",
                "observer_version",
                "observed_at",
                "phase",
                "status",
                "subject",
                "reference",
            },
            label="observation",
        )
        return cls(
            observer_kind=value["observer_kind"],
            observer_id=value["observer_id"],
            observer_version=value["observer_version"],
            observed_at=value["observed_at"],
            phase=value["phase"],
            status=value["status"],
            subject=ObservationSubject.from_dict(value["subject"]),
            reference=_reference_from_dict(value["reference"]),
            observation_id=value["observation_id"],
            schema_version=schema_version,
        )


__all__ = [
    "OBSERVATION_SCHEMA_VERSION",
    "OBSERVER_KINDS",
    "OBSERVATION_PHASES",
    "OBSERVATION_STATUSES",
    "SUBJECT_KINDS",
    "REVIEW_ATTEMPT_STATUSES",
    "UnsupportedObservationSchema",
    "ObservationSubject",
    "MechanicalEvidenceReference",
    "ModelReviewReference",
    "HumanDecisionReference",
    "ObservationReference",
    "Observation",
]
