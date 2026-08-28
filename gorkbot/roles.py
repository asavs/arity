"""gorkbot roles — Role registry, denial sets, and capability enforcement.

A bot is a role with a stable name and memory (Axiom 3).
Roles are defined by what they're good at and what they are denied (Axiom 2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class DenialSet:
    """Explicit restriction set for a role."""
    denied_tools: tuple[str, ...] = ()
    denied_paths: tuple[str, ...] = ()
    denied_hosts: tuple[str, ...] = ()
    denied_names: tuple[str, ...] = ()

    def is_tool_denied(self, tool_name: str) -> bool:
        return tool_name in self.denied_tools

    def is_path_denied(self, path: str) -> bool:
        norm_path = path.replace("\\", "/").lower()
        for denied in self.denied_paths:
            if denied.lower() in norm_path:
                return True
        return False

    def is_host_denied(self, host: str) -> bool:
        return host.lower() in (h.lower() for h in self.denied_hosts)

    def is_name_denied(self, name: str) -> bool:
        return name.lower() in (n.lower() for n in self.denied_names)


@dataclass(frozen=True)
class Role:
    """A role profile with tier level, capabilities, and restrictions."""
    name: str
    description: str
    tier: int = 2  # 0 = Voice (closest to Asa), 1 = Project lead, 2 = Leaf worker
    allowed_tools: tuple[str, ...] = ()  # Empty means all non-denied tools are allowed
    denial_set: DenialSet = field(default_factory=DenialSet)
    system_prompt: str = ""

    def can_use_tool(self, tool_name: str) -> bool:
        """Check if role is permitted to invoke a tool."""
        if self.denial_set.is_tool_denied(tool_name):
            return False
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False
        return True

    def can_access_path(self, path: str) -> bool:
        """Check if role is permitted to read/write a path."""
        return not self.denial_set.is_path_denied(path)


# -----------------------------------------------------------------------------
# Default Standard Archetypes
# -----------------------------------------------------------------------------

VOICE_ROLE = Role(
    name="voice",
    description="The front-door persona who talks directly with Asa.",
    tier=0,
    allowed_tools=("handoff", "search", "read_file", "pulse"),
    denial_set=DenialSet(
        denied_tools=("run_destructive_command", "drop_database"),
        denied_paths=(".ssh", "id_rsa", ".env.production"),
    ),
    system_prompt=(
        "You are the Voice of gorkbot. You talk directly with Asa. "
        "You manage other bots working on specialized tasks and brief Asa with clear, phone-sized lines."
    ),
)

ARCHITECT_ROLE = Role(
    name="architect",
    description="High-level systems thinker, planner, and code reviewer.",
    tier=1,
    allowed_tools=("read_file", "search", "handoff"),
    denial_set=DenialSet(
        denied_tools=("write_file", "run_command"),
        denied_paths=(".ssh", "id_rsa", ".env"),
    ),
    system_prompt=(
        "You are a careful systems architect. You review code, analyze tradeoffs, "
        "and produce clean specifications without modifying files directly."
    ),
)

BUILDER_ROLE = Role(
    name="builder",
    description="Software engineer implementing features and fixes in a workspace.",
    tier=2,
    allowed_tools=("read_file", "write_file", "run_command"),
    denial_set=DenialSet(
        denied_paths=(".ssh", "id_rsa", ".env", "C:/Users/example/.claude/keys"),
        denied_hosts=("api.stripe.com", "bank.com"),
    ),
    system_prompt=(
        "You are a focused builder. You write clean, working code, execute tests, "
        "and verify deliverables thoroughly inside the workspace."
    ),
)

REVIEWER_ROLE = Role(
    name="reviewer",
    description="Read-only code auditor and test verifier.",
    tier=2,
    allowed_tools=("read_file", "run_command"),
    denial_set=DenialSet(
        denied_tools=("write_file",),
        denied_paths=(".ssh", "id_rsa", ".env"),
    ),
    system_prompt=(
        "You are a strict code reviewer. You verify test execution and audit patches for correctness."
    ),
)


class RoleRegistry:
    """Registry of active roles with lookup and policy enforcement."""

    def __init__(self, initial_roles: Optional[list[Role]] = None):
        self._roles: dict[str, Role] = {}
        defaults = initial_roles or [VOICE_ROLE, ARCHITECT_ROLE, BUILDER_ROLE, REVIEWER_ROLE]
        for r in defaults:
            self.register(r)

    def register(self, role: Role) -> None:
        self._roles[role.name.lower()] = role

    def get(self, name: str) -> Optional[Role]:
        return self._roles.get(name.lower())

    def resolve(self, name_or_task: str) -> Role:
        """Resolve a role by exact name or match against role descriptions."""
        query = name_or_task.lower().strip()
        if query in self._roles:
            return self._roles[query]

        # Score each registered role by matching keywords against description/prompt
        best_role = self._roles.get("voice", VOICE_ROLE)
        best_score = 0

        keywords = {
            "reviewer": ("review", "audit", "critic", "lint", "inspect"),
            "architect": ("architect", "plan", "spec", "system design", "tradeoff"),
            "builder": ("build", "implement", "write", "code", "coder", "fix", "schema", "script"),
            "voice": ("talk", "conversation", "chat", "dm", "brief"),
        }

        for role_name, kw_list in keywords.items():
            score = sum(2 if kw in query.split() else (1 if kw in query else 0) for kw in kw_list)
            if score > best_score and role_name in self._roles:
                best_score = score
                best_role = self._roles[role_name]

        return best_role

    def filter_tools(self, role: Role, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter a list of OpenAI tool schemas according to role permissions."""
        filtered = []
        for t in tools:
            fn_name = t.get("function", {}).get("name", "")
            if role.can_use_tool(fn_name):
                filtered.append(t)
        return filtered
