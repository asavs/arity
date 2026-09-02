"""Library: files, written by people, in git. Keyed by name.

    library/
        roles/    typescript-developer.md
        skills/   dmmulroy/oxlinter-rules.md
        tools/    read_file.json          (a schema the model sees ...)
        tools/    read_file.py            (... and the runner the loop calls)

This is the only store a person edits by hand. A library entry may point at
another library entry, by name (a skill's front matter naming the tools it
wants). It points at nothing else.

Cast is the only reader. It calls these functions once, at kernel birth,
and copies the text into a State. Nothing reads the library mid-kernel.
"""
from __future__ import annotations

import importlib.util
import json
from typing import Any, Callable

from . import paths


def role(name: str) -> str:
    """The role text. A role is a stable name for an aptitude, not a project."""
    return (paths.library() / "roles" / f"{name}.md").read_text()


def skill(name: str) -> str:
    """The skill text, appended after the role. Text only.

    A skill carries no tool schemas of its own. If it needs tools it names them
    in its front matter, and the role's tool block is the union of what its
    skills name. This keeps the tool block stable when two skills are A/B'd
    (see trial.py for why that matters).
    """
    return (paths.library() / "skills" / f"{name}.md").read_text()


def skill_tools(name: str) -> tuple[str, ...]:
    """The tool names a skill's front matter asks for. Empty for most skills."""
    text = skill(name)
    if not text.startswith("---"):
        return ()
    header = text.split("---", 2)[1]
    for line in header.splitlines():
        if line.startswith("tools:"):
            return tuple(t.strip() for t in line[len("tools:"):].split(","))
    return ()


def tool_schema(name: str) -> dict[str, Any]:
    """What the model sees: name, description, input schema."""
    return json.loads((paths.library() / "tools" / f"{name}.json").read_text())


def tool_runner(name: str) -> Callable[..., str]:
    """What the loop calls: a plain Python function `run(**arguments) -> str`.

    Loaded from the file next to the schema. This is the one place a name in
    the library resolves to code rather than text.
    """
    path = paths.library() / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run
