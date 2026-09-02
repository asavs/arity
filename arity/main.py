"""The front door. `arity`.

    arity "fix the linter on app.ts"    one message to reception, print the reply, exit
    arity                               read lines from the keyboard until you stop

Both are the same thing: a person sending a Message to a bot, and the bot's
answer coming back out through the Console transport. There is no TUI and
no headless flag, because there is no full-screen mode to be headless from.
A TUI is a later surface on the Transport seam.

Who you are talking to. You start at reception, like calling a company.
Two things can happen from there, and both are the caller's choice, not the
bots':

    delegate    you ask reception for something; reception messages the
                engineer with the message tool, gets the answer, and tells you.
                Reception stays in the middle for that task.

    transfer    you address a teammate directly by starting a line with
                @engineer. From then on your lines go to the engineer until
                you address someone else. Reception is not in the loop.

Reception can suggest a transfer ("talk to the engineer, type @engineer")
but cannot force one. The phone is in your hand.

When the conversation ends, every kernel the loop woke is retired: its own
report and the archivist's account go to its ledger, and the next time it is
cast it wakes knowing what happened here.
"""
from __future__ import annotations

import sys

from . import cast
from .loop import Loop
from .types import Message

RECEPTION = "reception"
USER = "asa"


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    loop = Loop()
    talking_to = RECEPTION

    def say(line: str) -> None:
        nonlocal talking_to
        if line.startswith("@"):
            name, _, rest = line[1:].partition(" ")
            if cast.is_bot(name):
                talking_to = name
                line = rest
            if not line.strip():
                print(f"(now talking to {talking_to})")
                return
        loop.run(loop.wake(talking_to), Message(sender=USER, text=line))

    try:
        if argv:
            say(" ".join(argv))
        else:
            for raw in sys.stdin:
                if raw.strip():
                    say(raw.rstrip("\n"))
    finally:
        loop.retire_all()


if __name__ == "__main__":
    main()
