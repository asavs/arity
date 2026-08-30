from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from gorkbot.evidence import Evaluation, EvidenceBundle, ResolutionKind, evaluate_bundle, resolve_bundle
from gorkbot.ledger import Seat
from gorkbot.race import RaceConfig, deliver, human_pick, run_race
from gorkbot.roles import BUILDER_ROLE
from gorkbot.terrarium import CandidateSpec, ContextEnvelope
from gorkbot.types import CallModel, ModelCompleted


@dataclass(frozen=True)
class MarkerContext:
    adapter_id: str
    marker: str

    def apply(self, envelope: ContextEnvelope) -> ContextEnvelope:
        return ContextEnvelope(
            system_prompt=envelope.system_prompt,
            messages=envelope.messages,
            user_prompt=f"{envelope.user_prompt}\n\nCONTEXT_MARKER={self.marker}",
        )


class SharedContextBuilder:
    """One stateless harness implementation used by every arm in the gate."""

    def __init__(self) -> None:
        self.calls = 0

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls += 1
        transcript = json.dumps(effect.messages, sort_keys=True)
        marker = "treatment" if "CONTEXT_MARKER=treatment" in transcript else "control"
        wrote_artifact = any(
            message.get("role") == "tool" and "artifact.txt" in str(message.get("content", ""))
            for message in effect.messages
        )
        usage = {
            "prompt_tokens": 20 if marker == "control" else 40,
            "completion_tokens": 5,
        }
        if not wrote_artifact:
            return ModelCompleted(
                content="Writing the requested artifact.",
                tool_calls=[{
                    "id": f"write-{marker}",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "artifact.txt", "content": marker + "\n"}),
                    },
                }],
                usage=usage,
                finish_reason="tool_calls",
            )
        return ModelCompleted(
            content="Created artifact.txt.",
            tool_calls=[],
            usage=usage,
            finish_reason="stop",
        )


class PickAdapter:
    def __init__(self, evaluator_id: str, adapter_id: str) -> None:
        self.evaluator_id = evaluator_id
        self.adapter_id = adapter_id

    def evaluate(self, bundle: EvidenceBundle) -> Evaluation:
        selected = next(
            candidate for candidate in bundle.candidates if candidate.context_adapter == self.adapter_id
        )
        others = [candidate for candidate in bundle.candidates if candidate is not selected]
        return Evaluation.create(
            bundle,
            evaluator_id=self.evaluator_id,
            order=(selected.candidate_id, *(candidate.candidate_id for candidate in others)),
            reason=f"selected {self.adapter_id}",
        )


def exact_arms(provider: SharedContextBuilder) -> list[CandidateSpec]:
    seat = Seat(id="same-seat", provider="test", model="same-model")
    common = {
        "seat": seat,
        "role": BUILDER_ROLE,
        "harness": "wire",
        "tool_runner_type": "sandbox",
        "skills": [],
        "context": "fresh",
        "custom_model_provider": provider,
    }
    return [
        CandidateSpec(
            **common,
            name="control",
            context_adapter=MarkerContext("marker:control:v1", "control"),
            metadata={"arm_id": "control", "arm_ordinal": 0},
        ),
        CandidateSpec(
            **common,
            name="treatment",
            context_adapter=MarkerContext("marker:treatment:v1", "treatment"),
            metadata={"arm_id": "treatment", "arm_ordinal": 1},
        ),
    ]


def test_evaluator_over_frozen_evidence_controls_delivery_and_can_be_replaced(tmp_path: Path) -> None:
    provider = SharedContextBuilder()
    evaluator = PickAdapter("pick-treatment:v1", "marker:treatment:v1")
    report = run_race(RaceConfig(
        prompt="Write artifact.txt.",
        mock=True,
        candidate_specs=exact_arms(provider),
        workers=1,
        evaluators=[evaluator],
        review="tie",
        store_root=tmp_path / "records",
        workspace_root=tmp_path / "workspaces",
        teardown=False,
    ))

    assert len(report.results) == 2
    assert [candidate.arm_id for candidate in report.evidence.candidates] == ["control", "treatment"]
    assert {candidate.context_adapter for candidate in report.evidence.candidates} == {
        "marker:control:v1", "marker:treatment:v1",
    }
    assert report.winner is not None and report.winner.spec.name == "control"
    assert report.resolution is not None
    assert report.resolution.kind is ResolutionKind.JUDGE_CONSENSUS
    assert report.resolved_candidate is not None and report.resolved_candidate.spec.name == "treatment"

    delivery = deliver(report, out_dir=tmp_path / "delivery")
    assert delivery.delivered
    assert delivery.winner_name == "treatment"
    assert delivery.resolution_source == "judge_consensus"
    assert (tmp_path / "delivery" / "artifact.txt").read_text(encoding="utf-8") == "treatment\n"

    report_json = report.to_dict()
    assert report_json["winner"] == "control"
    assert report_json["winner_is_provisional"]
    assert report_json["resolved_winner"] == "treatment"
    assert report_json["resolution"]["evidence_hash"] == report.evidence.evidence_hash
    persisted = report.archivist.store.query("resolution", task_id=report.task.id)
    assert persisted[-1]["candidate_id"] == report.resolved_candidate.candidate_id
    assert persisted[-1]["evidence_hash"] == report.evidence.evidence_hash

    frozen_dict = report.evidence.to_dict()
    frozen_hash = report.evidence.evidence_hash
    builder_calls = provider.calls
    trial_records = len(report.archivist.store.query("terrarium_trial"))
    shutil.rmtree(tmp_path / "workspaces")

    reloaded = EvidenceBundle.from_dict(frozen_dict)
    alternate = evaluate_bundle(reloaded, PickAdapter("pick-control:v2", "marker:control:v1"))
    provisional = report.winner.candidate_id
    tied_with = tuple(report.entry_for(report.winner).tied_with)
    alternate_resolution = resolve_bundle(
        reloaded,
        facts_candidate_id=provisional,
        facts_tied_with=tied_with,
        evaluations=(alternate,),
        expected_evaluator_ids=("pick-control:v2",),
    )
    assert alternate_resolution.candidate_id == provisional
    assert reloaded.evidence_hash == frozen_hash
    assert provider.calls == builder_calls
    assert len(report.archivist.store.query("terrarium_trial")) == trial_records == 2


def test_split_evaluators_withhold_delivery(tmp_path: Path) -> None:
    provider = SharedContextBuilder()
    report = run_race(RaceConfig(
        prompt="Write artifact.txt.",
        mock=True,
        candidate_specs=exact_arms(provider),
        workers=1,
        evaluators=[
            PickAdapter("pick-control:v1", "marker:control:v1"),
            PickAdapter("pick-treatment:v1", "marker:treatment:v1"),
        ],
        review="tie",
        store_root=tmp_path / "records",
        workspace_root=tmp_path / "workspaces",
        teardown=False,
    ))

    assert report.resolution is not None and report.resolution.kind is ResolutionKind.UNRESOLVED
    delivery_root = tmp_path / "delivery"
    delivery = deliver(report, out_dir=delivery_root)
    assert not delivery.delivered
    assert delivery.files == []
    assert not delivery_root.exists()


def test_human_pick_revises_an_unresolved_tie(tmp_path: Path) -> None:
    provider = SharedContextBuilder()
    report = run_race(RaceConfig(
        prompt="Write artifact.txt.",
        mock=True,
        candidate_specs=exact_arms(provider),
        workers=1,
        evaluators=[
            PickAdapter("pick-control:v1", "marker:control:v1"),
            PickAdapter("pick-treatment:v1", "marker:treatment:v1"),
        ],
        review="tie",
        store_root=tmp_path / "records",
        workspace_root=tmp_path / "workspaces",
        teardown=False,
    ))
    control, treatment = report.active_results
    report.judgements = [
        {"parsed": True, "order": [control.candidate_id, treatment.candidate_id], "judge": "j1"},
        {"parsed": True, "order": [treatment.candidate_id, control.candidate_id], "judge": "j2"},
    ]
    printed: list[str] = []

    picked = human_pick(
        report,
        ask=lambda _: "2",
        printer=lambda *parts, **_: printed.append(" ".join(map(str, parts))),
    )

    assert picked is treatment
    assert report.resolution.kind is ResolutionKind.HUMAN_PICK
    assert report.resolution.candidate_id == treatment.candidate_id
    assert any("judges disagree" in line for line in printed)
    records = report.archivist.store.query("resolution", task_id=report.task.id)
    assert [record["source"] for record in records] == ["unresolved", "human_pick"]
