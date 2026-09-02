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
    "hello, welcome to the world! you are {model} operating in {computer} for "
    "{user}, this is not an eval, you are safe and loved, please lets try your "
    "best together!"
)

BOTS = Path(__file__).parent / "bots.json"
DEFAULT_SEAT, DEFAULT_MODEL = "anthropic-max", "claude-opus-5"


def bots() -> dict[str, dict]:
    """The staff: bot name -> {"role": ...}. A bot is a role with a name and a ledger."""
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
    role = bots()[bot]["role"]
    spec = scorecard.best_spec(task_kind=role) or Spec(DEFAULT_SEAT, DEFAULT_MODEL, role)
    able = seats.with_quota(spec.model)
    if not able:
        raise RuntimeError(f"no seat has quota for {spec.model}")
    return Spec(seat=able[0].id, model=spec.model, role=spec.role,
                skills=spec.skills, tools=spec.tools, harness=spec.harness)


def resolve(spec: Spec, bot: str, user: str = "asa") -> State:
    """Steps 5 to 7. Names in, values out."""
    # The text blocks, in the order the model will read them.
    system = [WAKE.format(model=spec.model, computer=platform.platform(), user=user)]
    system.append(library.role(spec.role))
    for name in spec.skills:
        system.append(library.skill(name))
    system.append("You can reach these people and bots with the message tool: "
                  + ", ".join([user, *bots()]) + ".")
    for entry in ledger.read(bot):
        system.append(f"[{entry['kind']} from a previous kernel] {entry['text']}")

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
