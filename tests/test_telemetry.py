"""Contracts for normalized, durable per-request usage evidence."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from arity.telemetry import (
    CachePolicyHint,
    JournaledModelProvider,
    TokenMeasurement,
    UnsupportedUsageEvidenceSchema,
    UsageEvidence,
    UsageRecordingContext,
    normalize_usage_evidence,
)
from arity.types import CallModel, ModelCompleted, ModelFailed


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
