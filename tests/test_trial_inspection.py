from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from gorkbot.evidence import (
    ArtifactEvidence,
    CandidateEvidence,
    Evaluation,
    EvidenceBundle,
    resolve_bundle,
)
from gorkbot.handlers import JsonlRecordStore
from gorkbot.inspection import (
    InspectionIssue,
    TrialCatalog,
    TrialInspection,
    TrialNotFound,
    TrialSummary,
    inspect_trial,
    inspect_trials,
)
from gorkbot.record_readers import JsonlRecordReader, SqliteRecordReader
from gorkbot.stores.sqlite import SqliteRecordStore
from gorkbot.trial_events import TrialEvent, TrialReplay
from gorkbot.types import StoreRecord


INTEGRITY_VALUES = {"valid", "unsupported", "corrupt"}
LIFECYCLE_VALUES = {
    "started",
    "evidenced",
    "unresolved",
    "resolved",
    "delivered",
    "unknown",
}


@contextmanager
def stored_reader(
    root: Path,
    backend: str,
    records: tuple[Mapping[str, Any], ...],
) -> Iterator[JsonlRecordReader | SqliteRecordReader]:
    if backend == "jsonl":
        path = root / "records"
        store = JsonlRecordStore(path)
        for record in records:
            store.append(StoreRecord(kind="trial_event", record=dict(record)))
        reader: JsonlRecordReader | SqliteRecordReader = JsonlRecordReader(path)
    else:
        path = root / "records.sqlite"
        store = SqliteRecordStore(path)
        for record in records:
            store.append(StoreRecord(kind="trial_event", record=dict(record)))
        store.close()
        reader = SqliteRecordReader(path)
    try:
        yield reader
    finally:
        reader.close()


def candidate_bundle(trial_id: str) -> EvidenceBundle:
    return EvidenceBundle.create(
        trial_id=trial_id,
        task_id=trial_id,
        task_name="Kernel comparison",
        brief="Compare two minimal harness arms.",
        candidates=(
            CandidateEvidence.create(
                candidate_id="candidate-a",
                name="A",
                signature="a",
                model="mock",
                provider="mock",
                role="builder",
                harness="minimal",
                tool_runner="local",
                verdict="success",
                rank=1,
                tied_with=("candidate-b",),
                arm_id="arm-a",
                arm_ordinal=0,
                axes={"tier": 3, "hidden_rate": 1.0, "own_rate": 0.0},
                artifacts=(ArtifactEvidence.from_bytes("answer.txt", b"a"),),
            ),
            CandidateEvidence.create(
                candidate_id="candidate-b",
                name="B",
                signature="b",
                model="mock",
                provider="mock",
                role="builder",
                harness="minimal",
                tool_runner="local",
                verdict="success",
                rank=2,
                tied_with=("candidate-a",),
                arm_id="arm-b",
                arm_ordinal=1,
                axes={"tier": 3, "hidden_rate": 1.0, "own_rate": 0.0},
                artifacts=(ArtifactEvidence.from_bytes("answer.txt", b"b"),),
            ),
        ),
    )


def arm_declaration(candidate: CandidateEvidence) -> dict[str, object]:
    return {
        "arm_id": candidate.arm_id,
        "arm_ordinal": candidate.arm_ordinal,
        "name": candidate.name,
        "signature": candidate.signature,
        "model": candidate.model,
        "provider": candidate.provider,
        "role": candidate.role,
        "harness": candidate.harness,
        "tool_runner": candidate.tool_runner,
        "skills": list(candidate.skills),
        "context": candidate.context,
        "context_adapter": candidate.context_adapter,
    }


def arm_completion(candidate: CandidateEvidence) -> dict[str, object]:
    return {
        **arm_declaration(candidate),
        "phase": "trial",
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "tokens_used": candidate.tokens_used,
        "duration_seconds": candidate.duration_seconds,
        "fallbacks": candidate.fallbacks,
    }


def lifecycle_events(
    trial_id: str,
    *,
    resolution: str = "resolved",
    delivered: bool = False,
) -> tuple[TrialEvent, ...]:
    evidence = candidate_bundle(trial_id)
    evaluation = Evaluation.create(
        evidence,
        evaluator_id="judge",
        order=("candidate-b", "candidate-a"),
    )
    evaluations = (evaluation,) if resolution == "resolved" else ()
    decision = resolve_bundle(
        evidence,
        facts_candidate_id="candidate-a",
        facts_tied_with=("candidate-b",),
        evaluations=evaluations,
        expected_evaluator_ids=("judge",),
    )
    candidates = evidence.candidates
    payloads: list[tuple[str, Mapping[str, Any]]] = [
        (
            "trial.started",
            {
                "task_id": trial_id,
                "task_name": "Kernel comparison",
                "brief": "Compare two minimal harness arms.",
                "role": "builder",
                "requested_arity": 5,
                "resolved_arity": 2,
                "hidden_test_hashes": {},
                "arms": [arm_declaration(candidate) for candidate in candidates],
                "evaluator_ids": ["judge"],
            },
        ),
        ("arm.completed", arm_completion(candidates[0])),
        ("arm.completed", arm_completion(candidates[1])),
        ("evidence.frozen", {"bundle": evidence.to_dict()}),
    ]
    if evaluations:
        payloads.append(
            (
                "review.recorded",
                {
                    "evaluator_id": evaluation.evaluator_id,
                    "evidence_hash": evidence.evidence_hash,
                    "status": "completed",
                    "evaluation": evaluation.to_dict(),
                },
            )
        )
    payloads.append(("resolution.recorded", {"resolution": decision.to_dict()}))
    resolution_sequence = len(payloads)
    if delivered:
        payloads.append(
            (
                "delivery.completed",
                {
                    "candidate_id": "candidate-b",
                    "resolution_sequence": resolution_sequence,
                    "resolution_id": decision.resolution_id,
                    "evidence_hash": evidence.evidence_hash,
                    "delivery": {
                        "files": ["answer.txt"],
                        "answer": None,
                        "winner_name": "B",
                        "signature": "b",
                        "delivered": True,
                        "resolution_source": "judge_consensus",
                    },
                },
            )
        )
    return tuple(
        TrialEvent.create(
            trial_id=trial_id,
            sequence=index,
            event_type=event_type,
            payload=payload,
            timestamp=float(index),
        )
        for index, (event_type, payload) in enumerate(payloads, 1)
    )


def records(events: tuple[TrialEvent, ...]) -> tuple[Mapping[str, Any], ...]:
    return tuple(event.to_dict() for event in events)


def started_event(trial_id: str, timestamp: float) -> TrialEvent:
    return TrialEvent.create(
        trial_id=trial_id,
        sequence=1,
        event_type="trial.started",
        payload={"task_id": trial_id, "brief": f"brief for {trial_id}"},
        timestamp=timestamp,
    )


def test_inspection_module_exports_the_public_api() -> None:
    import gorkbot.inspection as inspection

    assert inspection.TrialNotFound is TrialNotFound
    assert inspection.InspectionIssue is InspectionIssue
    assert inspection.TrialSummary is TrialSummary
    assert inspection.TrialInspection is TrialInspection
    assert inspection.TrialCatalog is TrialCatalog
    assert inspection.inspect_trial is inspect_trial
    assert inspection.inspect_trials is inspect_trials


def test_issue_and_missing_trial_have_stable_machine_readable_shapes() -> None:
    issue = InspectionIssue(
        code="invalid_replay",
        message="sequence gap",
        trial_id="trial-1",
        sequence=3,
        event_type="arm.completed",
    )
    assert issue.to_dict() == {
        "code": "invalid_replay",
        "message": "sequence gap",
        "trial_id": "trial-1",
        "sequence": 3,
        "event_type": "arm.completed",
    }

    with pytest.raises(TrialNotFound) as missing:
        raise TrialNotFound("absent")
    assert missing.value.trial_id == "absent"
    assert missing.value.to_dict() == {
        "code": "trial_not_found",
        "message": "trial 'absent' was not found",
        "trial_id": "absent",
    }


def test_valid_inspection_and_catalog_have_exact_v1_json_shapes(tmp_path: Path) -> None:
    events = lifecycle_events("trial-v1", delivered=True)
    encoded = records(events)
    results: list[dict[str, Any]] = []

    for backend in ("jsonl", "sqlite"):
        with stored_reader(tmp_path / backend, backend, encoded) as reader:
            inspection = inspect_trial(reader, "trial-v1")
            catalog = inspect_trials(reader)

        assert isinstance(inspection, TrialInspection)
        assert isinstance(inspection.summary, TrialSummary)
        assert inspection.integrity == "valid"
        assert inspection.status == "delivered"
        assert inspection.integrity in INTEGRITY_VALUES
        assert inspection.status in LIFECYCLE_VALUES
        assert inspection.issues == ()
        assert isinstance(inspection.replay, TrialReplay)
        assert [event["sequence"] for event in inspection.events] == list(
            range(1, len(events) + 1)
        )

        expected_summary = {
            "trial_id": "trial-v1",
            "integrity": "valid",
            "status": "delivered",
            "task_name": "Kernel comparison",
            "brief": "Compare two minimal harness arms.",
            "role": "builder",
            "requested_arity": 5,
            "resolved_arity": 2,
            "completed_arms": 2,
            "event_count": len(events),
            "started_at": 1.0,
            "updated_at": float(len(events)),
            "winner_candidate_id": "candidate-b",
            "resolution_kind": "judge_consensus",
            "issue_count": 0,
        }
        assert inspection.summary.to_dict() == expected_summary

        payload = inspection.to_dict()
        assert set(payload) == {
            "api_version",
            "trial_id",
            "integrity",
            "status",
            "summary",
            "projection",
            "events",
            "issues",
        }
        assert payload["api_version"] == 1
        assert payload["trial_id"] == "trial-v1"
        assert payload["integrity"] == "valid"
        assert payload["status"] == "delivered"
        assert payload["summary"] == expected_summary
        assert payload["events"] == list(encoded)
        assert payload["issues"] == []
        assert payload["projection"]["schema_version"] == 1
        assert payload["projection"]["status"] == "delivered"
        assert payload["projection"]["lifecycle_status"] == "delivered"
        assert "events" not in payload["projection"]

        replay_payload = inspection.replay.to_dict()
        assert replay_payload["schema_version"] == 1
        assert replay_payload["status"] == "delivered"
        assert replay_payload["lifecycle_status"] == "delivered"
        assert [event["sequence"] for event in replay_payload["events"]] == list(
            range(1, len(events) + 1)
        )

        assert isinstance(catalog, TrialCatalog)
        assert catalog.trials == (inspection,)
        assert catalog.summaries == (inspection.summary,)
        assert catalog.get("trial-v1") == inspection
        assert catalog.to_dict() == {
            "api_version": 1,
            "trials": [expected_summary],
            "issues": [],
        }
        json.dumps(payload, allow_nan=False)
        json.dumps(catalog.to_dict(), allow_nan=False)
        results.append(payload)

    assert results[0] == results[1]


@pytest.mark.parametrize(
    ("expected_status", "make_events"),
    [
        ("started", lambda: lifecycle_events("trial-status")[:1]),
        ("evidenced", lambda: lifecycle_events("trial-status")[:4]),
        (
            "unresolved",
            lambda: lifecycle_events("trial-status", resolution="unresolved"),
        ),
        ("resolved", lambda: lifecycle_events("trial-status")),
        ("delivered", lambda: lifecycle_events("trial-status", delivered=True)),
    ],
)
def test_integrity_and_lifecycle_are_independent_dimensions(
    tmp_path: Path,
    expected_status: str,
    make_events: Any,
) -> None:
    with stored_reader(
        tmp_path,
        "jsonl",
        records(make_events()),
    ) as reader:
        inspection = inspect_trial(reader, "trial-status")

    assert inspection.integrity == "valid"
    assert inspection.status == expected_status
    assert inspection.integrity in INTEGRITY_VALUES
    assert inspection.status in LIFECYCLE_VALUES
    assert inspection.replay is not None
    assert inspection.replay.lifecycle_status == expected_status
    assert inspection.replay.status == expected_status


def test_unknown_current_schema_event_is_unsupported_with_best_known_replay(
    tmp_path: Path,
) -> None:
    trial_id = "trial-future-event"
    started = started_event(trial_id, 1)
    unknown = TrialEvent.create(
        trial_id=trial_id,
        sequence=2,
        event_type="future.observed",
        payload={"future": True},
        timestamp=2,
    )
    with stored_reader(
        tmp_path,
        "jsonl",
        (unknown.to_dict(), started.to_dict()),
    ) as reader:
        inspection = inspect_trial(reader, trial_id)

    assert inspection.integrity == "unsupported"
    assert inspection.status == "started"
    assert inspection.replay is not None
    assert inspection.replay.status == "incomplete"
    assert inspection.replay.lifecycle_status == "started"
    assert [event["sequence"] for event in inspection.events] == [1, 2]
    assert [event.sequence for event in inspection.replay.events] == [1, 2]
    assert inspection.issues == (
        InspectionIssue(
            code="unsupported_event",
            message="unsupported trial event type 'future.observed'",
            trial_id=trial_id,
            sequence=2,
            event_type="future.observed",
        ),
    )
    payload = inspection.to_dict()
    assert payload["projection"]["schema_version"] == 1
    assert payload["projection"]["status"] == "incomplete"
    assert payload["projection"]["lifecycle_status"] == "started"


def test_unsupported_event_schema_is_not_misreported_as_corruption(tmp_path: Path) -> None:
    trial_id = "trial-future-schema"
    future = TrialEvent.create(
        trial_id=trial_id,
        sequence=2,
        event_type="future.schema-event",
        payload={"future": True},
        timestamp=2,
    ).to_dict()
    future["schema_version"] = 2
    with stored_reader(
        tmp_path,
        "jsonl",
        (future, started_event(trial_id, 1).to_dict()),
    ) as reader:
        inspection = inspect_trial(reader, trial_id)

    assert inspection.integrity == "unsupported"
    assert inspection.status == "started"
    assert inspection.replay is not None
    assert [event.sequence for event in inspection.replay.events] == [1]
    assert [event["schema_version"] for event in inspection.events] == [1, 2]
    assert inspection.issues == (
        InspectionIssue(
            code="unsupported_event_schema",
            message="unsupported trial event schema version 2",
            trial_id=trial_id,
            sequence=2,
            event_type="future.schema-event",
        ),
    )


def test_tampered_current_schema_envelope_is_corrupt(tmp_path: Path) -> None:
    trial_id = "trial-tampered-envelope"
    tampered = started_event(trial_id, 1).to_dict()
    tampered["timestamp"] = True
    with stored_reader(tmp_path, "jsonl", (tampered,)) as reader:
        inspection = inspect_trial(reader, trial_id)

    assert inspection.integrity == "corrupt"
    assert inspection.status == "unknown"
    assert inspection.replay is None
    assert inspection.events == (tampered,)
    assert inspection.issues[0].code == "invalid_event"
    assert inspection.issues[0].trial_id == trial_id
    assert inspection.issues[0].sequence == 1
    assert inspection.issues[0].event_type == "trial.started"
    assert "timestamp" in inspection.issues[0].message


@pytest.mark.parametrize(
    ("corruption", "message"),
    [("gap", "gaps"), ("conflict", "conflicting")],
)
def test_gap_or_conflicting_duplicate_is_corrupt_and_has_unknown_lifecycle(
    tmp_path: Path,
    corruption: str,
    message: str,
) -> None:
    trial_id = f"trial-{corruption}"
    started = started_event(trial_id, 1)
    if corruption == "gap":
        damaged = TrialEvent.create(
            trial_id=trial_id,
            sequence=3,
            event_type="future.event",
            payload={},
            timestamp=3,
        )
    else:
        damaged = TrialEvent.create(
            trial_id=trial_id,
            sequence=1,
            event_type="trial.started",
            payload={"task_id": trial_id, "brief": "tampered"},
            timestamp=1,
        )
    with stored_reader(
        tmp_path,
        "jsonl",
        (damaged.to_dict(), started.to_dict()),
    ) as reader:
        inspection = inspect_trial(reader, trial_id)

    assert inspection.integrity == "corrupt"
    assert inspection.status == "unknown"
    assert inspection.replay is None
    assert inspection.summary.issue_count == 1
    assert inspection.issues[0].code == "invalid_replay"
    assert message in inspection.issues[0].message
    assert inspection.integrity in INTEGRITY_VALUES
    assert inspection.status in LIFECYCLE_VALUES
    assert inspection.to_dict()["projection"] is None


def test_catalog_retains_corrupt_trials_and_sorts_by_update_then_id(
    tmp_path: Path,
) -> None:
    corrupt_started = started_event("corrupt", 30).to_dict()
    corrupt_gap = TrialEvent.create(
        trial_id="corrupt",
        sequence=3,
        event_type="future.event",
        payload={},
        timestamp=40,
    ).to_dict()
    orphan = started_event("placeholder", 50).to_dict()
    orphan["trial_id"] = ""
    encoded = (
        started_event("older", 10).to_dict(),
        started_event("zeta", 20).to_dict(),
        corrupt_gap,
        orphan,
        started_event("alpha", 20).to_dict(),
        corrupt_started,
    )
    catalogs: list[dict[str, Any]] = []

    for backend in ("jsonl", "sqlite"):
        with stored_reader(tmp_path / backend, backend, encoded) as reader:
            catalog = inspect_trials(reader)

        assert [trial.trial_id for trial in catalog.trials] == [
            "corrupt",
            "alpha",
            "zeta",
            "older",
        ]
        assert [trial.integrity for trial in catalog.trials] == [
            "corrupt",
            "valid",
            "valid",
            "valid",
        ]
        assert catalog.get("corrupt").status == "unknown"
        assert catalog.get("alpha").status == "started"
        assert catalog.issues == (
            InspectionIssue(
                code="orphan_event",
                message="trial event record 4 has no non-empty trial_id",
                sequence=1,
                event_type="trial.started",
            ),
        )
        payload = catalog.to_dict()
        assert set(payload) == {"api_version", "trials", "issues"}
        assert payload["api_version"] == 1
        assert [trial["trial_id"] for trial in payload["trials"]] == [
            "corrupt",
            "alpha",
            "zeta",
            "older",
        ]
        assert payload["issues"] == [catalog.issues[0].to_dict()]
        catalogs.append(payload)

    assert catalogs[0] == catalogs[1]


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_missing_trial_id_raises_trial_not_found(
    tmp_path: Path,
    backend: str,
) -> None:
    with stored_reader(
        tmp_path,
        backend,
        (started_event("present", 1).to_dict(),),
    ) as reader:
        with pytest.raises(TrialNotFound) as missing:
            inspect_trial(reader, "absent")
        with pytest.raises(TrialNotFound):
            inspect_trials(reader).get("absent")

    assert missing.value.trial_id == "absent"
