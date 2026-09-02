"""The runner half of the run_oxlint tool. The loop calls run(**arguments)."""
import subprocess


def run(path: str) -> str:
    done = subprocess.run(["oxlint", path], capture_output=True, text=True)
    return done.stdout or done.stderr or "clean"
