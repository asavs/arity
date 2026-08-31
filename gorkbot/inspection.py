"""Read-only trial discovery and replay projections for CLIs and visual clients."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Optional

from .evidence import _freeze_json, _thaw_json
from .seams import RecordReader
from .trial_events import (
    TRIAL_EVENT_SCHEMA_VERSION,
    TrialEvent,
    TrialReplay,
    UnsupportedTrialEventSchema,
    replay_trial,
)


TRIAL_INSPECTION_API_VERSION = 1
Integrity = Literal["valid", "unsupported", "corrupt"]
LifecycleStatus = Literal[
    "started", "evidenced", "unresolved", "resolved", "delivered", "unknown"
]


class TrialNotFound(LookupError):
    """An exact journal-backed trial id was not present in the reader."""

    code = "trial_not_found"

    def __init__(self, trial_id: str) -> None:
        super().__init__(f"trial {trial_id!r} was not found")
        self.trial_id = trial_id

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "trial_id": self.trial_id}


@dataclass(frozen=True)
class InspectionIssue:
    """A stable diagnostic that a terminal or visual client can render."""

    code: str
    message: str
    trial_id: Optional[str] = None
    sequence: Optional[int] = None
    event_type: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "trial_id": self.trial_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
        }


@dataclass(frozen=True)
class TrialSummary:
    """Small, content-free catalog row safe for routine display."""

    trial_id: str
    integrity: Integrity
    status: LifecycleStatus
    task_name: Optional[str]
    brief: str
    role: Optional[str]
    requested_arity: Optional[int]
    resolved_arity: int
    completed_arms: int
    event_count: int
    started_at: Optional[float]
    updated_at: Optional[float]
    winner_candidate_id: Optional[str]
    resolution_kind: Optional[str]
    issue_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "integrity": self.integrity,
            "status": self.status,
            "task_name": self.task_name,
            "brief": self.brief,
            "role": self.role,
            "requested_arity": self.requested_arity,
            "resolved_arity": self.resolved_arity,
            "completed_arms": self.completed_arms,
            "event_count": self.event_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "winner_candidate_id": self.winner_candidate_id,
            "resolution_kind": self.resolution_kind,
            "issue_count": self.issue_count,
        }


@dataclass(frozen=True)
class TrialInspection:
    """One journal's integrity result, best-known projection, and ordered records."""

    trial_id: str
    integrity: Integrity
    status: LifecycleStatus
    events: tuple[Mapping[str, Any], ...]
    replay: Optional[TrialReplay]
    issues: tuple[InspectionIssue, ...] = ()

    @property
    def summary(self) -> TrialSummary:
        started = self.replay.started if self.replay is not None else _started_payload(self.events)
        arms = started.get("arms") if isinstance(started, Mapping) else None
        declared_arity = len(arms) if isinstance(arms, (list, tuple)) else 0
        raw_resolved = started.get("resolved_arity") if isinstance(started, Mapping) else None
        resolved_arity = (
            raw_resolved
            if type(raw_resolved) is int and raw_resolved >= 0
            else declared_arity
        )
        raw_requested = started.get("requested_arity") if isinstance(started, Mapping) else None
        requested_arity = (
            raw_requested if type(raw_requested) is int and raw_requested >= 0 else None
        )
        completed_arm_ids = (
            {
                str(completion["arm_id"])
                for completion in self.replay.completed_arms
                if "arm_id" in completion
            }
            if self.replay is not None
            else set()
        )
        timestamps = tuple(
            float(event["timestamp"])
            for event in self.events
            if _finite_number(event.get("timestamp"))
        )
        started_at = timestamps[0] if timestamps else None
        updated_at = timestamps[-1] if timestamps else None
        resolution = self.replay.latest_resolution if self.replay is not None else None
        return TrialSummary(
            trial_id=self.trial_id,
            integrity=self.integrity,
            status=self.status,
            task_name=_optional_string(started.get("task_name")),
            brief=_string_or_empty(started.get("brief")),
            role=_optional_string(started.get("role")),
            requested_arity=requested_arity,
            resolved_arity=resolved_arity,
            completed_arms=len(completed_arm_ids),
            event_count=len(self.events),
            started_at=started_at,
            updated_at=updated_at,
            winner_candidate_id=(None if resolution is None else resolution.candidate_id),
            resolution_kind=(None if resolution is None else resolution.kind.value),
            issue_count=len(self.issues),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": TRIAL_INSPECTION_API_VERSION,
            "trial_id": self.trial_id,
            "integrity": self.integrity,
            "status": self.status,
            "summary": self.summary.to_dict(),
            "projection": (
                None if self.replay is None else self.replay.to_dict(include_events=False)
            ),
            "events": [_copy_json(event) for event in self.events],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class TrialCatalog:
    """All discoverable journal-backed trials plus unowned record diagnostics."""

    trials: tuple[TrialInspection, ...]
    issues: tuple[InspectionIssue, ...] = ()

    @property
    def summaries(self) -> tuple[TrialSummary, ...]:
        return tuple(trial.summary for trial in self.trials)

    def get(self, trial_id: str) -> TrialInspection:
        for trial in self.trials:
            if trial.trial_id == trial_id:
                return trial
        raise TrialNotFound(trial_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": TRIAL_INSPECTION_API_VERSION,
            "trials": [summary.to_dict() for summary in self.summaries],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _copy_json(value: Any) -> Any:
    return json.loads(
        json.dumps(
            _thaw_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def _freeze_record(value: Mapping[str, Any]) -> Mapping[str, Any]:
    copied = _copy_json(value)
    if not isinstance(copied, dict):
        raise TypeError("trial event record must be a JSON object")
    frozen = _freeze_json(copied)
    if not isinstance(frozen, Mapping):
        raise TypeError("trial event record must be a JSON object")
    return frozen


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _optional_string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _started_payload(events: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
    for event in events:
        if event.get("event_type") == "trial.started" and isinstance(event.get("payload"), Mapping):
            return event["payload"]
    return MappingProxyType({})


def _sequence(value: Mapping[str, Any]) -> Optional[int]:
    sequence = value.get("sequence")
    return sequence if type(sequence) is int and sequence >= 1 else None


def _event_sort_key(item: tuple[int, Mapping[str, Any]]) -> tuple[int, int]:
    index, event = item
    sequence = _sequence(event)
    return (sequence if sequence is not None else 2**63 - 1, index)


def _ordered_records(records: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    materialized = tuple(records)
    return tuple(
        _freeze_record(record)
        for _, record in sorted(enumerate(materialized), key=_event_sort_key)
    )


def _issue(
    code: str,
    message: str,
    trial_id: str,
    record: Optional[Mapping[str, Any]] = None,
) -> InspectionIssue:
    record = record or {}
    return InspectionIssue(
        code=code,
        message=message,
        trial_id=trial_id,
        sequence=_sequence(record),
        event_type=(
            record.get("event_type") if isinstance(record.get("event_type"), str) else None
        ),
    )


def _lifecycle_status(replay: Optional[TrialReplay]) -> LifecycleStatus:
    return "unknown" if replay is None else replay.lifecycle_status  # type: ignore[return-value]


def _inspect_records(
    trial_id: str,
    records: Iterable[Mapping[str, Any]],
) -> TrialInspection:
    try:
        ordered = _ordered_records(records)
    except (AttributeError, TypeError, ValueError) as exc:
        return TrialInspection(
            trial_id=trial_id,
            integrity="corrupt",
            status="unknown",
            events=(),
            replay=None,
            issues=(_issue("invalid_record", str(exc), trial_id),),
        )

    supported: list[TrialEvent] = []
    unsupported_records: list[Mapping[str, Any]] = []
    issues: list[InspectionIssue] = []
    for record in ordered:
        try:
            supported.append(TrialEvent.from_dict(record))
        except UnsupportedTrialEventSchema as exc:
            unsupported_records.append(record)
            issues.append(
                _issue(
                    "unsupported_event_schema",
                    str(exc),
                    trial_id,
                    record,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            return TrialInspection(
                trial_id=trial_id,
                integrity="corrupt",
                status="unknown",
                events=ordered,
                replay=None,
                issues=(_issue("invalid_event", str(exc), trial_id, record),),
            )

    if unsupported_records:
        unsupported_sequences = tuple(
            sequence
            for sequence in (_sequence(record) for record in unsupported_records)
            if sequence is not None
        )
        replay: Optional[TrialReplay] = None
        if len(unsupported_sequences) == len(unsupported_records):
            boundary = min(unsupported_sequences)
            prefix = tuple(event for event in supported if event.sequence < boundary)
            if prefix:
                try:
                    replay = replay_trial(prefix, trial_id)
                except (KeyError, TypeError, ValueError) as exc:
                    return TrialInspection(
                        trial_id=trial_id,
                        integrity="corrupt",
                        status="unknown",
                        events=ordered,
                        replay=None,
                        issues=tuple(issues) + (
                            _issue("invalid_replay", str(exc), trial_id),
                        ),
                    )
        return TrialInspection(
            trial_id=trial_id,
            integrity="unsupported",
            status=_lifecycle_status(replay),
            events=ordered,
            replay=replay,
            issues=tuple(issues),
        )

    try:
        replay = replay_trial(supported, trial_id)
    except (KeyError, TypeError, ValueError) as exc:
        return TrialInspection(
            trial_id=trial_id,
            integrity="corrupt",
            status="unknown",
            events=ordered,
            replay=None,
            issues=(_issue("invalid_replay", str(exc), trial_id),),
        )

    if replay.unhandled_events:
        issues.extend(
            _issue(
                "unsupported_event",
                f"unsupported trial event type {event.event_type!r}",
                trial_id,
                event.to_dict(),
            )
            for event in replay.unhandled_events
        )
        integrity: Integrity = "unsupported"
    else:
        integrity = "valid"
    return TrialInspection(
        trial_id=trial_id,
        integrity=integrity,
        status=_lifecycle_status(replay),
        events=tuple(_freeze_record(event.to_dict()) for event in replay.events),
        replay=replay,
        issues=tuple(issues),
    )


def inspect_trial(reader: RecordReader, trial_id: str) -> TrialInspection:
    """Inspect one exact trial without creating state or consulting live runtimes."""
    if not isinstance(trial_id, str) or not trial_id:
        raise ValueError("trial_id must be a non-empty string")
    records = reader.query("trial_event", trial_id=trial_id)
    if not records:
        raise TrialNotFound(trial_id)
    return _inspect_records(trial_id, records)


def inspect_trials(reader: RecordReader) -> TrialCatalog:
    """Discover every journal-backed trial while retaining per-trial failures."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    catalog_issues: list[InspectionIssue] = []
    for index, record in enumerate(reader.query("trial_event"), 1):
        if not isinstance(record, Mapping):
            catalog_issues.append(
                InspectionIssue(
                    code="invalid_record",
                    message=f"trial event record {index} is not a JSON object",
                )
            )
            continue
        trial_id = record.get("trial_id")
        if not isinstance(trial_id, str) or not trial_id:
            catalog_issues.append(
                InspectionIssue(
                    code="orphan_event",
                    message=f"trial event record {index} has no non-empty trial_id",
                    sequence=_sequence(record),
                    event_type=(
                        record.get("event_type")
                        if isinstance(record.get("event_type"), str)
                        else None
                    ),
                )
            )
            continue
        grouped.setdefault(trial_id, []).append(record)

    trials = [_inspect_records(trial_id, records) for trial_id, records in grouped.items()]
    trials.sort(
        key=lambda trial: (
            -(trial.summary.updated_at if trial.summary.updated_at is not None else float("-inf")),
            trial.trial_id,
        )
    )
    return TrialCatalog(trials=tuple(trials), issues=tuple(catalog_issues))
