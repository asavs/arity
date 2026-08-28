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
# Role Document Parsing & Discovery
# -----------------------------------------------------------------------------

def parse_role_document(content: str) -> Role:
    """Parse a Markdown role definition document with YAML/key-value frontmatter."""
    lines = content.strip().splitlines()
    if lines and lines[0].strip() == "---":
        end_idx = -1
        for idx, l in enumerate(lines[1:], 1):
            if l.strip() == "---":
                end_idx = idx
                break
        if end_idx != -1:
            fm_text = "\n".join(lines[1:end_idx])
            body_text = "\n".join(lines[end_idx + 1:]).strip()
        else:
            fm_text = ""
            body_text = content
    else:
        fm_text = ""
        body_text = content

    meta: dict[str, Any] = {}
    current_key = None
    for line in fm_text.splitlines():
        line_s = line.strip()
        if not line_s or line_s.startswith("#"):
            continue
        if ":" in line and not line_s.startswith("-"):
            k, v = line_s.split(":", 1)
            current_key = k.strip()
            v_s = v.strip()
            if v_s:
                if v_s.startswith("[") and v_s.endswith("]"):
                    meta[current_key] = [item.strip(" '\"") for item in v_s[1:-1].split(",") if item.strip()]
                elif v_s.isdigit():
                    meta[current_key] = int(v_s)
                else:
                    meta[current_key] = v_s.strip(" '\"")
            else:
                meta[current_key] = []
        elif line_s.startswith("-") and current_key:
            item_val = line_s[1:].strip(" '\"")
            if isinstance(meta.get(current_key), list):
                meta[current_key].append(item_val)

    name = meta.get("name", "unknown")
    desc = meta.get("description", "")
    tier = int(meta.get("tier", 2))
    skills = tuple(meta.get("skills", []))
    allowed_tools = tuple(meta.get("allowed_tools", []))
    denied_tools = tuple(meta.get("denied_tools", []))
    denied_paths = tuple(meta.get("denied_paths", []))
    denied_hosts = tuple(meta.get("denied_hosts", []))

    return Role(
        name=name,
        description=desc,
        tier=tier,
        skills=skills,
        allowed_tools=allowed_tools,
        denial_set=DenialSet(
            denied_tools=denied_tools,
            denied_paths=denied_paths,
            denied_hosts=denied_hosts,
        ),
        system_prompt=body_text,
    )


def load_role_from_file(path: Path) -> Role:
    """Load a Role instance from a markdown document."""
    content = path.read_text(encoding="utf-8")
    return parse_role_document(content)


class RoleRegistry:
    """Registry of active roles with discovery from role definition documents."""

    def __init__(self, initial_roles: Optional[list[Role]] = None):
        self._roles: dict[str, Role] = {}
        if initial_roles:
            for r in initial_roles:
                self.register(r)
        else:
            self._discover_from_definitions()

    def _discover_from_definitions(self) -> None:
        """Discover roles from packaged definitions, project .gorkbot/roles, and global ~/.gorkbot/roles."""
        definitions_dirs = [
            Path(__file__).parent / "definitions" / "roles",
            Path(".gorkbot/roles"),
            Path.home() / ".gorkbot" / "roles",
        ]
        for rdir in definitions_dirs:
            if not rdir.exists():
                continue
            for path in rdir.glob("*.md"):
                try:
                    role = load_role_from_file(path)
                    self.register(role)
                except Exception:
                    continue

        # Setup fallback alias mappings if specific file is not loaded
        if "voice" not in self._roles and "secretary" in self._roles:
            self._roles["voice"] = self._roles["secretary"]
        if "builder" not in self._roles and "python_developer" in self._roles:
            self._roles["builder"] = self._roles["python_developer"]
        if "architect" not in self._roles and "engineer" in self._roles:
            self._roles["architect"] = self._roles["engineer"]
        if "reviewer" not in self._roles and "tester" in self._roles:
            self._roles["reviewer"] = self._roles["tester"]
    def register(self, role: Role) -> None:
        self._roles[role.name.lower()] = role

    def get(self, name: str) -> Optional[Role]:
        return self._roles.get(name.lower())

    def list_roles(self) -> list[Role]:
        return list(set(self._roles.values()))

    def resolve(self, name_or_task: str) -> Role:
        """Resolve a role by exact name or semantic task intent."""
        query = name_or_task.lower().strip()

        # 1. Exact role name match
        if query in self._roles:
            return self._roles[query]

        # 2. Explicit @tag match (e.g. @builder, @scout, @engineer)
        if query.startswith("@"):
            tag = query[1:].split()[0]
            if tag in self._roles:
                return self._roles[tag]

        secretary_role = self._roles.get("secretary") or self._roles.get("voice") or next(iter(self._roles.values()))

        # 3. Conversational / Meta Queries stay with The Secretary (Axiom 1)
        question_starters = (
            "who ", "what ", "where ", "when ", "why ", "how ", "is ", "are ", "can ", "could ",
            "tell me", "explain", "show me", "status", "hello", "hi", "hey"
        )
        if any(query.startswith(qs) for qs in question_starters) or query.endswith("?"):
            return secretary_role

        # 4. Actionable task intent matching
        best_role = secretary_role
        best_score = 0

        keywords = {
            "python_developer": ("python", "pytest", "module", "class", "def "),
            "builder": ("implement", "build a", "create a", "write code", "schema", "table"),
            "engineer": ("architecture", "system design", "spec", "decompose"),
            "tester": ("verify tests", "run pytest", "check regression"),
            "reviewer": ("audit code", "check pr", "code review"),
            "scout": ("recon", "find repo", "locate docs"),
        }

        for role_name, kw_list in keywords.items():
            score = sum(3 if f" {kw} " in f" {query} " else (1 if kw in query else 0) for kw in kw_list)
            if score > best_score and role_name in self._roles:
                best_score = score
                best_role = self._roles[role_name]

        return best_role if best_score >= 2 else secretary_role
    def filter_tools(self, role: Role, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter a list of OpenAI tool schemas according to role permissions."""
        filtered = []
        for t in tools:
            fn_name = t.get("function", {}).get("name", "")
            if role.can_use_tool(fn_name):
                filtered.append(t)
        return filtered


# -----------------------------------------------------------------------------
# Module-level standard archetypes (loaded dynamically from definitions)
# -----------------------------------------------------------------------------

_default_registry = RoleRegistry()
SECRETARY_ROLE = _default_registry.get("secretary") or parse_role_document("---\nname: secretary\ntier: 0\n---\nSecretary")
VOICE_ROLE = _default_registry.get("voice") or SECRETARY_ROLE
ENGINEER_ROLE = _default_registry.get("engineer") or parse_role_document("---\nname: engineer\ntier: 1\n---\nEngineer")
ARCHITECT_ROLE = _default_registry.get("architect") or ENGINEER_ROLE
BUILDER_ROLE = _default_registry.get("builder") or parse_role_document("---\nname: builder\ntier: 2\n---\nBuilder")
PYTHON_DEVELOPER_ROLE = _default_registry.get("python_developer") or BUILDER_ROLE
SCOUT_ROLE = _default_registry.get("scout") or parse_role_document("---\nname: scout\ntier: 3\n---\nScout")
TESTER_ROLE = _default_registry.get("tester") or parse_role_document("---\nname: tester\ntier: 3\n---\nTester")
REVIEWER_ROLE = _default_registry.get("reviewer") or TESTER_ROLE
