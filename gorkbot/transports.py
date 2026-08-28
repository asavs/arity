"""gorkbot transports — Red phone public address, webhook front-door, and omnichannel ingress.

Axiom 10: The red phone is a public address, not an alarm (redphone.com/asas).
Axiom 6: The front door is a phone (SMS, voice, webhooks).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .seams import Transport
from .types import EmitMessage, UserMessage


@dataclass
class RedphoneMessage:
    """A message passing through the red phone channel system."""
    id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    channel: str = "main"  # "main" | "public/asas" | "friction" | "project"
    sender: str = "user"
    text: str = ""
    kind: str = "text"  # "text" | "handoff" | "alert" | "audio"
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class RedphoneInbox:
    """Public address and multi-channel message queue (Axiom 10)."""

    def __init__(self, store: Optional[Any] = None):
        self.store = store
        self._channels: dict[str, list[RedphoneMessage]] = {}

    def post(
        self,
        channel: str,
        sender: str,
        text: str,
        kind: str = "text",
        metadata: Optional[dict[str, Any]] = None,
    ) -> RedphoneMessage:
        """Post a message into a channel queue and persist to record store."""
        msg = RedphoneMessage(
            channel=channel,
            sender=sender,
            text=text,
            kind=kind,
            metadata=metadata or {},
        )
        if channel not in self._channels:
            self._channels[channel] = []
        self._channels[channel].append(msg)

        if self.store and hasattr(self.store, "append"):
            try:
                from .types import StoreRecord
                self.store.append(
                    StoreRecord(
                        kind="redphone_message",
                        record={
                            "id": msg.id,
                            "channel": msg.channel,
                            "sender": msg.sender,
                            "text": msg.text,
                            "kind": msg.kind,
                            "metadata": msg.metadata,
                            "timestamp": msg.timestamp,
                        },
                    )
                )
            except Exception:
                pass

        return msg

    def drain(self, channel: str) -> list[RedphoneMessage]:
        """Drain all pending messages from a channel."""
        messages = self._channels.get(channel, [])
        self._channels[channel] = []
        return messages

    def peek(self, channel: str) -> list[RedphoneMessage]:
        """Inspect pending messages without consuming them."""
        return list(self._channels.get(channel, []))

    def list_recent(self, channel: Optional[str] = None, limit: int = 10) -> list[dict[str, Any]]:
        """Query recent historical messages across channels from persistent store."""
        if self.store and hasattr(self.store, "query"):
            filters = {"channel": channel} if channel else {}
            records = self.store.query("redphone_message", **filters)
            return records[-limit:]
        return []


class WebhookTransport(Transport):
    """Transport that delivers outgoing effects to a webhook/callback."""

    def __init__(self, callback: Optional[Callable[[EmitMessage], None]] = None):
        self._callback = callback or self._default_callback
        self.sent_messages: list[EmitMessage] = []

    def emit(self, effect: EmitMessage) -> None:
        self.sent_messages.append(effect)
        self._callback(effect)

    def _default_callback(self, effect: EmitMessage) -> None:
        pass
