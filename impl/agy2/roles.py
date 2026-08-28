"""roles.py - Denial sets, permissions, and role definitions."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Role:
    name: str
    tier: int  # 0: voice/biograph, 1: project, 2: task/leaf
    harness: str = "default"
    os_user: str = "bot"
    allow: dict[str, list[str]] = field(default_factory=dict)
    deny: dict[str, list[str]] = field(default_factory=dict)
    aptitude_wanted: list[str] = field(default_factory=list)
    public: bool = False


class RoleRegistry:
    """Registry holding role definitions and enforcing denial sets."""

    def __init__(self) -> None:
        self.roles: dict[str, Role] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(Role(
            name="voice",
            tier=0,
            os_user="bot_voice",
            allow={
                "channels": ["dm_asa", "general", "handoffs", "voice"],
                "tools": ["redphone_handoff", "redphone_post"],
                "hosts": ["localhost"],
            },
            deny={
                "tools": ["rm_rf", "eval"],
                "paths": ["/private/keys", "/Users/example/.ssh"],
                "names": [],
            },
            aptitude_wanted=["conversation", "synthesis", "briefing"],
        ))

        self.register(Role(
            name="builder",
            tier=2,
            os_user="bot_builder",
            allow={
                "channels": ["handoffs", "builder"],
                "tools": ["write_file", "read_file", "list_files"],
                "paths": ["brokie/", "workspace/"],
            },
            deny={
                "channels": ["dm_asa"],
                "paths": ["/Users/example/secret", "/root", "/private"],
                "names": ["AsaPrivateBiograph"],
                "hosts": ["external-payment.com"],
            },
            aptitude_wanted=["schema", "coding", "sql", "accuracy"],
        ))

        self.register(Role(
            name="archivist",
            tier=0,
            os_user="bot_archivist",
            allow={
                "channels": ["archive", "handoffs", "general"],
                "tools": ["read_file", "list_files"],
            },
            deny={
                "tools": ["write_file", "delete_file"],
            },
            aptitude_wanted=["verification", "impartial", "forensics"],
        ))

        self.register(Role(
            name="scout",
            tier=2,
            os_user="bot_scout",
            allow={"tools": ["read_file"], "channels": ["scout", "handoffs"]},
            deny={"tools": ["write_file"]},
            aptitude_wanted=["research", "synthesis"],
        ))

    def register(self, role: Role) -> None:
        self.roles[role.name] = role

    def get(self, name: str) -> Role:
        if name not in self.roles:
            raise KeyError(f"Unknown role: {name}")
        return self.roles[name]

    def enforce(self, role: Role, action_type: str, target: str) -> None:
        denials = role.deny.get(action_type, [])
        for d in denials:
            if d == target or target.startswith(d):
                raise PermissionError(f"Role '{role.name}' denied {action_type} on '{target}'")
        allows = role.allow.get(action_type)
        if allows is not None and target not in allows:
            # Check prefix for paths/channels
            if not any(target.startswith(a) for a in allows):
                raise PermissionError(f"Role '{role.name}' not allowed {action_type} on '{target}'")
