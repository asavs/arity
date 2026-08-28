"""arity skills — Modular skill definitions, dynamic prompt injection, and tool grants.

Skills are specialized capabilities attached to roles and subagents on demand.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class Skill:
    """A modular capability definition."""
    name: str
    description: str
    instructions: str
    tools: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def render_prompt_section(self) -> str:
        """Render markdown section for injection into system prompt."""
        return f"### Skill: {self.name}\n{self.description}\n\n{self.instructions}\n"


# -----------------------------------------------------------------------------
# Built-in Core Skills
# -----------------------------------------------------------------------------

FIRECRAWL_SKILL = Skill(
    name="firecrawl-developer-index",
    description="Search issues, merged pull requests, READMEs, and official documentation.",
    instructions=(
        "Use when the question is how a library or API behaves, what an error means, "
        "or whether a bug was fixed. Ground your claims in primary documentation and official repos. "
        "Summarize concrete facts, exact API signatures, and version constraints."
    ),
    tags=("research", "docs", "external"),
)

PYTHON_DEVELOPER_SKILL = Skill(
    name="python-development",
    description="Write clean, idiomatic Python 3.13 code with strict syntax and type annotations.",
    instructions=(
        "1. Write clean, idiomatic Python 3.13. Use standard library first.\n"
        "2. Add clear type annotations to all function signatures.\n"
        "3. Always validate syntax before declaring complete.\n"
        "4. Avoid unnecessary abstractions or bloated third-party dependencies.\n"
        "5. Keep modules cohesive, focused, and testable."
    ),
    tools=("read_file", "write_file", "ast_validate", "run_command"),
    tags=("python", "coding", "engineering"),
)

PYTEST_TDD_SKILL = Skill(
    name="pytest-tdd",
    description="Test-driven verification using pytest.",
    instructions=(
        "1. Write focused unit & integration tests defending observable contracts.\n"
        "2. Execute `python -m pytest tests/ -v` inside the workspace sandbox.\n"
        "3. Every test must be deterministic, isolated, and fast.\n"
        "4. Fix any failures until all test suites pass with 100% green exit code."
    ),
    tools=("run_command", "read_file"),
    tags=("testing", "pytest", "verification"),
)

SCOUT_RECON_SKILL = Skill(
    name="scout-recon",
    description="Rapid read-only codebase reconnaissance with compressed architectural handoffs.",
    instructions=(
        "1. Locate relevant files using targeted glob and grep lookups.\n"
        "2. Read specific line ranges instead of full files.\n"
        "3. Map symbol dependencies, call sites, and contracts.\n"
        "4. Produce a concise, evidence-grounded architectural summary."
    ),
    tools=("read_file", "search"),
    tags=("recon", "search", "read-only"),
)


class SkillRegistry:
    """Discovers and registers skills from built-ins and directory manifests."""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = Path(skills_dir) if skills_dir else Path("skills")
        self._skills: dict[str, Skill] = {}
        self._register_defaults()
        self._discover_from_disk()

    def _register_defaults(self) -> None:
        for sk in (FIRECRAWL_SKILL, PYTHON_DEVELOPER_SKILL, PYTEST_TDD_SKILL, SCOUT_RECON_SKILL):
            self.register(sk)

    def _discover_from_disk(self) -> None:
        """Scan skills_dir for skills/*/SKILL.md or skills/*.md."""
        if not self.skills_dir.exists():
            return

        for path in self.skills_dir.rglob("*.md"):
            try:
                name = path.parent.name if path.name == "SKILL.md" else path.stem
                content = path.read_text(encoding="utf-8")
                # Parse markdown header and description
                lines = content.splitlines()
                title = lines[0].replace("#", "").strip() if lines else name
                desc = lines[1].strip() if len(lines) > 1 else ""
                body = "\n".join(lines[2:]).strip() if len(lines) > 2 else content

                self.register(
                    Skill(
                        name=name,
                        description=desc or title,
                        instructions=body,
                        tags=("disk",),
                    )
                )
            except Exception:
                continue

    def install(
        self,
        name: str,
        description: str,
        instructions: str,
        tools: tuple[str, ...] = (),
        tags: tuple[str, ...] = ("installed",),
    ) -> Skill:
        """Install a new skill manifest to disk and register in memory."""
        clean_name = name.lower().replace(" ", "-")
        target_dir = self.skills_dir / clean_name
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_file = target_dir / "SKILL.md"

        content = f"# {clean_name}\n{description}\n\n{instructions}\n"
        skill_file.write_text(content, encoding="utf-8")

        sk = Skill(
            name=clean_name,
            description=description,
            instructions=instructions,
            tools=tools,
            tags=tags,
        )
        self.register(sk)
        return sk

    def remove(self, name: str) -> bool:
        """Remove a skill from memory and delete its disk directory if present."""
        key = name.lower().replace(" ", "-")
        if key in self._skills:
            del self._skills[key]
            target_dir = self.skills_dir / key
            if target_dir.exists():
                import shutil
                shutil.rmtree(target_dir, ignore_errors=True)
            return True
        return False

    def register(self, skill: Skill) -> None:
        self._skills[skill.name.lower()] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name.lower().replace(" ", "-"))

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def compile_prompt(self, skill_names: list[str]) -> str:
        """Render a combined skills block for system prompt injection."""
        sections = []
        for name in skill_names:
            sk = self.get(name)
            if sk:
                sections.append(sk.render_prompt_section())
        if not sections:
            return ""
        return "\n## Active Role Skills\n\n" + "\n".join(sections)
