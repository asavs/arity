"""Behavioral contract for Arity's shipped personalities and message addressing."""
from __future__ import annotations

import json
from pathlib import Path

import arity
from arity.handlers import JsonlRecordStore
from arity.ledger import Seat, SeatLedger
from arity.roles import Role, RoleRegistry
from arity.terrarium import CandidateSpec, TaskRecord, TerrariumDispatcher
from arity.tiers import TierLevel
from arity.tools import SandboxToolRunner
from arity.types import CallModel, ExecuteTool, ModelCompleted


def test_designer_is_a_packaged_canonical_personality() -> None:
    role = RoleRegistry().get("designer")

    assert role is not None
    assert role.name == "designer"
    assert role.system_prompt.strip()
    assert arity.DESIGNER_ROLE.name == "designer"


def test_test_engineer_is_a_packaged_canonical_personality() -> None:
    role = RoleRegistry().get("test-engineer")

    assert role is not None
    assert role.name == "test-engineer"
    assert role.system_prompt.strip()
    assert arity.TEST_ENGINEER_ROLE.name == "test-engineer"


def test_judge_is_a_packaged_canonical_personality() -> None:
    role = RoleRegistry().get("judge")

    assert role is not None
    assert role.name == "judge"
    assert role.system_prompt.strip()
    assert arity.JUDGE_ROLE.name == "judge"


def test_developer_composes_with_each_shipped_language_type() -> None:
    registry = RoleRegistry()

    for language in ("python", "rust"):
        role = registry.get(f"developer:{language}")
        assert role is not None
        assert role.name == f"developer:{language}"
        assert role.base_name == "developer"
        assert role.type_name == language
        assert role.team == "engineering"


def test_test_engineer_composes_with_each_shipped_language_type() -> None:
    registry = RoleRegistry()

    for language in ("python", "rust"):
        role = registry.get(f"test-engineer:{language}")
        assert role is not None
        assert role.name == f"test-engineer:{language}"
        assert role.base_name == "test-engineer"
        assert role.type_name == language
        assert role.team == "engineering"


def test_implementation_personalities_share_the_engineering_team() -> None:
    registry = RoleRegistry()

    for name in ("engineer", "developer", "test-engineer"):
        role = registry.get(name)
        assert role is not None
        assert role.team == "engineering"


def test_engineer_and_designer_are_tier_one_peers_and_personalities_can_message() -> None:
    registry = RoleRegistry()
    engineer = registry.get("engineer")
    designer = registry.get("designer")

    assert engineer is not None
    assert designer is not None
    assert engineer.tier == designer.tier == TierLevel.TIER_1
    assert all(role.can_use_tool("message") for role in registry.list_roles())


def test_message_tool_accepts_every_registered_personality_without_an_allowlist(
    tmp_path: Path,
) -> None:
    registry = RoleRegistry()
    recipients = [role.name for role in registry.list_roles()]
    delivered: list[tuple[str, str]] = []
    runner = SandboxToolRunner(
        workspace_root=tmp_path,
        message_router=lambda recipient, text: (
            delivered.append((recipient, text)) or f"sent to {recipient}"
        ),
    )

    message_schema = next(
        schema["function"]
        for schema in runner.get_schemas()
        if schema["function"]["name"] == "message"
    )
    assert "enum" not in message_schema["parameters"]["properties"]["to"]

    for index, recipient in enumerate(recipients):
        result = runner.execute(ExecuteTool(
            call_id=f"message-{index}",
            name="message",
            arguments={"to": recipient, "text": "hello"},
        ))
        assert not result.is_error

    assert delivered == [(recipient, "hello") for recipient in recipients]


class _AskUnknownRecipient:
    def __init__(self, recipient: str) -> None:
        self.recipient = recipient
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        if len(self.calls) == 1:
            return ModelCompleted(
                content="I will ask that teammate.",
                tool_calls=[{
                    "id": "message-typo",
                    "type": "function",
                    "function": {
                        "name": "message",
                        "arguments": json.dumps({"to": self.recipient, "text": "please help"}),
                    },
                }],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        tool_output = str(effect.messages[-1]["content"])
        return ModelCompleted(
            content=tool_output,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


class _UnexpectedFallbackProvider:
    def call(self, effect: CallModel) -> ModelCompleted:
        return ModelCompleted(
            content="SECRETARY_FALLBACK_WAS_SPAWNED",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


class _CustomPeerProvider:
    def call(self, effect: CallModel) -> ModelCompleted:
        return ModelCompleted(
            content="CUSTOM_PEER_REPLIED",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def test_custom_registered_personality_is_exactly_addressable_through_terrarium(
    tmp_path: Path,
) -> None:
    registry = RoleRegistry()
    registry.register(Role(
        name="cartographer",
        description="Maps a bounded problem space.",
        system_prompt="Map the requested problem space.",
    ))
    seat = Seat(provider="test", model="scripted")
    store = JsonlRecordStore(tmp_path / "records")
    dispatcher = TerrariumDispatcher(
        ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
        store=store,
        roles=registry,
        base_workspace=tmp_path / "terrarium",
        model_factory=lambda _seat: _CustomPeerProvider(),
        quiet=True,
    )

    result = dispatcher.dispatch_single(
        task=TaskRecord(brief="Ask the custom teammate for help."),
        candidate_or_seat=CandidateSpec(
            seat=seat,
            role=registry.get("engineer"),
            custom_model_provider=_AskUnknownRecipient("cartographer"),
        ),
        run_verification=False,
    )

    custom_trials = store.query("terrarium_trial", role="cartographer")
    assert result.output == "CUSTOM_PEER_REPLIED"
    assert len(custom_trials) == 1
    assert custom_trials[0]["role"] == "cartographer"


def test_unknown_message_recipient_errors_instead_of_resolving_to_secretary(
    tmp_path: Path,
) -> None:
    typo = "desginer"
    seat = Seat(provider="test", model="scripted")
    child_factory_calls: list[str] = []

    def child_factory(child_seat: Seat) -> _UnexpectedFallbackProvider:
        child_factory_calls.append(str(child_seat.id))
        return _UnexpectedFallbackProvider()

    store = JsonlRecordStore(tmp_path / "records")
    dispatcher = TerrariumDispatcher(
        ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
        store=store,
        base_workspace=tmp_path / "terrarium",
        model_factory=child_factory,
        quiet=True,
    )
    result = dispatcher.dispatch_single(
        task=TaskRecord(brief="Ask a teammate for help."),
        candidate_or_seat=CandidateSpec(
            seat=seat,
            role=RoleRegistry().get("engineer"),
            custom_model_provider=_AskUnknownRecipient(typo),
        ),
        run_verification=False,
    )

    message_results = store.query(
        "tool_result",
        session_id=result.candidate_id,
        tool_name="message",
    )
    assert child_factory_calls == []
    assert len(message_results) == 1
    assert message_results[0]["is_error"] is True
    assert typo in message_results[0]["output_preview"].lower()
    assert "SECRETARY_FALLBACK_WAS_SPAWNED" not in (result.output or "")
