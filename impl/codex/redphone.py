"""The red phone is a log first. Delivery plugs can come later."""

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

    def post(self, channel: str, sender: str, body: Any, kind: str = "text",
             structured: bool = False) -> str:
        if channel not in self.channels:
            raise KeyError(f"unknown channel: {channel}")
        if sender in self.roles and not structured:
            self.roles[sender].enforce("channels", channel)
        message_id = uuid4().hex[:12]
        self.store.append("state/redphone.jsonl", {"id": message_id, "at": _now(), "channel": channel,
                          "sender": sender, "kind": kind, "body": body})
        return message_id

    def handoff(self, record: TaskRecord) -> str:
        if record.depth > self.max_depth or record.depth < 0:
            raise ValueError("handoff depth is outside its bound")
        if record.budget <= 0:
            raise ValueError("handoff has no budget")
        if record.to_role not in self.roles:
            raise KeyError(f"unknown role: {record.to_role}")
        # A task record may cross a channel's chat boundary. Its return route is explicit.
        return self.post(record.channel, record.sender, asdict(record), "handoff", structured=True)

    def reply(self, record: TaskRecord, sender: str, body: Any) -> str:
        return self.post(record.return_channel, sender, body, "handoff_reply", structured=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
