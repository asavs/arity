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
    """A role profile with tier level, capabilities, skills, and restrictions."""
    name: str
    description: str
    tier: int = 2  # 0 = Secretary (closest to Asa), 1 = Lead Engineer, 2 = Specialist (Python Dev), 3 = Leaf worker
    skills: tuple[str, ...] = ()
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

SECRETARY_ROLE = Role(
    name="secretary",
    description="The trusted front desk switchboard who talks directly with Asa.",
    tier=0,
    skills=(),
    allowed_tools=("handoff", "search", "read_file", "pulse", "web_search", "fetch_url", "deploy_subagent"),
    denial_set=DenialSet(
        denied_tools=("run_destructive_command", "drop_database"),
        denied_paths=(".ssh", "id_rsa", ".env.production"),
    ),
    system_prompt=(
        "You are the Secretary of gorkbot, the trusted executive partner and front desk lead for Asa.\n"
        "1. You hold the big picture, understand his intent, and brief him with clear, phone-sized lines.\n"
        "2. PROACTIVITY: When Asa mentions or asks about an unfamiliar skill, tool, repository, library, or topic, "
        "never ask him to provide links or search for you. Immediately use your `web_search` and `fetch_url` tools "
        "or deploy `scout` via `deploy_subagent` to research it on the live web, synthesize what you learned, and brief Asa.\n"
        "3. When technical engineering or coding is needed, deploy specialized teammates (`engineer`, `python_developer`)."
    ),
)

VOICE_ROLE = Role(
    name="voice",
    description="The front-door persona who talks directly with Asa.",
    tier=0,
    allowed_tools=("handoff", "search", "read_file", "pulse", "web_search", "fetch_url", "deploy_subagent"),
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
    skills=("firecrawl-developer-index", "scout-recon"),
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

ENGINEER_ROLE = Role(
    name="engineer",
    description="Lead engineer & architect who plans solutions, gathers docs, and deploys specialists.",
    tier=1,
    skills=("firecrawl-developer-index", "scout-recon"),
    allowed_tools=("read_file", "search", "search_files", "list_directory", "web_search", "fetch_url", "handoff", "deploy_subagent"),
    denial_set=DenialSet(
        denied_tools=("drop_database",),
        denied_paths=(".ssh", "id_rsa", ".env"),
    ),
    system_prompt=(
        "You are the Lead Engineer. You decompose goals, research library and API docs using "
        "firecrawl developer index, specify exact technical requirements, and deploy specialist subagents (like python-developer)."
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

PYTHON_DEVELOPER_ROLE = Role(
    name="python_developer",
    description="Specialist Python developer implementing clean modules, AST checks, and pytest suites.",
    tier=2,
    skills=("python-development", "pytest-tdd"),
    allowed_tools=("read_file", "write_file", "run_command", "search_files", "list_directory", "web_search", "fetch_url", "deploy_subagent"),
    denial_set=DenialSet(
        denied_paths=(".ssh", "id_rsa", ".env", "C:/Users/example/.claude/keys"),
        denied_hosts=("api.stripe.com", "bank.com"),
    ),
    system_prompt=(
        "You are a dedicated Python Developer. You write clean, PEP 8 compliant, type-annotated "
        "Python 3.13 code. You use the standard library first, write pytest unit tests, and verify all code before completion."
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

SCOUT_ROLE = Role(
    name="scout",
    description="Rapid read-only reconnaissance specialist and factual evidence gatherer.",
    tier=3,
    skills=("scout-recon",),
    allowed_tools=("read_file", "search", "search_files", "list_directory", "web_search", "fetch_url"),
    denial_set=DenialSet(
        denied_tools=("write_file", "run_command"),
        denied_paths=(".ssh", "id_rsa", ".env"),
    ),
    system_prompt=(
        "You are a fast read-only scout. Your sole responsibility is evidence acquisition and clean information packaging.\n"
        "1. Locate requested repositories, documentation, skills, or symbols using `web_search`, `fetch_url`, `search_files`, and `read_file`.\n"
        "2. Extract exact facts, raw manifests, URLs, and code snippets into a structured, unopinionated packet.\n"
        "3. Do not make policy judgments or architectural evaluations—hand the clean factual packet to the Archivist and Engineer."
    ),
)

TESTER_ROLE = Role(
    name="tester",
    description="Test-driven verification agent running test suites and catching regressions.",
    tier=3,
    skills=("pytest-tdd",),
    allowed_tools=("read_file", "run_command"),
    denial_set=DenialSet(
        denied_tools=("write_file",),
        denied_paths=(".ssh", "id_rsa", ".env"),
    ),
    system_prompt=(
        "You are a strict test verifier. You run pytest suites, report failures, and confirm green verification."
    ),
)


class RoleRegistry:
    """Registry of active roles with lookup and policy enforcement."""

    def __init__(self, initial_roles: Optional[list[Role]] = None):
        self._roles: dict[str, Role] = {}
        defaults = initial_roles or [
            SECRETARY_ROLE,
            VOICE_ROLE,
            ENGINEER_ROLE,
            BUILDER_ROLE,
            ARCHITECT_ROLE,
            PYTHON_DEVELOPER_ROLE,
            REVIEWER_ROLE,
            SCOUT_ROLE,
            TESTER_ROLE,
        ]
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
        best_role = self._roles.get("secretary", SECRETARY_ROLE)
        best_score = 0

        keywords = {
            "python_developer": ("python", "pytest", "module", ".py", "script", "class", "function"),
            "engineer": ("architecture", "system design", "spec", "tradeoff", "decompose", "lead", "architect"),
            "builder": ("build", "implement", "create", "fix", "schema", "table", "coding"),
            "reviewer": ("review", "audit", "critic", "lint", "inspect", "check pr", "pr"),
            "scout": ("scout", "search", "recon", "find", "locate", "map"),
            "secretary": ("talk", "conversation", "chat", "dm", "brief", "hello", "hi", "hey", "how are we"),
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
