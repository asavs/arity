"""The front door. `arity`.

    arity "fix the linter on app.ts"      one message to reception, print the reply, exit
    arity 3 "fix the linter on app.ts"    the same message to three kernels; you pick the winner
    arity                                 read lines from the keyboard until you stop

All three are the same thing: a person sending a Message to a bot, and the
bot's answer coming back out through the Console transport. There is no TUI
and no headless flag, because there is no full-screen mode to be headless
from. A TUI is a later surface on the Transport seam.

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

The number. `arity 3 "..."`, or a line starting with a number, is the name
of the project doing what it says: N kernels on one moment. Whoever you are
talking to is forked onto the N best models with quota, all N answer, the
answers print side by side, and you type the number that won. The winner's
answer becomes the conversation's answer, and the scorecard remembers who
won this kind of task.

When the conversation ends, every kernel the loop woke is retired: its own
report and the archivist's account go to its ledger, and the next time it is
cast it wakes knowing what happened here.
"""
from __future__ import annotations

import sys

from . import cast, trial
from .loop import Loop
from .types import Message, Send

RECEPTION = "reception"
USER = "asa"


class Collect:
    """TransportSeam that keeps Sends instead of printing them, for trials."""
    def __init__(self):
        self.sends: list[Send] = []

    def emit(self, effect: Send) -> None:
        self.sends.append(effect)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    loop = Loop()
    talking_to = RECEPTION

    def say(line: str) -> None:
        nonlocal talking_to
        # @name switches who you are talking to.
        if line.startswith("@"):
            name, _, rest = line[1:].partition(" ")
            if cast.is_bot(name):
                talking_to = name
                line = rest
            if not line.strip():
                print(f"(now talking to {talking_to})")
                return
        # A leading number is a trial.
        head, _, rest = line.partition(" ")
        if head.isdigit() and rest.strip():
            run_trial(loop, talking_to, int(head), rest)
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


def run_trial(loop: Loop, bot: str, n: int, text: str) -> None:
    base = loop.wake(bot)
    specs = trial.candidates(base.spec, n)
    if not specs:
        print("(no seat has quota for a trial)")
        return
    if len(specs) < n:
        print(f"(only {len(specs)} models have quota; running those)")

    # The forks talk into a bucket, not the console, so the answers can be
    # printed side by side with a number in front of each.
    quiet = Loop(transport=Collect())
    forks = trial.run(base, specs, Message(sender=USER, text=text), loop=quiet)

    for i, fork in enumerate(forks, 1):
        print(f"\n[{i}] {fork.spec.model} on {fork.spec.seat}\n{fork.output}")

    pick = ask_pick(len(forks))
    trial.judge(forks, pick)

    if pick is not None:
        # The winner's answer becomes this conversation's answer.
        base.messages = list(forks[pick].messages)
        base.output = forks[pick].output
        print(f"(kept {forks[pick].spec.model}'s answer)")


def ask_pick(n: int) -> int | None:
    """Which one won? Enter for none. Skipped when nobody is at the keyboard."""
    if not sys.stdin.isatty():
        return None
    raw = input(f"\nwhich won? (1-{n}, enter for none) ").strip()
    return int(raw) - 1 if raw.isdigit() and 1 <= int(raw) <= n else None


if __name__ == "__main__":
    main()
