"""The system heartbeat and keepalive scheduler."""

from __future__ import annotations

from datetime import datetime, timezone

from cadence import Cadence, Conversation
from kernel import Kernel
from ledger import Ledger


class Pulse:
    KEEPALIVE = "hi luv u"

    def __init__(self, cadence: Cadence, ledger: Ledger):
        self.cadence = cadence
        self.ledger = ledger

    def tick(self, kernel: Kernel, convo: Conversation, ping_cost: float = 0.0001) -> str:
        now = datetime.now(timezone.utc)
        horizon = max(0.0, (kernel.cache_expires_at - now).total_seconds())
        prob = self.cadence.predict(convo, horizon)["p_return"]
        penalty = self.ledger.cold_cost(kernel.seat.provider, kernel.prefix_tokens, kernel.is_warm())["penalty"]

        if prob * penalty > ping_cost:
            kernel.turn(self.KEEPALIVE, tools={})
            return "keepalive"

        kernel.die("quiet")
        return "let_go"
