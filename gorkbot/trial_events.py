"""Ordered trial-level events and observation replay.

This journal is deliberately outside the session/runtime statechart.  It records
the project lifecycle that CLIs, TUIs, GUIs, and game worlds need to observe,
without attempting a full runtime reducer rewrite.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from .evidence import EvidenceBundle, Evaluation, Resolution, _freeze_json, _thaw_json
from .seams import RecordStore
from .types import StoreRecord


TRIAL_EVENT_SCHEMA_VERSION = 1
TRIAL_REPLAY_SCHEMA_VERSION = 1
KNOWN_EVENT_TYPES = {
    "trial.started",
    "arm.completed",
    "evidence.frozen",
    "review.recorded",
    "resolution.recorded",
    "delivery.completed",
}
TRIAL_PHASES = {"trial", "conference"}
_JOURNAL_LOCKS_GUARD = threading.Lock()
_JOURNAL_LOCKS: dict[tuple[str, str], threading.RLock] = {}


class UnsupportedTrialEventSchema(ValueError):
    """A persisted event uses a schema this version cannot interpret."""

    def __init__(self, schema_version: int) -> None:
        super().__init__(f"unsupported trial event schema version {schema_version}")
        self.schema_version = schema_version


def _journal_lock(store: RecordStore, trial_id: str) -> threading.RLock:
    location = getattr(store, "path", None) or getattr(store, "root", None)
    if location is None:
        store_key = f"object:{id(store)}"
    else:
        store_type = f"{type(store).__module__}.{type(store).__qualname__}"
        canonical_location = os.path.normcase(str(Path(location).resolve()))
        store_key = f"{store_type}:{canonical_location}"
    key = (store_key, trial_id)
    with _JOURNAL_LOCKS_GUARD:
        return _JOURNAL_LOCKS.setdefault(key, threading.RLock())


def _strict_payload(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Reject lossy/default-string serialization and return a deep frozen copy."""
    if not isinstance(value, Mapping):
        raise TypeError("trial event payload must be a JSON object")
    encoded = json.dumps(
        _thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("trial event payload must be a JSON object")
    return _freeze_json(decoded)


@dataclass(frozen=True)
class TrialEvent:
    schema_version: int
    trial_id: str
    sequence: int
    event_type: str
    timestamp: float
    payload: Mapping[str, Any]
    idempotency_key: Optional[str] = None

    @classmethod
    def create(
        cls,
        *,
        trial_id: str,
        sequence: int,
        event_type: str,
        payload: Mapping[str, Any],
        timestamp: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> "TrialEvent":
        if not str(trial_id):
            raise ValueError("trial_id must not be empty")
        if int(sequence) < 1:
            raise ValueError("trial event sequence must be positive")
        if not str(event_type):
            raise ValueError("event_type must not be empty")
        if idempotency_key is not None and not str(idempotency_key):
            raise ValueError("idempotency_key must not be empty")
        resolved_timestamp = float(time.time() if timestamp is None else timestamp)
        if not math.isfinite(resolved_timestamp):
            raise ValueError("trial event timestamp must be finite")
        return cls(
            schema_version=TRIAL_EVENT_SCHEMA_VERSION,
            trial_id=str(trial_id),
            sequence=int(sequence),
            event_type=str(event_type),
            timestamp=resolved_timestamp,
            payload=_strict_payload(payload),
            idempotency_key=None if idempotency_key is None else str(idempotency_key),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trial_id": self.trial_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "payload": _thaw_json(self.payload),
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrialEvent":
        if not isinstance(value, Mapping):
            raise TypeError("trial event must be a JSON object")
        schema_version = value.get("schema_version")
        if type(schema_version) is not int:
            raise TypeError("trial event schema_version must be an integer")
        if schema_version != TRIAL_EVENT_SCHEMA_VERSION:
            raise UnsupportedTrialEventSchema(schema_version)
        trial_id = value.get("trial_id")
        if not isinstance(trial_id, str) or not trial_id:
            raise TypeError("trial event trial_id must be a non-empty string")
        sequence = value.get("sequence")
        if type(sequence) is not int:
            raise TypeError("trial event sequence must be an integer")
        event_type = value.get("event_type")
        if not isinstance(event_type, str) or not event_type:
            raise TypeError("trial event event_type must be a non-empty string")
        timestamp = value.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise TypeError("trial event timestamp must be a number")
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise TypeError("trial event payload must be a JSON object")
        idempotency_key = value.get("idempotency_key")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key
        ):
            raise TypeError("trial event idempotency_key must be a non-empty string or null")
        return cls.create(
            trial_id=trial_id,
            sequence=sequence,
            event_type=event_type,
            timestamp=timestamp,
            payload=payload,
            idempotency_key=idempotency_key,
        )


@dataclass(frozen=True)
class TrialReplay:
    """Immutable observation projection reconstructed only from trial events."""

    trial_id: str
    events: tuple[TrialEvent, ...]
    started: Mapping[str, Any]
    completed_arms: tuple[Mapping[str, Any], ...]
    evidence_bundles: tuple[EvidenceBundle, ...]
    reviews: tuple[Mapping[str, Any], ...]
    evaluations: tuple[Evaluation, ...]
    resolutions: tuple[Resolution, ...]
    resolution_sequences: tuple[int, ...]
    delivery: Optional[Mapping[str, Any]]
    unhandled_events: tuple[TrialEvent, ...] = ()

    @property
    def latest_resolution(self) -> Optional[Resolution]:
        return self.resolutions[-1] if self.resolutions else None

    @property
    def lifecycle_status(self) -> str:
        if self.delivery is not None:
            return "delivered"
        if self.latest_resolution is not None:
            return "resolved" if self.latest_resolution.resolved else "unresolved"
        if self.evidence_bundles:
            return "evidenced"
        return "started"

    @property
    def status(self) -> str:
        if self.unhandled_events:
            return "incomplete"
        return self.lifecycle_status

    def evidence(self, evidence_hash: str) -> EvidenceBundle:
        for bundle in self.evidence_bundles:
            if bundle.evidence_hash == evidence_hash:
                return bundle
        raise KeyError(evidence_hash)

    def to_dict(self, *, include_events: bool = True) -> dict[str, Any]:
        """Return an explicit, versioned JSON projection without lossy coercion."""
        projection: dict[str, Any] = {
            "schema_version": TRIAL_REPLAY_SCHEMA_VERSION,
            "trial_id": self.trial_id,
            "status": self.status,
            "lifecycle_status": self.lifecycle_status,
            "started": _thaw_json(self.started),
            "completed_arms": [_thaw_json(arm) for arm in self.completed_arms],
            "evidence_bundles": [bundle.to_dict() for bundle in self.evidence_bundles],
            "reviews": [_thaw_json(review) for review in self.reviews],
            "evaluations": [evaluation.to_dict() for evaluation in self.evaluations],
            "resolutions": [resolution.to_dict() for resolution in self.resolutions],
            "resolution_sequences": list(self.resolution_sequences),
            "delivery": None if self.delivery is None else _thaw_json(self.delivery),
            "unhandled_events": [event.to_dict() for event in self.unhandled_events],
        }
        if include_events:
            projection["events"] = [event.to_dict() for event in self.events]
        return projection


class TrialJournal:
    """Persist-before-publish journal for one coordinator process and trial.

    Journal instances sharing a local store path coordinate sequence allocation in-process.
    Cross-process orchestration requires a RecordStore with transactional sequence allocation.
    """

    def __init__(self, store: RecordStore, trial_id: str):
        self.store = store
        self.trial_id = str(trial_id)
        self._lock = _journal_lock(store, self.trial_id)
        existing = store.query("trial_event", trial_id=self.trial_id)
        self._events = [TrialEvent.from_dict(record) for record in existing]
        self._next_sequence = max((event.sequence for event in self._events), default=0) + 1

    @property
    def events(self) -> tuple[TrialEvent, ...]:
        with self._lock:
            self._events = [
                TrialEvent.from_dict(record)
                for record in self.store.query("trial_event", trial_id=self.trial_id)
            ]
            self._next_sequence = max((event.sequence for event in self._events), default=0) + 1
            return tuple(sorted(self._events, key=lambda event: event.sequence))

    def serialized(self) -> threading.RLock:
        """Return the shared in-process lock for one atomic orchestration operation."""
        return self._lock

    def append(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        timestamp: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> TrialEvent:
        with self._lock:
            persisted = [
                TrialEvent.from_dict(record)
                for record in self.store.query("trial_event", trial_id=self.trial_id)
            ]
            if idempotency_key is not None:
                prior = next(
                    (event for event in persisted if event.idempotency_key == idempotency_key),
                    None,
                )
                if prior is not None:
                    if prior.event_type != event_type or prior.payload != _strict_payload(payload):
                        raise ValueError("idempotency key was already used for a different trial event")
                    self._events = persisted
                    self._next_sequence = max(event.sequence for event in persisted) + 1
                    return prior
            self._events = persisted
            self._next_sequence = max((event.sequence for event in persisted), default=0) + 1
            event = TrialEvent.create(
                trial_id=self.trial_id,
                sequence=self._next_sequence,
                event_type=event_type,
                payload=payload,
                timestamp=timestamp,
                idempotency_key=idempotency_key,
            )
            self.store.append(StoreRecord(kind="trial_event", record=event.to_dict()))
            self._events.append(event)
            self._next_sequence += 1
            return event

    def replay(self) -> TrialReplay:
        return replay_trial(self.events, trial_id=self.trial_id)


def _coalesce_events(events: Iterable[TrialEvent], trial_id: str) -> tuple[TrialEvent, ...]:
    by_sequence: dict[int, TrialEvent] = {}
    by_idempotency_key: dict[str, TrialEvent] = {}
    for event in events:
        if event.trial_id != trial_id:
            continue
        existing = by_sequence.get(event.sequence)
        if existing is None:
            by_sequence[event.sequence] = event
        elif existing.to_dict() != event.to_dict():
            raise ValueError(f"conflicting trial events at sequence {event.sequence}")
        if event.idempotency_key is not None:
            prior = by_idempotency_key.get(event.idempotency_key)
            if prior is not None and prior.to_dict() != event.to_dict():
                raise ValueError(
                    f"idempotency key {event.idempotency_key!r} identifies distinct trial events"
                )
            by_idempotency_key[event.idempotency_key] = event
    ordered = tuple(by_sequence[index] for index in sorted(by_sequence))
    if not ordered:
        raise ValueError(f"no trial events found for {trial_id}")
    expected = list(range(1, len(ordered) + 1))
    actual = [event.sequence for event in ordered]
    if actual != expected:
        raise ValueError(f"trial event sequence has gaps: expected {expected}, got {actual}")
    return ordered


def replay_trial(
    source: RecordStore | Iterable[TrialEvent | Mapping[str, Any]],
    trial_id: Optional[str] = None,
) -> TrialReplay:
    """Reconstruct one trial without consulting workspaces, providers, or current policy."""
    if hasattr(source, "query"):
        if trial_id is None:
            raise ValueError("trial_id is required when replaying a RecordStore")
        raw_events: Iterable[TrialEvent | Mapping[str, Any]] = source.query("trial_event", trial_id=trial_id)  # type: ignore[union-attr]
    else:
        raw_events = source  # type: ignore[assignment]
    materialized = tuple(
        item if isinstance(item, TrialEvent) else TrialEvent.from_dict(item)
        for item in raw_events
    )
    resolved_trial_id = trial_id or (materialized[0].trial_id if materialized else "")
    ordered = _coalesce_events(materialized, resolved_trial_id)

    if ordered[0].event_type != "trial.started":
        raise ValueError("trial.started must be sequence 1")
    if sum(event.event_type == "trial.started" for event in ordered) != 1:
        raise ValueError("a trial replay requires exactly one trial.started event")

    started = ordered[0].payload
    declared_arms = started.get("arms") or ()
    declarations_by_arm = {
        str(arm.get("arm_id")): arm
        for arm in declared_arms
        if isinstance(arm, Mapping) and arm.get("arm_id") is not None
    }
    structured_declarations = tuple(arm for arm in declared_arms if isinstance(arm, Mapping))
    if structured_declarations and (
        len(structured_declarations) != len(declared_arms)
        or len(declarations_by_arm) != len(structured_declarations)
        or len({int(arm.get("arm_ordinal", -1)) for arm in structured_declarations})
        != len(structured_declarations)
    ):
        raise ValueError("trial arms require unique structured ids and ordinals")
    declared_arm_ids = {
        str(arm.get("arm_id")) if isinstance(arm, Mapping) else str(arm)
        for arm in declared_arms
    }
    declared_panel: Optional[tuple[str, ...]] = None
    if "evaluator_ids" in started:
        raw_panel = tuple(str(item) for item in (started.get("evaluator_ids") or ()))
        if len(raw_panel) != len(set(raw_panel)):
            raise ValueError("trial evaluator declarations must be unique")
        declared_panel = tuple(sorted(raw_panel))
    completed_arm_keys: set[tuple[str, str]] = set()
    completed_candidate_keys: set[tuple[str, str]] = set()
    completions_by_arm: dict[tuple[str, str], Mapping[str, Any]] = {}
    frozen_phases: set[str] = set()
    completed_arms: list[Mapping[str, Any]] = []
    bundles: list[EvidenceBundle] = []
    bundle_by_hash: dict[str, EvidenceBundle] = {}
    reviews: list[Mapping[str, Any]] = []
    evaluations: list[Evaluation] = []
    resolutions: list[Resolution] = []
    resolution_sequences: list[int] = []
    resolution_by_sequence: dict[int, Resolution] = {}
    delivery: Optional[Mapping[str, Any]] = None
    unhandled: list[TrialEvent] = []

    for event in ordered[1:]:
        payload = event.payload
        if event.event_type == "arm.completed":
            phase = str(payload.get("phase", "trial"))
            if phase not in TRIAL_PHASES:
                raise ValueError(f"unsupported trial phase {phase!r}")
            if phase in frozen_phases:
                raise ValueError("an arm cannot complete after its phase evidence was frozen")
            arm_id = str(payload.get("arm_id", ""))
            candidate_id = str(payload.get("candidate_id", ""))
            if not arm_id or not candidate_id:
                raise ValueError("completed arm requires arm and candidate identities")
            if declared_arm_ids and arm_id not in declared_arm_ids:
                raise ValueError("completed arm was not declared when the trial started")
            if (phase, arm_id) in completed_arm_keys or (phase, candidate_id) in completed_candidate_keys:
                raise ValueError("an arm may complete only once per trial phase")
            completed_arm_keys.add((phase, arm_id))
            completed_candidate_keys.add((phase, candidate_id))
            declaration = declarations_by_arm.get(arm_id)
            if declaration is not None and int(payload.get("arm_ordinal", -1)) != int(
                declaration.get("arm_ordinal", -2)
            ):
                raise ValueError("completed arm ordinal does not match its declaration")
            completions_by_arm[(phase, arm_id)] = payload
            completed_arms.append(payload)
        elif event.event_type == "evidence.frozen":
            if resolutions:
                raise ValueError("evidence cannot be revised after a resolution is recorded")
            bundle = EvidenceBundle.from_dict(payload["bundle"])
            if bundle.trial_id != resolved_trial_id:
                raise ValueError("evidence bundle belongs to a different trial")
            if bundle.task_id != resolved_trial_id:
                raise ValueError("evidence task id must match the outer trial id")
            if not declarations_by_arm:
                raise ValueError("frozen evidence requires structured arm declarations")
            if str(started.get("task_id", "")) != bundle.task_id:
                raise ValueError("frozen evidence task does not match the trial declaration")
            if str(started.get("brief", "")) != bundle.brief:
                raise ValueError("frozen evidence brief does not match the trial declaration")
            if started.get("task_name") != bundle.task_name:
                raise ValueError("frozen evidence task name does not match the trial declaration")
            if dict(started.get("hidden_test_hashes") or {}) != dict(bundle.hidden_test_hashes):
                raise ValueError("frozen evidence tests do not match the trial declaration")
            phase = str(bundle.metadata.get("phase", "trial"))
            if phase not in TRIAL_PHASES:
                raise ValueError(f"unsupported trial phase {phase!r}")
            if phase in frozen_phases:
                raise ValueError("a trial phase can freeze evidence only once")
            evidence_arms = {(phase, candidate.arm_id) for candidate in bundle.candidates}
            evidence_candidates = {(phase, candidate.candidate_id) for candidate in bundle.candidates}
            if evidence_arms != {key for key in completed_arm_keys if key[0] == phase}:
                raise ValueError("frozen evidence arms do not match completed arms for its phase")
            if evidence_candidates != {key for key in completed_candidate_keys if key[0] == phase}:
                raise ValueError("frozen evidence candidates do not match completed arms for its phase")
            if {candidate.arm_id for candidate in bundle.candidates} != declared_arm_ids:
                raise ValueError("frozen evidence must contain every declared trial arm")
            if "resolved_arity" in started and len(bundle.candidates) != int(started["resolved_arity"]):
                raise ValueError("frozen evidence count does not match the resolved trial arity")
            for candidate in bundle.candidates:
                declaration = declarations_by_arm[candidate.arm_id]
                if (
                    candidate.arm_ordinal != int(declaration.get("arm_ordinal", -1))
                    or candidate.name != str(declaration.get("name", ""))
                    or candidate.context_adapter != declaration.get("context_adapter")
                ):
                    raise ValueError("frozen evidence does not match its arm declaration")
                stable_declared = {
                    "model": candidate.model,
                    "provider": candidate.provider,
                    "role": candidate.role,
                    "tool_runner": candidate.tool_runner,
                    "skills": tuple(candidate.skills),
                }
                if any(declaration.get(key) != value for key, value in stable_declared.items()):
                    raise ValueError("resolved arm axes do not match the declared experiment")
                declared_harness = str(declaration.get("harness", ""))
                if not (
                    candidate.harness == declared_harness
                    or candidate.harness.startswith(declared_harness + "->")
                ):
                    raise ValueError("resolved harness is not a declared harness or recorded fallback")
                if phase == "trial" and (
                    candidate.context != declaration.get("context")
                    or candidate.signature != declaration.get("signature")
                ):
                    raise ValueError("initial arm context or signature differs from its declaration")
                if phase == "conference" and candidate.context != "fork":
                    raise ValueError("conference evidence must use the explicit fork context")
                completion = completions_by_arm[(phase, candidate.arm_id)]
                completed_identity = {
                    "candidate_id": candidate.candidate_id,
                    "name": candidate.name,
                    "signature": candidate.signature,
                    "model": candidate.model,
                    "provider": candidate.provider,
                    "role": candidate.role,
                    "harness": candidate.harness,
                    "tool_runner": candidate.tool_runner,
                    "skills": tuple(candidate.skills),
                    "context": candidate.context,
                    "context_adapter": candidate.context_adapter,
                    "status": candidate.status,
                    "tokens_used": candidate.tokens_used,
                    "duration_seconds": candidate.duration_seconds,
                    "fallbacks": candidate.fallbacks,
                }
                if any(completion.get(key) != value for key, value in completed_identity.items()):
                    raise ValueError("frozen evidence does not match its completed arm identity")
            parent_hash = bundle.metadata.get("parent_evidence_hash")
            if phase == "conference":
                if (
                    not bundles
                    or bundles[-1].metadata.get("phase", "trial") != "trial"
                    or parent_hash != bundles[-1].evidence_hash
                ):
                    raise ValueError("conference evidence must link to the prior frozen evidence")
            elif parent_hash is not None:
                raise ValueError("initial evidence cannot declare a parent bundle")
            frozen_phases.add(phase)
            bundles.append(bundle)
            bundle_by_hash[bundle.evidence_hash] = bundle
        elif event.event_type == "review.recorded":
            reviews.append(payload)
            encoded_evaluation = payload.get("evaluation")
            status = str(payload.get("status", ""))
            if status not in {"completed", "failed", "invalid", "missing"}:
                raise ValueError("review has an unsupported status")
            if (status == "completed") != (encoded_evaluation is not None):
                raise ValueError("only completed reviews may contain an evaluation")
            evidence_hash = str(payload.get("evidence_hash", ""))
            if evidence_hash not in bundle_by_hash:
                raise ValueError("review references evidence that was not frozen earlier")
            if encoded_evaluation is not None:
                if str(encoded_evaluation.get("evidence_hash", "")) != evidence_hash:
                    raise ValueError("review envelope and evaluation reference different evidence")
                evaluation = Evaluation.from_dict(bundle_by_hash[evidence_hash], encoded_evaluation)
                if str(payload.get("evaluator_id", "")) != evaluation.evaluator_id:
                    raise ValueError("review envelope and evaluation identify different evaluators")
                evaluations.append(evaluation)
        elif event.event_type == "resolution.recorded":
            if delivery is not None:
                raise ValueError("a delivered trial cannot record another resolution")
            resolution = Resolution.from_dict(payload["resolution"])
            bundle = bundle_by_hash.get(resolution.evidence_hash)
            if bundle is None:
                raise ValueError("resolution references evidence that was not frozen earlier")
            if not bundles or bundle.evidence_hash != bundles[-1].evidence_hash:
                raise ValueError("resolution must reference the latest frozen evidence")
            resolution.validate(
                bundle,
                tuple(evaluation for evaluation in evaluations if evaluation.evidence_hash == bundle.evidence_hash),
            )
            if resolution.expected_evaluator_ids:
                if declared_panel is None or resolution.expected_evaluator_ids != declared_panel:
                    raise ValueError("resolution panel does not match the trial declaration")
            resolutions.append(resolution)
            resolution_sequences.append(event.sequence)
            resolution_by_sequence[event.sequence] = resolution
        elif event.event_type == "delivery.completed":
            if delivery is not None:
                raise ValueError("a trial replay cannot contain multiple completed deliveries")
            resolution_sequence = int(payload["resolution_sequence"])
            resolution = resolution_by_sequence.get(resolution_sequence)
            if (
                resolution is None
                or resolution_sequence >= event.sequence
                or not resolution.resolved
                or not resolution_sequences
                or resolution_sequence != resolution_sequences[-1]
            ):
                raise ValueError("delivery must reference an earlier resolved resolution")
            if str(payload.get("candidate_id")) != resolution.candidate_id:
                raise ValueError("delivery candidate does not match its resolution")
            if str(payload.get("resolution_id")) != resolution.resolution_id:
                raise ValueError("delivery does not match its resolution id")
            if str(payload.get("evidence_hash")) != resolution.evidence_hash:
                raise ValueError("delivery does not match its resolution evidence")
            bundle = bundle_by_hash[resolution.evidence_hash]
            candidate = bundle.candidate(resolution.candidate_id or "")
            encoded_delivery = payload.get("delivery")
            if not isinstance(encoded_delivery, Mapping):
                raise ValueError("delivery event is missing its portable delivery record")
            expected_files = tuple(artifact.path for artifact in candidate.artifacts)
            expected_answer = None if expected_files else ((candidate.output or "").strip() or None)
            if tuple(encoded_delivery.get("files") or ()) != expected_files:
                raise ValueError("delivery files do not match the frozen artifact manifest")
            if encoded_delivery.get("answer") != expected_answer:
                raise ValueError("delivery answer does not match the frozen evidence")
            if (
                encoded_delivery.get("winner_name") != candidate.name
                or encoded_delivery.get("signature") != candidate.signature
                or encoded_delivery.get("delivered") is not True
                or encoded_delivery.get("resolution_source") != resolution.kind.value
            ):
                raise ValueError("portable delivery metadata does not match the frozen resolution")
            delivery = payload
        elif event.event_type not in KNOWN_EVENT_TYPES:
            unhandled.append(event)

    return TrialReplay(
        trial_id=resolved_trial_id,
        events=ordered,
        started=started,
        completed_arms=tuple(completed_arms),
        evidence_bundles=tuple(bundles),
        reviews=tuple(reviews),
        evaluations=tuple(evaluations),
        resolutions=tuple(resolutions),
        resolution_sequences=tuple(resolution_sequences),
        delivery=delivery,
        unhandled_events=tuple(unhandled),
    )
