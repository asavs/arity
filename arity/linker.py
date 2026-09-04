"""Linker: compiles a Spec into a runnable State.

A linker validates all symbols upfront (link-time) before execution:
1. Verify role exists in project, home, or builtins.
2. Verify all skills exist (local or fetchable remote coordinates).
3. Verify all tools have valid schemas (and runners, unless virtual).
4. Raise LinkError immediately if any symbol is undefined.
"""
from __future__ import annotations

import json
import platform
import uuid
from typing import Any

from . import ledger, paths, scorecard, seats, sources
from .types import Spec, State

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

WAKE = (
    "You are {bot}, a language model agent. You run on {model}.\n"
    "The computer is {computer}. The user is {user}."
)

HOW_MESSAGES_WORK = (
    "You can message teammates using the `message` tool: {team}, and {user}.\n"
    "When you message someone, your turn pauses until they answer.\n"
    "Answer whoever messaged you directly: your final text goes to them."
)

LEDGER_CAP = 600
DEFAULT_MODEL = "minimax/minimax-m2.7:free"

VIRTUAL_TOOLS = {"message"}


class LinkError(RuntimeError):
    """Raised when a Spec cannot be linked due to missing roles, skills, or tools."""
    pass


# ---------------------------------------------------------------------------
# Bot Manifest & Roster
# ---------------------------------------------------------------------------

def bots() -> dict[str, dict]:
    """The staff list: bot name -> manifest dictionary."""
    p = paths.bots()
    if not p.exists():
        # Fall back to builtins seeds if user home has no bots.json yet
        seeds_p = paths.SEEDS / "bots.json"
        if seeds_p.exists():
            return json.loads(seeds_p.read_text(encoding="utf-8"))
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def is_bot(name: str) -> bool:
    """True if name corresponds to a configured bot."""
    return name.removeprefix("new:") in bots()


# ---------------------------------------------------------------------------
# Validation (Link-Time Checks)
# ---------------------------------------------------------------------------

def validate(spec: Spec, bot: str | None = None) -> list[str]:
    """Inspect all symbols in a Spec and return a list of diagnostic errors."""
    errors: list[str] = []

    # 1. Role validation
    if not sources.find_role_file(spec.role):
        available = ", ".join(sources.available_roles()) or "(none)"
        errors.append(f"Role '{spec.role}' not found. Available roles: {available}")

    # 2. Skills validation
    skill_tool_names: set[str] = set()
    for s_ref in spec.skills:
        if sources.is_remote(s_ref):
            # Remote coordinate syntax check
            try:
                sources.parse_remote_reference(s_ref)
            except Exception as e:
                errors.append(f"Invalid remote skill coordinate '{s_ref}': {e}")
        else:
            if not sources.find_skill_file(s_ref):
                available = ", ".join(sources.available_skills()) or "(none)"
                errors.append(f"Skill '{s_ref}' not found. Available skills: {available}")
            else:
                try:
                    text = sources.load_skill(s_ref)
                    skill_tool_names.update(sources.parse_skill_tools(text))
                except Exception as e:
                    errors.append(f"Could not read skill '{s_ref}': {e}")

    # 3. Tools validation
    needed_tools = {"message", *spec.tools, *skill_tool_names}
    for t_name in needed_tools:
        schema_path, runner_path = sources.find_tool_files(t_name)
        if not schema_path:
            available = ", ".join(sources.available_tools()) or "(none)"
            errors.append(f"Tool '{t_name}' has no schema (.json). Available tools: {available}")
        elif t_name not in VIRTUAL_TOOLS and not runner_path:
            errors.append(f"Tool '{t_name}' has schema but no runner (.py).")

    return errors


# ---------------------------------------------------------------------------
# Linking & Assembly
# ---------------------------------------------------------------------------

def link(spec: Spec, bot: str, user: str = "asa") -> State:
    """Validate and compile a Spec into a runnable State.

    Fails fast with LinkError if any symbol is undefined.
    """
    errors = validate(spec, bot)
    if errors:
        msg = f"Link error for bot '{bot}':\n" + "\n".join(f"  - {e}" for e in errors)
        raise LinkError(msg)

    # 1. Assemble system prompt blocks
    system = [WAKE.format(bot=bot, model=spec.model, computer=platform.platform(), user=user)]
    system.append(sources.load_role(spec.role))

    # 2. Append skills
    wanted_tools = {"message", *spec.tools}
    for s_ref in spec.skills:
        skill_content = sources.load_skill(s_ref)
        system.append(skill_content)
        wanted_tools.update(sources.parse_skill_tools(skill_content))

    # 3. Team messaging instructions
    system.append(HOW_MESSAGES_WORK.format(team=", ".join(bots()), user=user))

    # 4. Ledger history
    for entry in ledger.read(bot):
        system.append(f"[{entry['kind']} from a previous kernel] {entry['text'][:LEDGER_CAP]}")

    # 5. Tool schemas
    tools = [sources.load_tool_schema(name) for name in sorted(wanted_tools)]

    return State(
        session_id=uuid.uuid4().hex[:8],
        bot=bot,
        spec=spec,
        system=system,
        tools=tools,
    )


def select_spec(bot: str) -> Spec:
    """Select a candidate Spec for a bot: ranked by scorecard, filtered by quota."""
    staff = bots()
    if bot not in staff:
        raise KeyError(f"Bot '{bot}' is not registered. Available: {', '.join(staff.keys())}")

    entry = staff[bot]
    role = entry["role"]
    ordered = scorecard.ranked(role)
    for model in [*ordered, DEFAULT_MODEL]:
        able = seats.with_quota(model)
        if able:
            return Spec(
                seat=able[0].id,
                model=model,
                role=role,
                skills=tuple(entry.get("skills", ())),
                tools=tuple(entry.get("tools", ())),
                harness=entry.get("harness", "kernel"),
                effort=entry.get("effort"),
            )
    raise RuntimeError(f"no seat has quota for any of {[*ordered, DEFAULT_MODEL]}")


def spawn(bot: str, user: str = "asa") -> State:
    """Select spec and link into a runnable State."""
    return link(select_spec(bot), bot, user=user)
