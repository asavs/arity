from __future__ import annotations

from pathlib import Path

import pytest

from gorkbot.evidence import ArtifactEvidence, CandidateEvidence, Evaluation, EvidenceBundle, resolve_bundle
from gorkbot.handlers import JsonlRecordStore
from gorkbot.stores.sqlite import SqliteRecordStore
from gorkbot.trial_events import TrialEvent, TrialJournal, replay_trial


def bundle() -> EvidenceBundle:
    return EvidenceBundle.create(
        trial_id="trial-1",
        task_id="trial-1",
        task_name=None,
        brief="brief",
        candidates=(
            CandidateEvidence.create(
                candidate_id="a", name="A", signature="a", model="m", provider="p",
                role="r", harness="h", tool_runner="t", verdict="success", rank=1,
                tied_with=("b",), arm_id="a", arm_ordinal=0,
                axes={"tier": 3, "hidden_rate": 1.0, "own_rate": 0.0},
                artifacts=(ArtifactEvidence.from_bytes("answer.txt", b"a"),),
            ),
            CandidateEvidence.create(
                candidate_id="b", name="B", signature="b", model="m", provider="p",
                role="r", harness="h", tool_runner="t", verdict="success", rank=2,
                tied_with=("a",), arm_id="b", arm_ordinal=1,
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


def resolved_events(*, declared_panel: tuple[str, ...] = ("judge",)) -> tuple[TrialEvent, ...]:
    evidence = bundle()
    evaluation = Evaluation.create(evidence, evaluator_id="judge", order=("b", "a"))
    resolution = resolve_bundle(
        evidence,
        facts_candidate_id="a",
        facts_tied_with=("b",),
        evaluations=(evaluation,),
        expected_evaluator_ids=("judge",),
    )
    candidates = evidence.candidates
    payloads = (
        (
            "trial.started",
            {
                "task_id": "trial-1",
                "task_name": None,
                "brief": "brief",
                "hidden_test_hashes": {},
                "arms": [arm_declaration(candidate) for candidate in candidates],
                "evaluator_ids": list(declared_panel),
            },
        ),
        ("arm.completed", arm_completion(candidates[0])),
        ("arm.completed", arm_completion(candidates[1])),
        ("evidence.frozen", {"bundle": evidence.to_dict()}),
        (
            "review.recorded",
            {
                "evaluator_id": "judge",
                "evidence_hash": evidence.evidence_hash,
                "status": "completed",
                "evaluation": evaluation.to_dict(),
            },
        ),
        ("resolution.recorded", {"resolution": resolution.to_dict()}),
    )
    return tuple(
        TrialEvent.create(
            trial_id="trial-1",
            sequence=index,
            event_type=event_type,
            payload=payload,
            timestamp=float(index),
        )
        for index, (event_type, payload) in enumerate(payloads, 1)
    )


@pytest.mark.parametrize("kind", ["jsonl", "sqlite"])
def test_journal_round_trip_and_delivery_replay(tmp_path: Path, kind: str) -> None:
    store = (
        JsonlRecordStore(tmp_path / "records")
        if kind == "jsonl"
        else SqliteRecordStore(tmp_path / "records.sqlite")
    )
    evidence = bundle()
    evaluation = Evaluation.create(evidence, evaluator_id="judge", order=("b", "a"))
    resolution = resolve_bundle(
        evidence,
        facts_candidate_id="a",
        facts_tied_with=("b",),
        evaluations=(evaluation,),
        expected_evaluator_ids=("judge",),
    )
    journal = TrialJournal(store, "trial-1")
    candidates = evidence.candidates
    journal.append(
        "trial.started",
        {
            "task_id": "trial-1",
            "task_name": None,
            "brief": "brief",
            "hidden_test_hashes": {},
            "arms": [arm_declaration(candidate) for candidate in candidates],
            "evaluator_ids": ["judge"],
        },
        timestamp=1,
    )
    journal.append("arm.completed", arm_completion(candidates[0]), timestamp=2)
    journal.append("arm.completed", arm_completion(candidates[1]), timestamp=3)
    journal.append("evidence.frozen", {"bundle": evidence.to_dict()}, timestamp=4)
    journal.append(
        "review.recorded",
        {
            "evaluator_id": evaluation.evaluator_id,
            "evidence_hash": evidence.evidence_hash,
            "status": "completed",
            "evaluation": evaluation.to_dict(),
        },
        timestamp=5,
    )
    resolution_event = journal.append(
        "resolution.recorded", {"resolution": resolution.to_dict()}, timestamp=6,
    )
    journal.append(
        "delivery.completed",
        {
            "candidate_id": "b",
            "resolution_sequence": resolution_event.sequence,
            "resolution_id": resolution.resolution_id,
            "evidence_hash": resolution.evidence_hash,
            "delivery": {
                "files": ["answer.txt"],
                "answer": None,
                "winner_name": "B",
                "signature": "b",
                "delivered": True,
                "resolution_source": "judge_consensus",
            },
        },
        timestamp=7,
    )

    replay = replay_trial(store, "trial-1")
    assert replay.status == "delivered"
    assert [arm["arm_id"] for arm in replay.completed_arms] == ["a", "b"]
    assert replay.latest_resolution == resolution
    assert replay.delivery["candidate_id"] == "b"
    assert [event.sequence for event in replay.events] == list(range(1, 8))

    if kind == "sqlite":
        store.close()
        reopened = SqliteRecordStore(tmp_path / "records.sqlite")
        assert replay_trial(reopened, "trial-1").status == "delivered"
        reopened.close()


def test_replay_rejects_gaps_conflicts_and_bad_delivery() -> None:
    started = TrialEvent.create(
        trial_id="t", sequence=1, event_type="trial.started", payload={}, timestamp=1,
    )
    gap = TrialEvent.create(
        trial_id="t", sequence=3, event_type="arm.completed", payload={}, timestamp=3,
    )
    with pytest.raises(ValueError, match="gaps"):
        replay_trial((started, gap), "t")

    conflict = TrialEvent.create(
        trial_id="t", sequence=1, event_type="trial.started", payload={"different": True}, timestamp=1,
    )
    with pytest.raises(ValueError, match="conflicting"):
        replay_trial((started, conflict), "t")

    lifecycle = resolved_events()
    resolution = lifecycle[-1].payload["resolution"]
    bad_delivery = TrialEvent.create(
        trial_id="trial-1",
        sequence=7,
        event_type="delivery.completed",
        payload={
            "candidate_id": "a",
            "resolution_sequence": 6,
            "resolution_id": resolution["resolution_id"],
            "evidence_hash": resolution["evidence_hash"],
            "delivery": {},
        },
        timestamp=7,
    )
    with pytest.raises(ValueError, match="candidate"):
        replay_trial((*lifecycle, bad_delivery), "trial-1")


def test_replay_rejects_a_consensus_that_shrinks_the_declared_panel() -> None:
    with pytest.raises(ValueError, match="panel"):
        replay_trial(resolved_events(declared_panel=("judge", "missing-judge")), "trial-1")


def test_replay_rejects_partial_or_late_arm_completion() -> None:
    lifecycle = resolved_events()
    full_evidence = bundle()
    partial = EvidenceBundle.create(
        trial_id=full_evidence.trial_id,
        task_id=full_evidence.task_id,
        task_name=full_evidence.task_name,
        brief=full_evidence.brief,
        candidates=(full_evidence.candidates[0],),
    )
    partial_freeze = TrialEvent.create(
        trial_id="trial-1",
        sequence=3,
        event_type="evidence.frozen",
        payload={"bundle": partial.to_dict()},
        timestamp=3,
    )
    with pytest.raises(ValueError, match="every declared"):
        replay_trial((lifecycle[0], lifecycle[1], partial_freeze), "trial-1")

    late_completion = TrialEvent.create(
        trial_id="trial-1",
        sequence=5,
        event_type="arm.completed",
        payload=arm_completion(full_evidence.candidates[0]),
        timestamp=5,
    )
    with pytest.raises(ValueError, match="after.*frozen"):
        replay_trial((*lifecycle[:4], late_completion), "trial-1")


def test_unknown_events_are_preserved_and_mark_replay_incomplete() -> None:
    events = (
        TrialEvent.create(trial_id="t", sequence=1, event_type="trial.started", payload={}, timestamp=1),
        TrialEvent.create(trial_id="t", sequence=2, event_type="future.event", payload={"x": 1}, timestamp=2),
    )
    replay = replay_trial(events, "t")
    assert replay.status == "incomplete"
    assert replay.unhandled_events == (events[1],)


def test_event_payload_is_strict_json() -> None:
    with pytest.raises(ValueError):
        TrialEvent.create(
            trial_id="t", sequence=1, event_type="trial.started", payload={"bad": float("nan")},
        )
    with pytest.raises(TypeError):
        TrialEvent.create(
            trial_id="t", sequence=1, event_type="trial.started", payload={"bad": object()},
        )
    with pytest.raises(ValueError, match="finite"):
        TrialEvent.create(
            trial_id="t", sequence=1, event_type="trial.started", payload={}, timestamp=float("inf"),
        )


def test_journal_instances_share_sequences_and_idempotent_retries(tmp_path: Path) -> None:
    store = JsonlRecordStore(tmp_path / "records")
    first = TrialJournal(store, "trial")
    second = TrialJournal(store, "trial")

    started = first.append(
        "trial.started", {"brief": "same"}, idempotency_key="start",
    )
    retried = second.append(
        "trial.started", {"brief": "same"}, idempotency_key="start",
    )
    completed = second.append(
        "arm.completed", {"arm_id": "a"}, idempotency_key="arm:a",
    )

    assert retried == started
    assert completed.sequence == 2
    assert len(store.query("trial_event", trial_id="trial")) == 2
    with pytest.raises(ValueError, match="idempotency"):
        first.append("trial.started", {"brief": "changed"}, idempotency_key="start")
