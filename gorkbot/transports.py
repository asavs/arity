"""Arity transports — an in-process channel queue and an outbound callback sink.

What exists here: `RedphoneInbox`, a per-instance dict of channel name to a list of
`RedphoneMessage`, optionally mirrored into a `RecordStore`; and `WebhookTransport`, a
`Transport` that records every outgoing `EmitMessage` and hands it to a caller-supplied
callback whose default does nothing. Both run entirely inside the calling process. No
code in this module opens a socket, serves an address, or contacts a carrier.
"""
from __future__ import annotations

import logging
from .diagnostics import record_data_loss

logger = logging.getLogger(__name__)

"""
Stated intent, not built:
Axiom 6 — the front door is a phone: voice calls, SMS/MMS, images, and URLs arriving on
a rented number. There is no carrier or voice transport; the only way a message enters
`RedphoneInbox` is an in-process call to `post`.

Axiom 10 — the red phone is a public address, not an alarm (`redphone.com/asas`): a
public inbox anyone can post a problem to, with bots triaging and escalating by email.
No address is served to anyone outside the process and there is no escalation path.
"""
# imports already at top
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
    """In-process multi-channel message queue, optionally mirrored to a record store.

    This is the local half of Axiom 10. Messages arrive only through direct `post`
    calls from inside this process; nothing here publishes an address, accepts a
    submission from outside, or escalates anywhere.
    """

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
        """Append a message to its channel queue, mirroring it to the store if one is set.

        A store failure is swallowed: the queued message is the return value either way.
        """
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
            except Exception as exc:
                logger.warning("Failed to persist redphone_message: %s", exc)
                record_data_loss("RedphoneMessage", exc)
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
        """Query recent stored messages, one channel or all. Empty without a store."""
        if self.store and hasattr(self.store, "query"):
            filters = {"channel": channel} if channel else {}
            records = self.store.query("redphone_message", **filters)
            return records[-limit:]
        return []


class WebhookTransport(Transport):
    """Egress-only sink: records each outgoing effect and passes it to a callback.

    Despite the name it speaks no HTTP and holds no URL. Posting to an actual webhook
    is the caller's job, inside the callback it supplies; the default callback drops
    the effect, leaving `sent_messages` as the only record. Nothing here receives, so
    this is not a front door in the Axiom 6 sense.
    """

    def __init__(self, callback: Optional[Callable[[EmitMessage], None]] = None):
        self._callback = callback or self._default_callback
        self.sent_messages: list[EmitMessage] = []

    def emit(self, effect: EmitMessage) -> None:
        self.sent_messages.append(effect)
        self._callback(effect)

    def _default_callback(self, effect: EmitMessage) -> None:
        pass
