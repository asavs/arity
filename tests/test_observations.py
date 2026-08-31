"""Contracts for independent, durable observation attribution."""
from __future__ import annotations

import json
import math

import pytest

from arity.observations import (
    HumanDecisionReference,
    MechanicalEvidenceReference,
    ModelReviewReference,
    Observation,
    ObservationSubject,
    UnsupportedObservationSchema,
)


HASH = "a" * 64


def test_mechanical_observation_round_trips_exact_json_and_is_immutable() -> None:
    observation = Observation(
        observer_kind="mechanical",
        observer_id="arity.metrics",
        observer_version="1.0.0",
        observed_at=12.5,
        phase="trial",
        status="recorded",
        subject=ObservationSubject("arm", "arm-1"),
        reference=MechanicalEvidenceReference(HASH, "arm-1"),
    )

    encoded = observation.to_dict()
    assert encoded == {
        "schema_version": 1,
        "observer_kind": "mechanical",
        "observer_id": "arity.metrics",
        "observer_version": "1.0.0",
        "observed_at": 12.5,
        "phase": "trial",
        "status": "recorded",
        "subject": {"kind": "arm", "subject_id": "arm-1"},
        "reference": {"kind": "mechanical_evidence", "evidence_hash": HASH, "arm_id": "arm-1"},
    }
    assert Observation.from_dict(json.loads(json.dumps(encoded))) == observation
    with pytest.raises(Exception):
        observation.status = "failed"  # type: ignore[misc]


def test_unknown_fields_and_future_schema_are_never_silently_dropped() -> None:
    observation = Observation(
        observer_kind="mechanical",
        observer_id="kernel",
        observer_version="v1",
        observed_at=1,
        phase="trial",
        status="recorded",
        subject=ObservationSubject("evidence", "evidence-1"),
        reference=MechanicalEvidenceReference(HASH),
    )
    future = observation.to_dict()
    future["schema_version"] = 2
    future["new_meaning"] = "must not vanish"
    with pytest.raises(UnsupportedObservationSchema) as stopped:
        Observation.from_dict(future)
    assert stopped.value.schema_version == 2

    malformed = observation.to_dict()
    malformed["copied_prompt"] = "never stored here"
    with pytest.raises(ValueError, match="fields differ"):
        Observation.from_dict(malformed)


@pytest.mark.parametrize("bad_time", [math.inf, -math.inf, math.nan, True, "12"])
def test_observation_requires_finite_time_and_nonblank_attribution(bad_time: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        Observation(
            observer_kind="mechanical",
            observer_id="",
            observer_version="v1",
            observed_at=bad_time,  # type: ignore[arg-type]
            phase="trial",
            status="recorded",
            subject=ObservationSubject("evidence", "evidence-1"),
            reference=MechanicalEvidenceReference(HASH),
        )


@pytest.mark.parametrize(
    ("attempt_status", "status", "evaluation_id"),
    [("completed", "recorded", "evaluation-1"), ("failed", "failed", None), ("missing", "unavailable", None)],
)
def test_model_observations_preserve_completed_failed_and_missing_attempts(
    attempt_status: str, status: str, evaluation_id: str | None
) -> None:
    observation = Observation(
        observer_kind="model",
        observer_id="reviewer.kernel",
        observer_version="v1",
        observed_at=2,
        phase="review",
        status=status,
        subject=ObservationSubject("review", "review-1"),
        reference=ModelReviewReference(HASH, "review-1", attempt_status, evaluation_id),
    )
    assert Observation.from_dict(observation.to_dict()) == observation


@pytest.mark.parametrize(
    ("decision", "status", "candidate_id"),
    [("selected", "recorded", "candidate-1"), ("declined", "declined", None)],
)
def test_human_observations_preserve_selected_and_explicitly_declined_choices(
    decision: str, status: str, candidate_id: str | None
) -> None:
    observation = Observation(
        observer_kind="human",
        observer_id="human.asa",
        observer_version="v1",
        observed_at=3,
        phase="resolution",
        status=status,
        subject=ObservationSubject("resolution", "resolution-1"),
        reference=HumanDecisionReference(HASH, decision, candidate_id),
    )
    assert Observation.from_dict(observation.to_dict()) == observation


def test_observer_reference_and_status_cannot_disagree() -> None:
    with pytest.raises(ValueError, match="mechanical observation"):
        Observation(
            observer_kind="mechanical",
            observer_id="kernel",
            observer_version="v1",
            observed_at=1,
            phase="trial",
            status="recorded",
            subject=ObservationSubject("evidence", "evidence-1"),
            reference=ModelReviewReference(HASH, "review-1", "failed"),
        )
    with pytest.raises(ValueError, match="status must match"):
        Observation(
            observer_kind="human",
            observer_id="human.asa",
            observer_version="v1",
            observed_at=1,
            phase="resolution",
            status="recorded",
            subject=ObservationSubject("resolution", "resolution-1"),
            reference=HumanDecisionReference(HASH, "declined"),
        )


def test_references_only_admit_opaque_ids_and_content_addresses() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        MechanicalEvidenceReference("prompt copied here")
    with pytest.raises(ValueError, match="opaque"):
        ObservationSubject("trial", "raw output with spaces")
    with pytest.raises(ValueError, match="completed"):
        ModelReviewReference(HASH, "review-1", "completed")
    with pytest.raises(ValueError, match="cannot claim"):
        HumanDecisionReference(HASH, "declined", "candidate-1")
