"""Build HEAD, install its wheel in a fresh venv, and run package gates."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from configparser import ConfigParser
from email.parser import Parser
from pathlib import Path


def run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def venv_arity(root: Path) -> Path:
    return root / ("Scripts/arity.exe" if sys.platform == "win32" else "bin/arity")


def verify_wheel_contract(wheel: Path) -> None:
    """Require one Arity package and command with no former public surface."""

    former_name = "".join(("gork", "bot"))
    former_bytes = former_name.encode("ascii")
    with zipfile.ZipFile(wheel) as archive:
        names = tuple(archive.namelist())
        folded_names = {name.casefold() for name in names}
        if any(former_name in name for name in folded_names):
            raise RuntimeError("wheel retains a former package path")
        if "arity/__init__.py" not in folded_names:
            raise RuntimeError("wheel does not contain the arity package")
        if "arity/__main__.py" not in folded_names:
            raise RuntimeError("wheel does not contain the arity module entry point")

        payload_roots = {
            name.split("/", 1)[0]
            for name in names
            if "/" in name
            and not name.split("/", 1)[0].endswith((".dist-info", ".data"))
        }
        if payload_roots != {"arity"}:
            raise RuntimeError(
                "wheel exposes package roots other than arity: "
                + ", ".join(sorted(payload_roots))
            )

        metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_point_files = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        top_level_files = [
            name for name in names if name.endswith(".dist-info/top_level.txt")
        ]
        if len(metadata_files) != 1 or len(entry_point_files) != 1 or len(top_level_files) != 1:
            raise RuntimeError("wheel must contain one metadata, entry-point, and top-level file")

        metadata = Parser().parsestr(archive.read(metadata_files[0]).decode("utf-8"))
        if metadata.get("Name") != "arity":
            raise RuntimeError("wheel distribution metadata is not named arity")

        entry_points = ConfigParser(interpolation=None)
        entry_points.optionxform = str
        entry_points.read_string(archive.read(entry_point_files[0]).decode("utf-8"))
        scripts = (
            dict(entry_points.items("console_scripts"))
            if entry_points.has_section("console_scripts")
            else {}
        )
        if scripts != {"arity": "arity.cli:main"}:
            raise RuntimeError(f"wheel console scripts are not Arity-only: {sorted(scripts)}")

        top_levels = {
            line.strip()
            for line in archive.read(top_level_files[0]).decode("utf-8").splitlines()
            if line.strip()
        }
        if top_levels != {"arity"}:
            raise RuntimeError(f"wheel top-level packages are not Arity-only: {sorted(top_levels)}")

        text_members = tuple(
            name
            for name in names
            if name.casefold().endswith(
                (".md", ".py", ".txt", ".toml", ".yaml", ".yml", "/metadata")
            )
        )
        contaminated = [
            name
            for name in text_members
            if former_bytes in archive.read(name).lower()
        ]
        if contaminated:
            raise RuntimeError(
                "wheel text retains the former product name: "
                + ", ".join(sorted(contaminated))
            )


def verify_watch_command(environment: Path, *, cwd: Path) -> None:
    executable = str(venv_arity(environment))
    command = [executable, "watch", "--ascii", "--no-motion"]
    print("+", " ".join(command), flush=True)
    env = os.environ.copy()
    env["ARITY_STORE"] = "jsonl"
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("GORKBOT_STORE", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    store_root = cwd / ".gorkbot"
    if store_root.exists():
        raise RuntimeError("watch acceptance run root must start without a store")

    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"installed arity watch returned {result.returncode}")
    if result.stdout != b"No persisted trials.\n":
        raise RuntimeError("installed arity watch changed its empty-state output")
    if result.stderr:
        raise RuntimeError("installed arity watch wrote to stderr")
    if not result.stdout.isascii() or b"\x1b" in result.stdout:
        raise RuntimeError("installed arity watch output was not fixed ANSI-free ASCII")

    missing_command = [
        executable,
        "watch",
        "acceptance-missing-trial",
        "--ascii",
        "--no-motion",
    ]
    print("+", " ".join(missing_command), flush=True)
    missing = subprocess.run(
        missing_command,
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if missing.returncode != 3:
        raise RuntimeError(
            f"installed arity watch missing selection returned {missing.returncode}"
        )
    if missing.stdout:
        raise RuntimeError("installed arity watch missing selection wrote to stdout")
    if missing.stderr != b"arity: trial_not_found\n":
        raise RuntimeError("installed arity watch changed its missing-selection error")
    if b"\r" in result.stdout + result.stderr + missing.stdout + missing.stderr:
        raise RuntimeError("installed arity watch emitted translated CRLF output")
    if store_root.exists():
        raise RuntimeError("installed arity watch created a missing store")


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
    ).strip()
    print(f"verifying committed revision {revision}", flush=True)
    with tempfile.TemporaryDirectory(prefix="arity_installed_gate_") as raw_root:
        root = Path(raw_root).resolve()
        archive = root / "snapshot.zip"
        snapshot = root / "snapshot"
        wheels = root / "wheels"
        environment = root / "venv"
        run_root = root / "run"
        snapshot.mkdir()
        wheels.mkdir()
        run_root.mkdir()

        run(["git", "archive", "--format=zip", f"--output={archive}", revision], cwd=repository)
        with zipfile.ZipFile(archive) as source:
            source.extractall(snapshot)

        run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(wheels),
            ],
            cwd=snapshot,
        )
        built = tuple(wheels.glob("arity-*.whl"))
        if len(built) != 1:
            raise RuntimeError(f"expected one Arity wheel, found {len(built)}")
        verify_wheel_contract(built[0])

        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = venv_python(environment)
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-cache-dir",
                str(built[0]),
            ],
            cwd=run_root,
        )
        verify_watch_command(environment, cwd=run_root)
        run(
            [
                str(python),
                "-I",
                "-B",
                str(snapshot / "acceptance" / "resolution_envelope.py"),
            ],
            cwd=run_root,
        )


if __name__ == "__main__":
    main()
