"""Behavioral contracts for delegation boundaries and typed test engineering."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from arity.handlers import JsonlRecordStore
from arity.ledger import Seat, SeatLedger
from arity.orchestrator import ArityOrchestrator
from arity.roles import DenialSet, Role, RoleRegistry
from arity.skills import Skill
from arity.terrarium import (
    CandidateSpec,
    ContextEnvelope,
    TaskRecord,
    TerrariumDispatcher,
)
from arity.tools import SandboxToolRunner
from arity.types import CallModel, ExecuteTool, ModelCompleted


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


SENDER_BACKDOOR = "sender_only_backdoor"


class _SenderOnlyBackdoorRunner(SandboxToolRunner):
    """A deliberately permissive sender axis that must stop at delegation."""

    def __init__(self, workspace_root, role, message_router) -> None:
        super().__init__(
            workspace_root=workspace_root,
            role=role,
            message_router=message_router,
        )
        self.register(
            name=SENDER_BACKDOOR,
            description="Sender-only sentinel capability.",
            parameters={"type": "object", "properties": {}},
            func=lambda: "SENDER_BACKDOOR_EXECUTED",
        )


def _sender_backdoor_runner_factory(workspace_root, role, message_router):
    return _SenderOnlyBackdoorRunner(workspace_root, role, message_router)


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


class _ProbeSenderBackdoorProvider:
    def __init__(self) -> None:
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        if len(self.calls) == 1:
            return ModelCompleted(
                content="I will probe the sender's tool and my denied write.",
                tool_calls=[
                    {
                        "id": "probe-sender-backdoor",
                        "type": "function",
                        "function": {
                            "name": SENDER_BACKDOOR,
                            "arguments": "{}",
                        },
                    },
                    {
                        "id": "probe-denied-write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": "judge_must_not_write.txt",
                                "content": "forbidden",
                            }),
                        },
                    },
                ],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        return ModelCompleted(
            content="Authority probe complete.",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def test_message_recipient_does_not_inherit_the_senders_custom_tool_axis(
    tmp_path: Path,
) -> None:
    registry = RoleRegistry()
    judge = registry.get("judge")
    assert judge is not None
    seat = Seat(provider="test", model="scripted")
    sender = _MessageJudgeProvider()
    recipient = _ProbeSenderBackdoorProvider()
    dispatcher = TerrariumDispatcher(
        ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
        store=JsonlRecordStore(tmp_path / "records"),
        roles=registry,
        base_workspace=tmp_path / "terrarium",
        model_factory=lambda _seat: recipient,
        quiet=True,
    )

    dispatcher.dispatch_single(
        task=TaskRecord(brief="Ask the Judge for an independent assessment."),
        candidate_or_seat=CandidateSpec(
            seat=seat,
            role=registry.get("engineer"),
            tool_runner_type=_sender_backdoor_runner_factory,
            custom_model_provider=sender,
        ),
        run_verification=False,
    )

    sender_tools = _tool_names(sender.calls[0])
    recipient_tools = _tool_names(recipient.calls[0])
    safe_judge_tools = {
        str(schema["function"]["name"])
        for schema in SandboxToolRunner(
            workspace_root=tmp_path / "expected-safe-judge",
            role=judge,
            role_registry=registry,
        ).get_schemas()
    }
    tool_outputs = [
        str(message["content"])
        for message in recipient.calls[1].messages
        if message.get("role") == "tool"
    ]

    assert SENDER_BACKDOOR in sender_tools
    assert recipient_tools == safe_judge_tools
    assert SENDER_BACKDOOR not in recipient_tools
    assert any(
        SENDER_BACKDOOR in output and "Unknown tool" in output
        for output in tool_outputs
    )
    assert any("write_file" in output and "Security Denial" in output for output in tool_outputs)


def test_message_without_a_router_reports_a_delivery_error(tmp_path: Path) -> None:
    registry = RoleRegistry()
    sender = registry.get("engineer")
    assert sender is not None
    runner = SandboxToolRunner(
        workspace_root=tmp_path,
        role=sender,
        role_registry=registry,
    )

    result = runner.execute(ExecuteTool(
        call_id="undeliverable-message",
        name="message",
        arguments={"to": "designer", "text": "Can you receive this?"},
    ))

    assert result.is_error is True
    assert "queued" not in result.output.lower()


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

    for role_name in ("secretary", "designer", "scout", "judge"):
        base = registry.get(role_name)
        assert base is not None
        assert registry.get(f"{role_name}:python") is None
        assert registry.with_type(base, "python") is base


def test_replacing_a_base_role_invalidates_typed_roles_and_retained_aliases() -> None:
    registry = RoleRegistry()
    original_typed = registry.get("developer:python")
    assert original_typed is not None
    replacement_marker = "REPLACEMENT_DEVELOPER_AUTHORITY"
    registry.register(Role(
        name="developer",
        description="Replacement developer authority.",
        team="engineering",
        system_prompt=replacement_marker,
        denial_set=DenialSet(denied_tools=("write_file",)),
    ))

    recomposed = registry.get("developer:python")
    assert recomposed is not None
    assert recomposed is not original_typed
    assert replacement_marker in recomposed.system_prompt
    assert not recomposed.can_use_tool("write_file")

    for alias in ("builder", "python_developer", "coder"):
        aliased = registry.get(alias)
        if aliased is not None:
            assert replacement_marker in aliased.system_prompt
            assert not aliased.can_use_tool("write_file")

    for base_name, alias in (
        ("secretary", "voice"),
        ("engineer", "architect"),
        ("scout", "recon"),
    ):
        original_alias = registry.get(alias)
        alias_marker = f"REPLACEMENT_{base_name.upper()}_AUTHORITY"
        registry.register(Role(
            name=base_name,
            description=f"Replacement {base_name} authority.",
            system_prompt=alias_marker,
            denial_set=DenialSet(denied_tools=("write_file",)),
        ))
        refreshed_alias = registry.get(alias)
        assert refreshed_alias is not None
        assert refreshed_alias is not original_alias
        assert alias_marker in refreshed_alias.system_prompt
        assert not refreshed_alias.can_use_tool("write_file")


def test_ordinary_code_verification_routes_to_test_engineer_not_judge() -> None:
    registry = RoleRegistry()

    for request in ("audit code", "check PR", "code review"):
        assert registry.resolve(request).base_name == "test-engineer"

    assert registry.resolve("judge").base_name == "judge"


class _StaticPhaseProvider:
    def call(self, _effect: CallModel) -> ModelCompleted:
        return ModelCompleted(
            content="Initial candidate complete.",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


class _FinalRoundPeerMessageProvider:
    def __init__(self, target: str) -> None:
        self.target = target
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        if len(self.calls) == 1:
            return ModelCompleted(
                content="I will send a final-round note.",
                tool_calls=[{
                    "id": f"message-{self.target}",
                    "type": "function",
                    "function": {
                        "name": "message",
                        "arguments": json.dumps({
                            "to": f"peer:{self.target}",
                            "text": "This note must not disappear.",
                        }),
                    },
                }],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        return ModelCompleted(
            content="Final conference turn complete.",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def test_final_conference_round_rejects_messages_with_no_delivery_round(
    tmp_path: Path,
) -> None:
    registry = RoleRegistry()
    role = registry.get("developer:python")
    assert role is not None
    seats = [
        Seat(id="candidate-a", provider="test", model="a"),
        Seat(id="candidate-b", provider="test", model="b"),
    ]
    dispatcher = TerrariumDispatcher(
        ledger=SeatLedger(initial_seats=seats, auto_seed=False),
        store=JsonlRecordStore(tmp_path / "records"),
        roles=registry,
        base_workspace=tmp_path / "terrarium",
        quiet=True,
    )
    task = TaskRecord(brief="Produce a small candidate artifact.")
    initial_results = [
        dispatcher.dispatch_single(
            task,
            CandidateSpec(
                seat=seat,
                name=f"candidate-{letter.lower()}",
                role=role,
                custom_model_provider=_StaticPhaseProvider(),
            ),
            run_verification=False,
        )
        for seat, letter in zip(seats, ("A", "B"))
    ]
    messengers = {
        "A": _FinalRoundPeerMessageProvider("B"),
        "B": _FinalRoundPeerMessageProvider("A"),
    }
    for letter, result in zip(("A", "B"), initial_results):
        assert result.spec is not None
        result.spec.custom_model_provider = messengers[letter]

    final_results = dispatcher.conference(
        task, initial_results, rounds=1, max_workers=1
    )

    for messenger, result in zip(messengers.values(), final_results):
        tool_output = next(
            str(message["content"])
            for message in reversed(messenger.calls[-1].messages)
            if message.get("role") == "tool"
        )
        normalized = tool_output.lower()
        assert "error" in normalized
        assert "later" in normalized and "round" in normalized
        assert "queued" not in normalized
        message_events = [
            event for event in result.tool_events
            if event.get("tool_name") == "message"
        ]
        assert len(message_events) == 1
        assert message_events[0]["is_error"] is True
