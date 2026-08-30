"""Arity tasks — briefs builders see paired with hidden tests they do not.

A task directory looks like:

    definitions/tasks/<name>/
        brief.md            frontmatter (name, description, module, entrypoint, tags) + the brief
        hidden_tests/       test_*.py files run against each candidate AFTER it finishes

Active local banks in `.gorkbot/tasks/` and `~/.gorkbot/tasks/` override packaged tasks by name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class RaceTask:
    name: str
    brief: str
    description: str = ""
    module: str = ""
    entrypoint: str = ""
    tags: tuple[str, ...] = ()
    hidden_tests: dict[str, str] = field(default_factory=dict)  # filename -> source
    path: Optional[Path] = None

    @property
    def has_hidden_tests(self) -> bool:
        return bool(self.hidden_tests)


def split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a `---` frontmatter block from a markdown body. Values: scalars or [a, b] lists."""
    lines = content.strip().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content.strip()
    meta: dict[str, Any] = {}
    end = None
    current: Optional[str] = None  # key whose value is a "- item" list
    for idx, line in enumerate(lines[1:], 1):
        s = line.strip()
        if s == "---":
            end = idx
            break
        if not s or s.startswith("#"):
            continue
        if s.startswith("-") and current is not None:
            meta.setdefault(current, [])
            if isinstance(meta[current], list):
                meta[current].append(s[1:].strip(" '\""))
            continue
        if ":" not in s:
            continue
        k, v = s.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k] = [x.strip(" '\"") for x in v[1:-1].split(",") if x.strip()]
            current = None
        elif v == "":
            meta[k] = []      # list follows on the next lines
            current = k
        else:
            meta[k] = v.strip(" '\"")
            current = None
    body = "\n".join(lines[end + 1:]).strip() if end is not None else content.strip()
    return meta, body


def load_task_dir(path: Path) -> RaceTask:
    brief_path = path / "brief.md"
    if not brief_path.exists():
        raise FileNotFoundError(f"task {path.name} has no brief.md")
    meta, body = split_frontmatter(brief_path.read_text(encoding="utf-8"))
    hidden: dict[str, str] = {}
    hdir = path / "hidden_tests"
    if hdir.is_dir():
        for p in sorted(hdir.glob("test_*.py")):
            hidden[p.name] = p.read_text(encoding="utf-8")
    return RaceTask(
        name=str(meta.get("name") or path.name),
        brief=body,
        description=str(meta.get("description", "")),
        module=str(meta.get("module", "")),
        entrypoint=str(meta.get("entrypoint", "")),
        tags=tuple(meta.get("tags", []) or ()),
        hidden_tests=hidden,
        path=path,
    )


class TaskBank:
    """Discover race tasks from packaged, project, and user directories (later wins by name)."""

    def __init__(self, extra_dirs: Optional[list[Path]] = None):
        self._tasks: dict[str, RaceTask] = {}
        dirs = [
            Path(__file__).parent / "definitions" / "tasks",
            Path(".gorkbot/tasks"),
            Path.home() / ".gorkbot" / "tasks",
        ] + list(extra_dirs or [])
        for d in dirs:
            if not d.is_dir():
                continue
            for tdir in sorted(p for p in d.iterdir() if p.is_dir()):
                try:
                    task = load_task_dir(tdir)
                except Exception:
                    continue
                self._tasks[task.name.lower()] = task

    def get(self, name: str) -> Optional[RaceTask]:
        return self._tasks.get(name.lower())

    def list_tasks(self) -> list[RaceTask]:
        return sorted(self._tasks.values(), key=lambda t: t.name)
