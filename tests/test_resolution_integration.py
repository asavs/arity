from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from gorkbot.evidence import Evaluation, EvidenceBundle, ResolutionKind, evaluate_bundle, resolve_bundle
from gorkbot.ledger import Seat
from gorkbot.race import RaceConfig, deliver, human_pick, record_evaluation, run_race
from gorkbot.roles import BUILDER_ROLE
from gorkbot.stores.sqlite import SqliteRecordStore
from gorkbot.terrarium import CandidateSpec, ContextEnvelope
from gorkbot.trial_events import TrialJournal, replay_trial
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
    store_path = tmp_path / "records.sqlite"
    store = SqliteRecordStore(store_path)
    report = run_race(RaceConfig(
        prompt="Write artifact.txt.",
        mock=True,
        candidate_specs=exact_arms(provider),
        workers=1,
        evaluators=[evaluator],
        review="tie",
        record_store=store,
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
    fact_keys = {
        (
            int(candidate.axes["tier"]),
            float(candidate.axes["hidden_rate"]),
            float(candidate.axes["own_rate"]),
        )
        for candidate in report.evidence.candidates
    }
    assert len(fact_keys) == 1
    assert report.evidence.candidates[0].tied_with == (report.evidence.candidates[1].candidate_id,)
    assert report.evidence.candidates[1].tied_with == (report.evidence.candidates[0].candidate_id,)

    with pytest.raises(ValueError, match="cannot override"):
        deliver(report, out_dir=tmp_path / "wrong-delivery", final=report.winner)
    assert not (tmp_path / "wrong-delivery").exists()
    approved_resolution = report.resolution
    report.resolution = None
    with pytest.raises(RuntimeError, match="journaled trial"):
        deliver(report, out_dir=tmp_path / "missing-resolution")
    assert not (tmp_path / "missing-resolution").exists()
    report.resolution = approved_resolution
    resolution_sequence = report.resolution_event_sequence
    report.resolution_event_sequence = None
    with pytest.raises(RuntimeError, match="not persisted"):
        deliver(report, out_dir=tmp_path / "unrecorded-resolution")
    assert not (tmp_path / "unrecorded-resolution").exists()
    report.resolution_event_sequence = resolution_sequence
    approved_evidence = report.evidence
    report.evidence = EvidenceBundle.create(
        trial_id=approved_evidence.trial_id,
        task_id=approved_evidence.task_id,
        task_name=approved_evidence.task_name,
        brief=approved_evidence.brief + " changed",
        candidates=approved_evidence.candidates,
        hidden_test_hashes=approved_evidence.hidden_test_hashes,
        metadata=approved_evidence.metadata,
    )
    with pytest.raises(ValueError, match="does not match"):
        deliver(report, out_dir=tmp_path / "wrong-evidence")
    assert not (tmp_path / "wrong-evidence").exists()
    report.evidence = approved_evidence
    occupied = tmp_path / "occupied-delivery"
    occupied.mkdir()
    with pytest.raises(FileExistsError, match="new output"):
        deliver(report, out_dir=occupied)
    (Path(report.resolved_candidate.workspace_path) / "artifact.txt").write_text(
        "mutated after freeze\n", encoding="utf-8",
    )
    live_name = report.resolved_candidate.spec.name
    live_signature = report.resolved_candidate.signature
    report.resolved_candidate.spec.name = "mutated live name"
    report.resolved_candidate.signature = "mutated-live-signature"
    stale_journal = TrialJournal(store, report.task.id)
    delivery = deliver(report, out_dir=tmp_path / "delivery")
    assert delivery.delivered
    assert delivery.winner_name == "treatment"
    assert delivery.resolution_source == "judge_consensus"
    assert delivery.signature == approved_evidence.candidate(report.resolution.candidate_id).signature
    assert (tmp_path / "delivery" / "artifact.txt").read_text(encoding="utf-8") == "treatment\n"
    report.resolved_candidate.spec.name = live_name
    report.resolved_candidate.signature = live_signature
    event_count = len(report.journal.events)
    with pytest.raises(RuntimeError, match="completed delivery"):
        deliver(report, out_dir=tmp_path / "delivery-copy")
    assert not (tmp_path / "delivery-copy").exists()
    assert len(report.journal.events) == event_count
    report.journal = stale_journal
    with pytest.raises(RuntimeError, match="completed delivery"):
        deliver(report, out_dir=tmp_path / "stale-journal-delivery")
    assert not (tmp_path / "stale-journal-delivery").exists()

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
    record_evaluation(report, alternate)
    assert alternate.evidence_hash == reloaded.evidence_hash
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
    assert report.resolution.candidate_id != alternate_resolution.candidate_id

    replay = report.journal.replay()
    assert replay.status == "delivered"
    assert [arm["arm_id"] for arm in replay.completed_arms] == ["control", "treatment"]
    assert [evaluation.evaluator_id for evaluation in replay.evaluations] == [
        "pick-treatment:v1", "pick-control:v2",
    ]
    event_types = [event.event_type for event in replay.events]
    assert event_types.index("evidence.frozen") > max(
        index for index, event_type in enumerate(event_types) if event_type == "arm.completed"
    )
    assert event_types.index("delivery.completed") < len(event_types) - 1

    store.close()
    reopened = SqliteRecordStore(store_path)
    reopened_replay = replay_trial(reopened, report.task.id)
    assert reopened_replay.status == "delivered"
    assert reopened_replay.latest_resolution.candidate_id == report.resolution.candidate_id
    assert reopened_replay.delivery["candidate_id"] == report.resolution.candidate_id
    reopened.close()


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
    assert report.resolution.expected_evaluator_ids == ("pick-control:v1", "pick-treatment:v1")
    assert any("judges disagree" in line for line in printed)
    records = report.archivist.store.query("resolution", task_id=report.task.id)
    assert [record["source"] for record in records] == ["unresolved", "human_pick"]
    delivery = deliver(report, out_dir=tmp_path / "delivery")
    assert delivery.asked_human and delivery.winner_name == "treatment"
    replay = report.journal.replay()
    assert replay.status == "delivered"
    assert replay.latest_resolution.kind is ResolutionKind.HUMAN_PICK
    assert replay.delivery["resolution_sequence"] == replay.resolution_sequences[-1]
