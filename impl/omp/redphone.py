"""The red phone is a message and channel log with structured handoffs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from roles import Role
from store import Store


@dataclass
class Channel:
    id: str
    visibility: str
    members: tuple[str, ...]


@dataclass
class TaskRecord:
    sender: str
    to_role: str
    want: str
    evidence: list[str]
    tier: int
    budget: int
    depth: int
    channel: str
    return_channel: str


class RedPhone:
    def __init__(self, store: Store, roles: dict[str, Role], max_depth: int = 3):
        self.store, self.roles, self.max_depth = store, roles, max_depth
        self.channels: dict[str, Channel] = {}

    def channel(self, ident: str, visibility: str, members: tuple[str, ...]) -> Channel:
        return self.channels.setdefault(ident, Channel(ident, visibility, members))

    def post(self, channel: str, sender: str, body: Any, kind: str = "text", structured: bool = False) -> str:
        if channel not in self.channels:
            raise KeyError(f"unknown channel: {channel}")
        if sender in self.roles and not structured:
            self.roles[sender].enforce("channels", channel)

        mid = uuid4().hex[:12]
        self.store.append("state/redphone.jsonl", {
            "id": mid, "at": datetime.now(timezone.utc).isoformat(),
            "channel": channel, "sender": sender, "kind": kind, "body": body,
        })
        return mid

    def dm(self, role_name: str, sender: str, msg: str) -> str:
        cid = f"dm-{role_name}"
        if cid not in self.channels:
            self.channel(cid, "private", (sender, role_name))
        return self.post(cid, sender, msg, kind="dm")

    def handoff(self, record: TaskRecord) -> str:
        if record.depth > self.max_depth or record.depth < 0:
            raise ValueError(f"handoff depth {record.depth} is outside allowed bound")
        if record.budget <= 0:
            raise ValueError(f"handoff has non-positive budget: {record.budget}")
        if record.to_role not in self.roles:
            raise KeyError(f"unknown role: {record.to_role}")
        return self.post(record.channel, record.sender, asdict(record), kind="handoff", structured=True)

    def reply(self, record: TaskRecord, sender: str, body: Any) -> str:
        return self.post(record.return_channel, sender, body, kind="handoff_reply", structured=True)
