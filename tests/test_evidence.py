from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from arity.evidence import (
    ArtifactEvidence,
    CandidateEvidence,
    Evaluation,
    EvidenceBundle,
    ResolutionKind,
    evaluate_bundle,
    resolve_bundle,
)


def candidate(candidate_id: str, *, rank: int, tied_with: tuple[str, ...] = ()) -> CandidateEvidence:
    return CandidateEvidence.create(
        candidate_id=candidate_id,
        name=candidate_id,
        signature=f"sig:{candidate_id}",
        model="mock",
        provider="test",
        role="developer:python",
        harness="wire",
        tool_runner="ast_tools",
        skills=("pytest-tdd",),
        context="fresh",
        verdict="success",
        rank=rank,
        tied_with=tied_with,
        test_results={"hidden": {"passed": 2, "total": 2}},
        axes={"tier": 3, "hidden_rate": 1.0},
        artifacts=(ArtifactEvidence.from_bytes("answer.py", candidate_id.encode()),),
    )


def bundle() -> EvidenceBundle:
    return EvidenceBundle.create(
        trial_id="trial-1",
        task_id="task-1",
        task_name="example",
        brief="do the thing",
        candidates=(
            candidate("a", rank=1, tied_with=("b",)),
            candidate("b", rank=2, tied_with=("a",)),
        ),
        hidden_test_hashes={"hidden.py": "abc"},
        metadata={"requested_arity": 2, "nested": [1, 2]},
    )


def test_evidence_is_deeply_immutable_content_addressed_and_round_trips() -> None:
    metadata = {"nested": [1, 2]}
    evidence = EvidenceBundle.create(
        trial_id="trial-1",
        task_id="task-1",
        task_name=None,
        brief="brief",
        candidates=(candidate("a", rank=1),),
        metadata=metadata,
    )
    metadata["nested"].append(3)

    assert evidence.metadata["nested"] == (1, 2)
    with pytest.raises(TypeError):
        evidence.metadata["new"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        evidence.candidates[0].axes["tier"] = 0  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        evidence.brief = "changed"  # type: ignore[misc]

    restored = EvidenceBundle.from_dict(evidence.to_dict())
    assert restored == evidence
    assert restored.evidence_hash == evidence.evidence_hash

    tampered = evidence.to_dict()
    tampered["brief"] = "different"
    with pytest.raises(ValueError, match="hash"):
        EvidenceBundle.from_dict(tampered)


def test_evaluation_requires_an_exact_candidate_permutation() -> None:
    evidence = bundle()
    with pytest.raises(ValueError, match="every candidate"):
        Evaluation.create(evidence, evaluator_id="judge", order=("a",))
    with pytest.raises(ValueError, match="every candidate"):
        Evaluation.create(evidence, evaluator_id="judge", order=("a", "ghost"))
    with pytest.raises(ValueError, match="distinct known"):
        Evaluation.create(evidence, evaluator_id="judge", order=("a", "b"), ties=(("a", "ghost"),))


def test_evaluator_is_bound_to_the_frozen_bundle_and_identity() -> None:
    evidence = bundle()

    class PickSecond:
        evaluator_id = "pick-second:v1"

        def evaluate(self, frozen: EvidenceBundle) -> Evaluation:
            return Evaluation.create(
                frozen,
                evaluator_id=self.evaluator_id,
                order=("b", "a"),
                reason="B is easier to maintain",
            )

    evaluation = evaluate_bundle(evidence, PickSecond())
    assert evaluation.order == ("b", "a")
    assert evaluation.evidence_hash == evidence.evidence_hash

    other = EvidenceBundle.create(
        trial_id="trial-2",
        task_id="task-1",
        task_name="example",
        brief="changed",
        candidates=evidence.candidates,
    )
    with pytest.raises(ValueError, match="different evidence"):
        evaluation.validate(other)


def test_resolution_facts_then_consensus_then_human() -> None:
    evidence = bundle()

    facts = resolve_bundle(evidence, facts_candidate_id="a", facts_tied_with=())
    assert facts.kind is ResolutionKind.FACTS_WINNER
    assert facts.candidate_id == "a"

    judge_one = Evaluation.create(evidence, evaluator_id="j1", order=("b", "a"))
    judge_two = Evaluation.create(evidence, evaluator_id="j2", order=("b", "a"))
    consensus = resolve_bundle(
        evidence,
        facts_candidate_id="a",
        facts_tied_with=("b",),
        evaluations=(judge_one, judge_two),
    )
    assert consensus.kind is ResolutionKind.JUDGE_CONSENSUS
    assert consensus.candidate_id == "b"
    assert consensus.eligible_candidate_ids == ("a", "b")
    assert consensus.to_dict()["status"] == "resolved"

    split = resolve_bundle(
        evidence,
        facts_candidate_id="a",
        facts_tied_with=("b",),
        evaluations=(judge_one, Evaluation.create(evidence, evaluator_id="j2", order=("a", "b"))),
    )
    assert split.kind is ResolutionKind.UNRESOLVED
    assert split.candidate_id is None

    human = resolve_bundle(
        evidence,
        facts_candidate_id="a",
        facts_tied_with=("b",),
        evaluations=(judge_one,),
        human_candidate_id="a",
    )
    assert human.kind is ResolutionKind.HUMAN_PICK
    assert human.candidate_id == "a"


def test_consensus_requires_the_complete_panel_and_a_unique_eligible_first() -> None:
    evidence = bundle()
    judge_one = Evaluation.create(evidence, evaluator_id="j1", order=("b", "a"))

    missing = resolve_bundle(
        evidence,
        facts_candidate_id="a",
        facts_tied_with=("b",),
        evaluations=(judge_one,),
        expected_evaluator_ids=("j1", "j2"),
    )
    assert missing.kind is ResolutionKind.UNRESOLVED
    assert "incomplete" in missing.reason

    explicit_tie = Evaluation.create(
        evidence,
        evaluator_id="j1",
        order=("b", "a"),
        ties=(("a", "b"),),
    )
    tied = resolve_bundle(
        evidence,
        facts_candidate_id="a",
        facts_tied_with=("b",),
        evaluations=(explicit_tie,),
        expected_evaluator_ids=("j1",),
    )
    assert tied.kind is ResolutionKind.UNRESOLVED
    assert "unique" in tied.reason

    with pytest.raises(ValueError, match="eligible"):
        resolve_bundle(
            evidence,
            facts_candidate_id="a",
            facts_tied_with=(),
            human_candidate_id="b",
        )


def test_no_supported_factual_candidate_stays_unresolved() -> None:
    evidence = bundle()
    evaluation = Evaluation.create(evidence, evaluator_id="judge", order=("b", "a"))
    resolution = resolve_bundle(
        evidence,
        facts_candidate_id="a",
        facts_tied_with=("b",),
        facts_supported=False,
        evaluations=(evaluation,),
    )
    assert resolution.kind is ResolutionKind.UNRESOLVED
    assert "sufficient factual evidence" in resolution.reason
