"""Contracts for the no-user Arity namespace and state clean break."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path


_FORMER_NAME = "".join(("gork", "bot"))
_FORMER_STATE = "." + _FORMER_NAME
_ACTIVE_DIRECTORIES = (
    ".github",
    "acceptance",
    "arity",
    "docs",
    _FORMER_NAME,
    "tests",
)
_ACTIVE_ROOT_FILES = (
    ".gitignore",
    "MANIFEST.in",
    "README.md",
    "SECURITY.md",
    "TODO.md",
    "pyproject.toml",
)
_TEXT_SUFFIXES = {".md", ".py", ".toml", ".txt", ".yml", ".yaml"}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _isolated_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONPATH"] = str(_repository_root())
    environment.pop("PYTHONHOME", None)
    return environment


def _active_files(repository: Path) -> tuple[Path, ...]:
    selected: set[Path] = set()
    for name in _ACTIVE_DIRECTORIES:
        root = repository / name
        if root.is_dir():
            selected.update(path for path in root.rglob("*") if path.is_file())
    selected.update(
        path for name in _ACTIVE_ROOT_FILES
        if (path := repository / name).is_file()
    )
    selected.update(repository.glob("*.py"))
    return tuple(sorted(selected))


def test_arity_is_the_import_namespace() -> None:
    package = importlib.import_module("arity")

    assert package.__name__ == "arity"
    assert Path(package.__file__).resolve().parent.name == "arity"
    assert isinstance(package.__version__, str)
    assert package.__version__


def test_python_m_arity_is_the_module_entrypoint(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    completed = subprocess.run(
        [sys.executable, "-m", "arity", "--version"],
        cwd=tmp_path,
        env=_isolated_environment(home),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("Arity ")
    assert completed.stderr == ""


def test_former_namespace_and_root_script_are_absent() -> None:
    repository = _repository_root()

    assert not (repository / _FORMER_NAME).exists()
    assert not (repository / f"{_FORMER_NAME}.py").exists()

    probe = (
        "import importlib.util,sys; "
        "sys.path.insert(0,sys.argv[1]); "
        "raise SystemExit(importlib.util.find_spec(sys.argv[2]) is not None)"
    )
    completed = subprocess.run(
        [sys.executable, "-S", "-c", probe, str(repository), _FORMER_NAME],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_active_repository_scope_has_no_former_name() -> None:
    repository = _repository_root()
    needle = _FORMER_NAME.casefold()
    findings: list[str] = []

    for path in _active_files(repository):
        relative = path.relative_to(repository).as_posix()
        if needle in relative.casefold():
            findings.append(f"{relative}:path")
        if path.suffix.casefold() not in _TEXT_SUFFIXES and path.name not in _ACTIVE_ROOT_FILES:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        hit_lines = [str(number) for number, line in enumerate(lines, 1) if needle in line.casefold()]
        if hit_lines:
            findings.append(f"{relative}:lines={','.join(hit_lines)}")

    visible = findings[:80]
    if len(findings) > len(visible):
        visible.append(f"... and {len(findings) - len(visible)} more")
    assert not findings, (
        f"former product name remains in active scope ({len(findings)} findings):\n"
        + "\n".join(visible)
    )
