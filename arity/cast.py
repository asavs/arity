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

from . import ledger, library, paths, scorecard, seats
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

DEFAULT_MODEL = "minimax/minimax-m2.7:free"     # used only when nothing with evidence has quota


def bots() -> dict[str, dict]:
    """The staff: bot name -> {"role": ..., "harness": ...?}. A bot is a role with a name and a ledger.

    Names are tangible on purpose (reception, engineer, designer) so that a model
    needs nothing more than this list to know whom to message about a task.
    """
    return json.loads(paths.bots().read_text())


def is_bot(name: str) -> bool:
    """Anyone who is not a bot is a person, and Sends to them go out the transport."""
    return name in bots()


def choose(bot: str) -> Spec:
    """Steps 1 to 4. Aptitude orders, quota filters.

    Two questions, asked in a fixed order. Question A, to the scorecard: which
    models have won this bot's kind of task, best first? Question B, to the
    seat table: which of those can be paid for right now? B may strike models
    from A's list; B never reorders it. The default model is the last entry,
    so it is used only when nothing with evidence has quota.

    Task kind is the bot's role for now. Skills, tools, harness and effort
    come from the bot's entry in bots.json; they are the bot's, not the
    model's, and a trial varies them on purpose rather than by accident.
    """
    entry = bots()[bot]
    role = entry["role"]
    ordered = scorecard.ranked(role)                          # question A
    for model in [*ordered, DEFAULT_MODEL]:
        able = seats.with_quota(model)                        # question B
        if able:
            return Spec(seat=able[0].id, model=model, role=role,
                        skills=tuple(entry.get("skills", ())),
                        tools=tuple(entry.get("tools", ())),
                        harness=entry.get("harness", "kernel"))
    raise RuntimeError(f"no seat has quota for any of {[*ordered, DEFAULT_MODEL]}")


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
