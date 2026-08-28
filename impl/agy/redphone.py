"""redphone.py - structured bounded handoffs across channel boundaries."""

from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from .store import Store


class HandoffRejectedError(RuntimeError):
    """Raised when a handoff exceeds depth or budget boundaries."""
    pass


@dataclass
class Handoff:
    handoff_id: str
    source_kernel_id: str
    from_role: str
    to_role: str
    channel: str
    task: str
    depth: int = 1
    max_depth: int = 3
    budget_tokens: int = 8000
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"
    reply: str | None = None
    timestamp: float = field(default_factory=time.time)


class Redphone:
    def __init__(self, default_max_depth: int = 3) -> None:
        self.default_max_depth = default_max_depth
        self.handoff_history: list[Handoff] = []

    def dispatch(
        self,
        source_kernel_id: str,
        from_role: str,
        to_role: str,
        channel: str,
        task: str,
        store: Store,
        executor: Callable[[Handoff], str],
        depth: int = 1,
        budget_tokens: int = 4000,
        payload: dict[str, Any] | None = None,
    ) -> Handoff:
        if depth > self.default_max_depth:
            raise HandoffRejectedError(f"Handoff depth {depth} exceeds max {self.default_max_depth}")
        if budget_tokens <= 0:
            raise HandoffRejectedError("Handoff budget must be positive")

        handoff = Handoff(
            handoff_id=f"ho-{uuid.uuid4().hex[:8]}",
            source_kernel_id=source_kernel_id,
            from_role=from_role,
            to_role=to_role,
            channel=channel,
            task=task,
            depth=depth,
            max_depth=self.default_max_depth,
            budget_tokens=budget_tokens,
            payload=payload or {},
            status="IN_PROGRESS",
        )
        self.handoff_history.append(handoff)

        store.post_message(
            channel=channel,
            sender=f"redphone:{from_role}",
            content=f"[HANDOFF {handoff.handoff_id}] -> {to_role}: {task}",
            meta={"handoff_id": handoff.handoff_id, "depth": depth, "budget": budget_tokens},
        )

        try:
            reply_text = executor(handoff)
            handoff.reply = reply_text
            handoff.status = "COMPLETED"
        except Exception as e:
            handoff.reply = f"Handoff failed: {e}"
            handoff.status = "FAILED"

        store.post_message(
            channel=channel,
            sender=f"redphone:{to_role}",
            content=f"[REPLY {handoff.handoff_id}] {handoff.reply}",
            meta={"handoff_id": handoff.handoff_id, "status": handoff.status},
        )
        return handoff
