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
    TIER_0 = 0  # Secretary: knows personal context, biograph, switchboard
    TIER_1 = 1  # Lead Engineer & Scout: project context, roadmap, general web/repo intelligence
    TIER_2 = 2  # Python Developer: sandboxed implementation scope
    TIER_3 = 3  # Reviewer: code auditor and regression verifier
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
    """Compiles briefs with distance-based memory tiers, skill injection, and prefix cache preservation."""

    def __init__(
        self,
        tier0_context: str = "Asa: Creator of gorkbot. Working on autonomous agent statecharts.",
        tier1_context: str = "Project gorkbot: Composable statechart agent chassis with 5 explicit seams.",
        skills_registry: Optional[Any] = None,
        scorecard: Optional[Any] = None,
    ):
        self.tier0_context = tier0_context
        self.tier1_context = tier1_context
        self.scorecard = scorecard
        if skills_registry is None:
            try:
                from .skills import SkillRegistry
                self.skills = SkillRegistry()
            except Exception:
                self.skills = None
        else:
            self.skills = skills_registry


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

        # Layer 2: Skills Prompt Injection
        if getattr(role, "skills", None) and self.skills:
            skills_section = self.skills.compile_prompt(list(role.skills))
            if skills_section:
                layers.append(skills_section)

        # Layer 3: Context by Role Profile (Axiom 8)
        rname = role.name.lower()
        if rname in ("secretary", "voice"):
            scorecard_txt = ""
            if self.scorecard and hasattr(self.scorecard, "get_summary"):
                scorecard_txt = f"\n\n# Live Model Ratings & Scorecard Standings (Axiom 9)\n{self.scorecard.get_summary()}"
            layers.append(f"# Personal Context\n{self.tier0_context}\n\n# Project Context\n{self.tier1_context}{scorecard_txt}")
        elif rname in ("engineer", "scout", "architect", "recon"):
            layers.append(f"# Project Context\n{self.tier1_context}")
        else:  # python_developer, reviewer, builder, etc.
            layers.append(f"# Operational Scope ({role.name})\nYou are a focused teammate. Execute your task cleanly and verify thoroughly.")
        # Layer 4: Predecessor Accounts (Axiom 9)
        if predecessor:
            rendered_pred = predecessor.render()
            if rendered_pred:
                layers.append(rendered_pred)

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
        """Scan compiled brief for private identity / memory leaks across tiers (Axiom 8 DLP)."""
        import re
        combined = f"{system_prompt}\n{user_task}".lower()
        for denied_name in role.denial_set.denied_names:
            dn = denied_name.lower().strip()
            if dn:
                pattern = rf"\b{re.escape(dn)}\b"
                if re.search(pattern, combined):
                    raise BriefRefusalError(
                        f"Brief refused for role '{role.name}': contains denied entity name '{denied_name}'"
                    )
