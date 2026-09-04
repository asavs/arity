"""The runner half of the bash tool. The loop calls run(**arguments)."""
import subprocess


def run(command: str) -> str:
    try:
        done = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = done.stdout + done.stderr
        if out:
            return out
        return "(no output, exit 0)" if done.returncode == 0 else f"(exit {done.returncode})"
    except subprocess.TimeoutExpired:
        return "error: command timed out after 60s"
    except Exception as exc:
        return f"error: {exc}"
