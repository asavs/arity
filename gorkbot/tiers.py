"""gorkbot tiers — Distance-from-Asa memory tiers, brief compilation, and identity.

Axiom 8: Memory is tiered by distance from Asa.
Axiom 9: Two accounts of every kernel (self-report + impartial archivist entry).
Axiom 7: Prompt cache prefix preservation and identity tuple.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Optional

from .roles import Role, VOICE_ROLE


class TierLevel(IntEnum):
    """Distance from Asa (Axiom 8)."""
    TIER_0 = 0  # Voice: knows the biograph, user personal preferences, daily context
    TIER_1 = 1  # Project: knows the repo architecture, roadmap, shared contracts
    TIER_2 = 2  # Leaf: knows only the immediate task, input artifacts, scratchpad


class BriefRefusalError(Exception):
    """Raised when an assembled brief violates a role's denial set."""
    pass


@dataclass(frozen=True)
class PredecessorAccounts:
    """The two accounts of a predecessor kernel (Axiom 9)."""
    self_report: Optional[str] = None
    archivist_entry: Optional[str] = None

    def render(self) -> str:
        parts = []
        if self.self_report:
            parts.append(f"### Predecessor Self-Report\n{self.self_report}")
        if self.archivist_entry:
            parts.append(f"### Archivist Audit Entry\n{self.archivist_entry}")
        return "\n\n".join(parts)


@dataclass(frozen=True)
class CompiledBrief:
    """A fully compiled brief ready for model ingestion with cache alignment."""
    system_prompt: str
    user_prompt: str
    tier: int
    identity_tuple: str
    brief_hash: str
    filtered_tools: list[dict[str, Any]] = field(default_factory=list)


def compute_identity(
    provider: str,
    endpoint: str,
    model: str,
    workspace: str,
    session_id: str,
    brief_hash: str,
) -> str:
    """Compute the immutable identity tuple for a kernel (Axiom 7)."""
    raw = f"{provider}:{endpoint}:{model}:{workspace}:{session_id}:{brief_hash}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{provider}:{model}:{session_id}:{h}"


class BriefCompiler:
    """Compiles briefs with distance-based memory tiers and prefix cache preservation."""

    def __init__(
        self,
        tier0_context: str = "Asa: Creator of gorkbot. Working on autonomous agent statecharts.",
        tier1_context: str = "Project gorkbot: Composable statechart agent chassis with 5 explicit seams.",
    ):
        self.tier0_context = tier0_context
        self.tier1_context = tier1_context

    def assemble(
        self,
        role: Role,
        task: str,
        predecessor: Optional[PredecessorAccounts] = None,
        task_context: str = "",
        provider: str = "openai",
        endpoint: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        workspace: str = "default",
        session_id: str = "sess_0",
        all_tools: Optional[list[dict[str, Any]]] = None,
    ) -> CompiledBrief:
        """Assemble the multi-layered system prompt and user task."""
        layers: list[str] = []

        # Layer 1: Universal Base Facts & Role Persona (STABLE Breakpoint 1)
        layers.append(f"# Role: {role.name.upper()}\n{role.system_prompt}")

        # Layer 2: Tiered Memory by Distance from Asa (Axiom 8)
        if role.tier == TierLevel.TIER_0:
            layers.append(f"# Personal Memory (Tier 0)\n{self.tier0_context}\n\n# Project Context (Tier 1)\n{self.tier1_context}")
        elif role.tier == TierLevel.TIER_1:
            layers.append(f"# Project Context (Tier 1)\n{self.tier1_context}")
        else:
            # Tier 2 (Leaf): knows only task and scratchpad
            layers.append("# Operational Scope (Tier 2)\nYou are a sandboxed worker. Focus strictly on the assigned task.")

        # Layer 3: Task Context (if any static reference material exists)
        if task_context:
            layers.append(f"# Task Context\n{task_context}")

        # Layer 4: Predecessor Accounts (Axiom 9)
        if predecessor and (predecessor.self_report or predecessor.archivist_entry):
            layers.append(f"# Lineage & Predecessor Context\n{predecessor.render()}")

        full_system_prompt = "\n\n---\n\n".join(layers)

        # Refusal check: ensure no denied paths, hosts, or names leaked into the brief
        self._enforce_denial_set(role, full_system_prompt, task)

        # Filter tools
        filtered_tools = []
        if all_tools:
            for t in all_tools:
                fn_name = t.get("function", {}).get("name", "")
                if role.can_use_tool(fn_name):
                    filtered_tools.append(t)

        brief_hash = hashlib.sha256(f"{full_system_prompt}\n{task}".encode("utf-8")).hexdigest()[:16]
        identity = compute_identity(provider, endpoint, model, workspace, session_id, brief_hash)

        return CompiledBrief(
            system_prompt=full_system_prompt,
            user_prompt=task,
            tier=role.tier,
            identity_tuple=identity,
            brief_hash=brief_hash,
            filtered_tools=filtered_tools,
        )

    def _enforce_denial_set(self, role: Role, system_prompt: str, user_task: str) -> None:
        """Scan prompt content against role denial set and raise BriefRefusalError if violated."""
        combined = f"{system_prompt}\n{user_task}".lower()
        for denied_path in role.denial_set.denied_paths:
            if denied_path.lower() in combined:
                raise BriefRefusalError(
                    f"Brief refused for role '{role.name}': contains denied path '{denied_path}'"
                )
        for denied_host in role.denial_set.denied_hosts:
            if denied_host.lower() in combined:
                raise BriefRefusalError(
                    f"Brief refused for role '{role.name}': contains denied host '{denied_host}'"
                )
        for denied_name in role.denial_set.denied_names:
            if denied_name.lower() in combined:
                raise BriefRefusalError(
                    f"Brief refused for role '{role.name}': contains denied name '{denied_name}'"
                )
