"""Tests for linker symbol validation, LinkError, and sources caching."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import pytest

from arity import cast, linker, sources
from arity.types import Spec


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Run in an isolated ARITY_HOME and ARITY_CACHE."""
    monkeypatch.setenv("ARITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ARITY_CACHE", str(tmp_path / "cache"))


def test_link_error_missing_tool():
    """Defends against dogfood crash: missing tool raises LinkError upfront with clear error."""
    spec = Spec(
        seat="mock",
        model="mock-1",
        role="generalist",
        tools=("nonexistent_search_tool",),
    )

    # validate should return diagnostic errors
    errors = linker.validate(spec, "tester")
    assert any("nonexistent_search_tool" in e for e in errors)
    assert any("Available tools:" in e for e in errors)

    # linker.link and cast.resolve should both raise LinkError before any model is invoked
    with pytest.raises(linker.LinkError) as exc_info:
        linker.link(spec, "tester")
    assert "nonexistent_search_tool" in str(exc_info.value)
    assert "Available tools:" in str(exc_info.value)

    with pytest.raises(cast.LinkError) as exc_info2:
        cast.resolve(spec, "tester")
    assert "nonexistent_search_tool" in str(exc_info2.value)


def test_link_error_missing_role():
    """Missing role raises LinkError upfront listing available roles."""
    spec = Spec(
        seat="mock",
        model="mock-1",
        role="nonexistent_role_wizard",
        tools=("read_file",),
    )

    with pytest.raises(linker.LinkError) as exc_info:
        linker.link(spec, "tester")
    assert "nonexistent_role_wizard" in str(exc_info.value)
    assert "Available roles:" in str(exc_info.value)


def test_link_error_missing_skill():
    """Missing local skill raises LinkError upfront listing available skills."""
    spec = Spec(
        seat="mock",
        model="mock-1",
        role="generalist",
        skills=("missing_skill_xyz",),
    )

    with pytest.raises(linker.LinkError) as exc_info:
        linker.link(spec, "tester")
    assert "missing_skill_xyz" in str(exc_info.value)
    assert "Available skills:" in str(exc_info.value)


def test_builtins_fallback_without_seed_copy(tmp_path, monkeypatch):
    """Builtin tools and roles are resolvable even in an empty ARITY_HOME."""
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setenv("ARITY_HOME", str(empty_home))

    # Builtin tools exist via package builtins
    s_path, r_path = sources.find_tool_files("read_file")
    assert s_path is not None and s_path.exists()
    assert r_path is not None and r_path.exists()

    # Search tool is also in builtins
    s_search, r_search = sources.find_tool_files("search")
    assert s_search is not None and s_search.exists()
    assert r_search is not None and r_search.exists()

    # Linking a spec using builtins succeeds
    spec = Spec(
        seat="mock",
        model="mock-1",
        role="generalist",
        tools=("read_file", "search"),
    )
    state = linker.link(spec, "tester")
    assert state.bot == "tester"
    tool_names = {t["name"] for t in state.tools}
    assert "read_file" in tool_names
    assert "search" in tool_names
    assert "message" in tool_names  # virtual tool added automatically


def test_remote_coordinate_parsing():
    """Remote coordinates parse into GitHub raw URLs cleanly."""
    url, rev, path = sources.parse_remote_reference("github.com/dmmulroy/oxlinter-rules@v1:rules.md")
    assert url == "https://raw.githubusercontent.com/dmmulroy/oxlinter-rules/v1/rules.md"
    assert rev == "v1"
    assert path == "rules.md"

    url2, rev2, path2 = sources.parse_remote_reference("owner/repo:skill.md")
    assert url2 == "https://raw.githubusercontent.com/owner/repo/main/skill.md"
    assert rev2 == "main"
    assert path2 == "skill.md"


def test_remote_skill_caching(tmp_path, monkeypatch):
    """Remote skill is fetched once, cached on disk, and served from cache."""
    cache = tmp_path / "custom_cache"
    monkeypatch.setenv("ARITY_CACHE", str(cache))

    ref = "github.com/fake-org/fake-repo@v1:skill.md"

    # Pre-seed the cache file to simulate a fetched skill without needing internet
    import hashlib
    key = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:16]
    sources_cache = cache / "sources"
    sources_cache.mkdir(parents=True, exist_ok=True)
    cached_file = sources_cache / f"{key}.md"
    cached_file.write_text("---\ntools: read_file\n---\n# Cached Remote Skill\nAlways lint.", encoding="utf-8")

    # load_skill reads from cache
    content = sources.load_skill(ref)
    assert "# Cached Remote Skill" in content

    # Linking a spec with this remote skill succeeds and picks up front-matter tools
    spec = Spec(
        seat="mock",
        model="mock-1",
        role="generalist",
        skills=(ref,),
    )
    state = linker.link(spec, "tester")
    assert "# Cached Remote Skill" in state.system_text()
    tool_names = {t["name"] for t in state.tools}
    assert "read_file" in tool_names
