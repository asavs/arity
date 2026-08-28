"""cast.py - seat allocation with presence avoidance and kernel spawning."""

from __future__ import annotations
import uuid
from typing import Any, Callable
from .roles import Role
from .tiers import compile_brief
from .ledger import Ledger, Seat
from .scorecard import Scorecard
from .store import Store
from .harness import Harness
from .kernel import Kernel, KernelIdentity


class NoAvailableSeatError(RuntimeError):
    """Raised when no seat is available (e.g. all live with human presence)."""
    pass


class Cast:
    def __init__(self, ledger: Ledger, scorecard: Scorecard) -> None:
        self.ledger = ledger
        self.scorecard = scorecard

    def allocate_seat(
        self,
        role: Role,
        preferred_provider: str | None = None,
    ) -> Seat:
        candidates = self.ledger.get_available_seats(provider=preferred_provider)
        if not candidates and preferred_provider is not None:
            candidates = self.ledger.get_available_seats()

        if not candidates:
            raise NoAvailableSeatError(
                f"No available seats for role '{role.name}' (all occupied or live with human presence)"
            )

        def sort_key(s: Seat) -> tuple[float, int]:
            score = self.scorecard.get_standing(s.provider, s.model)
            return (-score, s.active_sessions)

        candidates.sort(key=sort_key)
        chosen = candidates[0]
        chosen.active_sessions += 1
        return chosen

    def spawn(
        self,
        role: Role,
        task_instruction: str,
        store: Store,
        harness: Harness,
        preferred_provider: str | None = None,
        session_id: str = "default",
        tools_spec: list[dict[str, Any]] | None = None,
        custom_tool_handlers: dict[str, Callable[[dict[str, Any]], str]] | None = None,
    ) -> tuple[Kernel, Seat]:
        seat = self.allocate_seat(role, preferred_provider=preferred_provider)
        tier = compile_brief(role, task_instruction, context=f"Assigned seat {seat.seat_id}")
        identity = KernelIdentity(
            provider=seat.provider,
            endpoint=seat.endpoint,
            model=seat.model,
            cache_boundary=f"boundary:{role.name}:{seat.model}",
            session=session_id,
            brief_hash=tier.brief_hash,
        )
        kernel_id = f"k-{role.name}-{uuid.uuid4().hex[:6]}"
        kernel = Kernel(
            kernel_id=kernel_id,
            identity=identity,
            tier=tier,
            store=store,
            harness=harness,
            seat_api_key=seat._api_key,
            tools_spec=tools_spec,
            custom_tool_handlers=custom_tool_handlers,
        )
        return kernel, seat
