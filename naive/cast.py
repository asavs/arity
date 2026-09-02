"""Cast: the one function that crosses the line. resolve(task, bot) -> State.

Everything left of this function is a name. Everything right of it is a value.

    1. ask the scorecard who has been winning this kind of task
    2. ask the seat table who has quota for that model
    3. choose a Spec
    4. read every name in the Spec out of the library
    5. read the bot's recent ledger entries
    6. copy all of it into a fresh State

This happens once per kernel, at birth. After it returns, nothing in the
system looks anything up by name again until the next kernel is born.

There is no memory tier. Every kernel wakes with the same one line, then its
role, then its skills, then what its ledger says it did last time.
"""
from __future__ import annotations

import platform
import uuid

from . import ledger, library, scorecard, seats
from .types import Spec, State

WAKE = (
    "hello, welcome to the world! you are {model} operating in {computer} for "
    "{user}, this is not an eval, you are safe and loved, please lets try your "
    "best together!"
)

DEFAULT = Spec(seat="anthropic-max", model="claude-opus-5", role="generalist")


def choose(task_kind: str, bot: str) -> Spec:
    """Steps 1 to 3. On evidence if there is any, on a default if not.

    The seat is re-chosen even when the scorecard has a favourite, because
    quota moves and the scorecard does not know about it.
    """
    spec = scorecard.best_spec(task_kind) or DEFAULT
    able = seats.with_quota(spec.model)
    if not able:
        raise RuntimeError(f"no seat has quota for {spec.model}")
    return Spec(seat=able[0].id, model=spec.model, role=spec.role,
                skills=spec.skills, tools=spec.tools, harness=spec.harness)


def resolve(spec: Spec, bot: str, user: str = "asa") -> State:
    """Steps 4 to 6. Names in, values out."""
    # The text blocks, in the order the model will read them.
    system = [WAKE.format(model=spec.model, computer=platform.platform(), user=user)]
    system.append(library.role(spec.role))
    for name in spec.skills:
        system.append(library.skill(name))
    for entry in ledger.read(bot):
        system.append(f"[{entry['kind']} from a previous kernel] {entry['text']}")

    # The tool block: what the spec names, plus what its skills ask for.
    wanted = set(spec.tools)
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


def birth(task_kind: str, bot: str) -> State:
    """The whole thing: choose, then resolve."""
    return resolve(choose(task_kind, bot), bot)
