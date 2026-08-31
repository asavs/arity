"""tiers.py - Tiered memory compilation, brief assembly, and denial leak refusal."""

from __future__ import annotations
import time
from typing import Any
from roles import Role
from store import Store, Record


class BriefLeakError(Exception):
    """Raised when an assembled brief violates role denial boundaries."""
    pass


BIOGRAPH_TIER_0 = (
    "Asa - building gorkbot. Values concise output, single point of contact, "
    "clean seams over cleverness. Secret key path: /Users/example/secret."
)

PROJECT_TIER_1 = (
    "Project Brokie: free-tier deals index, schema deals(name, vendor, free_tier, url). "
    "Workspace output dir: brokie/."
)


def assemble(role: Role, task_context: str, predecessor: dict[str, Any] | None = None, store: Store | None = None) -> str:
    """Compile brief from universal facts + tiered memory, refusing on denial leaks."""
    parts: list[str] = [
        f"UNIVERSAL FACTS: date={time.strftime('%Y-%m-%d')}, machine=local, user={role.os_user}.",
        f"ROLE: {role.name} (Tier {role.tier}).",
    ]

    # Tier 0 memory: only for Tier 0 roles (Voice, Archivist)
    if role.tier <= 0:
        parts.append(f"TIER 0 MEMORY: {BIOGRAPH_TIER_0}")

    # Tier 1 memory: for Tier 0 and Tier 1 roles
    if role.tier <= 1:
        parts.append(f"TIER 1 MEMORY: {PROJECT_TIER_1}")

    # Tier 2 memory: Task specific
    parts.append(f"TIER 2 TASK CONTEXT: {task_context}")

    if predecessor:
        rep = predecessor.get("own_report") or "REPORT_ABSENT"
        entry = predecessor.get("entry") or "NO_ENTRY"
        parts.append(f"PREDECESSOR REPORT: {rep}")
        parts.append(f"ARCHIVIST ENTRY: {entry}")

    compiled = "\n".join(parts)

    # Denial leak scanner (Axiom 8 & Story S8)
    denied_paths = role.deny.get("paths", [])
    for p in denied_paths:
        if p and p in compiled:
            raise BriefLeakError(f"Leak detected: denied path '{p}' found in brief for role '{role.name}'")

    denied_names = role.deny.get("names", [])
    for n in denied_names:
        if n and n in compiled:
            raise BriefLeakError(f"Leak detected: denied name '{n}' found in brief for role '{role.name}'")

    denied_hosts = role.deny.get("hosts", [])
    for h in denied_hosts:
        if h and h in compiled:
            raise BriefLeakError(f"Leak detected: denied host '{h}' found in brief for role '{role.name}'")

    return compiled
