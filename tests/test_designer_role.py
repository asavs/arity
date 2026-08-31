"""Contracts for the packaged tier-1 Arity designer role."""
from __future__ import annotations

from importlib import resources

import arity
from arity.roles import (
    DESIGNER_ROLE,
    DEVELOPER_ROLE,
    ENGINEER_ROLE,
    REVIEWER_ROLE,
    SECRETARY_ROLE,
    RoleRegistry,
)
from arity.tiers import BriefCompiler, TierLevel


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


def test_designer_is_read_only_and_credentials_stay_denied() -> None:
    assert DESIGNER_ROLE.can_use_tool("read_file")
    assert not DESIGNER_ROLE.can_use_tool("write_file")
    assert not DESIGNER_ROLE.can_use_tool("run_command")
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
    assert "engineer" in secretary and "developer" in secretary and "reviewer" in secretary
    assert REVIEWER_ROLE.name == "reviewer"


def test_designer_constant_is_exported_from_the_installed_package_surface() -> None:
    packaged_definition = resources.files("arity").joinpath("definitions", "roles", "designer.md")

    assert packaged_definition.is_file()
    assert arity.DESIGNER_ROLE is DESIGNER_ROLE
    assert "DESIGNER_ROLE" in arity.__all__
