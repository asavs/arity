"""Acceptance contract for ``gorkbot.watch``.

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

from gorkbot.inspection import InspectionIssue, TrialCatalog, TrialInspection
from gorkbot.trial_events import TrialEvent, TrialReplay
from gorkbot.watch import WatchProjector, WatchViewModel, watch_fingerprint


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
    "unsupported_evaluation_schema",
    "unsupported_resolution_schema",
    "invalid_record",
    "invalid_event",
    "invalid_replay",
}


@dataclass(frozen=True)
class HiddenResolution:
    resolved: bool
    resolution_id: str = BLIND_LEAK_SENTINEL
    candidate_id: str = BLIND_LEAK_SENTINEL
    kind: str = BLIND_LEAK_SENTINEL


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
        "read_at",
        "trials",
        "trials_more_omitted",
        "selected_label",
    }
    assert value["backend"] in {"jsonl", "sqlite"}
    assert type(value["read_at"]) is float and math.isfinite(value["read_at"])
    assert type(value["trials_more_omitted"]) is bool
    assert value["selected_label"] is None or isinstance(value["selected_label"], str)
    assert isinstance(value["trials"], list)
    for trial in value["trials"]:
        assert set(trial) == {
            "label",
            "integrity",
            "lifecycle",
            "agents",
            "agents_more_omitted",
            "evidence_count",
            "review_count",
            "resolution_count",
            "delivery_present",
            "issues",
        }
        assert isinstance(trial["label"], str)
        assert trial["integrity"] in ALLOWED_INTEGRITY
        assert trial["lifecycle"] in ALLOWED_LIFECYCLE
        assert type(trial["agents_more_omitted"]) is bool
        assert type(trial["delivery_present"]) is bool
        for count_name in ("evidence_count", "review_count", "resolution_count"):
            assert type(trial[count_name]) is int
            assert 0 <= trial[count_name] <= 256
        assert isinstance(trial["agents"], list)
        for agent in trial["agents"]:
            assert set(agent) == {"label", "completion_recorded"}
            assert isinstance(agent["label"], str)
            assert type(agent["completion_recorded"]) is bool
        assert isinstance(trial["issues"], list)
        for issue in trial["issues"]:
            assert set(issue) == {"code", "text"}
            assert issue["code"] in ALLOWED_ISSUE_CODES
            assert isinstance(issue["text"], str) and issue["text"]


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

    model = WatchProjector().project(
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
    assert value == {
        "backend": "jsonl",
        "read_at": 123.0,
        "trials": [
            {
                "label": "Trial 1",
                "integrity": "valid",
                "lifecycle": "delivered",
                "agents": [
                    {"label": "Agent A", "completion_recorded": True},
                ],
                "agents_more_omitted": False,
                "evidence_count": 1,
                "review_count": 1,
                "resolution_count": 1,
                "delivery_present": True,
                "issues": [],
            },
        ],
        "trials_more_omitted": False,
        "selected_label": "Trial 1",
    }


class BoundaryInspection:
    """Inspection-like input whose untrusted post-boundary fields explode on read."""

    def __init__(
        self,
        trial_id: str,
        integrity: str,
        replay_value: TrialReplay | None,
        issue: InspectionIssue,
    ) -> None:
        self.trial_id = trial_id
        self.integrity = integrity
        self.status = BLIND_LEAK_SENTINEL
        self.replay = replay_value
        self.issues = (issue,)

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
    assert rows["partial"]["agents"] == [
        {"label": "Agent A", "completion_recorded": True},
    ]
    assert rows["partial"]["evidence_count"] == 1
    assert rows["partial"]["issues"][0]["code"] == "unsupported_event_schema"
    assert rows["corrupt"]["lifecycle"] == "unknown"
    assert rows["corrupt"]["agents"] == []
    assert rows["corrupt"]["evidence_count"] == 0
    assert rows["corrupt"]["review_count"] == 0
    assert rows["corrupt"]["resolution_count"] == 0
    assert rows["corrupt"]["delivery_present"] is False
    assert rows["corrupt"]["issues"][0]["code"] == "invalid_replay"


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
    assert row["agents"] == []
    assert row["evidence_count"] == 0
    assert row["review_count"] == 0
    assert row["resolution_count"] == 0
    assert row["delivery_present"] is False
    assert BLIND_LEAK_SENTINEL not in json.dumps(row, sort_keys=True)


def test_safe_issue_text_is_canned_and_unknown_issue_codes_are_discarded() -> None:
    first = inspection(
        "first",
        integrity="unsupported",
        replay_value=replay("first"),
        issues=(
            InspectionIssue(
                code="unsupported_event",
                message=f"first raw message {BLIND_LEAK_SENTINEL}",
                trial_id=BLIND_LEAK_SENTINEL,
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
            ),
        ),
    )

    rows = document(
        WatchProjector().project(
            catalog(first, second), backend="jsonl", read_at=1.0,
        )
    )["trials"]

    assert rows[0]["issues"] == rows[1]["issues"]
    assert rows[0]["issues"][0]["code"] == "unsupported_event"
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
    first_by_lifecycle = {row["lifecycle"]: row["label"] for row in first["trials"]}
    assert first_by_lifecycle == {"started": "Trial 1", "evidenced": "Trial 2"}
    assert first["selected_label"] == "Trial 2"

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
    second_by_lifecycle = {row["lifecycle"]: row["label"] for row in second["trials"]}
    assert second_by_lifecycle == {
        "evidenced": "Trial 2",
        "started": "Trial 1",
        "resolved": "Trial 3",
    }
    assert second["selected_label"] == "Trial 1"

    trial_d = inspection(
        "raw-d", replay_value=lifecycle_replay("raw-d", "unresolved", timestamp=1),
    )
    third = document(
        projector.project(
            catalog(trial_c, trial_d), backend="jsonl", read_at=3.0,
        )
    )
    third_by_lifecycle = {row["lifecycle"]: row["label"] for row in third["trials"]}
    assert third_by_lifecycle == {"resolved": "Trial 3", "unresolved": "Trial 4"}
    assert third["selected_label"] is None


def test_agent_labels_use_bounded_sorted_positions_not_raw_ordinals() -> None:
    huge = 10**1000
    arms = (
        {"arm_id": "huge", "arm_ordinal": huge},
        {"arm_id": "negative-first", "arm_ordinal": -huge},
        {"arm_id": "negative-second", "arm_ordinal": -huge},
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

    agents = document(
        WatchProjector().project(
            catalog(source), backend="jsonl", read_at=1.0,
        )
    )["trials"][0]["agents"]

    assert agents == [
        {"label": "Agent A", "completion_recorded": False},
        {"label": "Agent B", "completion_recorded": True},
        {"label": "Agent C", "completion_recorded": False},
        {"label": "Agent D", "completion_recorded": True},
    ]
    assert all(len(agent["label"]) <= len("Agent IV") for agent in agents)
    assert str(huge) not in json.dumps(agents)
    assert "running" not in json.dumps(agents)
    assert "failed" not in json.dumps(agents)


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
    assert trial_value["trials_more_omitted"] is True
    assert type(trial_value["trials_more_omitted"]) is bool

    arms = tuple(
        {"arm_id": f"hidden-arm-{index}", "arm_ordinal": index}
        for index in range(258)
    )
    arm_source = inspection(
        "oversized-arm-trial",
        replay_value=replay("oversized-arm-trial", arms=arms),
    )
    arm_row = document(
        WatchProjector().project(
            catalog(arm_source), backend="jsonl", read_at=1.0,
        )
    )["trials"][0]
    assert len(arm_row["agents"]) == 256
    assert arm_row["agents_more_omitted"] is True
    assert arm_row["agents"][0]["label"] == "Agent A"
    assert arm_row["agents"][25]["label"] == "Agent Z"
    assert arm_row["agents"][26]["label"] == "Agent AA"
    assert arm_row["agents"][-1]["label"] == "Agent IV"


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
    assert isinstance(first_digest, str) and first_digest
    assert BLIND_LEAK_SENTINEL not in first_digest


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
    import gorkbot.auth as auth
    import gorkbot.handlers as handlers
    import gorkbot.runtime as runtime
    import gorkbot.tools as tools

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

    assert document(model)["trials"][0]["label"] == "Trial 1"
    assert watch_fingerprint(model)
