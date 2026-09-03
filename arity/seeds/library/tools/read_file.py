"""The runner half of the read_file tool. The loop calls run(**arguments)."""
from pathlib import Path


def run(path: str) -> str:
    try:
        p = Path(path)
        if not p.exists():
            return f"error: file '{path}' does not exist"
        if not p.is_file():
            return f"error: '{path}' is not a file"
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"error: {exc}"
