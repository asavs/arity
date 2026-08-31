"""Dependency-free installed-wheel acceptance for Arity's A/B resolution envelope.

Run this file with the Python interpreter from a fresh environment containing the
built wheel. It intentionally refuses to pass when ``arity`` resolves to a source
checkout instead of that environment's site-packages.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import arity
from arity.evidence import Evaluation, EvidenceBundle, ResolutionKind, evaluate_bundle, resolve_bundle
from arity.ledger import Seat
from arity.race import RaceConfig, deliver, record_evaluation, run_race
from arity.roles import BUILDER_ROLE
from arity.stores.sqlite import SqliteRecordStore
from arity.terrarium import CandidateSpec, ContextEnvelope
from arity.trial_events import replay_trial
from arity.types import CallModel, ModelCompleted


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


class SharedBuilder:
    """One stateless provider implementation shared by both trial arms."""

    def __init__(self) -> None:
        self.calls = 0

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls += 1
        transcript = json.dumps(effect.messages, sort_keys=True)
        marker = "treatment" if "CONTEXT_MARKER=treatment" in transcript else "control"
        artifact_written = any(
            message.get("role") == "tool" and "artifact.txt" in str(message.get("content", ""))
            for message in effect.messages
        )
        usage = {
            "prompt_tokens": 20 if marker == "control" else 40,
            "completion_tokens": 5,
        }
        if not artifact_written:
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
            candidate for candidate in bundle.candidates
            if candidate.context_adapter == self.adapter_id
        )
        remainder = tuple(
            candidate.candidate_id for candidate in bundle.candidates
            if candidate.candidate_id != selected.candidate_id
        )
        return Evaluation.create(
            bundle,
            evaluator_id=self.evaluator_id,
            order=(selected.candidate_id, *remainder),
            reason=f"selected {self.adapter_id}",
        )


def exact_arms(provider: SharedBuilder) -> list[CandidateSpec]:
    seat = Seat(id="shared-seat", provider="acceptance", model="shared-model")
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


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def main() -> None:
    package_path = Path(arity.__file__).resolve()
    environment = Path(sys.prefix).resolve()
    assert package_path.is_relative_to(environment), (
        f"acceptance imported source checkout instead of installed wheel: {package_path}"
    )
    assert version("arity") == arity.__version__

    with tempfile.TemporaryDirectory(prefix="arity_resolution_acceptance_") as raw_root:
        root = Path(raw_root).resolve()
        cwd = root / "cwd"
        workspaces = root / "workspaces"
        database = root / "records.sqlite"
        delivery_root = root / "delivery"
        evidence_path = root / "evidence.json"
        cwd.mkdir()
        previous_cwd = Path.cwd()
        store: SqliteRecordStore | None = None
        try:
            os.chdir(cwd)
            provider = SharedBuilder()
            store = SqliteRecordStore(database)
            report = run_race(RaceConfig(
                prompt="Write artifact.txt.",
                mock=True,
                candidate_specs=exact_arms(provider),
                workers=1,
                evaluators=[PickAdapter("pick-treatment:v1", "marker:treatment:v1")],
                review="tie",
                record_store=store,
                workspace_root=workspaces,
                teardown=False,
            ))

            assert all(_inside(path, root) for path in (cwd, workspaces, database))
            assert len(report.results) == 2
            assert [candidate.arm_id for candidate in report.evidence.candidates] == [
                "control", "treatment",
            ]
            assert [candidate.context_adapter for candidate in report.evidence.candidates] == [
                "marker:control:v1", "marker:treatment:v1",
            ]
            assert all(_inside(Path(result.workspace_path), root) for result in report.results)

            fact_keys = {
                (
                    int(candidate.axes["tier"]),
                    float(candidate.axes["hidden_rate"]),
                    float(candidate.axes["own_rate"]),
                )
                for candidate in report.evidence.candidates
            }
            assert len(fact_keys) == 1
            control, treatment = report.evidence.candidates
            assert control.tied_with == (treatment.candidate_id,)
            assert treatment.tied_with == (control.candidate_id,)
            assert report.winner is not None and report.winner.candidate_id == control.candidate_id
            assert report.resolution is not None
            assert report.resolution.kind is ResolutionKind.JUDGE_CONSENSUS
            assert report.resolution.candidate_id == treatment.candidate_id

            evidence_path.write_text(
                json.dumps(report.evidence.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            reloaded = EvidenceBundle.from_dict(json.loads(evidence_path.read_text(encoding="utf-8")))
            assert reloaded.evidence_hash == report.evidence.evidence_hash
            assert all(not Path(artifact.path).is_absolute() for candidate in reloaded.candidates for artifact in candidate.artifacts)

            resolved_result = report.resolved_candidate
            assert resolved_result is not None
            (Path(resolved_result.workspace_path) / "artifact.txt").write_text(
                "mutated after evidence freeze\n", encoding="utf-8",
            )
            delivery = deliver(report, out_dir=delivery_root)
            assert delivery.delivered and delivery.winner_name == "treatment"
            assert delivery.resolution_source == "judge_consensus"
            assert (delivery_root / "artifact.txt").read_text(encoding="utf-8") == "treatment\n"
            assert _inside(delivery_root, root)

            builder_calls = provider.calls
            assert builder_calls == 4
            trial_records = len(store.query("terrarium_trial"))
            canonical_resolution_id = report.resolution.resolution_id
            shutil.rmtree(workspaces)
            assert not workspaces.exists()

            alternate = evaluate_bundle(
                reloaded,
                PickAdapter("pick-control:v2", "marker:control:v1"),
            )
            record_evaluation(report, alternate)
            alternate_resolution = resolve_bundle(
                reloaded,
                facts_candidate_id=control.candidate_id,
                facts_tied_with=(treatment.candidate_id,),
                evaluations=(alternate,),
                expected_evaluator_ids=("pick-control:v2",),
            )
            assert alternate_resolution.candidate_id == control.candidate_id
            assert alternate_resolution.kind is ResolutionKind.JUDGE_CONSENSUS
            assert alternate_resolution.expected_evaluator_ids == ("pick-control:v2",)
            assert alternate_resolution.evaluation_ids == (alternate.evaluation_id,)
            assert report.resolution.resolution_id == canonical_resolution_id
            assert provider.calls == builder_calls
            assert len(store.query("terrarium_trial")) == trial_records == 2

            projection = report.journal.replay()
            assert projection.status == "delivered"
            assert [arm["arm_id"] for arm in projection.completed_arms] == ["control", "treatment"]
            assert [evaluation.evaluator_id for evaluation in projection.evaluations] == [
                "pick-treatment:v1", "pick-control:v2",
            ]
            assert projection.latest_resolution.resolution_id == canonical_resolution_id
            assert projection.evidence(report.evidence.evidence_hash).evidence_hash == reloaded.evidence_hash
            assert projection.delivery["candidate_id"] == treatment.candidate_id
            types = [event.event_type for event in projection.events]
            assert max(index for index, item in enumerate(types) if item == "arm.completed") < types.index("evidence.frozen")
            assert types.index("evidence.frozen") < types.index("resolution.recorded") < types.index("delivery.completed")
            assert types.index("delivery.completed") < len(types) - 1

            store.close()
            store = None
            reopened = SqliteRecordStore(database)
            try:
                reopened_projection = replay_trial(reopened, report.task.id)
                assert reopened_projection.status == "delivered"
                assert reopened_projection.latest_resolution.resolution_id == canonical_resolution_id
                assert reopened_projection.delivery["candidate_id"] == treatment.candidate_id
            finally:
                reopened.close()
        finally:
            if store is not None:
                store.close()
            os.chdir(previous_cwd)

    print(f"PASS arity {arity.__version__} installed at {package_path}")


if __name__ == "__main__":
    main()
