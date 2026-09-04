"""Sources: storage, retrieval, and caching of roles, skills, and tools.

De-complects storage and retrieval:
1. Project / Repo: Owns local configuration, tools, and roles (in git).
2. Remote Sources: Live in external repositories, addressed by coordinate and revision.
3. Cache: Read-only, path-addressed local store (~/.cache/arity/sources).
4. Built-ins: Shipped with the package, immutable fallback.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable
import urllib.request

from . import paths


def cache_dir() -> Path:
    """The local read-only cache directory for fetched dependencies."""
    if "ARITY_CACHE" in os.environ:
        p = Path(os.environ["ARITY_CACHE"])
    elif sys.platform == "win32" and "LOCALAPPDATA" in os.environ:
        p = Path(os.environ["LOCALAPPDATA"]) / "arity" / "cache"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        p = Path(xdg) / "arity" if xdg else Path.home() / ".cache" / "arity"
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_root() -> Path:
    """The working directory or nearest ancestor containing .git or bots.json."""
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists() or (parent / "bots.json").exists() or (parent / "pyproject.toml").exists():
            return parent
    return cwd


def is_remote(reference: str) -> bool:
    """True if reference points to a remote coordinate or URL."""
    return (
        reference.startswith("http://")
        or reference.startswith("https://")
        or reference.startswith("github.com/")
        or "@" in reference
    )


def parse_remote_reference(reference: str) -> tuple[str, str, str]:
    """Parse coordinate into (repo_url_or_raw, revision, subpath).

    Examples:
        https://raw.githubusercontent.com/org/repo/main/skill.md
        github.com/org/repo@v1:path/to/skill.md
        org/repo@main:skill.md
    """
    if reference.startswith("http://") or reference.startswith("https://"):
        return reference, "", ""

    ref = reference.removeprefix("github.com/")
    subpath = ""
    if ":" in ref:
        ref, subpath = ref.split(":", 1)

    if "@" in ref:
        repo, revision = ref.split("@", 1)
    else:
        repo, revision = ref, "main"

    raw_url = f"https://raw.githubusercontent.com/{repo}/{revision}/{subpath}".rstrip("/")
    return raw_url, revision, subpath


def fetch_remote_text(reference: str) -> str:
    """Fetch remote text using cache. Fetched once, stored read-only."""
    key = hashlib.sha256(reference.encode("utf-8")).hexdigest()[:16]
    sources_cache = cache_dir() / "sources"
    sources_cache.mkdir(parents=True, exist_ok=True)
    cached_file = sources_cache / f"{key}.md"

    if cached_file.exists():
        return cached_file.read_text(encoding="utf-8")

    raw_url, _, _ = parse_remote_reference(reference)
    req = urllib.request.Request(
        raw_url,
        headers={"User-Agent": "arity-linker/1.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        content = resp.read().decode("utf-8")

    cached_file.write_text(content, encoding="utf-8")
    return content


# ---------------------------------------------------------------------------
# Search Path for Roles, Skills, and Tools
# ---------------------------------------------------------------------------

def candidate_roots() -> list[Path]:
    """Roots searched in order: project repo -> ARITY_HOME library -> builtins seeds."""
    roots = []
    # 1. Project local directory
    p_root = project_root()
    if (p_root / "library").exists():
        roots.append(p_root / "library")
    roots.append(p_root)

    # 2. User home / ARITY_HOME library
    home_lib = paths.library()
    if home_lib not in roots:
        roots.append(home_lib)

    # 3. Builtins (shipped package seeds)
    seeds_lib = paths.SEEDS / "library"
    if seeds_lib not in roots:
        roots.append(seeds_lib)

    return roots


def find_role_file(name: str) -> Path | None:
    """Find a role markdown file across roots."""
    for root in candidate_roots():
        # Check root/roles/{name}.md and root/{name}.md
        candidate = root / "roles" / f"{name}.md"
        if candidate.exists():
            return candidate
        candidate2 = root / f"{name}.md"
        if candidate2.exists():
            return candidate2
    return None


def find_skill_file(name: str) -> Path | None:
    """Find a local skill markdown file across roots."""
    for root in candidate_roots():
        candidate = root / "skills" / f"{name}.md"
        if candidate.exists():
            return candidate
        candidate2 = root / f"{name}.md"
        if candidate2.exists():
            return candidate2
    return None


def find_tool_files(name: str) -> tuple[Path | None, Path | None]:
    """Find (schema_json, runner_py) for a tool across roots."""
    schema_path: Path | None = None
    runner_path: Path | None = None

    for root in candidate_roots():
        s = root / "tools" / f"{name}.json"
        if not schema_path and s.exists():
            schema_path = s
        r = root / "tools" / f"{name}.py"
        if not runner_path and r.exists():
            runner_path = r

        if schema_path and runner_path:
            break

    return schema_path, runner_path


def available_tools() -> list[str]:
    """All tools currently discovered across candidate roots."""
    found = set()
    for root in candidate_roots():
        tools_dir = root / "tools" if (root / "tools").exists() else root
        for p in tools_dir.glob("*.json"):
            found.add(p.stem)
    return sorted(found)


def available_roles() -> list[str]:
    """All roles currently discovered across candidate roots."""
    found = set()
    for root in candidate_roots():
        roles_dir = root / "roles" if (root / "roles").exists() else root
        for p in roles_dir.glob("*.md"):
            found.add(p.stem)
    return sorted(found)


def available_skills() -> list[str]:
    """All local skills currently discovered across candidate roots."""
    found = set()
    for root in candidate_roots():
        skills_dir = root / "skills" if (root / "skills").exists() else root
        for p in skills_dir.rglob("*.md"):
            try:
                rel = p.relative_to(skills_dir).with_suffix("").as_posix()
                found.add(rel)
            except ValueError:
                found.add(p.stem)
    return sorted(found)


# ---------------------------------------------------------------------------
# Retrieval API
# ---------------------------------------------------------------------------

def load_role(name: str) -> str:
    """Load role text. Raises FileNotFoundError if not found."""
    p = find_role_file(name)
    if not p:
        raise FileNotFoundError(f"Role '{name}' not found. Available: {', '.join(available_roles())}")
    return p.read_text(encoding="utf-8")


def load_skill(reference: str) -> str:
    """Load skill text from remote coordinate or local file."""
    if is_remote(reference):
        return fetch_remote_text(reference)
    p = find_skill_file(reference)
    if not p:
        raise FileNotFoundError(f"Skill '{reference}' not found. Available: {', '.join(available_skills())}")
    return p.read_text(encoding="utf-8")


def parse_skill_tools(skill_text: str) -> tuple[str, ...]:
    """Extract tool names from skill front matter."""
    if not skill_text.startswith("---"):
        return ()
    parts = skill_text.split("---", 2)
    if len(parts) < 3:
        return ()
    for line in parts[1].splitlines():
        if line.strip().startswith("tools:"):
            val = line.strip()[len("tools:"):].strip()
            return tuple(t.strip() for t in val.split(",") if t.strip())
    return ()


def load_tool_schema(name: str) -> dict[str, Any]:
    """Load tool JSON schema."""
    s_path, _ = find_tool_files(name)
    if not s_path:
        raise FileNotFoundError(f"Tool schema for '{name}' not found. Available: {', '.join(available_tools())}")
    return json.loads(s_path.read_text(encoding="utf-8"))


def load_tool_runner(name: str) -> Callable[..., str]:
    """Load tool Python runner function `run(**arguments) -> str`."""
    _, r_path = find_tool_files(name)
    if not r_path:
        raise FileNotFoundError(f"Tool runner for '{name}' not found.")
    spec = importlib.util.spec_from_file_location(name, r_path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load module spec for tool runner '{name}' from {r_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run
