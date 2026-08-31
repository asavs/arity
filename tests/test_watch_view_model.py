"""Acceptance contract for the blind-safe watch view model.

The Stage-1 public API is intentionally small::

    projector = WatchProjector()
    model = projector.project(
        catalog,
        backend="jsonl",
        read_at=123.0,
        selected_trial_id="exact internal id",
    )
    digest = watch_fingerprint(model)

``WatchProjector`` may retain only its in-memory neutral-label assignment between
calls.  Projection and fingerprinting are otherwise pure: they consume an already
inspected ``TrialCatalog`` and perform no I/O.
"""

from __future__ import annotations

import base64
import builtins
import dataclasses
import json
import math
import socket
import subprocess
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast

import pytest

from arity.inspection import InspectionIssue, TrialCatalog, TrialInspection
from arity.trial_events import TrialEvent, TrialReplay
from arity.watch_view_model import WatchProjector, WatchViewModel, watch_fingerprint


BLIND_LEAK_SENTINEL = "BLIND_LEAK_SENTINEL"
ALLOWED_INTEGRITY = {"valid", "partial", "corrupt"}
ALLOWED_LIFECYCLE = {
    "started",
    "evidenced",
    "unresolved",
    "resolved",
    "delivered",
    "unknown",
}
ALLOWED_ISSUE_CODES = {
    "unsupported_event",
    "unsupported_event_schema",
    "unsupported_evidence_schema",
    "unsupported_usage_evidence_schema",
    "unsupported_observation_schema",
    "unsupported_evaluation_schema",
    "unsupported_resolution_schema",
    "invalid_record",
    "orphan_event",
    "invalid_event",
    "invalid_replay",
    "inspection_incomplete",
}


@dataclass(frozen=True)
class HiddenResolution:
    resolved: bool
    resolution_id: str = BLIND_LEAK_SENTINEL
    candidate_id: str = BLIND_LEAK_SENTINEL
    kind: str = BLIND_LEAK_SENTINEL


class LeakyStr(str):
    def __repr__(self) -> str:
        return BLIND_LEAK_SENTINEL


def hidden_blob(marker: str = BLIND_LEAK_SENTINEL) -> dict[str, Any]:
    """Place one unique marker in every currently known free-form family."""
    return {
        "trial_id": marker,
        "task_id": marker,
        "task_name": marker,
        "brief": marker,
        "role": marker,
        "arm_id": marker,
        "candidate_id": marker,
        "name": marker,
        "signature": marker,
        "model": marker,
        "provider": marker,
        "harness": marker,
        "tool_runner": marker,
        "skills": [marker],
        "context": marker,
        "context_adapter": marker,
        "axis": marker,
        "status": marker,
        "phase": marker,
        "evaluator_id": marker,
        "resolution_id": marker,
        "evidence_hash": marker,
        "output": marker,
        "artifact": {"path": marker, "body": marker},
        "delivery": {"files": [marker], "answer": marker},
        "credential": marker,
        "filesystem_path": marker,
        "network_location": marker,
    }


def event(
    trial_id: str,
    sequence: int,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    timestamp: float | None = None,
    idempotency_key: str | None = None,
) -> TrialEvent:
    return TrialEvent.create(
        trial_id=trial_id,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
        timestamp=float(sequence if timestamp is None else timestamp),
        idempotency_key=idempotency_key,
    )


def replay(
    trial_id: str,
    *,
    arms: tuple[object, ...] = (),
    completed: tuple[Mapping[str, Any], ...] = (),
    evidence_count: int = 0,
    review_count: int = 0,
    resolution: bool | None = None,
    delivered: bool = False,
    timestamp: float = 1.0,
    hidden: Mapping[str, Any] | None = None,
) -> TrialReplay:
    started_payload: dict[str, Any] = dict(hidden or {})
    started_payload["arms"] = list(arms)
    started = event(
        trial_id,
        1,
        "trial.started",
        started_payload,
        timestamp=timestamp,
        idempotency_key=(
            f"{BLIND_LEAK_SENTINEL}:idempotency" if hidden is not None else None
        ),
    )
    resolutions = (
        ()
        if resolution is None
        else (cast(Any, HiddenResolution(resolved=resolution)),)
    )
    return TrialReplay(
        trial_id=trial_id,
        events=(started,),
        started=started.payload,
        completed_arms=completed,
        evidence_bundles=tuple(cast(Any, object()) for _ in range(evidence_count)),
        reviews=tuple(cast(Any, {"evaluator_id": BLIND_LEAK_SENTINEL}) for _ in range(review_count)),
        evaluations=tuple(cast(Any, object()) for _ in range(review_count)),
        resolutions=resolutions,
        resolution_sequences=(() if resolution is None else (1,)),
        delivery=(
            {"delivery": hidden_blob()} if delivered else None
        ),
        unhandled_events=(),
    )


def lifecycle_replay(
    trial_id: str,
    lifecycle: str,
    *,
    timestamp: float = 1.0,
) -> TrialReplay:
    options: dict[str, Any] = {}
    if lifecycle == "evidenced":
        options["evidence_count"] = 1
    elif lifecycle == "unresolved":
        options["resolution"] = False
    elif lifecycle == "resolved":
        options["resolution"] = True
    elif lifecycle == "delivered":
        options.update(resolution=True, delivered=True)
    elif lifecycle != "started":
        raise AssertionError(f"unsupported test lifecycle {lifecycle}")
    return replay(trial_id, timestamp=timestamp, **options)


def inspection(
    trial_id: str,
    *,
    integrity: str = "valid",
    replay_value: TrialReplay | None = None,
    issues: tuple[InspectionIssue, ...] = (),
    events: tuple[Mapping[str, Any], ...] = (),
) -> TrialInspection:
    resolved_replay = replay_value
    if resolved_replay is None and integrity == "valid":
        resolved_replay = replay(trial_id)
    status = (
        "unknown" if resolved_replay is None else resolved_replay.lifecycle_status
    )
    return TrialInspection(
        trial_id=trial_id,
        integrity=cast(Any, integrity),
        status=cast(Any, status),
        events=events,
        replay=resolved_replay,
        issues=issues,
    )


def catalog(*trials: Any, issues: tuple[InspectionIssue, ...] = ()) -> TrialCatalog:
    return TrialCatalog(trials=cast(Any, tuple(trials)), issues=issues)


def document(model: WatchViewModel) -> dict[str, Any]:
    assert dataclasses.is_dataclass(model)
    value = json.loads(
        json.dumps(
            dataclasses.asdict(model),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    assert isinstance(value, dict)
    return value


def assert_strict_allowlist(value: dict[str, Any]) -> None:
    assert set(value) == {
        "backend",
        "catalog_integrity",
        "catalog_issues",
        "more_trials_omitted",
        "read_at",
        "requested_trial_missing",
        "selected_trial_omitted",
        "selected_trial_number",
        "trials",
    }
    assert value["backend"] in {"jsonl", "sqlite"}
    assert value["catalog_integrity"] in ALLOWED_INTEGRITY
    assert type(value["read_at"]) is float and math.isfinite(value["read_at"])
    assert type(value["more_trials_omitted"]) is bool
    assert type(value["requested_trial_missing"]) is bool
    assert type(value["selected_trial_omitted"]) is bool
    assert value["selected_trial_number"] is None or (
        type(value["selected_trial_number"]) is int
        and value["selected_trial_number"] >= 1
    )
    assert isinstance(value["catalog_issues"], list)
    for issue in value["catalog_issues"]:
        assert set(issue) == {"code", "message"}
        assert issue["code"] in ALLOWED_ISSUE_CODES
        assert isinstance(issue["message"], str) and issue["message"]
    assert isinstance(value["trials"], list)
    for trial in value["trials"]:
        assert set(trial) == {
            "trial_number",
            "integrity",
            "lifecycle",
            "detail",
            "issue",
            "selected",
        }
        assert type(trial["trial_number"]) is int and trial["trial_number"] >= 1
        assert trial["integrity"] in ALLOWED_INTEGRITY
        assert trial["lifecycle"] in ALLOWED_LIFECYCLE
        assert type(trial["selected"]) is bool
        issue = trial["issue"]
        if issue is not None:
            assert set(issue) == {"code", "message"}
            assert issue["code"] in ALLOWED_ISSUE_CODES
            assert isinstance(issue["message"], str) and issue["message"]
        detail = trial["detail"]
        if detail is None:
            continue
        assert set(detail) == {
            "agents",
            "arms",
            "cache_heat",
            "completed_agents",
            "evidence",
            "reviews",
            "resolutions",
            "delivery_recorded",
            "mechanical_observations",
            "model_observations",
            "human_observations",
        }
        assert type(detail["delivery_recorded"]) is bool
        cache_heat = detail["cache_heat"]
        assert cache_heat is None or set(cache_heat) == {
            "state",
            "activity_confidence",
            "deadline_at",
            "seconds_remaining",
        }
        if cache_heat is not None:
            assert cache_heat["state"] in {
                "confirmed",
                "estimated",
                "elapsed",
                "unknown",
                "unsupported",
            }
            assert cache_heat["deadline_at"] is None or type(
                cache_heat["deadline_at"]
            ) is float
            assert cache_heat["seconds_remaining"] is None or type(
                cache_heat["seconds_remaining"]
            ) is int
        for count_name in (
            "arms",
            "completed_agents",
            "evidence",
            "reviews",
            "resolutions",
            "mechanical_observations",
            "model_observations",
            "human_observations",
        ):
            assert set(detail[count_name]) == {"value", "more_omitted"}
            assert type(detail[count_name]["value"]) is int
            assert 0 <= detail[count_name]["value"] <= 256
            assert type(detail[count_name]["more_omitted"]) is bool
        assert isinstance(detail["agents"], list)
        for agent in detail["agents"]:
            assert set(agent) == {"position", "completion_recorded"}
            assert type(agent["position"]) is int
            assert 0 <= agent["position"] < 256
            assert type(agent["completion_recorded"]) is bool


@pytest.mark.parametrize(
    "lifecycle",
    ["started", "evidenced", "unresolved", "resolved", "delivered"],
)
def test_valid_projection_uses_only_replay_lifecycle(lifecycle: str) -> None:
    raw_id = f"hidden-{lifecycle}"
    source = inspection(raw_id, replay_value=lifecycle_replay(raw_id, lifecycle))

    value = document(
        WatchProjector().project(
            catalog(source), backend="jsonl", read_at=10.0,
        )
    )

    assert_strict_allowlist(value)
    assert value["trials"][0]["integrity"] == "valid"
    assert value["trials"][0]["lifecycle"] == lifecycle


def test_view_model_is_a_strict_positive_allowlist_and_recursively_blind() -> None:
    marker = BLIND_LEAK_SENTINEL
    arm_id = f"{marker}:arm"
    raw = hidden_blob(marker)
    source_replay = replay(
        marker,
        arms=(
            {
                **raw,
                "arm_id": arm_id,
                "arm_ordinal": 0,
            },
        ),
        completed=(
            {
                **raw,
                "arm_id": arm_id,
                "candidate_id": f"{marker}:candidate",
                "status": f"{marker}:running",
            },
        ),
        evidence_count=1,
        review_count=1,
        resolution=True,
        delivered=True,
        hidden=raw,
    )
    raw_event = {
        "schema_version": 999,
        "trial_id": marker,
        "sequence": 999,
        "event_type": marker,
        "timestamp": 999.0,
        "payload": hidden_blob(marker),
        "idempotency_key": marker,
    }
    source = inspection(
        marker,
        replay_value=source_replay,
        events=(raw_event,),
        issues=(
            InspectionIssue(
                code=marker,
                message=marker,
                trial_id=marker,
                sequence=999,
                event_type=marker,
            ),
        ),
    )
    source_catalog = catalog(
        source,
        issues=(
            InspectionIssue(
                code=marker,
                message=marker,
                trial_id=marker,
                sequence=999,
                event_type=marker,
            ),
        ),
    )

    projector = WatchProjector()
    model = projector.project(
        source_catalog,
        backend="jsonl",
        read_at=123.0,
        selected_trial_id=marker,
    )
    value = document(model)
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True)

    assert_strict_allowlist(value)
    assert marker not in encoded
    assert marker.encode().hex() not in encoded.lower()
    assert base64.b64encode(marker.encode()).decode() not in encoded
    assert marker not in repr(projector)
    assert marker not in repr(model)
    assert marker not in repr(watch_fingerprint(model))
    assert value == {
        "backend": "jsonl",
        "catalog_integrity": "corrupt",
        "catalog_issues": [
            {
                "code": "inspection_incomplete",
                "message": "The persisted trial could not be fully inspected.",
            },
        ],
        "more_trials_omitted": False,
        "read_at": 123.0,
        "requested_trial_missing": False,
        "selected_trial_omitted": False,
        "selected_trial_number": 1,
        "trials": [
            {
                "trial_number": 1,
                "integrity": "valid",
                "lifecycle": "delivered",
                "detail": {
                    "agents": [
                        {"position": 0, "completion_recorded": True},
                    ],
                    "arms": {"value": 1, "more_omitted": False},
                    "cache_heat": {
                        "state": "unknown",
                        "activity_confidence": None,
                        "deadline_at": None,
                        "seconds_remaining": None,
                    },
                    "completed_agents": {"value": 1, "more_omitted": False},
                    "evidence": {"value": 1, "more_omitted": False},
                    "reviews": {"value": 1, "more_omitted": False},
                    "resolutions": {"value": 1, "more_omitted": False},
                    "delivery_recorded": True,
                    "mechanical_observations": {"value": 0, "more_omitted": False},
                    "model_observations": {"value": 0, "more_omitted": False},
                    "human_observations": {"value": 0, "more_omitted": False},
                },
                "issue": None,
                "selected": True,
            },
        ],
    }


class BoundaryInspection:
    """Inspection-like input whose untrusted post-boundary fields explode on read."""

    def __init__(
        self,
        trial_id: str,
        integrity: str,
        replay_value: TrialReplay | None,
        issue: InspectionIssue | tuple[InspectionIssue, ...],
    ) -> None:
        self.trial_id = trial_id
        self.integrity = integrity
        self.status = BLIND_LEAK_SENTINEL
        self.replay = replay_value
        self.issues = issue if isinstance(issue, tuple) else (issue,)

    @property
    def events(self) -> object:
        raise AssertionError("post-boundary TrialInspection.events was read")

    @property
    def summary(self) -> object:
        raise AssertionError("raw TrialInspection.summary was read")


def test_unsupported_uses_only_verified_replay_prefix_and_corrupt_suppresses_detail() -> None:
    prefix_id = "hidden-prefix-id"
    prefix = replay(
        prefix_id,
        arms=(
            {"arm_id": "hidden-arm", "arm_ordinal": 0, "name": BLIND_LEAK_SENTINEL},
        ),
        completed=(
            {"arm_id": "hidden-arm", "status": BLIND_LEAK_SENTINEL},
        ),
        evidence_count=1,
    )
    unsupported = BoundaryInspection(
        prefix_id,
        "unsupported",
        prefix,
        InspectionIssue(
            code="unsupported_event_schema",
            message=f"future message {BLIND_LEAK_SENTINEL}",
            trial_id=BLIND_LEAK_SENTINEL,
            sequence=2,
            event_type=BLIND_LEAK_SENTINEL,
        ),
    )
    corrupt = BoundaryInspection(
        "hidden-corrupt-id",
        "corrupt",
        None,
        InspectionIssue(
            code="invalid_replay",
            message=f"corrupt message {BLIND_LEAK_SENTINEL}",
            trial_id=BLIND_LEAK_SENTINEL,
            sequence=999,
            event_type=BLIND_LEAK_SENTINEL,
        ),
    )

    value = document(
        WatchProjector().project(
            catalog(unsupported, corrupt), backend="sqlite", read_at=7.0,
        )
    )
    rows = {row["integrity"]: row for row in value["trials"]}

    assert_strict_allowlist(value)
    assert BLIND_LEAK_SENTINEL not in json.dumps(value, sort_keys=True)
    assert rows["partial"]["lifecycle"] == "evidenced"
    assert rows["partial"]["detail"]["agents"] == [
        {"position": 0, "completion_recorded": True},
    ]
    assert rows["partial"]["detail"]["evidence"]["value"] == 1
    assert rows["partial"]["issue"]["code"] == "unsupported_event_schema"
    assert rows["corrupt"]["lifecycle"] == "unknown"
    assert rows["corrupt"]["detail"] is None
    assert rows["corrupt"]["issue"]["code"] == "invalid_replay"


def test_unsupported_without_verified_prefix_has_no_lifecycle_or_agent_detail() -> None:
    source = BoundaryInspection(
        "hidden-no-prefix",
        "unsupported",
        None,
        InspectionIssue(
            code="unsupported_event",
            message=BLIND_LEAK_SENTINEL,
            trial_id=BLIND_LEAK_SENTINEL,
            sequence=1,
            event_type=BLIND_LEAK_SENTINEL,
        ),
    )

    row = document(
        WatchProjector().project(
            catalog(source), backend="jsonl", read_at=1.0,
        )
    )["trials"][0]

    assert row["integrity"] == "partial"
    assert row["lifecycle"] == "unknown"
    assert row["detail"] is None
    assert row["issue"]["code"] == "unsupported_event"
    assert BLIND_LEAK_SENTINEL not in json.dumps(row, sort_keys=True)


def test_post_boundary_issue_append_cannot_change_projection_or_fingerprint() -> None:
    prefix = replay(
        "raw-boundary",
        arms=({"arm_id": "raw-arm", "arm_ordinal": 0},),
        timestamp=4.0,
    )
    boundary = InspectionIssue(
        code=BLIND_LEAK_SENTINEL,
        message=BLIND_LEAK_SENTINEL,
        sequence=2,
        event_type=BLIND_LEAK_SENTINEL,
    )
    later_known_looking = InspectionIssue(
        code="unsupported_event",
        message=f"later {BLIND_LEAK_SENTINEL}",
        sequence=1,
        event_type="trial.started",
    )
    first = BoundaryInspection("raw-boundary", "unsupported", prefix, boundary)
    appended = BoundaryInspection(
        "raw-boundary",
        "unsupported",
        prefix,
        (boundary, later_known_looking),
    )

    before = WatchProjector().project(
        catalog(first), backend="jsonl", read_at=1.0,
    )
    after = WatchProjector().project(
        catalog(appended), backend="jsonl", read_at=2.0,
    )

    assert before.trials[0].issue is not None
    assert before.trials[0].issue.code == "inspection_incomplete"
    assert document(before) | {"read_at": 2.0} == document(after)
    assert watch_fingerprint(before) == watch_fingerprint(after)


def test_partial_replay_cannot_retain_a_post_boundary_event() -> None:
    valid = inspection(
        "raw-valid",
        replay_value=lifecycle_replay("raw-valid", "started", timestamp=50.0),
    )

    def forged_partial(timestamp: float) -> BoundaryInspection:
        prefix = replay(
            "raw-partial",
            arms=({"arm_id": "raw-arm", "arm_ordinal": 0},),
            timestamp=10.0,
        )
        future = event(
            "raw-partial",
            2,
            "arm.completed",
            {
                "arm_id": "raw-arm",
                "candidate_id": BLIND_LEAK_SENTINEL,
                "status": BLIND_LEAK_SENTINEL,
            },
            timestamp=timestamp,
        )
        forged = dataclasses.replace(prefix, events=prefix.events + (future,))
        return BoundaryInspection(
            "raw-partial",
            "unsupported",
            forged,
            InspectionIssue(
                code="unsupported_event_schema",
                message=BLIND_LEAK_SENTINEL,
                sequence=2,
                event_type=BLIND_LEAK_SENTINEL,
            ),
        )

    before = WatchProjector().project(
        catalog(forged_partial(100.0), valid),
        backend="jsonl",
        read_at=1.0,
    )
    after = WatchProjector().project(
        catalog(forged_partial(0.0), valid),
        backend="jsonl",
        read_at=2.0,
    )

    assert [(trial.integrity, trial.lifecycle) for trial in before.trials] == [
        ("valid", "started"),
        ("partial", "unknown"),
    ]
    assert document(before) | {"read_at": 2.0} == document(after)
    assert watch_fingerprint(before) == watch_fingerprint(after)
    assert BLIND_LEAK_SENTINEL not in repr(before)


def test_sorting_uses_only_verified_prefix_timestamps() -> None:
    older = BoundaryInspection(
        "raw-older",
        "unsupported",
        lifecycle_replay("raw-older", "evidenced", timestamp=10.0),
        InspectionIssue(
            code="unsupported_event",
            message=BLIND_LEAK_SENTINEL,
            sequence=2,
        ),
    )
    newer = BoundaryInspection(
        "raw-newer",
        "unsupported",
        lifecycle_replay("raw-newer", "started", timestamp=20.0),
        InspectionIssue(
            code="unsupported_event",
            message=BLIND_LEAK_SENTINEL,
            sequence=2,
        ),
    )

    model = WatchProjector().project(
        catalog(older, newer), backend="jsonl", read_at=1.0,
    )

    assert [trial.lifecycle for trial in model.trials] == ["started", "evidenced"]
    assert [trial.trial_number for trial in model.trials] == [1, 2]


@pytest.mark.parametrize(
    "malformation",
    ["mismatched_id", "missing_event", "split_started", "bad_resolution"],
)
def test_self_inconsistent_replay_objects_fail_closed(malformation: str) -> None:
    source_replay = replay(
        "raw-consistent",
        arms=({"arm_id": "raw-arm", "arm_ordinal": 0},),
    )
    if malformation == "mismatched_id":
        source_replay = dataclasses.replace(source_replay, trial_id="raw-other")
    elif malformation == "missing_event":
        source_replay = dataclasses.replace(
            source_replay,
            events=(cast(Any, None),),
        )
    elif malformation == "split_started":
        source_replay = dataclasses.replace(
            source_replay,
            started=cast(Any, {"arms": [BLIND_LEAK_SENTINEL]}),
        )
    else:
        source_replay = dataclasses.replace(
            source_replay,
            resolutions=(cast(Any, object()),),
        )

    source = TrialInspection(
        trial_id="raw-consistent",
        integrity="valid",
        status="started",
        events=(),
        replay=source_replay,
    )

    model = WatchProjector().project(
        catalog(source),
        backend="jsonl",
        read_at=1.0,
    )
    row = model.trials[0]

    assert row.integrity == "corrupt"
    assert row.lifecycle == "unknown"
    assert row.detail is None
    assert row.issue is not None
    assert row.issue.code == "inspection_incomplete"
    assert BLIND_LEAK_SENTINEL not in repr(model)


def test_safe_issue_text_is_canned_and_unknown_issue_codes_become_generic() -> None:
    first = inspection(
        "first",
        integrity="unsupported",
        replay_value=replay("first"),
        issues=(
            InspectionIssue(
                code="unsupported_event",
                message=f"first raw message {BLIND_LEAK_SENTINEL}",
                trial_id=BLIND_LEAK_SENTINEL,
                sequence=2,
            ),
            InspectionIssue(
                code=BLIND_LEAK_SENTINEL,
                message=BLIND_LEAK_SENTINEL,
                trial_id=BLIND_LEAK_SENTINEL,
            ),
        ),
    )
    second = inspection(
        "second",
        integrity="unsupported",
        replay_value=replay("second"),
        issues=(
            InspectionIssue(
                code="unsupported_event",
                message="a completely different raw message",
                trial_id="another hidden id",
                sequence=2,
            ),
        ),
    )
    third = inspection(
        "third",
        integrity="unsupported",
        replay_value=replay("third"),
        issues=(
            InspectionIssue(
                code=BLIND_LEAK_SENTINEL,
                message=BLIND_LEAK_SENTINEL,
                trial_id=BLIND_LEAK_SENTINEL,
                sequence=2,
            ),
        ),
    )

    rows = document(
        WatchProjector().project(
            catalog(first, second, third), backend="jsonl", read_at=1.0,
        )
    )["trials"]

    assert rows[0]["issue"] == rows[1]["issue"]
    assert rows[0]["issue"]["code"] == "unsupported_event"
    assert rows[2]["issue"]["code"] == "inspection_incomplete"
    assert BLIND_LEAK_SENTINEL not in json.dumps(rows, sort_keys=True)


def test_trial_labels_survive_reordering_insertion_selection_and_removal() -> None:
    projector = WatchProjector()
    trial_a = inspection(
        "raw-a", replay_value=lifecycle_replay("raw-a", "started", timestamp=20),
    )
    trial_b = inspection(
        "raw-b", replay_value=lifecycle_replay("raw-b", "evidenced", timestamp=10),
    )
    first = document(
        projector.project(
            catalog(trial_a, trial_b),
            backend="jsonl",
            read_at=1.0,
            selected_trial_id="raw-b",
        )
    )
    first_by_lifecycle = {
        row["lifecycle"]: row["trial_number"] for row in first["trials"]
    }
    assert first_by_lifecycle == {"started": 1, "evidenced": 2}
    assert first["selected_trial_number"] == 2

    reordered_b = inspection(
        "raw-b", replay_value=lifecycle_replay("raw-b", "evidenced", timestamp=30),
    )
    reordered_a = inspection(
        "raw-a", replay_value=lifecycle_replay("raw-a", "started", timestamp=5),
    )
    trial_c = inspection(
        "raw-c", replay_value=lifecycle_replay("raw-c", "resolved", timestamp=1),
    )
    second = document(
        projector.project(
            catalog(reordered_b, reordered_a, trial_c),
            backend="jsonl",
            read_at=2.0,
            selected_trial_id="raw-a",
        )
    )
    second_by_lifecycle = {
        row["lifecycle"]: row["trial_number"] for row in second["trials"]
    }
    assert second_by_lifecycle == {
        "evidenced": 2,
        "started": 1,
        "resolved": 3,
    }
    assert second["selected_trial_number"] == 1

    trial_d = inspection(
        "raw-d", replay_value=lifecycle_replay("raw-d", "unresolved", timestamp=1),
    )
    third = document(
        projector.project(
            catalog(trial_c, trial_d), backend="jsonl", read_at=3.0,
        )
    )
    third_by_lifecycle = {
        row["lifecycle"]: row["trial_number"] for row in third["trials"]
    }
    assert third_by_lifecycle == {"resolved": 3, "unresolved": 4}
    assert third["selected_trial_number"] is None


def test_duplicate_trial_id_collapses_to_one_failure_closed_row() -> None:
    duplicate_id = "raw-duplicate"
    first = inspection(
        duplicate_id,
        replay_value=lifecycle_replay(duplicate_id, "started", timestamp=20.0),
    )
    second = inspection(
        duplicate_id,
        replay_value=lifecycle_replay(duplicate_id, "evidenced", timestamp=10.0),
    )

    model = WatchProjector().project(
        catalog(first, second),
        backend="jsonl",
        read_at=1.0,
        selected_trial_id=duplicate_id,
    )

    assert len(model.trials) == 1
    assert model.trials[0].integrity == "corrupt"
    assert model.trials[0].lifecycle == "unknown"
    assert model.trials[0].detail is None
    assert model.trials[0].selected is True
    assert model.selected_trial_number == model.trials[0].trial_number
    assert model.selected_trial_omitted is False
    assert model.requested_trial_missing is False


def test_missing_and_offscreen_selection_are_safe_structural_state() -> None:
    projector = WatchProjector()
    missing = projector.project(
        catalog(inspection("raw-present")),
        backend="jsonl",
        read_at=1.0,
        selected_trial_id="raw-missing",
    )

    assert missing.requested_trial_missing is True
    assert missing.selected_trial_number is None
    assert missing.selected_trial_omitted is False
    assert all(not trial.selected for trial in missing.trials)
    assert watch_fingerprint(missing) == watch_fingerprint(
        WatchProjector().project(
            catalog(inspection("raw-present")),
            backend="jsonl",
            read_at=99.0,
        )
    )

    many = tuple(inspection(f"raw-{index:03d}") for index in range(257))
    offscreen = projector.project(
        catalog(*many),
        backend="jsonl",
        read_at=2.0,
        selected_trial_id="raw-256",
    )

    assert offscreen.more_trials_omitted is True
    assert offscreen.requested_trial_missing is False
    assert offscreen.selected_trial_number is None
    assert offscreen.selected_trial_omitted is True
    assert all(not trial.selected for trial in offscreen.trials)


def test_catalog_issues_are_deduplicated_and_never_echo_raw_messages() -> None:
    issues = (
        InspectionIssue(code="orphan_event", message=BLIND_LEAK_SENTINEL),
        InspectionIssue(code="orphan_event", message="different hidden message"),
        InspectionIssue(
            code=BLIND_LEAK_SENTINEL,
            message=BLIND_LEAK_SENTINEL,
            trial_id=BLIND_LEAK_SENTINEL,
        ),
    )

    model = WatchProjector().project(
        catalog(issues=issues), backend="sqlite", read_at=1.0,
    )

    assert [issue.code for issue in model.catalog_issues] == [
        "inspection_incomplete",
        "orphan_event",
    ]
    assert model.catalog_integrity == "corrupt"
    assert BLIND_LEAK_SENTINEL not in repr(model)


def test_agent_labels_use_bounded_sorted_positions_not_raw_ordinals() -> None:
    huge = 10**1000
    arms = (
        {"arm_id": "huge", "arm_ordinal": huge},
        {"arm_id": "negative-first", "arm_ordinal": -huge},
        {"arm_id": "negative-second", "arm_ordinal": -huge + 1},
        {"arm_id": "middle", "arm_ordinal": 5},
    )
    completed = (
        {"arm_id": "huge", "status": "running"},
        {"arm_id": "negative-second", "status": "failed"},
    )
    source = inspection(
        "ordinal-hidden",
        replay_value=replay("ordinal-hidden", arms=arms, completed=completed),
    )

    model = WatchProjector().project(
        catalog(source), backend="jsonl", read_at=1.0,
    )
    detail = model.trials[0].detail
    assert detail is not None
    agents = detail.agents

    assert [(agent.label, agent.completion_recorded) for agent in agents] == [
        ("Agent A", False),
        ("Agent B", True),
        ("Agent C", False),
        ("Agent D", True),
    ]
    assert all(len(agent.label) <= len("Agent IV") for agent in agents)
    encoded = json.dumps(document(model), sort_keys=True)
    assert str(huge) not in encoded
    assert "running" not in encoded
    assert "failed" not in encoded


def test_trial_and_agent_collections_are_capped_at_256_with_boolean_omission_flags() -> None:
    oversized_trials = tuple(
        inspection(f"raw-trial-{index}") for index in range(258)
    )
    trial_value = document(
        WatchProjector().project(
            catalog(*oversized_trials), backend="jsonl", read_at=1.0,
        )
    )
    assert len(trial_value["trials"]) == 256
    assert trial_value["more_trials_omitted"] is True
    assert type(trial_value["more_trials_omitted"]) is bool

    arms = tuple(
        {"arm_id": f"hidden-arm-{index}", "arm_ordinal": index}
        for index in range(258)
    )
    arm_source = inspection(
        "oversized-arm-trial",
        replay_value=replay("oversized-arm-trial", arms=arms),
    )
    arm_model = WatchProjector().project(
        catalog(arm_source), backend="jsonl", read_at=1.0,
    )
    detail = arm_model.trials[0].detail
    assert detail is not None
    assert len(detail.agents) == 256
    assert detail.arms.value == 256
    assert detail.arms.more_omitted is True
    assert detail.agents[0].label == "Agent A"
    assert detail.agents[25].label == "Agent Z"
    assert detail.agents[26].label == "Agent AA"
    assert detail.agents[-1].label == "Agent IV"


def test_counts_are_bounded_and_duplicate_phase_completions_count_once() -> None:
    arm_id = "raw-arm"
    source_replay = replay(
        "raw-counts",
        arms=({"arm_id": arm_id, "arm_ordinal": 0},),
        completed=tuple(
            {"arm_id": arm_id, "phase": f"hidden-phase-{index}"}
            for index in range(258)
        ),
    )
    source_replay = dataclasses.replace(
        source_replay,
        evidence_bundles=tuple(cast(Any, object()) for _ in range(258)),
        reviews=tuple(cast(Any, {}) for _ in range(258)),
        resolutions=tuple(
            cast(Any, HiddenResolution(resolved=True)) for _ in range(258)
        ),
    )

    model = WatchProjector().project(
        catalog(inspection("raw-counts", replay_value=source_replay)),
        backend="jsonl",
        read_at=1.0,
    )
    detail = model.trials[0].detail

    assert detail is not None
    assert detail.completed_agents.value == 1
    assert detail.completed_agents.more_omitted is False
    assert detail.agents[0].completion_recorded is True
    for count in (detail.evidence, detail.reviews, detail.resolutions):
        assert count.value == 256
        assert count.more_omitted is True


def test_legacy_scalar_arms_keep_declaration_order() -> None:
    source = inspection(
        "raw-legacy",
        replay_value=replay(
            "raw-legacy",
            arms=("raw-z", "raw-a"),
            completed=({"arm_id": "raw-a", "status": BLIND_LEAK_SENTINEL},),
        ),
    )

    model = WatchProjector().project(
        catalog(source), backend="jsonl", read_at=1.0,
    )
    detail = model.trials[0].detail

    assert detail is not None
    assert [agent.completion_recorded for agent in detail.agents] == [False, True]


def test_duplicate_legacy_arm_ids_fail_closed_instead_of_double_counting() -> None:
    duplicate_id = "raw-duplicate-arm"
    source = inspection(
        "raw-legacy-duplicate",
        replay_value=replay(
            "raw-legacy-duplicate",
            arms=(duplicate_id, duplicate_id),
            completed=({"arm_id": duplicate_id},),
        ),
    )

    model = WatchProjector().project(
        catalog(source), backend="jsonl", read_at=1.0,
    )
    row = model.trials[0]

    assert row.integrity == "corrupt"
    assert row.lifecycle == "unknown"
    assert row.detail is None
    assert row.issue is not None
    assert row.issue.code == "inspection_incomplete"


def hidden_variant(
    marker: str,
    *,
    completed: bool,
    selected: bool,
    read_at: float,
) -> WatchViewModel:
    arm_id = f"{marker}:arm"
    source = inspection(
        f"{marker}:trial",
        replay_value=replay(
            f"{marker}:trial",
            arms=(
                {
                    **hidden_blob(marker),
                    "arm_id": arm_id,
                    "arm_ordinal": 0,
                },
            ),
            completed=(
                ({"arm_id": arm_id, "status": marker},) if completed else ()
            ),
            hidden=hidden_blob(marker),
        ),
        issues=(
            InspectionIssue(
                code="unsupported_event",
                message=marker,
                trial_id=marker,
                event_type=marker,
            ),
        ),
    )
    return WatchProjector().project(
        catalog(source),
        backend="jsonl",
        read_at=read_at,
        selected_trial_id=(f"{marker}:trial" if selected else None),
    )


def test_safe_fingerprint_ignores_hidden_data_read_time_and_selection() -> None:
    first = hidden_variant("hidden-one", completed=False, selected=False, read_at=1.0)
    hidden_only_change = hidden_variant(
        "hidden-two", completed=False, selected=True, read_at=999.0,
    )

    first_digest = watch_fingerprint(first)
    second_digest = watch_fingerprint(hidden_only_change)

    assert first_digest == second_digest
    assert isinstance(first_digest, tuple) and first_digest
    assert BLIND_LEAK_SENTINEL not in repr(first_digest)


def test_safe_fingerprint_changes_when_visible_structure_changes() -> None:
    incomplete = hidden_variant("same-hidden", completed=False, selected=False, read_at=1.0)
    complete = hidden_variant("same-hidden", completed=True, selected=False, read_at=1.0)

    assert watch_fingerprint(incomplete) != watch_fingerprint(complete)


@pytest.mark.parametrize("backend", ["memory", "JSONL", ""])
def test_projection_rejects_non_allowlisted_backends(backend: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        WatchProjector().project(
            catalog(inspection("raw")), backend=backend, read_at=1.0,
        )


def test_string_subclasses_cannot_smuggle_custom_representations() -> None:
    leaky_backend = LeakyStr("jsonl")
    with pytest.raises((TypeError, ValueError)) as captured:
        WatchProjector().project(
            catalog(inspection("raw")),
            backend=cast(Any, leaky_backend),
            read_at=1.0,
        )
    assert BLIND_LEAK_SENTINEL not in str(captured.value)

    source = inspection(
        "raw-partial",
        integrity="unsupported",
        replay_value=replay("raw-partial"),
        issues=(
            InspectionIssue(
                code=cast(Any, LeakyStr("unsupported_event")),
                message=BLIND_LEAK_SENTINEL,
            ),
        ),
    )
    model = WatchProjector().project(
        catalog(source), backend="jsonl", read_at=1.0,
    )

    assert model.trials[0].issue is not None
    assert model.trials[0].issue.code == "inspection_incomplete"
    assert BLIND_LEAK_SENTINEL not in repr(model)
    assert BLIND_LEAK_SENTINEL not in repr(watch_fingerprint(model))


@pytest.mark.parametrize("read_at", [float("nan"), float("inf"), -float("inf"), True])
def test_projection_rejects_non_finite_or_boolean_read_times(read_at: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        WatchProjector().project(
            catalog(inspection("raw")),
            backend="jsonl",
            read_at=cast(Any, read_at),
        )


def test_projection_and_fingerprint_have_no_runtime_or_external_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import arity.auth as auth
    import arity.handlers as handlers
    import arity.runtime as runtime
    import arity.tools as tools

    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("observer projection attempted an external side effect")

    for owner, name in (
        (builtins, "open"),
        (Path, "open"),
        (Path, "read_text"),
        (Path, "write_text"),
        (Path, "mkdir"),
        (socket, "create_connection"),
        (subprocess, "run"),
        (urllib.request, "urlopen"),
        (webbrowser, "open"),
        (runtime.Runtime, "run"),
        (handlers, "create_model_provider"),
        (handlers, "create_default_model_provider"),
        (handlers, "default_record_store"),
        (handlers.LocalToolRunner, "execute"),
        (tools.SandboxToolRunner, "execute"),
        (auth.TokenStore, "load_all"),
        (auth.TokenStore, "save_credential"),
        (auth, "login_google_antigravity"),
        (auth, "login_openai_codex"),
        (auth, "login_xai_grok"),
        (auth, "login_anthropic"),
    ):
        monkeypatch.setattr(owner, name, forbidden)

    source = inspection(
        "raw-side-effect-test",
        replay_value=replay(
            "raw-side-effect-test",
            arms=({"arm_id": "raw-arm", "arm_ordinal": 0},),
        ),
    )
    model = WatchProjector().project(
        catalog(source), backend="jsonl", read_at=1.0,
    )

    assert model.trials[0].label == "Trial 1"
    assert watch_fingerprint(model)
