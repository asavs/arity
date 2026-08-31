"""Build HEAD, install its wheel in a fresh venv, and run the resolution gate."""
from __future__ import annotations

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
