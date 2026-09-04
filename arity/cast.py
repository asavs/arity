"""Cast: backward-compatible adapter over linker.py."""
from __future__ import annotations

import sys
from typing import Any

from . import linker
from .types import Spec, State

LinkError = linker.LinkError
WAKE = linker.WAKE
HOW_MESSAGES_WORK = linker.HOW_MESSAGES_WORK
LEDGER_CAP = linker.LEDGER_CAP
DEFAULT_MODEL = linker.DEFAULT_MODEL


def bots() -> dict[str, dict]:
    """The staff list."""
    return linker.bots()


def is_bot(name: str) -> bool:
    """Check if name is a configured bot."""
    return linker.is_bot(name)


def choose(bot: str) -> Spec:
    """Select a candidate Spec for a bot."""
    # If cast.DEFAULT_MODEL was modified (e.g. in tests), keep linker in sync
    linker.DEFAULT_MODEL = DEFAULT_MODEL
    return linker.select_spec(bot)


def resolve(spec: Spec, bot: str, user: str = "asa") -> State:
    """Compile Spec into State."""
    return linker.link(spec, bot, user=user)


def birth(bot: str, user: str = "asa") -> State:
    """Choose Spec and compile into State."""
    linker.DEFAULT_MODEL = DEFAULT_MODEL
    return linker.spawn(bot, user=user)
