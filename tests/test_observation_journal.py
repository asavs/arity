"""Durable wiring contracts for independent mechanical/model/human observations."""
from __future__ import annotations

import dataclasses
import json

from arity.inspection import inspect_trial, inspect_trials
from arity.observations import (
    HumanDecisionReference,
    MechanicalEvidenceReference,
    ModelReviewReference,
    Observation,
)
from arity.race import RaceConfig, human_pick, run_race
from arity.watch_terminal import TerminalCapabilities, render_watch_follow_frame
from arity.watch_view_model import WatchProjector


def _mock_report(tmp_path, **changes):
    options = {
        "prompt": "Build a tiny cache.",
        "mock": True,
        "workspace_root": tmp_path / "workspaces",
        "store_root": tmp_path / "records",
    }
    options.update(changes)
    return run_race(RaceConfig(**options))


def _observation_events(report):
    return [
        event
        for event in report.journal.events
        if event.event_type == "observation.recorded"
    ]


def test_race_records_content_addressed_mechanical_observation_after_evidence(
    tmp_path,
) -> None:
    report = _mock_report(tmp_path)
    replay = report.journal.replay()
    events = list(replay.events)
    observations = list(replay.observations)

    assert len(observations) == 1
    mechanical = observations[0]
    assert mechanical.observer_kind == "mechanical"
    assert isinstance(mechanical.reference, MechanicalEvidenceReference)
    assert mechanical.reference.evidence_hash == report.evidence.evidence_hash
    observation_event = next(
        event for event in events if event.event_type == "observation.recorded"
    )
    evidence_event = next(
        event for event in events if event.event_type == "evidence.frozen"
    )
    assert evidence_event.sequence < observation_event.sequence
    assert observation_event.timestamp == mechanical.observed_at
    assert observation_event.idempotency_key == (
        f"observation.recorded:{mechanical.observation_id}"
    )
    assert "observations" not in replay.to_dict(include_events=False)


def test_existing_blind_review_attempts_become_separate_model_observations(
    tmp_path,
) -> None:
    report = _mock_report(
        tmp_path,
        judges=["gpt-5.6-sol", "claude-3-7-sonnet"],
        review="always",
    )
    replay = report.journal.replay()
    model_observations = [
        observation
        for observation in replay.observations
        if observation.observer_kind == "model"
    ]

    assert len(model_observations) == 2
    assert all(isinstance(item.reference, ModelReviewReference) for item in model_observations)
    assert {item.reference.attempt_status for item in model_observations} == {"completed"}
    review_events = {
        f"event-{event.sequence}": event
        for event in replay.events
        if event.event_type == "review.recorded"
    }
    for observation in model_observations:
        reference = observation.reference
        assert reference.review_id in review_events
        assert review_events[reference.review_id].sequence < next(
            event.sequence
            for event in replay.events
            if event.event_type == "observation.recorded"
            and event.payload["observation"]["observation_id"] == observation.observation_id
        )

    encoded = json.dumps(
        [event.to_dict()["payload"] for event in _observation_events(report)]
    )
    assert "gpt-5.6-sol" not in encoded
    assert "claude-3-7-sonnet" not in encoded


def test_human_decline_is_durable_without_becoming_a_resolution(tmp_path) -> None:
    report = _mock_report(tmp_path)
    before_resolution_count = len(report.journal.replay().resolutions)
    report.judgements = [
        {
            "parsed": True,
            "order": [result.candidate_id for result in report.active_results],
            "judge": "neutral-a",
        }
    ]

    picked = human_pick(
        report,
        ask=lambda _: "",
        printer=lambda *args, **kwargs: None,
        observer_id="local-human",
    )

    replay = report.journal.replay()
    human = [item for item in replay.observations if item.observer_kind == "human"]
    assert picked is None
    assert len(human) == 1
    assert human[0].status == "declined"
    assert isinstance(human[0].reference, HumanDecisionReference)
    assert human[0].reference.decision == "declined"
    assert human[0].reference.candidate_id is None
    assert len(replay.resolutions) == before_resolution_count


def test_future_observation_schema_stops_at_a_blind_safe_partial_boundary(
    tmp_path,
) -> None:
    report = _mock_report(tmp_path)
    original = report.journal.replay().observations[0].to_dict()
    original["schema_version"] = 2
    original["private_future_field"] = "PRIVATE_OBSERVATION_MARKER"
    report.journal.append(
        "observation.recorded",
        {"observation": original},
        idempotency_key="future-observation",
    )

    inspection = inspect_trial(report.archivist.store, report.task.id)

    assert inspection.integrity == "unsupported"
    assert inspection.issues[-1].code == "unsupported_observation_schema"
    assert inspection.replay is not None
    assert all(
        "PRIVATE_OBSERVATION_MARKER" not in repr(observation)
        for observation in inspection.replay.observations
    )


def test_follow_view_counts_each_lens_without_rendering_attribution(tmp_path) -> None:
    report = _mock_report(
        tmp_path,
        judges=["gpt-5.6-sol", "claude-3-7-sonnet"],
        review="always",
    )
    human_pick(
        report,
        ask=lambda _: "",
        printer=lambda *args, **kwargs: None,
        observer_id="PRIVATE_HUMAN_ATTRIBUTION",
    )
    catalog = inspect_trials(report.archivist.store)
    model = WatchProjector().project(
        catalog,
        backend="jsonl",
        read_at=100.0,
        selected_trial_id=report.task.id,
    )
    detail = model.trials[0].detail

    assert detail is not None
    assert detail.mechanical_observations.value == 1
    assert detail.model_observations.value == 2
    assert detail.human_observations.value == 1
    frame = render_watch_follow_frame(
        model,
        TerminalCapabilities(width=120, ascii=True, motion=False, color=False),
        expanded=True,
    )
    assert "observations mechanical 1 | model 2 | human 1" in frame
    assert "PRIVATE_HUMAN_ATTRIBUTION" not in frame
    assert report.task.id not in frame


def test_watch_rejects_observation_counts_not_backed_by_journal_events(
    tmp_path,
) -> None:
    report = _mock_report(tmp_path)
    catalog = inspect_trials(report.archivist.store)
    source = catalog.trials[0]
    replay = source.replay

    assert replay is not None
    assert len(replay.observations) == 1
    forged_collections = (
        (),
        replay.observations + (replay.observations[0],),
    )

    for observations in forged_collections:
        forged_replay = dataclasses.replace(replay, observations=observations)
        forged_source = dataclasses.replace(source, replay=forged_replay)
        forged_catalog = dataclasses.replace(catalog, trials=(forged_source,))
        model = WatchProjector().project(
            forged_catalog,
            backend="jsonl",
            read_at=100.0,
        )
        row = model.trials[0]

        assert row.integrity == "corrupt"
        assert row.lifecycle == "unknown"
        assert row.detail is None
        assert row.issue is not None
        assert row.issue.code == "inspection_incomplete"
