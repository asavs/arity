"""Build HEAD, install its wheel in a fresh venv, and run package gates."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


def run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def venv_arity(root: Path) -> Path:
    return root / ("Scripts/arity.exe" if sys.platform == "win32" else "bin/arity")


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
