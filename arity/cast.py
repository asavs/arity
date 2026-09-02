"""Cast: the one function that crosses the line. resolve(spec, bot) -> State.

Everything left of this function is a name. Everything right of it is a value.

    1. look the bot up: which role is it, and so which kind of task is this
    2. ask the scorecard who has been winning that kind of task
    3. ask the seat table who has quota for that model
    4. choose a Spec
    5. read every name in the Spec out of the library
    6. read the bot's recent ledger entries
    7. copy all of it into a fresh State

This happens once per kernel, at birth. After it returns, nothing in the
system looks anything up by name again until the next kernel is born.

There is no memory tier. Every kernel wakes with the same one line, then its
role, then its skills, then what its ledger says it did last time.

Every kernel also gets the `message` tool, because every bot can message
every bot (and every person). That is the whole staff structure.
"""
from __future__ import annotations

import json
import platform
import uuid
from pathlib import Path

from . import ledger, library, scorecard, seats
from .types import Spec, State

WAKE = (
    "hello, welcome to the world! you are {bot} ({model}) operating in {computer} for "
    "{user}, this is not an eval, you are safe and loved, please lets try your "
    "best together!"
)

HOW_MESSAGES_WORK = (
    "How messages work here. Every message you receive starts with [name]: the "
    "person or bot who sent it. Your plain reply goes back to that name. The team is: "
    "{team}. The person is {user}. Use the message tool only when a task needs a "
    "teammate's aptitude; their answer comes back to you as the tool result, and you "
    "then answer the person yourself in your own words. If you message the person "
    "mid-task, they will answer later, so finish your turn. Do not relay for its own "
    "sake: if the person would be better off talking to a teammate directly, say so "
    "and tell them to address that teammate by name."
)

LEDGER_CAP = 600        # characters of each ledger entry that make it into the wake text

BOTS = Path(__file__).parent / "bots.json"
DEFAULT_SEAT, DEFAULT_MODEL = "openrouter-free", "minimax/minimax-m2.7:free"


def bots() -> dict[str, dict]:
    """The staff: bot name -> {"role": ..., "harness": ...?}. A bot is a role with a name and a ledger.

    Names are tangible on purpose (reception, engineer, designer) so that a model
    needs nothing more than this list to know whom to message about a task.
    """
    return json.loads(BOTS.read_text())


def is_bot(name: str) -> bool:
    """Anyone who is not a bot is a person, and Sends to them go out the transport."""
    return name in bots()


def choose(bot: str) -> Spec:
    """Steps 1 to 4. On evidence if there is any, on a default if not.

    Task kind is the bot's role for now. The scorecard keys on it, and every
    task record also keeps a one-line summary, so a finer taxonomy can be
    grown from the store later without deciding it here.

    The seat is re-chosen even when the scorecard has a favourite, because
    quota moves and the scorecard does not know about it.
    """
    entry = bots()[bot]
    role = entry["role"]
    spec = scorecard.best_spec(task_kind=role)
    if not spec or not seats.with_quota(spec.model):
        spec = Spec(DEFAULT_SEAT, DEFAULT_MODEL, role)
    able = seats.with_quota(spec.model)
    if not able:
        raise RuntimeError(f"no seat has quota for {spec.model}")
    # A bot may pin its harness in bots.json (e.g. "claude" to run through Claude Code
    # on a subscription). Otherwise the scorecard's favourite, otherwise our own loop.
    return Spec(seat=able[0].id, model=spec.model, role=spec.role,
                skills=spec.skills, tools=spec.tools,
                harness=entry.get("harness", spec.harness))


def resolve(spec: Spec, bot: str, user: str = "asa") -> State:
    """Steps 5 to 7. Names in, values out."""
    # The text blocks, in the order the model will read them.
    system = [WAKE.format(bot=bot, model=spec.model, computer=platform.platform(), user=user)]
    system.append(library.role(spec.role))
    for name in spec.skills:
        system.append(library.skill(name))
    system.append(HOW_MESSAGES_WORK.format(team=", ".join(bots()), user=user))
    for entry in ledger.read(bot):
        system.append(f"[{entry['kind']} from a previous kernel] {entry['text'][:LEDGER_CAP]}")

    # The tool block: message, plus what the spec names, plus what its skills ask for.
    wanted = {"message", *spec.tools}
    for name in spec.skills:
        wanted.update(library.skill_tools(name))
    tools = [library.tool_schema(name) for name in sorted(wanted)]

    return State(
        session_id=uuid.uuid4().hex[:8],
        bot=bot,
        spec=spec,
        system=system,
        tools=tools,
    )


def birth(bot: str) -> State:
    """The whole thing: choose, then resolve."""
    return resolve(choose(bot), bot)
