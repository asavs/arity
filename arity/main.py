"""The front door. `arity`.

    arity "fix the linter on app.ts"    one message to reception, print the reply, exit
    arity                               read lines from the keyboard until you stop

Both are the same thing: a person sending a Message to the bot called
"reception", and reception's answer coming back out through the Console
transport. There is no TUI and no headless flag, because there is no
full-screen mode to be headless from. A TUI is a later surface on the
Transport seam.

When the conversation ends, every kernel the loop woke is retired: its own
report and the archivist's account go to its ledger, and the next time it is
cast it wakes knowing what happened here.
"""
from __future__ import annotations

import sys

from .loop import Loop, default_loop
from .types import Message

RECEPTION = "reception"
USER = "asa"


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv

    reception = _wake_reception()
    loop = default_loop(reception.spec)
    loop.live[RECEPTION] = reception

    try:
        if argv:
            loop.run(reception, Message(sender=USER, text=" ".join(argv)))
        else:
            for line in sys.stdin:
                if line.strip():
                    loop.run(reception, Message(sender=USER, text=line.rstrip("\n")))
    finally:
        loop.retire_all()


def _wake_reception():
    from . import cast
    return cast.birth(RECEPTION)


if __name__ == "__main__":
    main()
