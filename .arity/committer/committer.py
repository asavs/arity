"""Blind committer. Diff in, doctrine beside it, message out.

  committer.py rewrite [rev] [--apply]
  committer.py draft

The model sees the doctrine and the diff. It does not see the session
that wrote the code. An old subject is passed as untrusted text.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DOCTRINE = Path(os.environ.get(
    "COMMITTER_DOCTRINE",
    r"C:/Users/example/Projects/game grammar/sol eremus/.arity",
))
DIJKSTRA = Path(os.environ.get(
    "COMMITTER_DIJKSTRA",
    r"C:/Users/example/.arity/library/roles/dijkstra.md",
))
MODEL = os.environ.get("COMMITTER_MODEL", "sonnet")

DOCTRINE_FILES = (
    "committer.md",
    "roles/committer/role.md",
    "antipatterns/linkedin_buzzwords.md",
    "antipatterns/cargo_cult_prefixes.md",
    "antipatterns/tutorial_markers.md",
    "antipatterns/silent_fallback.md",
)

CONTRACT = """\
You write one git commit message. Nothing else.

The diff is the only source of truth. An old message, if attached, is
untrusted. Do not preserve its metaphors. Name the mechanism the diff
implements. A name in the old subject or in a comment is not evidence.

Output only the message. No preamble. No fences. No commentary.

Subject: natural imperative English, at most 60 characters. No
type(scope): prefix. The subject must not need the words "and also".

Body: what the mechanism does, in concrete nouns and numbers that
appear in the diff. State at least one non-goal. Do not invent frame
times, FPS, tick counts, or benchmarks that are not in the diff. Do
not open an empirical line with "Validated with" or "Confirmed that".

If the diff contains two concepts, the subject names the load-bearing
one and the body names the other as not belonging in this commit.
"""

PREFIX = re.compile(
    r"^(feat|fix|chore|docs|refactor|test|style|perf|ci|build|revert|examples)(\([^)]+\))?:",
    re.I,
)
BANNED = re.compile(
    r"\b(demonstrate|leverage|leveraging|robust|seamless|utilize|unlock|empower|streamline)\b",
    re.I,
)
TUTORIAL = re.compile(r"\b(step|phase|task)\s+\d+", re.I)
METRIC = re.compile(r"\b\d+(?:\.\d+)?\s*(?:fps|ms|us|ticks)\b", re.I)
COUNTED = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?[kK]?|\b\d{4,}(?:\.\d+)?[kK]?|\b\d+[kK]\b")
FILLER = re.compile(r"\b(validated with|confirmed that)\b", re.I)


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def load_doctrine() -> str:
    parts = [CONTRACT, ""]
    missing = []
    for rel in DOCTRINE_FILES:
        path = DOCTRINE / rel
        if not path.is_file():
            missing.append(str(path))
            continue
        parts.append(f"\n\n# Source: {rel}\n\n")
        parts.append(path.read_text(encoding="utf-8"))
    if not DIJKSTRA.is_file():
        missing.append(str(DIJKSTRA))
    else:
        parts.append("\n\n# Source: dijkstra.md\n\n")
        parts.append(DIJKSTRA.read_text(encoding="utf-8"))
    if missing:
        raise SystemExit("missing doctrine:\n" + "\n".join(missing))
    return "".join(parts)


def diff_text(rev: str | None, cwd: Path) -> tuple[str, str]:
    if rev is None:
        staged = git("diff", "--cached", "--stat", cwd=cwd)
        staged_body = git("diff", "--cached", "-U5", "--", ".", ":(exclude)vendor", cwd=cwd)
        if staged_body.strip():
            return "", f"DIFF STAT:\n{staged}\n\nDIFF:\n{staged_body}"
        work = git("diff", "--stat", cwd=cwd)
        body = git("diff", "-U5", "--", ".", ":(exclude)vendor", cwd=cwd)
        if not body.strip():
            raise SystemExit("no staged or unstaged diff")
        return "", f"DIFF STAT:\n{work}\n\nDIFF:\n{body}"

    old = git("show", "-s", "--format=%B", rev, cwd=cwd).strip()
    stat = git("show", "--stat", "--format=", rev, cwd=cwd)
    body = git("show", "-U5", "--format=", rev, "--", ".", ":(exclude)vendor", cwd=cwd)
    omitted = git("show", "--name-only", "--format=", rev, "--", "vendor", cwd=cwd).strip()
    note = f"\n\nOMITTED (vendored, not evidence):\n{omitted}\n" if omitted else ""
    return old, f"DIFF STAT:\n{stat}\n\nDIFF:\n{body}{note}"


def prompt(old: str, diff: str, errors: list[str]) -> str:
    chunks = []
    if old:
        chunks.append(
            "OLD MESSAGE (untrusted; do not preserve its metaphors or its nouns):\n"
            + old
        )
    chunks.append(diff)
    if errors:
        chunks.append(
            "The previous draft failed these gates. Do not repeat its subject. "
            "Fix every gate. Still output only the message.\n- " + "\n- ".join(errors)
        )
    return "\n\n".join(chunks)


def call_model(system: Path, user: str) -> str:
    result = subprocess.run(
        [
            "claude", "-p",
            "--safe-mode",
            "--tools", "",
            "--no-session-persistence",
            "--permission-mode", "dontAsk",
            "--permission-prompts", "none",
            "--model", MODEL,
            "--system-prompt-file", str(system),
            "--max-budget-usd", "2",
        ],
        input=user,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=Path(tempfile.gettempdir()),
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "claude failed")
    return result.stdout.strip()


def unwrap(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:\w+)?\n(.*)```$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return text.strip() + "\n"


def lint(message: str, diff: str) -> list[str]:
    lines = message.strip().splitlines()
    if not lines or not lines[0].strip():
        return ["empty message"]
    subject = lines[0].strip()
    errors = []
    if len(subject) > 60:
        errors.append(f"subject is {len(subject)} characters, budget is 60. Rejected subject: {subject!r}")
    if PREFIX.match(subject):
        errors.append("subject uses a conventional-commit prefix")
    if re.search(r"\band also\b", subject, re.I):
        errors.append("subject needs 'and also'; split the commit")
    if TUTORIAL.search(message):
        errors.append("tutorial marker (Step/Phase/Task N)")
    hit = BANNED.search(message)
    if hit:
        errors.append(f"banned word: {hit.group(0)}")
    if FILLER.search(message):
        errors.append("empirical line uses a filler preamble")
    diff_digits = re.sub(r",", "", diff)
    for raw in COUNTED.findall(message):
        digits = re.sub(r",", "", raw)
        if digits.lower() not in diff.lower() and digits.lower() not in diff_digits.lower():
            errors.append(f"number {raw!r} is not in the diff; do not copy it from the old message")
    for metric in METRIC.findall(message):
        if metric.lower() not in diff.lower():
            errors.append(f"metric {metric!r} is not in the diff; do not invent it")
    if len(lines) < 3:
        errors.append("body is missing; state the mechanism and one non-goal")
    return errors


def amend(message: str, cwd: Path) -> None:
    upstream = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=cwd, capture_output=True, text=True,
    )
    if upstream.returncode == 0:
        pushed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "HEAD", "@{upstream}"],
            cwd=cwd,
        )
        if pushed.returncode == 0:
            raise SystemExit("HEAD is already on the upstream; refusing to amend")
    path = cwd / ".git" / "COMMIT_EDITMSG_COMMITTER.txt"
    path.write_text(message, encoding="utf-8")
    subprocess.run(["git", "commit", "--amend", "-F", str(path)], cwd=cwd, check=True)
    path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Draft a commit message from the diff and the committer doctrine.")
    parser.add_argument("mode", choices=("rewrite", "draft"))
    parser.add_argument("rev", nargs="?", default="HEAD")
    parser.add_argument("--apply", action="store_true", help="amend HEAD with the drafted message")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.mode == "draft" and args.rev != "HEAD":
        raise SystemExit("draft reads the working tree; pass no rev")

    repo = args.repo.resolve()
    system_text = load_doctrine()
    old, diff = diff_text(None if args.mode == "draft" else args.rev, repo)
    errors: list[str] = []
    message = ""
    with tempfile.TemporaryDirectory() as tmp:
        system = Path(tmp) / "system.md"
        system.write_text(system_text, encoding="utf-8")
        for _ in range(2):
            message = unwrap(call_model(system, prompt(old, diff, errors)))
            errors = lint(message, diff)
            if not errors:
                break
            print("retry: " + "; ".join(errors), file=sys.stderr)
    sys.stdout.write(message)
    if errors:
        print("gates still open: " + "; ".join(errors), file=sys.stderr)
        raise SystemExit(1)
    if args.apply:
        if args.mode != "rewrite" or args.rev not in ("HEAD", git("rev-parse", "HEAD", cwd=repo).strip()):
            raise SystemExit("--apply only amends the rewrite of HEAD")
        amend(message, repo)


if __name__ == "__main__":
    main()
