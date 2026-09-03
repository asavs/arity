"""The front door. `arity`.

    arity "fix the linter on app.ts"      one message to reception, print the reply, exit
    arity 3 "fix the linter on app.ts"    the same message to three kernels; you pick the winner
    arity                                 read lines from the keyboard until you stop
    arity resume [session]                fold a crashed session's journal back and keep going
    arity doctor                          keys, seats, locks, library, CLIs: what is and is not in place

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

`ARITY_TRACE=1` prints one line per hop to stderr (seams.Trace), so the
conversation on stdout can be read next to the events and effects behind it.
"""
from __future__ import annotations

import os
import sys
import threading
import time

from . import cast, library, paths, seats, store, trial
from .loop import Loop, LOCK_TTL, _alive
from .seams import ObserverSeam, Quiet, Trace
from .types import Message, Send

RECEPTION = "reception"
USER = "asa"


def observer() -> ObserverSeam:
    return Trace() if os.environ.get("ARITY_TRACE") else Quiet()


class Collect:
    """TransportSeam that keeps Sends instead of printing them, for trials."""
    def __init__(self):
        self.sends: list[Send] = []

    def emit(self, effect: Send) -> None:
        self.sends.append(effect)


class Warmer(threading.Thread):
    """Keeps the current bot's cached prefix warm while you are typing.

    Every few seconds, if the seat's warm window is about to lapse and the
    seat is a subscription (cheap to ping), send one keepalive. At most a few
    per silence: a person who has walked away is not worth chasing, and the
    next real message simply pays cold. See loop.keep_warm.
    """
    MARGIN, PINGS_PER_SILENCE = 30, 3

    def __init__(self, loop: Loop):
        super().__init__(daemon=True)
        self.loop, self.bot, self.pings = loop, RECEPTION, 0

    def talking_to(self, bot: str) -> None:
        self.bot, self.pings = bot, 0

    def run(self) -> None:
        while True:
            time.sleep(5)
            state = self.loop.live.get(self.bot)
            if not state or self.pings >= self.PINGS_PER_SILENCE:
                continue
            left = self.loop.warmth(state)
            if left is not None and left < self.MARGIN and seats.lookup(state.spec.seat).kind == "subscription":
                if self.loop.keep_warm(state):
                    self.pings += 1
                    print("(kept the cache warm)")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    loop = Loop(observer=observer())
    talking_to = RECEPTION
    warmer = Warmer(loop)
    warmer.start()

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
        warmer.talking_to(talking_to)

    try:
        if argv and argv[0] == "doctor":
            doctor()
            return
        if argv and argv[0] == "resume":
            # `arity resume [session]`: fold a journal back into a live kernel and keep going.
            unfinished = store.unfinished()
            session = argv[1] if len(argv) > 1 else (unfinished[-1] if unfinished else None)
            if not session:
                print("(nothing to resume)")
                return
            state = loop.resume(session)
            talking_to = state.bot
            print(f"(resumed {session} as {talking_to}; {len(state.messages)} messages)")
            for raw in sys.stdin:
                if raw.strip():
                    say(raw.rstrip("\n"))
        elif argv:
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
    quiet = Loop(transport=Collect(), observer=observer())
    forks = trial.run(base, specs, Message(sender=USER, text=text), loop=quiet)

    for i, fork in enumerate(forks, 1):
        print(f"\n[{i}] {fork.spec.model} on {fork.spec.seat}\n{fork.output}")

    pick = ask_pick(len(forks))
    trial.judge(forks, pick)

    if pick is None:
        print("(no winner; nothing kept)")
        return
    # The winner's turn becomes this conversation's turn: its events go into
    # the base's journal, so the base is still a fold over its own file.
    winner = forks[pick]
    store.adopt(base.session_id, winner.session_id)
    base.messages = list(winner.messages)
    base.output = winner.output
    print(f"(kept {winner.spec.model}'s answer)")


def doctor() -> None:
    """One line per thing that can be wrong before a model is ever called.

    Nothing here spends tokens: a seat "answers" if its URL returns any HTTP
    status at all, which a bare GET gets for free. Nothing is fixed, only said.
    """
    import shutil
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone

    def line(ok: bool | None, text: str) -> None:
        print(f"  {'ok' if ok else '--' if ok is None else '!!'}  {text}")

    print(f"home  {paths.home()}")

    print("seats")
    now = datetime.now(timezone.utc).isoformat()
    for s in seats.all_seats():
        if s.provider == "mock":
            continue
        keyed = not s.key_env or s.key_env in os.environ
        line(keyed, f"{s.id}: {s.key_env or 'no key needed'} {'set' if keyed else 'missing'}")
        if s.kind == "subscription" and s.resets_at and s.resets_at < now:
            line(None, f"{s.id}: quota reset at {s.resets_at} has passed; remaining still says {s.remaining}")
        elif s.remaining <= 0:
            line(False, f"{s.id}: no quota left")
        if keyed and s.url:
            try:
                urllib.request.urlopen(urllib.request.Request(s.url, method="GET"), timeout=5)
                line(True, f"{s.id}: {s.url} answers")
            except urllib.error.HTTPError as e:      # any status is an answer; the endpoint is there
                line(True, f"{s.id}: {s.url} answers (HTTP {e.code} to a bare GET)")
            except Exception as e:
                line(False, f"{s.id}: {s.url} unreachable: {str(e)[:80]}")

    print("locks")
    locks = list(paths.locks().glob("*.lock"))
    if not locks:
        line(True, "no bot is live")
    for lock in locks:
        pid = int(lock.read_text() or 0)
        age = time.time() - lock.stat().st_mtime
        stale = age > LOCK_TTL or not _alive(pid)
        line(not stale, f"{lock.stem}: pid {pid} {'gone' if not _alive(pid) else 'alive'}, "
                        f"{age / 60:.0f} min old{' (stale; delete it)' if stale else ''}")

    print("store")
    open_sessions = store.unfinished()
    line(not open_sessions, f"{len(store.sessions())} sessions, {len(open_sessions)} unfinished"
                            + (f": {' '.join(open_sessions[-5:])}" if open_sessions else ""))

    print("library")
    for bot, entry in cast.bots().items():
        role = paths.library() / "roles" / f"{entry['role']}.md"
        line(role.exists(), f"{bot}: role {entry['role']}{'' if role.exists() else ' missing'}")
        for skill in entry.get("skills", ()):
            line((paths.library() / "skills" / f"{skill}.md").exists(), f"{bot}: skill {skill}")
    for schema in sorted((paths.library() / "tools").glob("*.json")):
        if schema.stem == "message":            # the post office runs this one, not a runner
            continue
        runner = schema.with_suffix(".py").exists()
        line(runner, f"tool {schema.stem}: schema{' and runner' if runner else ', no runner'}")
    for skill in sorted((paths.library() / "skills").rglob("*.md")):
        name = skill.relative_to(paths.library() / "skills").with_suffix("").as_posix()
        for tool in library.skill_tools(name):
            exists = (paths.library() / "tools" / f"{tool}.json").exists()
            line(exists, f"skill {name} wants tool {tool}{'' if exists else ', which is missing'}")

    print("commands")
    for name in ("claude", "codex", "agy", "oxlint"):
        found = shutil.which(name)
        line(bool(found) or None, f"{name}: {found or 'not on PATH'}")


def ask_pick(n: int) -> int | None:
    """Which one won? Enter for none. Skipped when nobody is at the keyboard."""
    if not sys.stdin.isatty():
        return None
    try:
        raw = input(f"\nwhich won? (1-{n}, enter for none) ").strip()
    except EOFError:
        return None
    return int(raw) - 1 if raw.isdigit() and 1 <= int(raw) <= n else None


if __name__ == "__main__":
    main()
