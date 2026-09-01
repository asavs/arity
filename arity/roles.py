"""Arity roles — role registry, denial sets, and capability enforcement.

A bot is a role with a stable name and memory (Axiom 3).
Roles are defined by what they're good at and what they are denied (Axiom 2).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
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

    def is_host_denied(self, host_or_url: str) -> bool:
        low = host_or_url.lower()
        for h in self.denied_hosts:
            if h.lower() in low:
                return True
        return False

    def is_name_denied(self, name: str) -> bool:
        return name.lower() in (n.lower() for n in self.denied_names)


@dataclass(frozen=True)
class Role:
    """A stable personality profile with team, tier, capabilities, and restrictions."""
    name: str
    description: str
    tier: int = 2  # 0 = Secretary, 1 = project peers, 2 = specialists, 3 = leaf workers
    team: str = ""
    skills: tuple[str, ...] = ()
    accepts_types: bool = False
    # Host-issued project/history capability. Project role documents cannot
    # self-grant it during discovery.
    research_access: bool = False
    allowed_tools: tuple[str, ...] = ()  # Empty means all non-denied tools are allowed
    denial_set: DenialSet = field(default_factory=DenialSet)
    system_prompt: str = ""
    # Set when a TypePack is attached (developer:python). The pack's verify block tells the
    # sandbox how to run this type's tests; empty means "no language-specific verification".
    type_name: str = ""
    verify: dict[str, Any] = field(default_factory=dict)

    @property
    def base_name(self) -> str:
        return self.name.split(":", 1)[0]

    @property
    def key_name(self) -> str:
        """Name safe for colon-separated scorecard keys and signatures (developer.python)."""
        return self.name.replace(":", ".")

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

    def can_access_host(self, host_or_url: str) -> bool:
        """Check if role is permitted to access a host or URL."""
        return not self.denial_set.is_host_denied(host_or_url)

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
    team = str(meta.get("team", ""))
    skills = tuple(meta.get("skills", []))
    accepts_types = str(meta.get("accepts_types", "false")).lower() not in ("false", "no", "off", "0")
    research_access = str(meta.get("research_access", "false")).lower() not in ("false", "no", "off", "0")
    allowed_tools = tuple(meta.get("allowed_tools", []))
    denied_tools = tuple(meta.get("denied_tools", []))
    denied_paths = tuple(meta.get("denied_paths", []))
    denied_hosts = tuple(meta.get("denied_hosts", []))

    return Role(
        name=name,
        description=desc,
        tier=tier,
        team=team,
        skills=skills,
        accepts_types=accepts_types,
        research_access=research_access,
        allowed_tools=allowed_tools,
        denial_set=DenialSet(
            denied_tools=denied_tools,
            denied_paths=denied_paths,
            denied_hosts=denied_hosts,
        ),
        system_prompt=body_text,
    )


def load_role_from_file(path: Path, *, allow_research_access: bool = False) -> Role:
    """Load a Role instance from a markdown document."""
    content = path.read_text(encoding="utf-8")
    role = parse_role_document(content)
    if role.research_access and not allow_research_access:
        role = replace(role, research_access=False)
    return role


# -----------------------------------------------------------------------------
# Type packs: a language/domain toolkit for compatible personalities (role:type)
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class TypePack:
    """What a role needs to work in one language or domain: skills, rules, and how to verify.

    A role is who you are and what you may not touch; a type is what you are working in.
    developer:python, test-engineer:python and engineer:python share one pack.
    """
    name: str
    description: str = ""
    skills: tuple[str, ...] = ()
    system_prompt: str = ""      # appended to the role's prompt under "# Type: <name>"
    verify: dict[str, Any] = field(default_factory=dict)  # test commands/globs, including hidden-test authoring globs
    tags: tuple[str, ...] = ()   # task-bank tags that select this type automatically


def parse_type_document(content: str) -> TypePack:
    from .tasks import split_frontmatter
    meta, body = split_frontmatter(content)
    verify = {
        k: meta[k]
        for k in ("test_command", "test_globs", "hidden_test_globs", "hidden_dir", "hidden_command")
        if k in meta
    }
    for key in ("test_globs", "hidden_test_globs"):
        if isinstance(verify.get(key), str):
            verify[key] = [verify[key]]
    return TypePack(
        name=str(meta.get("name", "unknown")).lower(),
        description=str(meta.get("description", "")),
        skills=tuple(meta.get("skills", []) or ()),
        system_prompt=body,
        verify=verify,
        tags=tuple(t.lower() for t in (meta.get("tags", []) or ())),
    )


def compose(role: Role, pack: TypePack) -> Role:
    """role + type -> a new Role named role:type with the pack's skills, rules, and verification."""
    if not role.accepts_types:
        raise ValueError(f"Personality '{role.base_name}' does not accept type packs")
    skills = tuple(role.skills) + tuple(s for s in pack.skills if s not in role.skills)
    prompt = role.system_prompt.rstrip() + f"\n\n# Type: {pack.name}\n{pack.system_prompt.strip()}"
    return Role(
        name=f"{role.base_name}:{pack.name}",
        description=f"{role.description} [{pack.name}]",
        tier=role.tier,
        team=role.team,
        skills=skills,
        accepts_types=role.accepts_types,
        research_access=role.research_access,
        allowed_tools=role.allowed_tools,
        denial_set=role.denial_set,
        system_prompt=prompt,
        type_name=pack.name,
        verify=dict(pack.verify),
    )


class RoleRegistry:
    """Registry of active roles with discovery from role definition documents."""

    def __init__(self, initial_roles: Optional[list[Role]] = None):
        self._roles: dict[str, Role] = {}
        self._types: dict[str, TypePack] = {}
        self._composed: dict[str, Role] = {}
        if initial_roles:
            for r in initial_roles:
                self.register(r)
        else:
            self._discover_from_definitions()

    def _definition_dirs(self, kind: str) -> list[Path]:
        return [
            Path(__file__).parent / "definitions" / kind,
            Path(f".arity/{kind}"),
            Path.home() / ".arity" / kind,
        ]

    def _discover_from_definitions(self) -> None:
        """Discover packaged definitions plus active ``.arity`` overrides."""
        for directory_index, rdir in enumerate(self._definition_dirs("roles")):
            if not rdir.exists():
                continue
            for path in rdir.glob("*.md"):
                try:
                    self.register(
                        load_role_from_file(
                            path,
                            allow_research_access=directory_index == 0,
                        )
                    )
                except Exception:
                    continue
        for tdir in self._definition_dirs("types"):
            if not tdir.exists():
                continue
            for path in tdir.glob("*.md"):
                try:
                    self.register_type(parse_type_document(path.read_text(encoding="utf-8")))
                except Exception:
                    continue

        # Setup clean alias mappings to canonical roles.
        for base_name in ("secretary", "developer", "engineer", "scout"):
            self._refresh_role_aliases(base_name)

    def _refresh_role_aliases(self, base_name: str) -> None:
        role = self._roles.get(base_name)
        if role is None:
            return
        retained_aliases = {
            "secretary": ("voice",),
            "engineer": ("architect",),
            "scout": ("recon",),
        }
        for alias in retained_aliases.get(base_name, ()):
            self._roles[alias] = role
        if base_name == "developer":
            self._refresh_developer_aliases()

    def _refresh_developer_aliases(self) -> None:
        developer = self._roles.get("developer")
        if developer is None:
            return
        default_developer = developer
        python_pack = self._types.get("python")
        if python_pack is not None and developer.accepts_types:
            default_developer = compose(developer, python_pack)
            self._composed["developer:python"] = default_developer
        for alias in ("python_developer", "builder", "coder"):
            self._roles[alias] = default_developer

    def register(self, role: Role) -> None:
        key = role.name.lower()
        self._roles[key] = role
        for composed_key in tuple(self._composed):
            if composed_key.startswith(f"{key}:"):
                del self._composed[composed_key]
        self._refresh_role_aliases(key)

    def register_type(self, pack: TypePack) -> None:
        self._types[pack.name.lower()] = pack
        self._composed.clear()
        if pack.name.lower() == "python":
            self._refresh_developer_aliases()

    def get_type(self, name: str) -> Optional[TypePack]:
        return self._types.get(name.lower())

    def type_for_tags(self, tags: tuple[str, ...] | list[str]) -> Optional[TypePack]:
        """The type pack whose tags overlap a task's tags (first match wins)."""
        wanted = {t.lower() for t in tags}
        return next((p for p in self._types.values() if wanted & set(p.tags)), None)

    def get(self, name: str) -> Optional[Role]:
        """Look up 'role' or 'role:type'. Composition is cached; an unknown type returns None."""
        key = name.lower().strip()
        if key in self._roles:
            return self._roles[key]
        if ":" in key:
            if key in self._composed:
                return self._composed[key]
            base_name, type_name = key.split(":", 1)
            base, pack = self._roles.get(base_name), self._types.get(type_name)
            if base is None or pack is None or not base.accepts_types:
                return None
            self._composed[key] = compose(base, pack)
            return self._composed[key]
        return None

    def with_type(self, role: Role, type_name: Optional[str]) -> Role:
        """Attach a type to a compatible personality; no-op when absent or incompatible."""
        if not type_name or role.type_name == type_name or not role.accepts_types:
            return role
        return self.get(f"{role.base_name}:{type_name}") or role

    def list_roles(self) -> list[Role]:
        # Aliases share Role instances, whose mapping fields intentionally make them unhashable.
        unique = {id(role): role for role in self._roles.values()}
        return sorted(unique.values(), key=lambda role: role.name)

    def list_types(self) -> list[TypePack]:
        return sorted(self._types.values(), key=lambda p: p.name)

    def resolve(self, name_or_task: str) -> Role:
        """Resolve a role by exact name or semantic task intent."""
        query = name_or_task.lower().strip()

        # 1. Exact role name match (including role:type)
        exact = self.get(query)
        if exact is not None:
            return exact

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
            "developer:python": ("python", "pytest", "module", "class", "def "),
            "developer:rust": ("rust", "cargo", "crate"),
            "builder": ("implement", "build a", "create a", "write code", "schema", "table"),
            "engineer": ("architecture", "system design", "spec", "decompose", "technical audit", "design review"),
            "test-engineer": (
                "verify tests", "run pytest", "check regression", "audit", "review", "audit code", "code review",
                "review this code", "check pr", "pull request review", "evaluation design",
            ),
            "judge": ("frozen evidence", "blind bundle", "rank candidates", "adjudicate candidates"),
            "scout": ("recon", "find repo", "locate docs"),
        }

        for role_name, kw_list in keywords.items():
            score = sum(3 if f" {kw} " in f" {query} " else (1 if kw in query else 0) for kw in kw_list)
            candidate = self.get(role_name)
            if score > best_score and candidate is not None:
                best_score = score
                best_role = candidate

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
ENGINEER_ROLE = _default_registry.get("engineer") or parse_role_document("---\nname: engineer\ntier: 1\nteam: engineering\naccepts_types: true\n---\nEngineer")
ARCHITECT_ROLE = _default_registry.get("architect") or ENGINEER_ROLE
DESIGNER_ROLE = _default_registry.get("designer") or parse_role_document("---\nname: designer\ntier: 1\n---\nDesigner")
DEVELOPER_ROLE = _default_registry.get("developer") or parse_role_document("---\nname: developer\ntier: 2\nteam: engineering\naccepts_types: true\n---\nDeveloper")
BUILDER_ROLE = _default_registry.get("builder") or DEVELOPER_ROLE
PYTHON_DEVELOPER_ROLE = _default_registry.get("developer:python") or BUILDER_ROLE
SCOUT_ROLE = _default_registry.get("scout") or parse_role_document("---\nname: scout\ntier: 1\n---\nScout")
TEST_ENGINEER_ROLE = _default_registry.get("test-engineer") or parse_role_document("---\nname: test-engineer\ntier: 2\nteam: engineering\naccepts_types: true\n---\nTest Engineer")
JUDGE_ROLE = _default_registry.get("judge") or parse_role_document("---\nname: judge\ntier: 3\n---\nJudge")
