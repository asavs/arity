"""tiers.py - brief compilation and refusal enforcement."""

from __future__ import annotations
import hashlib
from dataclasses import dataclass
from .roles import Role


class BriefRefusalError(ValueError):
    """Raised when a brief contains a path or name denied for the assigned role."""
    pass


@dataclass(frozen=True)
class Tier:
    role: Role
    brief: str
    brief_hash: str


def compile_brief(role: Role, task_instruction: str, context: str = "") -> Tier:
    full_text = f"{task_instruction} {context}"

    for denied_path in role.denied_paths:
        if denied_path and denied_path in full_text:
            raise BriefRefusalError(
                f"Brief compilation REFUSED: denied path '{denied_path}' leaked into brief for role '{role.name}'"
            )

    for denied_name in role.denied_names:
        if denied_name and denied_name.lower() in full_text.lower():
            raise BriefRefusalError(
                f"Brief compilation REFUSED: denied name '{denied_name}' leaked into brief for role '{role.name}'"
            )

    system_brief = (
        f"You are operating as role [{role.name}].\n"
        f"Role description: {role.description}\n"
        f"Denied tools: {', '.join(sorted(role.denied_tools)) or 'none'}\n"
        f"Denied channels: {', '.join(sorted(role.denied_channels)) or 'none'}\n"
        f"Task Instruction:\n{task_instruction}\n"
    )
    if context:
        system_brief += f"\nContext:\n{context}\n"

    brief_hash = hashlib.sha256(system_brief.encode("utf-8")).hexdigest()[:16]
    return Tier(role=role, brief=system_brief, brief_hash=brief_hash)
