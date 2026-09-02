"""Where things live on disk.

Two kinds of files, two homes:

    the package     code, and the seeds: a starter library, staff list, seat table
    ~/.arity        everything a person edits and everything the system writes

        ~/.arity/library/     roles, skills, tools        (edited by people)
        ~/.arity/bots.json    the staff list               (edited by people)
        ~/.arity/seats.json   seats and quota left         (edited by people, updated by the wire)
        ~/.arity/store/       one JSONL per session        (written by the loop)
        ~/.arity/ledger/      one JSONL per bot            (written at death)
        ~/.arity/locks/       one file per live bot        (presence; see loop.py)

The first time anything asks for the home folder, the seeds are copied in.
`ARITY_HOME` overrides the location, which is how a test or a second
person on one machine gets their own world.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

SEEDS = Path(__file__).parent / "seeds"


def home() -> Path:
    root = Path(os.environ.get("ARITY_HOME", Path.home() / ".arity"))
    if not (root / "bots.json").exists():
        seed(root)
    return root


def seed(root: Path) -> None:
    """Copy the starter files in. Never overwrites: a person's edits are theirs."""
    root.mkdir(parents=True, exist_ok=True)
    for name in ("bots.json", "seats.json"):
        if not (root / name).exists():
            shutil.copy(SEEDS / name, root / name)
    if not (root / "library").exists():
        shutil.copytree(SEEDS / "library", root / "library")
    (root / "store").mkdir(exist_ok=True)
    (root / "ledger").mkdir(exist_ok=True)
    (root / "locks").mkdir(exist_ok=True)


def library() -> Path: return home() / "library"
def bots() -> Path: return home() / "bots.json"
def seats() -> Path: return home() / "seats.json"
def store() -> Path: p = home() / "store"; p.mkdir(exist_ok=True); return p
def ledger() -> Path: p = home() / "ledger"; p.mkdir(exist_ok=True); return p
def locks() -> Path: p = home() / "locks"; p.mkdir(exist_ok=True); return p
