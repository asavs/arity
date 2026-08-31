"""Blind-safe, read-only projection for the observer UI.

``WatchViewModel`` is the boundary that terminal and visual clients may render.  Raw
trial identities remain in ``WatchLabelRegistry``, which is controller state and is
deliberately not part of the view model.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Optional

from .inspection import InspectionIssue, TrialCatalog, TrialInspection
from .trial_events import (
    KNOWN_EVENT_TYPES,
    TRIAL_EVENT_SCHEMA_VERSION,
    TrialEvent,
    TrialReplay,
)


MAX_WATCH_TRIALS = 256
MAX_WATCH_AGENTS = 256
MAX_WATCH_COUNT = 256

WatchBackend = Literal["jsonl", "sqlite"]
WatchIntegrity = Literal["valid", "partial", "corrupt"]
WatchLifecycle = Literal[
    "started", "evidenced", "unresolved", "resolved", "delivered", "unknown"
]
WatchIssueCode = Literal[
    "invalid_record",
    "orphan_event",
    "invalid_event",
    "invalid_replay",
    "unsupported_event",
    "unsupported_event_schema",
    "unsupported_evidence_schema",
    "unsupported_evaluation_schema",
    "unsupported_resolution_schema",
    "inspection_incomplete",
]

_LIFECYCLES = {
    "started",
    "evidenced",
    "unresolved",
    "resolved",
    "delivered",
    "unknown",
}
_ISSUE_MESSAGES: dict[str, str] = {
    "invalid_record": "A persisted trial record is not valid event data.",
    "orphan_event": "A persisted event has no usable trial identity.",
    "invalid_event": "A persisted trial event envelope is invalid.",
    "invalid_replay": "The trial journal violates lifecycle invariants.",
    "unsupported_event": (
        "The trial contains an event type this version does not understand."
    ),
    "unsupported_event_schema": "The trial contains a newer event schema.",
    "unsupported_evidence_schema": "The trial contains a newer evidence schema.",
    "unsupported_evaluation_schema": (
        "The trial contains a newer evaluation schema."
    ),
    "unsupported_resolution_schema": (
        "The trial contains a newer resolution schema."
    ),
    "inspection_incomplete": "The persisted trial could not be fully inspected.",
}
_UNSUPPORTED_ISSUES = {
    "unsupported_event",
    "unsupported_event_schema",
    "unsupported_evidence_schema",
    "unsupported_evaluation_schema",
    "unsupported_resolution_schema",
}
_CORRUPT_ISSUES = {
    "invalid_record",
    "invalid_event",
    "invalid_replay",
}
_CATALOG_ISSUES = {"invalid_record", "orphan_event"}


@dataclass(frozen=True)
class BoundedCount:
    """A count that cannot expose or allocate from an unbounded source total."""

    value: int
    more_omitted: bool = False

    def __post_init__(self) -> None:
        if type(self.value) is not int or not 0 <= self.value <= MAX_WATCH_COUNT:
            raise ValueError(f"count must be between 0 and {MAX_WATCH_COUNT}")
        if type(self.more_omitted) is not bool:
            raise TypeError("more_omitted must be a boolean")


@dataclass(frozen=True)
class WatchIssue:
    """An allowlisted issue whose explanation never comes from persisted text."""

    code: WatchIssueCode
    message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not str:
            raise TypeError("watch issue code must be a plain string")
        message = _ISSUE_MESSAGES.get(self.code)
        if message is None:
            raise ValueError("unsupported watch issue code")
        object.__setattr__(self, "message", message)


@dataclass(frozen=True)
class WatchAgent:
    """One neutral, position-labelled arm from a verified replay prefix."""

    position: int
    completion_recorded: bool

    def __post_init__(self) -> None:
        if type(self.position) is not int or not 0 <= self.position < MAX_WATCH_AGENTS:
            raise ValueError("agent position is outside the visible watch range")
        if type(self.completion_recorded) is not bool:
            raise TypeError("completion_recorded must be a boolean")

    @property
    def label(self) -> str:
        return f"Agent {_agent_letters(self.position)}"

    @property
    def status(self) -> Literal["completion recorded", "no completion recorded"]:
        if self.completion_recorded:
            return "completion recorded"
        return "no completion recorded"


@dataclass(frozen=True)
class WatchTrialDetail:
    """Finite structural detail derived exclusively from a verified replay."""

    agents: tuple[WatchAgent, ...]
    arms: BoundedCount
    completed_agents: BoundedCount
    evidence: BoundedCount
    reviews: BoundedCount
    resolutions: BoundedCount
    delivery_recorded: bool

    def __post_init__(self) -> None:
        if type(self.agents) is not tuple or len(self.agents) > MAX_WATCH_AGENTS:
            raise ValueError("agents must be a bounded tuple")
        if any(type(agent) is not WatchAgent for agent in self.agents):
            raise TypeError("agents must contain only WatchAgent values")
        for value in (
            self.arms,
            self.completed_agents,
            self.evidence,
            self.reviews,
            self.resolutions,
        ):
            if type(value) is not BoundedCount:
                raise TypeError("watch detail counts must be bounded")
        if type(self.delivery_recorded) is not bool:
            raise TypeError("delivery_recorded must be a boolean")


@dataclass(frozen=True)
class WatchTrial:
    """A neutral trial row containing no persisted identity or free-form text."""

    trial_number: int
    integrity: WatchIntegrity
    lifecycle: WatchLifecycle
    detail: Optional[WatchTrialDetail]
    issue: Optional[WatchIssue]
    selected: bool = False

    def __post_init__(self) -> None:
        if type(self.trial_number) is not int or self.trial_number < 1:
            raise ValueError("trial_number must be a positive integer")
        if type(self.integrity) is not str or self.integrity not in {
            "valid",
            "partial",
            "corrupt",
        }:
            raise ValueError("unsupported watch integrity")
        if type(self.lifecycle) is not str or self.lifecycle not in _LIFECYCLES:
            raise ValueError("unsupported watch lifecycle")
        if self.detail is not None and type(self.detail) is not WatchTrialDetail:
            raise TypeError("detail must be WatchTrialDetail or None")
        if self.issue is not None and type(self.issue) is not WatchIssue:
            raise TypeError("issue must be WatchIssue or None")
        if type(self.selected) is not bool:
            raise TypeError("selected must be a boolean")
        if self.integrity == "corrupt" and (
            self.lifecycle != "unknown" or self.detail is not None
        ):
            raise ValueError("corrupt trials cannot expose lifecycle detail")
        if self.integrity == "valid" and (
            self.lifecycle == "unknown" or self.detail is None or self.issue is not None
        ):
            raise ValueError("valid trials require verified detail and no issue")
        if self.integrity != "valid" and self.issue is None:
            raise ValueError("partial and corrupt trials require a safe issue")
        if self.lifecycle == "unknown" and self.detail is not None:
            raise ValueError("an unknown lifecycle cannot expose trial detail")

    @property
    def label(self) -> str:
        return f"Trial {self.trial_number}"


@dataclass(frozen=True)
class WatchViewModel:
    """The complete positive allowlist that a blind observer may render."""

    backend: WatchBackend
    read_at: float
    trials: tuple[WatchTrial, ...]
    more_trials_omitted: bool
    catalog_issues: tuple[WatchIssue, ...] = ()
    selected_trial_number: Optional[int] = None
    requested_trial_missing: bool = False

    def __post_init__(self) -> None:
        if type(self.backend) is not str or self.backend not in {"jsonl", "sqlite"}:
            raise ValueError("unsupported watch backend")
        if type(self.read_at) is not float or not _finite_number(self.read_at):
            raise ValueError("read_at must be a finite number")
        if type(self.trials) is not tuple or len(self.trials) > MAX_WATCH_TRIALS:
            raise ValueError("trials must be a bounded tuple")
        if any(type(trial) is not WatchTrial for trial in self.trials):
            raise TypeError("trials must contain only WatchTrial values")
        if type(self.more_trials_omitted) is not bool:
            raise TypeError("more_trials_omitted must be a boolean")
        if type(self.catalog_issues) is not tuple or any(
            type(issue) is not WatchIssue for issue in self.catalog_issues
        ):
            raise TypeError("catalog_issues must contain only WatchIssue values")
        codes = tuple(issue.code for issue in self.catalog_issues)
        if len(codes) != len(set(codes)):
            raise ValueError("catalog issue codes must be unique")
        if self.selected_trial_number is not None and (
            type(self.selected_trial_number) is not int
            or self.selected_trial_number < 1
        ):
            raise ValueError("selected_trial_number must be positive or None")
        if type(self.requested_trial_missing) is not bool:
            raise TypeError("requested_trial_missing must be a boolean")
        if self.requested_trial_missing and self.selected_trial_number is not None:
            raise ValueError("a missing requested trial cannot be selected")
        selected = tuple(trial for trial in self.trials if trial.selected)
        if len(selected) > 1:
            raise ValueError("at most one visible trial may be selected")
        if selected and selected[0].trial_number != self.selected_trial_number:
            raise ValueError("visible selection must match selected_trial_number")

    @property
    def fingerprint(self) -> tuple[object, ...]:
        """Return the visible journal state, excluding clock and selection state."""
        return watch_fingerprint(self)


class WatchLabelRegistry:
    """Controller-private session labels; this object is not blind-renderable.

    Raw trial IDs are retained only as dictionary keys.  Labels are never removed or
    recycled during the lifetime of this registry.
    """

    __slots__ = ("_labels", "_next_number")

    def __init__(self) -> None:
        self._labels: dict[str, int] = {}
        self._next_number = 1

    def assign(self, trial_ids: Iterable[str]) -> None:
        for trial_id in trial_ids:
            if type(trial_id) is not str or not trial_id:
                raise ValueError("trial labels require non-empty string identities")
            if trial_id not in self._labels:
                self._labels[trial_id] = self._next_number
                self._next_number += 1

    def number_for(self, trial_id: str) -> int:
        try:
            return self._labels[trial_id]
        except KeyError as exc:
            raise KeyError("trial identity has not been assigned a neutral label") from exc

    def __repr__(self) -> str:
        return "WatchLabelRegistry(<controller-private>)"


class WatchProjector:
    """Retain neutral labels while projecting successive snapshots in one session."""

    __slots__ = ("_label_registry",)

    def __init__(self) -> None:
        self._label_registry = WatchLabelRegistry()

    def project(
        self,
        catalog: TrialCatalog,
        *,
        backend: WatchBackend,
        read_at: float,
        selected_trial_id: Optional[str] = None,
    ) -> WatchViewModel:
        return build_watch_view_model(
            catalog,
            backend=backend,
            read_at=read_at,
            selected_trial_id=selected_trial_id,
            label_registry=self._label_registry,
        )

    def __repr__(self) -> str:
        return "WatchProjector(<controller-private labels>)"


@dataclass(frozen=True)
class _TrialSource:
    trial_id: str = field(repr=False)
    trusted_updated_at: Optional[float]
    integrity: WatchIntegrity
    lifecycle: WatchLifecycle
    detail: Optional[WatchTrialDetail]
    issue: Optional[WatchIssue]


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _bounded_count(value: int) -> BoundedCount:
    if type(value) is not int or value < 0:
        raise ValueError("source count must be a non-negative integer")
    return BoundedCount(min(value, MAX_WATCH_COUNT), value > MAX_WATCH_COUNT)


def _agent_letters(position: int) -> str:
    if type(position) is not int or not 0 <= position < MAX_WATCH_AGENTS:
        raise ValueError("agent position is outside the visible watch range")
    encoded: list[str] = []
    value = position + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        encoded.append(chr(ord("A") + remainder))
    return "".join(reversed(encoded))


def _trial_issue(
    inspection: TrialInspection, integrity: WatchIntegrity
) -> Optional[WatchIssue]:
    if integrity == "valid":
        return None
    allowed = _UNSUPPORTED_ISSUES if integrity == "partial" else _CORRUPT_ISSUES
    # Inspection emits issues in boundary order.  Keep the first boundary even when
    # its code is unknown, so appended diagnostics cannot change the projection.
    issues = inspection.issues
    if type(issues) is not tuple or not issues or type(issues[0]) is not InspectionIssue:
        return WatchIssue("inspection_incomplete")
    code = issues[0].code
    if type(code) is str and code in allowed:
        return WatchIssue(code)  # type: ignore[arg-type]
    return WatchIssue("inspection_incomplete")


def _catalog_issues(issues: Iterable[InspectionIssue]) -> tuple[WatchIssue, ...]:
    codes: set[str] = set()
    for issue in issues:
        if (
            type(issue) is InspectionIssue
            and type(issue.code) is str
            and issue.code in _CATALOG_ISSUES
        ):
            codes.add(issue.code)
        else:
            codes.add("inspection_incomplete")
    return tuple(WatchIssue(code) for code in sorted(codes))  # type: ignore[arg-type]


def _trusted_timestamp(
    replay: TrialReplay,
    *,
    boundary_sequence: Optional[int],
) -> float:
    if type(replay.events) is not tuple or not replay.events:
        raise ValueError("verified replay has no events")
    for expected_sequence, event in enumerate(replay.events, 1):
        if (
            type(event) is not TrialEvent
            or type(event.schema_version) is not int
            or event.schema_version != TRIAL_EVENT_SCHEMA_VERSION
            or type(event.trial_id) is not str
            or event.trial_id != replay.trial_id
            or type(event.sequence) is not int
            or event.sequence != expected_sequence
            or type(event.event_type) is not str
            or event.event_type not in KNOWN_EVENT_TYPES
            or type(event.timestamp) is not float
            or not _finite_number(event.timestamp)
        ):
            raise ValueError("verified replay structure is inconsistent")
    first = replay.events[0]
    if first.event_type != "trial.started" or replay.started is not first.payload:
        raise ValueError("verified replay start is inconsistent")
    if boundary_sequence is not None and boundary_sequence != len(replay.events) + 1:
        raise ValueError("verified replay crosses its unsupported boundary")
    last = replay.events[-1]
    timestamp = last.timestamp
    return float(timestamp)


def _partial_boundary_sequence(inspection: TrialInspection) -> Optional[int]:
    issues = inspection.issues
    if type(issues) is not tuple or not issues or type(issues[0]) is not InspectionIssue:
        return None
    sequence = issues[0].sequence
    return sequence if type(sequence) is int and sequence >= 1 else None


def _ordered_arm_ids(replay: TrialReplay) -> tuple[str, ...]:
    encoded = replay.started.get("arms") or ()
    if not isinstance(encoded, (list, tuple)):
        raise TypeError("verified replay arms are not an array")
    if not encoded:
        return ()
    if all(isinstance(arm, Mapping) for arm in encoded):
        structured: list[tuple[int, int, str]] = []
        seen_ids: set[str] = set()
        seen_ordinals: set[int] = set()
        for index, arm in enumerate(encoded):
            arm_id = arm.get("arm_id")
            ordinal = arm.get("arm_ordinal")
            if (
                type(arm_id) is not str
                or not arm_id
                or type(ordinal) is not int
                or arm_id in seen_ids
                or ordinal in seen_ordinals
            ):
                raise ValueError("verified replay has an invalid arm declaration")
            seen_ids.add(arm_id)
            seen_ordinals.add(ordinal)
            structured.append((ordinal, index, arm_id))
        structured.sort(key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in structured)
    if all(type(arm) is str and arm for arm in encoded):
        legacy = tuple(encoded)
        if len(legacy) != len(set(legacy)):
            raise ValueError("verified replay has duplicate legacy arm identities")
        return legacy
    raise ValueError("verified replay mixes structured and legacy arm declarations")


def _trial_detail(replay: TrialReplay) -> WatchTrialDetail:
    arm_ids = _ordered_arm_ids(replay)
    completed_ids: set[str] = set()
    for completion in replay.completed_arms:
        if not isinstance(completion, Mapping):
            raise TypeError("verified replay completion is not an object")
        arm_id = completion.get("arm_id")
        if type(arm_id) is not str or not arm_id:
            raise ValueError("verified replay completion has no arm identity")
        completed_ids.add(arm_id)
    completed_count = sum(arm_id in completed_ids for arm_id in arm_ids)
    agents = tuple(
        WatchAgent(position, arm_id in completed_ids)
        for position, arm_id in enumerate(arm_ids[:MAX_WATCH_AGENTS])
    )
    return WatchTrialDetail(
        agents=agents,
        arms=_bounded_count(len(arm_ids)),
        completed_agents=_bounded_count(completed_count),
        evidence=_bounded_count(len(replay.evidence_bundles)),
        reviews=_bounded_count(len(replay.reviews)),
        resolutions=_bounded_count(len(replay.resolutions)),
        delivery_recorded=replay.delivery is not None,
    )


def _project_trial(inspection: TrialInspection) -> _TrialSource:
    raw_integrity = inspection.integrity
    if raw_integrity == "corrupt":
        return _TrialSource(
            trial_id=inspection.trial_id,
            trusted_updated_at=None,
            integrity="corrupt",
            lifecycle="unknown",
            detail=None,
            issue=_trial_issue(inspection, "corrupt"),
        )
    if raw_integrity not in {"valid", "unsupported"}:
        return _TrialSource(
            trial_id=inspection.trial_id,
            trusted_updated_at=None,
            integrity="corrupt",
            lifecycle="unknown",
            detail=None,
            issue=WatchIssue("inspection_incomplete"),
        )

    integrity: WatchIntegrity = "valid" if raw_integrity == "valid" else "partial"
    replay = inspection.replay
    if replay is None:
        if integrity == "valid":
            return _TrialSource(
                trial_id=inspection.trial_id,
                trusted_updated_at=None,
                integrity="corrupt",
                lifecycle="unknown",
                detail=None,
                issue=WatchIssue("inspection_incomplete"),
            )
        return _TrialSource(
            trial_id=inspection.trial_id,
            trusted_updated_at=None,
            integrity=integrity,
            lifecycle="unknown",
            detail=None,
            issue=_trial_issue(inspection, integrity),
        )
    if (
        type(replay) is not TrialReplay
        or type(replay.trial_id) is not str
        or replay.trial_id != inspection.trial_id
    ):
        return _TrialSource(
            trial_id=inspection.trial_id,
            trusted_updated_at=None,
            integrity="corrupt" if integrity == "valid" else "partial",
            lifecycle="unknown",
            detail=None,
            issue=WatchIssue("inspection_incomplete"),
        )
    try:
        replay_collections = (
            replay.events,
            replay.completed_arms,
            replay.evidence_bundles,
            replay.reviews,
            replay.evaluations,
            replay.resolutions,
            replay.resolution_sequences,
            replay.unhandled_events,
        )
        if any(type(value) is not tuple for value in replay_collections):
            raise TypeError("verified replay collections must be tuples")
        if replay.unhandled_events:
            raise ValueError("verified replay contains unhandled events")
        if replay.delivery is not None and not isinstance(replay.delivery, Mapping):
            raise TypeError("verified replay delivery is not an object")
        boundary_sequence = (
            None
            if integrity == "valid"
            else _partial_boundary_sequence(inspection)
        )
        if integrity == "partial" and boundary_sequence is None:
            raise ValueError("partial replay has no trusted boundary")
        lifecycle = replay.lifecycle_status
        if (
            type(lifecycle) is not str
            or lifecycle not in _LIFECYCLES
            or lifecycle == "unknown"
        ):
            raise ValueError("verified replay lifecycle is invalid")
        detail = _trial_detail(replay)
        trusted_updated_at = _trusted_timestamp(
            replay,
            boundary_sequence=boundary_sequence,
        )
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
        return _TrialSource(
            trial_id=inspection.trial_id,
            trusted_updated_at=None,
            integrity="corrupt" if integrity == "valid" else "partial",
            lifecycle="unknown",
            detail=None,
            issue=WatchIssue("inspection_incomplete"),
        )
    return _TrialSource(
        trial_id=inspection.trial_id,
        trusted_updated_at=trusted_updated_at,
        integrity=integrity,
        lifecycle=lifecycle,  # type: ignore[arg-type]
        detail=detail,
        issue=_trial_issue(inspection, integrity),
    )


def _trial_sort_key(source: _TrialSource) -> tuple[int, float, str]:
    if source.trusted_updated_at is None:
        return (1, 0.0, source.trial_id)
    return (0, -source.trusted_updated_at, source.trial_id)


def _collapse_duplicate_trials(
    sources: Iterable[_TrialSource],
) -> tuple[_TrialSource, ...]:
    """Represent an impossible duplicate identity once and without trusted detail."""
    by_id: dict[str, _TrialSource] = {}
    order: list[str] = []
    for source in sources:
        if source.trial_id not in by_id:
            by_id[source.trial_id] = source
            order.append(source.trial_id)
            continue
        by_id[source.trial_id] = _TrialSource(
            trial_id=source.trial_id,
            trusted_updated_at=None,
            integrity="corrupt",
            lifecycle="unknown",
            detail=None,
            issue=WatchIssue("inspection_incomplete"),
        )
    return tuple(by_id[trial_id] for trial_id in order)


def build_watch_view_model(
    catalog: TrialCatalog,
    *,
    backend: WatchBackend,
    read_at: float,
    selected_trial_id: Optional[str] = None,
    label_registry: Optional[WatchLabelRegistry] = None,
) -> WatchViewModel:
    """Build one blind-safe snapshot without consulting any live or writable seam.

    Pass the same ``WatchLabelRegistry`` on every refresh to retain session labels.
    Omitting it is appropriate for a single non-interactive snapshot.
    """
    if type(catalog) is not TrialCatalog:
        raise TypeError("catalog must be a TrialCatalog")
    if type(backend) is not str or backend not in {"jsonl", "sqlite"}:
        raise ValueError("unsupported watch backend")
    if not _finite_number(read_at):
        raise ValueError("read_at must be a finite number")
    if selected_trial_id is not None and (
        type(selected_trial_id) is not str or not selected_trial_id
    ):
        raise ValueError("selected_trial_id must be a non-empty string or None")
    if label_registry is not None and type(label_registry) is not WatchLabelRegistry:
        raise TypeError("label_registry must be WatchLabelRegistry or None")

    registry = label_registry if label_registry is not None else WatchLabelRegistry()
    projected = sorted(
        _collapse_duplicate_trials(
            _project_trial(inspection) for inspection in catalog.trials
        ),
        key=_trial_sort_key,
    )
    registry.assign(source.trial_id for source in projected)
    visible = projected[:MAX_WATCH_TRIALS]

    rows = tuple(
        WatchTrial(
            trial_number=registry.number_for(source.trial_id),
            integrity=source.integrity,
            lifecycle=source.lifecycle,
            detail=source.detail,
            issue=source.issue,
            selected=source.trial_id == selected_trial_id,
        )
        for source in visible
    )
    selected_source = next(
        (source for source in projected if source.trial_id == selected_trial_id),
        None,
    )
    requested_missing = selected_trial_id is not None and selected_source is None
    selected_number = (
        None
        if selected_source is None
        else registry.number_for(selected_source.trial_id)
    )
    return WatchViewModel(
        backend=backend,
        read_at=float(read_at),
        trials=rows,
        more_trials_omitted=len(projected) > MAX_WATCH_TRIALS,
        catalog_issues=_catalog_issues(catalog.issues),
        selected_trial_number=selected_number,
        requested_trial_missing=requested_missing,
    )


def watch_fingerprint(model: WatchViewModel) -> tuple[object, ...]:
    """Return only visible, journal-derived values that may cue an update."""
    if type(model) is not WatchViewModel:
        raise TypeError("model must be a WatchViewModel")
    trials = tuple(
        (
            trial.trial_number,
            trial.integrity,
            trial.lifecycle,
            trial.detail,
            None if trial.issue is None else trial.issue.code,
        )
        for trial in model.trials
    )
    return (
        model.backend,
        model.more_trials_omitted,
        tuple(issue.code for issue in model.catalog_issues),
        trials,
    )


__all__ = [
    "MAX_WATCH_AGENTS",
    "MAX_WATCH_COUNT",
    "MAX_WATCH_TRIALS",
    "BoundedCount",
    "WatchAgent",
    "WatchBackend",
    "WatchIntegrity",
    "WatchIssue",
    "WatchIssueCode",
    "WatchLabelRegistry",
    "WatchLifecycle",
    "WatchProjector",
    "WatchTrial",
    "WatchTrialDetail",
    "WatchViewModel",
    "build_watch_view_model",
    "watch_fingerprint",
]
