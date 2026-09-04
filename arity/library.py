"""Library: backward-compatible adapter over sources.py."""
from __future__ import annotations

from typing import Any, Callable

from . import paths, sources


def epoch() -> int:
    """The ruleset epoch: an integer in library/EPOCH, 1 if the file is absent."""
    path = paths.library() / "EPOCH"
    return int(path.read_text().strip()) if path.exists() else 1


def role(name: str) -> str:
    """The role text. A role is a stable name for an aptitude, not a project."""
    return sources.load_role(name)


def skill(name: str) -> str:
    """The skill text. Text only."""
    return sources.load_skill(name)


def skill_tools(name: str) -> tuple[str, ...]:
    """The tool names a skill's front matter asks for."""
    return sources.parse_skill_tools(skill(name))


def tool_schema(name: str) -> dict[str, Any]:
    """What the model sees: name, description, input schema."""
    return sources.load_tool_schema(name)


def tool_runner(name: str) -> Callable[..., str]:
    """What the loop calls: a plain Python function `run(**arguments) -> str`."""
    return sources.load_tool_runner(name)
