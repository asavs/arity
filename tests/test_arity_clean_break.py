"""Contracts for the no-user Arity namespace and state clean break."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_defaults_use_only_dot_arity_in_synthetic_home_and_cwd(
    tmp_path: Path, monkeypatch,
) -> None:
    auth = importlib.import_module("arity.auth")
    configuration = importlib.import_module("arity.configuration")
    handlers = importlib.import_module("arity.handlers")
    record_readers = importlib.import_module("arity.record_readers")
    roles = importlib.import_module("arity.roles")
    skills = importlib.import_module("arity.skills")
    tasks = importlib.import_module("arity.tasks")

    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("ARITY_STORE", raising=False)

    former_project = cwd / _FORMER_STATE
    former_home = home / _FORMER_STATE
    (former_project / "records").mkdir(parents=True)
    (former_project / "skills" / "former-only").mkdir(parents=True)
    (former_project / "tasks" / "former-only").mkdir(parents=True)
    former_home.mkdir()
    (former_project / "config.json").write_text(
        json.dumps({"ARITY_STORE": "sqlite"}), encoding="utf-8"
    )
    (former_project / "records" / "sentinel.jsonl").write_text(
        "synthetic former record\n", encoding="utf-8"
    )
    (former_project / "skills" / "former-only" / "SKILL.md").write_text(
        "# former-only\nSynthetic marker\n\nDo nothing.\n", encoding="utf-8"
    )
    (former_home / "auth.json").write_text(
        json.dumps({"former-only": {"marker": "synthetic"}}), encoding="utf-8"
    )
    before = {
        "project": _tree_bytes(former_project),
        "home": _tree_bytes(former_home),
    }

    assert configuration.get_config_value("ARITY_STORE") is None
    token_store = auth.TokenStore()
    assert token_store.auth_path == home / ".arity" / "auth.json"
    assert token_store.load_all() == {}
    token_store.save_credential("synthetic", {"marker": "test-only"})
    assert token_store.auth_path.is_file()

    default_spec = record_readers.configured_store_spec()
    assert default_spec.backend == "jsonl"
    assert default_spec.path == Path(".arity/records")
    writable = handlers.JsonlRecordStore()
    assert writable.root == Path(".arity/records")
    assert (cwd / ".arity" / "records").is_dir()

    (cwd / ".arity" / "config.json").write_text(
        json.dumps({"ARITY_STORE": "sqlite"}), encoding="utf-8"
    )
    assert configuration.get_config_value("ARITY_STORE") == "sqlite"
    sqlite_spec = record_readers.configured_store_spec()
    assert sqlite_spec.backend == "sqlite"
    assert sqlite_spec.path == Path(".arity/records.db")

    (cwd / ".arity" / "skills" / "current-only").mkdir(parents=True)
    (cwd / ".arity" / "skills" / "current-only" / "SKILL.md").write_text(
        "# current-only\nSynthetic marker\n\nDo nothing.\n", encoding="utf-8"
    )
    registry = skills.SkillRegistry()
    assert registry.skills_dir == Path(".arity/skills")
    assert registry.get("current-only") is not None
    assert registry.get("former-only") is None

    (cwd / ".arity" / "tasks" / "current-only").mkdir(parents=True)
    with patch.object(
        tasks,
        "load_task_dir",
        side_effect=lambda path: SimpleNamespace(name=path.name),
    ):
        bank = tasks.TaskBank()
    assert bank.get("current-only") is not None
    assert bank.get("former-only") is None

    role_registry = roles.RoleRegistry(initial_roles=[roles.BUILDER_ROLE])
    definition_dirs = role_registry._definition_dirs("roles")
    assert cwd / ".arity" / "roles" in [cwd / path for path in definition_dirs if not path.is_absolute()]
    assert home / ".arity" / "roles" in definition_dirs
    assert all(_FORMER_STATE not in path.parts for path in definition_dirs)

    assert _tree_bytes(former_project) == before["project"]
    assert _tree_bytes(former_home) == before["home"]


def test_former_environment_names_do_not_change_runtime_behavior(
    tmp_path: Path, monkeypatch,
) -> None:
    auth = importlib.import_module("arity.auth")
    cli = importlib.import_module("arity.cli")
    ledger = importlib.import_module("arity.ledger")
    race = importlib.import_module("arity.race")
    record_readers = importlib.import_module("arity.record_readers")
    tools = importlib.import_module("arity.tools")

    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    former_prefix = _FORMER_NAME.upper()
    former_environment = {
        f"{former_prefix}_CONCURRENCY": "9",
        f"{former_prefix}_STORE": "sqlite",
        f"{former_prefix}_SKIP_QUOTA": "1",
        f"{former_prefix}_NONINTERACTIVE": "1",
    }
    for name, value in former_environment.items():
        monkeypatch.setenv(name, value)
    for name in (
        "ARITY",
        "ARITY_STORE",
        "ARITY_SKIP_QUOTA",
        "ARITY_NONINTERACTIVE",
    ):
        monkeypatch.delenv(name, raising=False)

    assert tools.resolve_arity(default=3) == 3
    assert record_readers.configured_store_spec() == record_readers.StoreSpec(
        "jsonl", Path(".arity/records")
    )

    class SyntheticStore:
        def refresh_if_needed(self, key: str):
            assert key == "seat"
            return {"access": "synthetic", "projectId": "synthetic"}

    expected_quota = {"remaining": 1.0}
    with patch.object(auth, "fetch_antigravity_quota", return_value=expected_quota) as fetch:
        actual_quota = ledger.SeatLedger._antigravity_quota(
            SyntheticStore(), "seat", {"access": "synthetic", "projectId": "synthetic"}
        )
    assert actual_quota == expected_quota
    fetch.assert_called_once()

    args = SimpleNamespace(
        prompt="synthetic brief",
        task=None,
        role=None,
        candidates=None,
        judges=None,
        conference=0,
        tester=False,
        out=None,
        mock=True,
        json=False,
        verbose=False,
    )
    report = SimpleNamespace()
    delivery = SimpleNamespace(answer=None, files=(), receipt="synthetic")
    terminal = SimpleNamespace(isatty=lambda: True)
    with (
        patch.object(cli.sys, "stdin", terminal),
        patch.object(cli.sys, "stdout", terminal),
        patch.object(cli, "safe_print"),
        patch.object(race, "run_front_door", return_value=(report, delivery)) as run,
    ):
        cli.handle_run_command(args)
    assert run.call_args.kwargs["interactive"] is True
