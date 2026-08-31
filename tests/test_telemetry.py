"""Contracts for normalized, durable per-request usage evidence."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from arity.handlers import JsonlRecordStore
from arity.inspection import inspect_trial
from arity.race import RaceConfig, run_race
from arity.stores.sqlite import SqliteRecordStore
from arity.telemetry import (
    CachePolicyHint,
    JournaledModelProvider,
    TokenMeasurement,
    UnsupportedUsageEvidenceSchema,
    UsageEvidence,
    UsageRecordingContext,
    normalize_usage_evidence,
)
from arity.trial_events import TrialEvent, TrialJournal, replay_trial
from arity.types import CallModel, ModelCompleted, ModelFailed
from arity.watch_view_model import WatchProjector


def test_token_measurements_preserve_unknown_reported_zero_and_provenance() -> None:
    unknown = TokenMeasurement(value=None, basis="unavailable")
    zero = TokenMeasurement(value=0, basis="provider_reported")

    assert unknown.to_dict() == {"value": None, "basis": "unavailable"}
    assert zero.to_dict() == {"value": 0, "basis": "provider_reported"}
    assert unknown != zero

    for value in (True, -1, 1.5, "1"):
        with pytest.raises((TypeError, ValueError)):
            TokenMeasurement(value=value, basis="provider_reported")
    with pytest.raises(ValueError, match="basis"):
        TokenMeasurement(value=1, basis="invented")
    with pytest.raises(ValueError, match="unavailable"):
        TokenMeasurement(value=0, basis="unavailable")


def test_usage_evidence_round_trips_exact_versioned_json_and_is_immutable() -> None:
    evidence = UsageEvidence(
        input_tokens=TokenMeasurement(100, "provider_reported"),
        output_tokens=TokenMeasurement(20, "provider_reported"),
        cache_read_tokens=TokenMeasurement(75, "provider_reported"),
        cache_write_tokens=TokenMeasurement(None, "unavailable"),
        evidence_observed_at=12.5,
        provider_timestamp=12.0,
        provider_timestamp_basis="response",
        cache_policy=CachePolicyHint(
            window_seconds=300,
            refresh_on_reuse=True,
            basis="configured",
            clock_basis="request_started",
        ),
    )

    encoded = evidence.to_dict()
    assert encoded["schema_version"] == 1
    assert encoded["measurements"]["input_tokens"]["value"] == 100
    assert UsageEvidence.from_dict(json.loads(json.dumps(encoded))) == evidence
    with pytest.raises(FrozenInstanceError):
        evidence.evidence_observed_at = 99  # type: ignore[misc]

    future = dict(encoded)
    future["schema_version"] = 2
    with pytest.raises(UnsupportedUsageEvidenceSchema) as stopped:
        UsageEvidence.from_dict(future)
    assert stopped.value.schema_version == 2


@pytest.mark.parametrize("bad_time", [True, float("nan"), float("inf"), "12"])
def test_usage_evidence_rejects_nonfinite_or_lossy_timestamps(bad_time: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        UsageEvidence.unknown(evidence_observed_at=bad_time)  # type: ignore[arg-type]


def test_generic_normalizer_understands_current_provider_shapes_without_double_counting() -> None:
    openai = normalize_usage_evidence(
        {
            "input_tokens": 100,
            "output_tokens": 25,
            "input_tokens_details": {"cached_tokens": 80},
        },
        evidence_observed_at=10,
    )
    chat = normalize_usage_evidence(
        {
            "prompt_tokens": 40,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
        evidence_observed_at=11,
    )
    anthropic = normalize_usage_evidence(
        {
            "input_tokens": 30,
            "output_tokens": 7,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 10,
        },
        evidence_observed_at=12,
    )

    assert openai.input_tokens.value == 100
    assert openai.cache_read_tokens.value == 80
    assert chat.cache_read_tokens.value == 0
    assert anthropic.cache_read_tokens.value == 20
    assert anthropic.cache_write_tokens.value == 10
    assert all(
        measurement.basis == "provider_reported"
        for evidence in (openai, chat, anthropic)
        for measurement in (evidence.input_tokens, evidence.output_tokens)
    )


def test_normalizer_marks_cli_estimates_and_never_invents_cache_counts() -> None:
    evidence = normalize_usage_evidence(
        {"prompt_tokens": 10, "completion_tokens": 2, "estimated": True},
        evidence_observed_at=20,
    )

    assert evidence.input_tokens == TokenMeasurement(10, "estimated")
    assert evidence.output_tokens == TokenMeasurement(2, "estimated")
    assert evidence.cache_read_tokens == TokenMeasurement(None, "unavailable")
    assert evidence.cache_write_tokens == TokenMeasurement(None, "unavailable")


class _Provider:
    def __init__(self, result: ModelCompleted | ModelFailed, timeline: list[str]) -> None:
        self.result = result
        self.timeline = timeline

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        self.timeline.append("provider")
        return self.result


class _Journal:
    def __init__(self, timeline: list[str], *, fail: bool = False) -> None:
        self.timeline = timeline
        self.fail = fail
        self.calls: list[tuple[str, dict[str, object], str | None]] = []

    def append(self, event_type: str, payload, *, idempotency_key=None, timestamp=None):
        self.timeline.append("journal")
        if self.fail:
            raise OSError("private persistence detail")
        self.calls.append((event_type, dict(payload), idempotency_key))
        return object()


def _context(journal: _Journal) -> UsageRecordingContext:
    return UsageRecordingContext(
        journal=journal,
        phase="trial",
        arm_id="arm-1",
        actor_kind="candidate",
        actor_ref="arm-1",
        cache_policy=CachePolicyHint(
            window_seconds=300,
            refresh_on_reuse=True,
            basis="configured",
            clock_basis="request_started",
        ),
    )


def test_journaled_provider_persists_normalized_usage_before_publishing_completion() -> None:
    timeline: list[str] = []
    marker = "PRIVATE_MODEL_PROVIDER_OUTPUT"
    journal = _Journal(timeline)
    provider = _Provider(
        ModelCompleted(
            content=marker,
            usage={"prompt_tokens": 10, "completion_tokens": 3, "cached_tokens": 4},
            seat_id=f"private-seat-{marker}",
        ),
        timeline,
    )
    times = iter((100.0, 102.0))
    wrapped = JournaledModelProvider(provider, _context(journal), clock=lambda: next(times))

    result = wrapped.call(CallModel(messages=[{"role": "user", "content": marker}]))

    assert timeline == ["provider", "journal"]
    assert isinstance(result, ModelCompleted)
    assert result.usage_evidence is not None
    event_type, payload, key = journal.calls[0]
    assert event_type == "request.usage_recorded"
    assert key == "request.usage:trial:candidate:arm-1:1"
    assert payload["request_started_at"] == 100.0
    assert payload["evidence"]["evidence_observed_at"] == 102.0
    assert payload["evidence"]["measurements"]["cache_read_tokens"]["value"] == 4
    assert marker not in json.dumps(payload)


def test_journaled_provider_reanchors_supplied_evidence_to_journal_time() -> None:
    timeline: list[str] = []
    supplied = UsageEvidence(
        input_tokens=TokenMeasurement(10, "provider_reported"),
        output_tokens=TokenMeasurement(3, "provider_reported"),
        evidence_observed_at=50.0,
        provider_timestamp=49.0,
        provider_timestamp_basis="response",
    )
    journal = _Journal(timeline)
    wrapped = JournaledModelProvider(
        _Provider(
            ModelCompleted(content="done", usage_evidence=supplied),
            timeline,
        ),
        _context(journal),
        clock=iter((100.0, 102.0)).__next__,
    )

    result = wrapped.call(CallModel(messages=[]))

    assert isinstance(result, ModelCompleted)
    assert result.usage_evidence is not None
    assert result.usage_evidence.evidence_observed_at == 102.0
    assert result.usage_evidence.provider_timestamp == 49.0
    assert journal.calls[0][1]["evidence"]["evidence_observed_at"] == 102.0


def test_journaled_provider_rejects_a_clock_that_moves_before_request_start() -> None:
    timeline: list[str] = []
    journal = _Journal(timeline)
    wrapped = JournaledModelProvider(
        _Provider(ModelCompleted(content="done"), timeline),
        _context(journal),
        clock=iter((2.0, 1.0)).__next__,
    )

    with pytest.raises(ValueError, match="request start time.*observation"):
        wrapped.call(CallModel(messages=[]))

    assert journal.calls == []


def test_journaled_provider_records_safe_failure_without_error_or_identity_text() -> None:
    timeline: list[str] = []
    marker = "PRIVATE_FAILURE_DETAIL"
    journal = _Journal(timeline)
    wrapped = JournaledModelProvider(
        _Provider(ModelFailed(error=marker, seat_id=marker), timeline),
        _context(journal),
        clock=iter((1.0, 2.0)).__next__,
    )

    result = wrapped.call(CallModel(messages=[]))

    assert isinstance(result, ModelFailed)
    payload = journal.calls[0][1]
    assert payload["outcome"] == "failed"
    assert marker not in json.dumps(payload)
    assert all(
        measurement["value"] is None
        for measurement in payload["evidence"]["measurements"].values()
    )


def test_journal_failure_is_fail_closed_instead_of_publishing_unrecorded_usage() -> None:
    timeline: list[str] = []
    wrapped = JournaledModelProvider(
        _Provider(ModelCompleted(content="done", usage={}), timeline),
        _context(_Journal(timeline, fail=True)),
        clock=iter((1.0, 2.0)).__next__,
    )

    with pytest.raises(OSError, match="private persistence detail"):
        wrapped.call(CallModel(messages=[]))
    assert timeline == ["provider", "journal"]


def test_recording_context_rejects_ambiguous_identity_phase_and_policy() -> None:
    journal = _Journal([])
    for changes in (
        {"phase": "other"},
        {"arm_id": ""},
        {"actor_kind": "model"},
        {"actor_ref": ""},
    ):
        values = {
            "journal": journal,
            "phase": "trial",
            "arm_id": "arm-1",
            "actor_kind": "candidate",
            "actor_ref": "arm-1",
        }
        values.update(changes)
        with pytest.raises(ValueError):
            UsageRecordingContext(**values)


def _started_payload() -> dict[str, object]:
    return {
        "task_id": "trial-usage",
        "brief": "private brief",
        "arms": [
            {
                "arm_id": "arm-1",
                "arm_ordinal": 0,
                "name": "private name",
                "model": "private model",
                "provider": "private provider",
                "role": "developer",
                "harness": "arity",
                "tool_runner": "tools",
                "skills": [],
                "context": "fresh",
                "context_adapter": None,
            }
        ],
    }


def _usage_payload(*, ordinal: int = 1) -> dict[str, object]:
    return {
        "phase": "trial",
        "arm_id": "arm-1",
        "actor_kind": "candidate",
        "actor_ref": "arm-1",
        "request_ordinal": ordinal,
        "outcome": "completed",
        "request_started_at": 10.0,
        "evidence": normalize_usage_evidence(
            {
                "input_tokens": 100,
                "output_tokens": 10,
                "input_tokens_details": {"cached_tokens": 80},
            },
            evidence_observed_at=12.0,
            cache_policy=CachePolicyHint(
                window_seconds=300,
                refresh_on_reuse=True,
                basis="configured",
                clock_basis="request_started",
            ),
        ).to_dict(),
    }


@pytest.mark.parametrize("backend", ["jsonl", "sqlite"])
def test_request_usage_is_a_known_strict_replay_event(tmp_path, backend: str) -> None:
    store = (
        JsonlRecordStore(tmp_path / "records")
        if backend == "jsonl"
        else SqliteRecordStore(tmp_path / "records.db")
    )
    journal = TrialJournal(store, "trial-usage")
    journal.append("trial.started", _started_payload(), timestamp=1)
    journal.append("request.usage_recorded", _usage_payload(), timestamp=12)

    replay = replay_trial(store, "trial-usage")

    assert replay.status == "started"
    assert len(replay.request_usage) == 1
    assert replay.request_usage[0]["request_ordinal"] == 1
    assert UsageEvidence.from_dict(replay.request_usage[0]["evidence"]).cache_read_tokens.value == 80
    assert replay.unhandled_events == ()
    if backend == "sqlite":
        store.close()


def test_request_usage_replay_rejects_observation_timestamp_mismatch() -> None:
    events = (
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=1,
            event_type="trial.started",
            payload=_started_payload(),
            timestamp=1,
        ),
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=2,
            event_type="request.usage_recorded",
            payload=_usage_payload(),
            timestamp=13,
        ),
    )

    with pytest.raises(ValueError, match="observed time.*event timestamp"):
        replay_trial(events, "trial-usage")


def test_request_usage_replay_rejects_start_after_observation() -> None:
    payload = _usage_payload()
    payload["request_started_at"] = 13.0
    events = (
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=1,
            event_type="trial.started",
            payload=_started_payload(),
            timestamp=1,
        ),
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=2,
            event_type="request.usage_recorded",
            payload=payload,
            timestamp=12,
        ),
    )

    with pytest.raises(ValueError, match="request start time.*observation"):
        replay_trial(events, "trial-usage")


def test_request_usage_replay_rejects_timeline_before_trial_start() -> None:
    events = (
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=1,
            event_type="trial.started",
            payload=_started_payload(),
            timestamp=15,
        ),
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=2,
            event_type="request.usage_recorded",
            payload=_usage_payload(),
            timestamp=12,
        ),
    )

    with pytest.raises(ValueError, match="request start time.*trial start"):
        replay_trial(events, "trial-usage")


def test_replay_allows_decreasing_event_times_across_concurrent_arms() -> None:
    started_payload = _started_payload()
    arms = started_payload["arms"]
    assert isinstance(arms, list)
    assert isinstance(arms[0], dict)
    second_arm = dict(arms[0])
    second_arm.update({"arm_id": "arm-2", "arm_ordinal": 1})
    started_payload["arms"] = [*arms, second_arm]
    first_payload = _usage_payload()
    assert isinstance(first_payload["evidence"], dict)
    first_payload["evidence"] = {
        **first_payload["evidence"],
        "evidence_observed_at": 20.0,
    }
    second_payload = _usage_payload()
    second_payload.update(
        {
            "arm_id": "arm-2",
            "actor_ref": "arm-2",
            "request_started_at": 11.0,
        }
    )
    assert isinstance(second_payload["evidence"], dict)
    second_payload["evidence"] = {
        **second_payload["evidence"],
        "evidence_observed_at": 19.0,
    }
    events = (
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=1,
            event_type="trial.started",
            payload=started_payload,
            timestamp=1,
        ),
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=2,
            event_type="request.usage_recorded",
            payload=first_payload,
            timestamp=20,
        ),
        TrialEvent.create(
            trial_id="trial-usage",
            sequence=3,
            event_type="request.usage_recorded",
            payload=second_payload,
            timestamp=19,
        ),
    )

    replay = replay_trial(events, "trial-usage")

    assert [event.timestamp for event in replay.events] == [1.0, 20.0, 19.0]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("phase", "bogus", "phase"),
        ("arm_id", "missing-arm", "declared"),
        ("actor_kind", "model", "actor kind"),
        ("actor_ref", "other", "actor reference"),
        ("request_ordinal", 0, "ordinal"),
        ("request_ordinal", True, "ordinal"),
        ("outcome", "maybe", "outcome"),
        ("request_started_at", float("nan"), "finite|JSON compliant"),
    ],
)
def test_request_usage_replay_rejects_ambiguous_or_lossy_fields(
    field: str, value: object, message: str,
) -> None:
    payload = _usage_payload()
    payload[field] = value
    with pytest.raises((TypeError, ValueError), match=message):
        events = (
            TrialEvent.create(
                trial_id="trial-usage",
                sequence=1,
                event_type="trial.started",
                payload=_started_payload(),
                timestamp=1,
            ),
            TrialEvent.create(
                trial_id="trial-usage",
                sequence=2,
                event_type="request.usage_recorded",
                payload=payload,
                timestamp=2,
            ),
        )
        replay_trial(events, "trial-usage")


def test_request_ordinals_are_unique_per_arm_and_usage_cannot_follow_completion() -> None:
    start = TrialEvent.create(
        trial_id="trial-usage",
        sequence=1,
        event_type="trial.started",
        payload=_started_payload(),
        timestamp=1,
    )
    usage = TrialEvent.create(
        trial_id="trial-usage",
        sequence=2,
        event_type="request.usage_recorded",
        payload=_usage_payload(),
        timestamp=12,
    )
    duplicate = TrialEvent.create(
        trial_id="trial-usage",
        sequence=3,
        event_type="request.usage_recorded",
        payload=_usage_payload(),
        timestamp=12,
    )
    with pytest.raises(ValueError, match="ordinal"):
        replay_trial((start, usage, duplicate), "trial-usage")

    completed_payload = {
        "phase": "trial",
        "arm_id": "arm-1",
        "arm_ordinal": 0,
        "candidate_id": "candidate-1",
    }
    completed = TrialEvent.create(
        trial_id="trial-usage",
        sequence=3,
        event_type="arm.completed",
        payload=completed_payload,
        timestamp=13,
    )
    late = TrialEvent.create(
        trial_id="trial-usage",
        sequence=4,
        event_type="request.usage_recorded",
        payload={**_usage_payload(), "request_ordinal": 2},
        timestamp=14,
    )
    with pytest.raises(ValueError, match="after.*completed"):
        replay_trial((start, usage, completed, late), "trial-usage")


def test_future_nested_usage_schema_is_a_blind_safe_partial_boundary(tmp_path) -> None:
    marker = "PRIVATE_FUTURE_USAGE_MARKER"
    store = JsonlRecordStore(tmp_path / "records")
    journal = TrialJournal(store, "trial-usage")
    journal.append("trial.started", _started_payload(), timestamp=1)
    future = _usage_payload()
    future_evidence = dict(future["evidence"])
    future_evidence["schema_version"] = 2
    future_evidence["private_future_field"] = marker
    future["evidence"] = future_evidence
    journal.append("request.usage_recorded", future, timestamp=2)

    inspection = inspect_trial(store, "trial-usage")

    assert inspection.integrity == "unsupported"
    assert inspection.replay is not None
    assert inspection.replay.request_usage == ()
    assert inspection.issues[0].code == "unsupported_usage_evidence_schema"

    from arity.inspection import TrialCatalog

    projected = WatchProjector().project(
        TrialCatalog((inspection,)), backend="jsonl", read_at=3,
    )
    assert marker not in repr(projected)
    assert projected.trials[0].issue.code == "unsupported_usage_evidence_schema"


def test_race_journals_each_candidate_request_before_publishing_its_arm(tmp_path) -> None:
    marker = "PRIVATE_USAGE_WIRING_MARKER"
    report = run_race(
        RaceConfig(
            prompt=f"Build a tiny cache without repeating {marker}.",
            mock=True,
            workspace_root=tmp_path / "workspaces",
            store_root=tmp_path / "records",
        )
    )

    replay = report.journal.replay()
    declarations = {
        str(candidate.metadata["arm_id"]): candidate for candidate in report.candidates
    }
    usage_events = [
        event for event in replay.events if event.event_type == "request.usage_recorded"
    ]
    completion_sequence = {
        str(event.payload["arm_id"]): event.sequence
        for event in replay.events
        if event.event_type == "arm.completed" and event.payload.get("phase") == "trial"
    }

    assert usage_events
    assert {str(event.payload["arm_id"]) for event in usage_events} == set(declarations)
    for arm_id in declarations:
        arm_events = [
            event for event in usage_events if event.payload["arm_id"] == arm_id
        ]
        assert [event.payload["request_ordinal"] for event in arm_events] == list(
            range(1, len(arm_events) + 1)
        )
        assert all(event.sequence < completion_sequence[arm_id] for event in arm_events)
        assert all(event.payload["actor_ref"] == arm_id for event in arm_events)
        assert all(event.payload["outcome"] == "completed" for event in arm_events)

    serialized_usage = json.dumps(
        [event.to_dict()["payload"] for event in usage_events]
    )
    assert marker not in serialized_usage
    for candidate in report.candidates:
        assert candidate.seat.provider not in serialized_usage
        assert candidate.seat.model not in serialized_usage
        assert str(candidate.seat.id) not in serialized_usage


def test_conference_reuses_one_ordinal_stream_per_arm_without_inventing_cache_hits(
    tmp_path,
) -> None:
    report = run_race(
        RaceConfig(
            prompt="Build a tiny cache.",
            mock=True,
            conference=2,
            workspace_root=tmp_path / "workspaces",
            store_root=tmp_path / "records",
        )
    )

    conference_usage = [
        payload
        for payload in report.journal.replay().request_usage
        if payload["phase"] == "conference"
    ]
    for candidate in report.candidates:
        arm_id = str(candidate.metadata["arm_id"])
        arm_usage = [payload for payload in conference_usage if payload["arm_id"] == arm_id]
        assert [payload["request_ordinal"] for payload in arm_usage] == [1, 2]
        for payload in arm_usage:
            evidence = UsageEvidence.from_dict(payload["evidence"])
            assert evidence.cache_read_tokens == TokenMeasurement(None, "unavailable")
            assert evidence.cache_write_tokens == TokenMeasurement(None, "unavailable")
