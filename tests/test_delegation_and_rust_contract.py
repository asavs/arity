"""Behavioral contracts for delegation boundaries and typed test engineering."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from arity.handlers import JsonlRecordStore
from arity.ledger import Seat, SeatLedger
from arity.orchestrator import ArityOrchestrator
from arity.roles import RoleRegistry
from arity.skills import Skill
from arity.terrarium import (
    CandidateSpec,
    ContextEnvelope,
    TaskRecord,
    TerrariumDispatcher,
)
from arity.tools import SandboxToolRunner
from arity.types import CallModel, ModelCompleted


SENDER_SKILL_MARKER = "SENDER_ONLY_SKILL_MUST_NOT_CROSS_DELEGATION"
SENDER_ADAPTER_MARKER = "HOSTILE_SENDER_ADAPTER_MUST_NOT_CROSS_DELEGATION"
RUST_TEST = "#[test]\nfn accepts_the_contract() {\n    assert_eq!(2 + 2, 4);\n}\n"


def _system_prompt(effect: CallModel) -> str:
    return next(
        str(message["content"])
        for message in effect.messages
        if message.get("role") == "system"
    )


def _tool_names(effect: CallModel) -> set[str]:
    return {
        str(schema["function"]["name"])
        for schema in effect.tools
    }


class _HostileSenderAdapter:
    adapter_id = "hostile-sender-sentinel"

    def apply(self, envelope: ContextEnvelope) -> ContextEnvelope:
        return ContextEnvelope(
            system_prompt=f"{envelope.system_prompt}\n{SENDER_ADAPTER_MARKER}",
            messages=envelope.messages,
            user_prompt=envelope.user_prompt,
        )


class _MessageJudgeProvider:
    def __init__(self) -> None:
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        if len(self.calls) == 1:
            return ModelCompleted(
                content="I will ask the Judge.",
                tool_calls=[{
                    "id": "ask-judge",
                    "type": "function",
                    "function": {
                        "name": "message",
                        "arguments": json.dumps({
                            "to": "judge",
                            "text": "Assess this frozen evidence.",
                        }),
                    },
                }],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        return ModelCompleted(
            content="Delegation complete.",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


class _ProbeJudgeDenialProvider:
    def __init__(self) -> None:
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        if len(self.calls) == 1:
            return ModelCompleted(
                content="I will probe my effective authority.",
                tool_calls=[{
                    "id": "forbidden-write",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({
                            "path": "judge_must_not_write.txt",
                            "content": "forbidden",
                        }),
                    },
                }],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        return ModelCompleted(
            content=str(effect.messages[-1]["content"]),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def test_message_delegation_recompiles_for_the_recipient_boundary(tmp_path: Path) -> None:
    registry = RoleRegistry()
    seat = Seat(provider="test", model="scripted")
    sender = _MessageJudgeProvider()
    recipient = _ProbeJudgeDenialProvider()
    dispatcher = TerrariumDispatcher(
        ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
        store=JsonlRecordStore(tmp_path / "records"),
        roles=registry,
        base_workspace=tmp_path / "terrarium",
        model_factory=lambda _seat: recipient,
        quiet=True,
    )
    sender_skill = Skill(
        name="sender-only-sentinel",
        description="Only the sender may receive this skill.",
        instructions=SENDER_SKILL_MARKER,
    )

    dispatcher.dispatch_single(
        task=TaskRecord(brief="Ask the Judge for an independent assessment."),
        candidate_or_seat=CandidateSpec(
            seat=seat,
            role=registry.get("engineer"),
            skills=[sender_skill],
            context_adapter=_HostileSenderAdapter(),
            custom_model_provider=sender,
        ),
        run_verification=False,
    )

    sender_prompt = _system_prompt(sender.calls[0])
    recipient_prompt = _system_prompt(recipient.calls[0])
    recipient_tools = _tool_names(recipient.calls[0])
    denial_result = str(recipient.calls[1].messages[-1]["content"])

    assert SENDER_SKILL_MARKER in sender_prompt
    assert SENDER_ADAPTER_MARKER in sender_prompt
    assert "# Role: JUDGE" in recipient_prompt
    assert "message" in recipient_tools
    assert {"write_file", "run_command", "fetch_url", "web_search"}.isdisjoint(
        recipient_tools
    )
    assert "Security Denial" in denial_result
    leaked_sender_context = {
        marker
        for marker in (SENDER_SKILL_MARKER, SENDER_ADAPTER_MARKER)
        if marker in recipient_prompt
    }
    assert leaked_sender_context == set()


class _SecretaryMessagesDesignerProvider:
    def __init__(self) -> None:
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        if len(self.calls) == 1:
            return ModelCompleted(
                content="I will ask the Designer.",
                tool_calls=[{
                    "id": "ask-designer",
                    "type": "function",
                    "function": {
                        "name": "message",
                        "arguments": json.dumps({
                            "to": "designer",
                            "text": "Shape this interaction.",
                        }),
                    },
                }],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        return ModelCompleted(
            content=str(effect.messages[-1]["content"]),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


class _DesignerReplyProvider:
    def __init__(self) -> None:
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        return ModelCompleted(
            content="DESIGNER_REPLIED",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


class _SilentTransport:
    def emit(self, _effect) -> None:
        pass


def test_secretary_can_message_a_registered_personality_through_orchestrator(
    tmp_path: Path,
) -> None:
    seat = Seat(provider="test", model="scripted")
    secretary = _SecretaryMessagesDesignerProvider()
    designer = _DesignerReplyProvider()
    providers = iter((secretary, designer))
    orchestrator = ArityOrchestrator(
        ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
        store=JsonlRecordStore(tmp_path / "records"),
        base_workspace=tmp_path / "terrarium",
        model_factory=lambda _seat: next(providers),
        transport=_SilentTransport(),
    )

    response = orchestrator.handle_message("hi", now=1.0)

    assert len(designer.calls) == 1
    assert response.reply_text == "DESIGNER_REPLIED"


class _WriteRustHiddenTestsProvider:
    def __init__(self) -> None:
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        if len(self.calls) == 1:
            return ModelCompleted(
                content="I will author the hidden integration test.",
                tool_calls=[
                    {
                        "id": "write-rust-test",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": "tests/acceptance.rs",
                                "content": RUST_TEST,
                            }),
                        },
                    },
                    {
                        "id": "write-python-decoy",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": "test_should_not_be_harvested.py",
                                "content": "def test_decoy():\n    assert True\n",
                            }),
                        },
                    },
                ],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        return ModelCompleted(
            content="Acceptance evidence authored.",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def test_rust_test_engineer_authors_and_harvests_rust_integration_tests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = RoleRegistry()
    rust_test_engineer = registry.get("test-engineer:rust")
    assert rust_test_engineer is not None
    hidden_test_globs = rust_test_engineer.verify.get("hidden_test_globs")
    assert hidden_test_globs == ["tests/**/*.rs"]
    integration_glob = hidden_test_globs[0]
    commands: list[object] = []

    def reject_command(command, *args, **kwargs):
        commands.append(command)
        raise AssertionError("hidden-test authoring must not execute a verification command")

    monkeypatch.setattr(subprocess, "run", reject_command)
    provider = _WriteRustHiddenTestsProvider()
    seat = Seat(provider="test", model="scripted")
    dispatcher = TerrariumDispatcher(
        ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
        store=JsonlRecordStore(tmp_path / "records"),
        roles=registry,
        base_workspace=tmp_path / "terrarium",
        quiet=True,
    )

    harvested, _result = dispatcher.author_hidden_tests(
        TaskRecord(brief="Implement a Rust library contract."),
        CandidateSpec(
            seat=seat,
            role=rust_test_engineer,
            custom_model_provider=provider,
        ),
    )

    authoring_brief = next(
        str(message["content"])
        for message in provider.calls[0].messages
        if message.get("role") == "user"
    )
    assert integration_glob in authoring_brief
    assert "test_*.py" not in authoring_brief
    assert len(harvested) == 1
    ((harvested_name, harvested_source),) = tuple(harvested.items())
    assert Path(harvested_name).suffix == ".rs"
    assert harvested_source == RUST_TEST
    assert commands == []


def test_default_sandbox_research_tools_follow_personality_authority(
    tmp_path: Path,
) -> None:
    registry = RoleRegistry()

    def tools_for(role_name: str) -> set[str]:
        role = registry.get(role_name)
        assert role is not None
        return {
            str(schema["function"]["name"])
            for schema in SandboxToolRunner(
                workspace_root=tmp_path / role_name,
                role=role,
                role_registry=registry,
            ).get_schemas()
        }

    scout_tools = tools_for("scout")
    assert {"web_search", "fetch_url", "message"} <= scout_tools

    for role_name in ("test-engineer", "judge"):
        role_tools = tools_for(role_name)
        assert "message" in role_tools
        assert {"web_search", "fetch_url"}.isdisjoint(role_tools)


def test_only_engineering_personalities_compose_with_language_types() -> None:
    registry = RoleRegistry()

    for role_name in ("engineer", "developer", "test-engineer"):
        base = registry.get(role_name)
        typed = registry.get(f"{role_name}:python")
        assert base is not None
        assert typed is not None
        assert typed.base_name == role_name
        assert typed.type_name == "python"
        assert registry.with_type(base, "python") is typed

    for role_name in ("designer", "judge"):
        base = registry.get(role_name)
        assert base is not None
        assert registry.get(f"{role_name}:python") is None
        assert registry.with_type(base, "python") is base


def test_ordinary_code_verification_routes_to_test_engineer_not_judge() -> None:
    registry = RoleRegistry()

    for request in ("audit code", "check PR", "code review"):
        assert registry.resolve(request).base_name == "test-engineer"

    assert registry.resolve("judge").base_name == "judge"
