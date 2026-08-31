from __future__ import annotations

import json
import runpy
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from arity.cli import main as cli_main
from arity.evidence import (
    ArtifactEvidence,
    CandidateEvidence,
    Evaluation,
    EvidenceBundle,
    resolve_bundle,
)
from arity.handlers import JsonlRecordStore
from arity.inspection import (
    InspectionIssue,
    TrialCatalog,
    TrialInspection,
    TrialNotFound,
    TrialSummary,
    inspect_trial,
    inspect_trials,
)
from arity.record_readers import JsonlRecordReader, SqliteRecordReader
from arity.stores.sqlite import SqliteRecordStore
from arity.trial_events import TrialEvent, TrialReplay
from arity.types import StoreRecord


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
    import arity.inspection as inspection

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


def persist_cli_events(events: tuple[TrialEvent, ...]) -> None:
    store = JsonlRecordStore(Path(".arity/records"))
    for event in events:
        store.append(StoreRecord(kind="trial_event", record=event.to_dict()))


def persist_cli_records(records_to_write: tuple[Mapping[str, Any], ...]) -> None:
    store = JsonlRecordStore(Path(".arity/records"))
    for record in records_to_write:
        store.append(StoreRecord(kind="trial_event", record=dict(record)))


def invoke_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["arity", *arguments])
    exit_code = cli_main()
    output = capsys.readouterr()
    return exit_code, output.out, output.err


def test_empty_cli_catalog_is_read_only_in_human_and_json_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")

    code, stdout, stderr = invoke_cli(monkeypatch, capsys, "trials")
    assert (code, stdout, stderr) == (0, "No persisted trials.\n", "")
    assert not (tmp_path / ".arity").exists()

    code, stdout, stderr = invoke_cli(monkeypatch, capsys, "trials", "--json")
    document = json.loads(stdout)
    assert code == 0
    assert stderr == ""
    assert document == {
        "api_version": 1,
        "command": "trials",
        "result": "ok",
        "data": {"api_version": 1, "trials": [], "issues": []},
        "error": None,
        "warnings": [],
    }
    assert not (tmp_path / ".arity").exists()


def test_cli_list_show_and_replay_share_the_versioned_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    persist_cli_events(lifecycle_events("trial-cli", delivered=True))

    code, stdout, stderr = invoke_cli(monkeypatch, capsys, "trials", "--json")
    catalog = json.loads(stdout)
    assert (code, stderr, catalog["result"]) == (0, "", "ok")
    assert catalog["data"]["trials"][0]["trial_id"] == "trial-cli"
    assert catalog["data"]["trials"][0]["status"] == "delivered"

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "trial-cli", "--json",
    )
    shown = json.loads(stdout)
    assert (code, stderr, shown["result"]) == (0, "", "ok")
    assert shown["data"]["summary"] == catalog["data"]["trials"][0]
    assert shown["data"]["arms"][1]["candidate_id"] == "candidate-b"
    assert shown["data"]["delivery"]["files"] == ["answer.txt"]
    assert '"text"' not in stdout
    assert '"content_base64"' not in stdout
    assert '"output"' not in stdout

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "replay", "trial-cli", "--json",
    )
    replayed = json.loads(stdout)
    assert (code, stderr, replayed["result"]) == (0, "", "ok")
    assert [event["sequence"] for event in replayed["data"]["events"]] == list(range(1, 8))
    assert replayed["data"]["projection"]["lifecycle_status"] == "delivered"
    assert '"text"' in stdout
    assert '"content_base64"' in stdout


def test_cli_human_show_and_replay_are_ansi_free_and_content_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    persist_cli_events(lifecycle_events("trial-human", delivered=True))

    code, shown, stderr = invoke_cli(monkeypatch, capsys, "trial", "show", "trial-human")
    assert (code, stderr) == (0, "")
    assert "Trial trial-human" in shown
    assert "judge_consensus -> candidate-b" in shown
    assert "answer.txt" in shown
    assert "\x1b[" not in shown
    assert "content_base64" not in shown

    code, replayed, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "replay", "trial-human",
    )
    assert (code, stderr) == (0, "")
    assert "trial.started" in replayed
    assert "delivery.completed" in replayed
    assert "\x1b[" not in replayed
    assert "content_base64" not in replayed


def test_cli_exit_codes_distinguish_missing_unsupported_and_corrupt_trials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "missing", "--json",
    )
    missing = json.loads(stdout)
    assert (code, stderr, missing["error"]["code"]) == (3, "", "trial_not_found")

    future = (
        started_event("future", 1),
        TrialEvent.create(
            trial_id="future",
            sequence=2,
            event_type="future.event",
            payload={"node": "new"},
            timestamp=2,
        ),
    )
    persist_cli_events(future)
    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "replay", "future", "--json",
    )
    partial = json.loads(stdout)
    assert (code, stderr, partial["result"]) == (4, "", "partial")
    assert partial["data"]["status"] == "started"
    assert partial["warnings"][0]["code"] == "unsupported_event"

    gap = (
        started_event("gap", 1),
        TrialEvent.create(
            trial_id="gap",
            sequence=3,
            event_type="future.event",
            payload={},
            timestamp=3,
        ),
    )
    persist_cli_events(gap)
    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "replay", "gap", "--json",
    )
    corrupt = json.loads(stdout)
    assert (code, stderr, corrupt["result"]) == (5, "", "error")
    assert corrupt["error"]["code"] == "trial_corrupt"
    assert corrupt["data"]["integrity"] == "corrupt"


def test_cli_reports_physical_store_corruption_as_one_json_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    root = tmp_path / ".arity" / "records"
    root.mkdir(parents=True)
    (root / "trial_event.jsonl").write_text("not-json\n", encoding="utf-8")

    code, stdout, stderr = invoke_cli(monkeypatch, capsys, "trials", "--json")
    document = json.loads(stdout)
    assert (code, stderr, document["result"]) == (5, "", "error")
    assert document["data"] is None
    assert document["error"]["code"] == "record_store_corrupt"


def test_cli_reads_configured_sqlite_without_mutating_the_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "sqlite")
    path = tmp_path / ".arity" / "records.db"
    store = SqliteRecordStore(path)
    store.append(
        StoreRecord(kind="trial_event", record=started_event("sqlite-cli", 1).to_dict())
    )
    store.close()
    before = {
        item.relative_to(tmp_path).as_posix(): (item.read_bytes(), item.stat().st_mtime_ns)
        for item in tmp_path.rglob("*")
        if item.is_file()
    }

    code, stdout, stderr = invoke_cli(monkeypatch, capsys, "trials", "--json")
    document = json.loads(stdout)
    after = {
        item.relative_to(tmp_path).as_posix(): (item.read_bytes(), item.stat().st_mtime_ns)
        for item in tmp_path.rglob("*")
        if item.is_file()
    }

    assert (code, stderr, document["result"]) == (0, "", "ok")
    assert document["data"]["trials"][0]["trial_id"] == "sqlite-cli"
    assert after == before


def test_module_entry_point_propagates_semantic_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    monkeypatch.setattr(
        sys, "argv", ["arity", "trial", "show", "missing", "--json"],
    )

    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("arity", run_name="__main__")
    output = capsys.readouterr()
    assert stopped.value.code == 3
    assert json.loads(output.out)["error"]["code"] == "trial_not_found"
    assert output.err == ""


def test_cli_rejects_an_empty_trial_id_at_the_parser_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["arity", "trial", "show", ""])
    with pytest.raises(SystemExit) as stopped:
        cli_main()
    output = capsys.readouterr()
    assert stopped.value.code == 2
    assert output.out == ""
    assert "non-empty" in output.err


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
    assert inspection.replay.status == "started"
    assert inspection.replay.lifecycle_status == "started"
    assert [event["sequence"] for event in inspection.events] == [1, 2]
    assert [event.sequence for event in inspection.replay.events] == [1]
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
    assert payload["projection"]["status"] == "started"
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


@pytest.mark.parametrize(
    ("sequence", "document_key", "issue_code", "expected_status"),
    [
        (4, "bundle", "unsupported_evidence_schema", "started"),
        (5, "evaluation", "unsupported_evaluation_schema", "evidenced"),
        (6, "resolution", "unsupported_resolution_schema", "evidenced"),
    ],
)
def test_future_nested_contracts_stop_at_the_verified_prefix(
    tmp_path: Path,
    sequence: int,
    document_key: str,
    issue_code: str,
    expected_status: str,
) -> None:
    trial_id = f"future-{document_key}"
    encoded = json.loads(json.dumps(records(lifecycle_events(trial_id))))
    encoded[sequence - 1]["payload"][document_key]["schema_version"] += 1

    with stored_reader(tmp_path, "jsonl", tuple(encoded)) as reader:
        inspection = inspect_trial(reader, trial_id)

    assert inspection.integrity == "unsupported"
    assert inspection.status == expected_status
    assert inspection.replay is not None
    assert [event.sequence for event in inspection.replay.events] == list(range(1, sequence))
    assert [event["sequence"] for event in inspection.events] == list(
        range(1, len(encoded) + 1)
    )
    assert inspection.issues[0].code == issue_code
    assert inspection.issues[0].sequence == sequence


def test_cli_reports_nested_future_schema_as_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    encoded = json.loads(json.dumps(records(lifecycle_events("future-cli"))))
    encoded[5]["payload"]["resolution"]["schema_version"] += 1
    persist_cli_records(tuple(encoded))

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "future-cli", "--json",
    )
    document = json.loads(stdout)

    assert (code, stderr, document["result"]) == (4, "", "partial")
    assert document["data"]["status"] == "evidenced"
    assert document["warnings"][0]["code"] == "unsupported_resolution_schema"


def test_unknown_event_prevents_trusting_later_known_state(tmp_path: Path) -> None:
    trial_id = "unknown-boundary"
    encoded = list(records(lifecycle_events(trial_id, delivered=True)))
    for record in encoded[1:]:
        record["sequence"] += 1
    unknown = TrialEvent.create(
        trial_id=trial_id,
        sequence=2,
        event_type="future.transition",
        payload={"could_change_state": True},
        timestamp=1.5,
    ).to_dict()

    with stored_reader(tmp_path, "jsonl", tuple([encoded[0], unknown, *encoded[1:]])) as reader:
        inspection = inspect_trial(reader, trial_id)

    assert inspection.integrity == "unsupported"
    assert inspection.status == "started"
    assert inspection.replay is not None
    assert [event.sequence for event in inspection.replay.events] == [1]
    assert inspection.replay.delivery is None
    assert len(inspection.events) == 8


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_exact_duplicate_is_retained_in_raw_journal_but_coalesced_for_projection(
    tmp_path: Path,
    backend: str,
) -> None:
    started = started_event("duplicate", 1).to_dict()
    with stored_reader(tmp_path, backend, (started, started)) as reader:
        inspection = inspect_trial(reader, "duplicate")

    assert inspection.integrity == "valid"
    assert len(inspection.events) == 2
    assert inspection.summary.event_count == 2
    assert inspection.replay is not None
    assert len(inspection.replay.events) == 1
    assert len(inspection.to_dict()["events"]) == 2


@pytest.mark.parametrize(
    "malformation",
    ["arms", "bundle", "evaluation", "resolution"],
)
def test_malformed_nested_documents_are_typed_corruption_without_tracebacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    malformation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    trial_id = f"malformed-{malformation}"
    encoded = json.loads(json.dumps(records(lifecycle_events(trial_id))))
    if malformation == "arms":
        encoded = encoded[:1]
        encoded[0]["payload"]["arms"] = 1
    elif malformation == "bundle":
        encoded[3]["payload"]["bundle"] = 1
    elif malformation == "evaluation":
        encoded[4]["payload"]["evaluation"] = 1
    else:
        encoded[5]["payload"]["resolution"] = 1
    persist_cli_records(tuple(encoded))

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "replay", trial_id, "--json",
    )
    document = json.loads(stdout)

    assert (code, stderr, document["result"]) == (5, "", "error")
    assert document["data"]["integrity"] == "corrupt"
    assert document["error"]["code"] == "trial_corrupt"


def test_show_projection_rejects_nested_arm_values_without_leaking_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    event = TrialEvent.create(
        trial_id="nested-arm",
        sequence=1,
        event_type="trial.started",
        timestamp=1,
        payload={
            "brief": "safe",
            "arms": [
                {
                    "arm_id": "arm-a",
                    "arm_ordinal": 0,
                    "name": {"secret": "DO_NOT_LEAK"},
                    "context": {"secret": "DO_NOT_LEAK"},
                }
            ],
        },
    )
    persist_cli_events((event,))

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "nested-arm", "--json",
    )
    document = json.loads(stdout)
    assert (code, stderr, document["result"]) == (5, "", "error")
    assert "DO_NOT_LEAK" not in stdout

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "nested-arm",
    )
    assert (code, stderr) == (5, "")
    assert "DO_NOT_LEAK" not in stdout


def test_show_projection_handles_failed_review_with_non_scalar_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    trial_id = "failed-review"
    base = lifecycle_events(trial_id)[:4]
    review = TrialEvent.create(
        trial_id=trial_id,
        sequence=5,
        event_type="review.recorded",
        timestamp=5,
        payload={
            "evaluator_id": {"secret": "DO_NOT_LEAK"},
            "evidence_hash": candidate_bundle(trial_id).evidence_hash,
            "status": "failed",
            "evaluation": None,
        },
    )
    persist_cli_events((*base, review))

    for arguments in (
        ("trial", "show", trial_id, "--json"),
        ("trial", "show", trial_id),
    ):
        code, stdout, stderr = invoke_cli(monkeypatch, capsys, *arguments)
        assert (code, stderr) == (5, "")
        assert "DO_NOT_LEAK" not in stdout


def test_show_diagnostics_do_not_echo_malformed_nested_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    events = (
        started_event("safe-diagnostic", 1),
        TrialEvent.create(
            trial_id="safe-diagnostic",
            sequence=2,
            event_type="arm.completed",
            timestamp=2,
            payload={
                "phase": {"secret": "TOP_SECRET"},
                "arm_id": "arm-a",
                "candidate_id": "candidate-a",
            },
        ),
    )
    persist_cli_events(events)

    for arguments in (
        ("trial", "show", "safe-diagnostic", "--json"),
        ("trial", "show", "safe-diagnostic"),
    ):
        code, stdout, stderr = invoke_cli(monkeypatch, capsys, *arguments)
        assert (code, stderr) == (5, "")
        assert "TOP_SECRET" not in stdout


def test_minimal_future_evaluation_does_not_require_current_schema_fields(
    tmp_path: Path,
) -> None:
    trial_id = "minimal-future-evaluation"
    encoded = list(records(lifecycle_events(trial_id)))
    encoded[4]["payload"]["evaluation"] = {"schema_version": 2}

    with stored_reader(tmp_path, "jsonl", tuple(encoded)) as reader:
        inspection = inspect_trial(reader, trial_id)

    assert inspection.integrity == "unsupported"
    assert inspection.status == "evidenced"
    assert inspection.issues[0].code == "unsupported_evaluation_schema"


def test_legacy_scalar_arm_is_a_visible_graph_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    event = TrialEvent.create(
        trial_id="legacy-arm",
        sequence=1,
        event_type="trial.started",
        timestamp=1,
        payload={"arms": ["arm-a"]},
    )
    persist_cli_events((event,))

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "legacy-arm", "--json",
    )
    document = json.loads(stdout)
    assert (code, stderr, document["result"]) == (0, "", "ok")
    assert document["data"]["arms"][0]["arm_id"] == "arm-a"
    assert document["data"]["arms"][0]["completion_status"] == "pending"


def test_human_replay_handles_malformed_delivery_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    encoded = json.loads(json.dumps(records(lifecycle_events("bad-delivery", delivered=True))))
    encoded[-1]["payload"]["delivery"]["files"] = 7
    persist_cli_records(tuple(encoded))

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "replay", "bad-delivery",
    )
    assert (code, stderr) == (5, "")
    assert "invalid_replay" in stdout


def test_show_projection_normalizes_legacy_completion_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    started = TrialEvent.create(
        trial_id="legacy-completion",
        sequence=1,
        event_type="trial.started",
        timestamp=1,
        payload={"arms": [{"arm_id": "arm-a", "arm_ordinal": 0}]},
    )
    completed = TrialEvent.create(
        trial_id="legacy-completion",
        sequence=2,
        event_type="arm.completed",
        timestamp=2,
        payload={
            "arm_id": "arm-a",
            "arm_ordinal": 0,
            "candidate_id": "candidate-a",
        },
    )
    persist_cli_events((started, completed))

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "legacy-completion", "--json",
    )
    document = json.loads(stdout)
    completion = document["data"]["arms"][0]["completions"][0]
    assert (code, stderr, document["result"]) == (0, "", "ok")
    assert completion["phase"] == "trial"
    assert completion["status"] == "completed"
    assert completion["duration_seconds"] is None


def test_huge_completion_duration_is_corruption_without_a_renderer_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    started = TrialEvent.create(
        trial_id="huge-completion",
        sequence=1,
        event_type="trial.started",
        timestamp=1,
        payload={"arms": [{"arm_id": "arm-a", "arm_ordinal": 0}]},
    )
    completed = TrialEvent.create(
        trial_id="huge-completion",
        sequence=2,
        event_type="arm.completed",
        timestamp=2,
        payload={
            "arm_id": "arm-a",
            "candidate_id": "candidate-a",
            "duration_seconds": 10**500,
        },
    )
    persist_cli_events((started, completed))

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "huge-completion", "--json",
    )
    document = json.loads(stdout)
    assert (code, stderr, document["result"]) == (5, "", "error")
    assert document["data"]["integrity"] == "corrupt"


def test_human_output_escapes_terminal_controls_and_bidi_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    event = TrialEvent.create(
        trial_id="terminal-safe",
        sequence=1,
        event_type="trial.started",
        timestamp=1,
        payload={"brief": "safe\x1b]0;PWN\x07\u202eend"},
    )
    persist_cli_events((event,))

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "terminal-safe",
    )
    assert (code, stderr) == (0, "")
    assert "\x1b" not in stdout
    assert "\x07" not in stdout
    assert "\u202e" not in stdout
    assert "\\x1b" in stdout
    assert "\\x07" in stdout
    assert "\\u202e" in stdout


def test_machine_json_round_trips_a_persisted_lone_surrogate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    root = tmp_path / ".arity" / "records"
    root.mkdir(parents=True)
    record = started_event("surrogate", 1).to_dict()
    record["payload"]["brief"] = "\ud800"
    encoded = json.dumps(record, ensure_ascii=True)
    (root / "trial_event.jsonl").write_text(encoded + "\n", encoding="ascii")

    code, stdout, stderr = invoke_cli(
        monkeypatch, capsys, "trial", "show", "surrogate", "--json",
    )
    document = json.loads(stdout)
    assert (code, stderr, document["result"]) == (0, "", "ok")
    assert document["data"]["summary"]["brief"] == "\ud800"
    assert "\\ud800" in stdout


def test_future_schema_huge_timestamp_is_partial_across_cli_renderers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARITY_STORE", "jsonl")
    future = {
        "schema_version": 2,
        "trial_id": "huge-future",
        "sequence": 2,
        "event_type": "future.event",
        "timestamp": 10**500,
        "payload": {},
        "idempotency_key": None,
    }
    persist_cli_records((started_event("huge-future", 1).to_dict(), future))

    for arguments in (
        ("trials", "--json"),
        ("trial", "show", "huge-future", "--json"),
        ("trial", "replay", "huge-future", "--json"),
        ("trial", "show", "huge-future"),
        ("trial", "replay", "huge-future"),
    ):
        code, stdout, stderr = invoke_cli(monkeypatch, capsys, *arguments)
        assert (code, stderr) == (4, "")
        assert stdout


@pytest.mark.parametrize("damage", ["gap", "conflict"])
def test_future_event_schema_does_not_bypass_canonical_sequence_validation(
    tmp_path: Path,
    damage: str,
) -> None:
    started = started_event("future-canonical", 1).to_dict()
    future = {
        **started,
        "schema_version": 2,
        "sequence": 3 if damage == "gap" else 1,
        "event_type": "future.event",
        "payload": {},
    }
    with stored_reader(tmp_path, "jsonl", (started, future)) as reader:
        inspection = inspect_trial(reader, "future-canonical")

    assert inspection.integrity == "corrupt"
    assert inspection.status == "unknown"
    assert inspection.issues[0].code == "invalid_replay"


def test_deep_record_is_reported_as_corrupt_instead_of_recursing_out() -> None:
    record = started_event("deep", 1).to_dict()
    nested: list[Any] = []
    for _ in range(1200):
        nested = [nested]
    record["payload"]["nested"] = nested

    class DeepReader:
        def query(self, kind: str, **filters: Any) -> list[dict[str, Any]]:
            return [record]

    inspection = inspect_trial(DeepReader(), "deep")
    assert inspection.integrity == "corrupt"
    assert inspection.issues[0].code == "invalid_record"
