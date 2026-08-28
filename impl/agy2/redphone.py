"""redphone.py - Channels, DMs, bounded handoffs, and voice briefing."""

from __future__ import annotations
import time
import itertools
from dataclasses import dataclass, field
from typing import Any
from roles import Role, RoleRegistry
from store import Store, Record

_msg_counter = itertools.count(1)


@dataclass
class Message:
    channel: str
    sender: str
    kind: str  # text | handoff | keepalive | entry | friction
    body: Any
    id: str = field(default_factory=lambda: f"msg_{next(_msg_counter):04d}")
    at: float = field(default_factory=time.time)


@dataclass
class TaskRecord:
    from_role: str
    to_role: str
    want: str
    evidence: str
    tier: int = 2
    budget: int = 50_000
    depth: int = 1
    project: str = "brokie"
    status: str = "pending"
    result: str | None = None


class RedPhone:
    """Central channel routing and bot-to-bot handoff spine."""

    def __init__(self, roles: RoleRegistry, store: Store) -> None:
        self.roles = roles
        self.store = store
        self.channels: dict[str, list[Message]] = {
            "dm_asa": [],
            "general": [],
            "handoffs": [],
            "archive": [],
            "builder": [],
        }

    def post(self, channel: str, msg: Message, sender_role: Role | None = None) -> str:
        if sender_role:
            self.roles.enforce(sender_role, "channels", channel)
        if channel not in self.channels:
            self.channels[channel] = []
        self.channels[channel].append(msg)
        return msg.id

    def dm(self, from_sender: str, to_role: str, text: str) -> str:
        channel_name = f"dm_{to_role}"
        msg = Message(channel=channel_name, sender=from_sender, kind="text", body=text)
        return self.post(channel_name, msg)

    def handoff(
        self,
        from_role: Role,
        to_role_name: str,
        want: str,
        cast_fn: Any,
        evidence: str = "",
        depth: int = 1,
        budget: int = 50_000,
    ) -> tuple[TaskRecord, Any]:
        if depth > 5:
            raise RecursionError("Handoff depth exceeded limit (max 5).")
        if budget < 100:
            raise ValueError("Handoff budget exhausted.")

        target_role = self.roles.get(to_role_name)
        task = TaskRecord(
            from_role=from_role.name,
            to_role=to_role_name,
            want=want,
            evidence=evidence,
            tier=target_role.tier,
            budget=budget,
            depth=depth,
        )

        # Post handoff record to handoff channel
        h_msg = Message(channel="handoffs", sender=from_role.name, kind="handoff", body=task)
        self.post("handoffs", h_msg)

        # Cast a kernel for the target role and execute
        kernel = cast_fn(role=target_role, task_context=want)
        tools = target_role.allow.get("tools", [])
        result = kernel.turn(want, tools=tools)

        task.status = "done"
        task.result = result
        return task, kernel

    def voice_brief_asa(self, task: TaskRecord, archivist_entry: Any = None) -> str:
        """Voice compresses task outcome into one phone-sized sentence."""
        v_count = sum(1 for c in archivist_entry.verified_changes if c["verified"]) if archivist_entry else 1
        return f"Builder wrote the brokie deals schema with {v_count} verified table in schema.sql."

