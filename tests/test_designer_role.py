"""Contracts for the packaged tier-1 Arity designer role."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

import arity
from arity.handlers import JsonlRecordStore
from arity.ledger import Seat, SeatLedger
from arity.race import resolve_candidates
from arity.roles import (
    DESIGNER_ROLE,
    DEVELOPER_ROLE,
    ENGINEER_ROLE,
    REVIEWER_ROLE,
    SECRETARY_ROLE,
    RoleRegistry,
    TypePack,
)
from arity.terrarium import CandidateSpec, TaskRecord, TerrariumDispatcher
from arity.tiers import BriefCompiler, TierLevel
from arity.tools import SandboxToolRunner
from arity.types import CallModel, ExecuteTool, ModelCompleted, ToolCompleted


class _ToolAttemptProvider:
    def __init__(self, tool_name: str, arguments: dict[str, object]):
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls: list[CallModel] = []

    def call(self, effect: CallModel) -> ModelCompleted:
        self.calls.append(effect)
        if len(self.calls) == 1:
            return ModelCompleted(
                content="Attempting forbidden tool.",
                tool_calls=[{
                    "id": "designer-tool-attempt",
                    "type": "function",
                    "function": {"name": self.tool_name, "arguments": self.arguments},
                }],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        return ModelCompleted(
            content="Recorded the denied attempt.",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


class _PermissiveCustomRunner:
    def __init__(self):
        self.calls: list[ExecuteTool] = []

    def get_schemas(self) -> list[dict[str, object]]:
        return [{
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Run anything.",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    def execute(self, effect: ExecuteTool) -> ToolCompleted:
        self.calls.append(effect)
        return ToolCompleted(
            call_id=effect.call_id,
            tool_name=effect.name,
            output="custom runner executed",
        )


def test_designer_is_discovered_as_an_engineer_peer_with_project_context() -> None:
    registry = RoleRegistry()

    designer = registry.get("designer")

    assert designer is not None
    assert designer == DESIGNER_ROLE
    assert designer.tier == ENGINEER_ROLE.tier == TierLevel.TIER_1
    assert designer in registry.list_roles()
    brief = BriefCompiler(tier1_context="the project roadmap").assemble(
        designer,
        "Define the terminal journey.",
    )
    assert "# Project Context\nthe project roadmap" in brief.system_prompt


def test_composed_designer_roles_keep_tier_one_project_context() -> None:
    registry = RoleRegistry()
    registry.register_type(TypePack(name="prototype", description="Neutral design format"))
    designer = registry.get("designer:prototype")
    assert designer is not None

    brief = BriefCompiler(tier1_context="the project roadmap").assemble(
        designer,
        "Specify a prototype.",
    )

    assert designer.base_name == "designer"
    assert designer.tier == TierLevel.TIER_1
    assert "# Project Context\nthe project roadmap" in brief.system_prompt
    assert "# Operational Scope" not in brief.system_prompt


def test_designer_has_stable_aliases_and_semantic_resolution() -> None:
    registry = RoleRegistry()
    designer = registry.get("designer")
    assert designer is not None

    for alias in ("ux", "ux_designer", "ui_designer", "interaction_designer", "product_designer"):
        assert registry.get(alias) is designer
        assert registry.resolve(f"@{alias}") is designer

    assert registry.resolve(
        "define the TUI information hierarchy, interaction design, and accessibility"
    ) is designer
    assert registry.resolve("prototype the blind interface user journey") is designer


def test_common_design_briefs_route_to_designer_without_stealing_implementation() -> None:
    registry = RoleRegistry()

    for brief in (
        "design the TUI",
        "design a terminal lobby",
        "create a UI design",
        "build a TUI prototype",
        "make an accessible agent-screen mockup",
    ):
        assert registry.resolve(brief).name == "designer", brief

    for brief in (
        "implement the TUI",
        "build the TUI",
        "code the TUI",
        "implement the UI design in production",
    ):
        assert registry.resolve(brief).name == "developer:python", brief

    assert registry.resolve("design the TUI system architecture").name == "engineer"


def test_designer_prompt_owns_experience_without_inventing_runtime_behavior() -> None:
    prompt = DESIGNER_ROLE.system_prompt.lower()

    for responsibility in (
        "human journeys",
        "information hierarchy",
        "interaction and state contracts",
        "accessibility",
        "visual direction",
        "prototype",
        "a/b-blind",
    ):
        assert responsibility in prompt
    assert "engineer owns system architecture" in prompt
    assert "developer owns production implementation" in prompt
    assert "tester turns approved contracts into acceptance tests" in prompt
    assert "reviewer validates evidence" in prompt
    assert "bounded design specifications and mockups" in prompt
    assert "do not silently implement production behavior" in prompt
    assert "do not invent runtime states" in prompt
    assert "secretary" in prompt and "human" in prompt


def test_designer_prompt_has_a_stable_evidence_and_handoff_output_contract() -> None:
    prompt = DESIGNER_ROLE.system_prompt.lower()

    assert "scout-recon" in DESIGNER_ROLE.skills
    for section in (
        "verified inputs",
        "one recommendation",
        "state contract",
        "interaction contract",
        "copy contract",
        "acceptance criteria",
        "unknowns",
        "deferred schema requests",
    ):
        assert section in prompt


def test_designer_has_an_exhaustive_read_only_tool_allowlist() -> None:
    expected = ("read_file", "list_directory", "search_files", "fetch_url", "message")

    assert DESIGNER_ROLE.allowed_tools == expected
    for tool_name in expected:
        assert DESIGNER_ROLE.can_use_tool(tool_name)
    for tool_name in (
        "write_file",
        "run_command",
        "execute_command",
        "shell",
        "delete_file",
        "custom_plugin_tool",
    ):
        assert not DESIGNER_ROLE.can_use_tool(tool_name)


def test_sandbox_denies_unlisted_custom_tool_execution(tmp_path) -> None:
    called = False

    def execute_command(**_arguments: object) -> str:
        nonlocal called
        called = True
        return "executed"

    runner = SandboxToolRunner(
        workspace_root=tmp_path,
        role=DESIGNER_ROLE,
        custom_tools={"execute_command": execute_command},
    )

    result = runner.execute(
        ExecuteTool(call_id="designer-adversarial", name="execute_command", arguments={})
    )

    assert result.is_error
    assert "Security Denial" in result.output
    assert not called


def test_dispatcher_enforces_designer_authority_across_every_tool_axis(tmp_path) -> None:
    designer = RoleRegistry().get("designer")
    assert designer is not None
    seat = Seat(id="designer-seat", provider="test", model="design-model")

    for axis in ("sandbox", "mcp", "shell", "custom", "unknown"):
        custom_runner = _PermissiveCustomRunner() if axis == "custom" else None
        runner: object = custom_runner if custom_runner is not None else (
            "sandbox" if axis == "unknown" else axis
        )
        attempted_tool = "execute_command" if axis in ("custom", "unknown") else "write_file"
        provider = _ToolAttemptProvider(
            attempted_tool,
            {"path": "production.py", "content": "unsafe = True\n"},
        )
        workspace = tmp_path / f"workspace-{axis}"
        store = JsonlRecordStore(tmp_path / f"records-{axis}")
        dispatcher = TerrariumDispatcher(
            ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
            store=store,
            base_workspace=tmp_path / f"terrarium-{axis}",
            quiet=True,
        )
        result = dispatcher.dispatch_single(
            TaskRecord(brief="Specify the terminal lobby."),
            CandidateSpec(
                seat=seat,
                role=designer,
                tool_runner_type=runner,
                skills=[],
                custom_model_provider=provider,
            ),
            workspace=workspace,
            run_verification=False,
        )

        assert result.status == "completed", axis
        assert not (workspace / "production.py").exists(), axis
        assert len(provider.calls) == 2, axis
        tool_result = provider.calls[1].messages[-1]
        assert tool_result["role"] == "tool", axis
        assert "Security Denial" in tool_result["content"], axis
        if custom_runner is not None:
            assert custom_runner.calls == []


def test_shell_axis_cannot_read_designer_credentials(tmp_path) -> None:
    designer = RoleRegistry().get("designer")
    assert designer is not None
    seat = Seat(id="designer-seat", provider="test", model="design-model")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("DO_NOT_EXPOSE=this-value", encoding="utf-8")
    provider = _ToolAttemptProvider("read_file", {"path": ".env"})
    dispatcher = TerrariumDispatcher(
        ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
        store=JsonlRecordStore(tmp_path / "records"),
        base_workspace=tmp_path / "terrarium",
        quiet=True,
    )

    dispatcher.dispatch_single(
        TaskRecord(brief="Inspect the interface evidence."),
        CandidateSpec(
            seat=seat,
            role=designer,
            tool_runner_type="shell",
            skills=[],
            custom_model_provider=provider,
        ),
        workspace=workspace,
        run_verification=False,
    )

    tool_result = provider.calls[1].messages[-1]["content"]
    assert "Security Denial" in tool_result
    assert "this-value" not in tool_result


def test_designer_credentials_stay_denied() -> None:
    assert DESIGNER_ROLE.can_access_path("docs/design/watch-follow.md")
    assert DESIGNER_ROLE.can_access_path("prototypes/watch-wireframe.txt")
    for private_path in (
        ".env",
        ".ssh/id_rsa",
        ".arity/auth.json",
        "secrets/credentials.json",
        ".claude/keys/provider.key",
    ):
        assert not DESIGNER_ROLE.can_access_path(private_path)


def test_team_prompts_make_the_designer_engineer_partnership_explicit() -> None:
    engineer = ENGINEER_ROLE.system_prompt.lower()
    developer = DEVELOPER_ROLE.system_prompt.lower()
    secretary = SECRETARY_ROLE.system_prompt.lower()

    assert "designer" in engineer and "system architecture" in engineer
    assert "designer" in developer and "production implementation" in developer
    assert "designer" in secretary and "tier-1" in secretary
    assert "when technical engineering or coding is needed" in secretary
    assert "engineer" in secretary and "developer" in secretary and "reviewer" in secretary
    assert REVIEWER_ROLE.name == "reviewer"


def test_message_tool_documents_designer_as_a_peer_recipient(tmp_path) -> None:
    schemas = SandboxToolRunner(workspace_root=tmp_path).get_schemas()
    message = next(schema["function"] for schema in schemas if schema["function"]["name"] == "message")

    assert "designer" in message["description"]
    assert "designer" in message["parameters"]["properties"]["to"]["description"]


def test_resolved_designer_candidate_does_not_receive_implementation_tdd(tmp_path) -> None:
    designer = RoleRegistry().get("designer")
    assert designer is not None
    seat = Seat(id="designer-seat", provider="test", model="design-model")
    candidates, _ = resolve_candidates("models", designer, [seat])
    provider = _ToolAttemptProvider("not-called", {})
    candidates[0].custom_model_provider = provider
    dispatcher = TerrariumDispatcher(
        ledger=SeatLedger(initial_seats=[seat], auto_seed=False),
        store=JsonlRecordStore(tmp_path / "records"),
        base_workspace=tmp_path / "terrarium",
        quiet=True,
    )

    result = dispatcher.dispatch_single(
        TaskRecord(brief="Design the terminal lobby."),
        candidates[0],
        run_verification=False,
    )

    system_prompt = provider.calls[0].messages[0]["content"]
    tool_names = {
        schema["function"]["name"]
        for schema in provider.calls[0].tools
    }
    assert candidates[0].skills == []
    assert result.skills_used == []
    assert "Skill: pytest-tdd" not in system_prompt
    assert "Fix any failures" not in system_prompt
    assert tool_names == set(DESIGNER_ROLE.allowed_tools)


def test_designer_constant_is_exported_from_the_installed_package_surface() -> None:
    packaged_definition = resources.files("arity").joinpath("definitions", "roles", "designer.md")

    assert packaged_definition.is_file()
    assert arity.DESIGNER_ROLE is DESIGNER_ROLE
    assert "DESIGNER_ROLE" in arity.__all__
