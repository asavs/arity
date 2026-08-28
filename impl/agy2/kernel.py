"""kernel.py - Kernel runtime lifecycle, identity tuple, self-report, and die."""

from __future__ import annotations
import hashlib
import time
import itertools
from dataclasses import dataclass, field
from typing import Any
from ledger import Seat, SeatLedger
from roles import Role
from store import Store, Record
from harness import Harness

REPORT_PROMPT = (
    "You're being visited one last time. No tools. In a few lines: "
    "what you were doing, what you believe you changed and why, "
    "what's open, the last thing you know is safe, and one piece of advice for whoever picks this up."
)

_kernel_counter = itertools.count(1)


@dataclass
class EvidenceEnvelope:
    kernel_id: str
    identity: tuple[str, str, str, str, str, str]
    role: Role
    seat: Seat
    transcript: list[dict[str, Any]]
    tool_log: list[dict[str, Any]]
    ended_by: str
    tokens_used: int


class Kernel:
    """A single model runtime instance holding a role for a period."""

    def __init__(
        self,
        seat: Seat,
        role: Role,
        brief: str,
        effort: str = "medium",
        convo_id: str = "convo_default",
        harness: Harness | None = None,
    ) -> None:
        self.id = f"k_{next(_kernel_counter):04d}"
        self.seat = seat
        self.role = role
        self.brief = brief
        self.effort = effort
        self.convo_id = convo_id
        self.harness = harness or Harness(Store())

        brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()[:12]
        self.identity = (
            seat.provider,
            seat.endpoint,
            seat.model,
            seat.cache_boundary,
            convo_id,
            brief_hash,
        )

        self.born_at = time.time()
        self.last_turn_at = self.born_at
        self.cache_expires_at = self.born_at + seat.cache_window
        self.prefix_tokens = 2_000
        self.state = "alive"  # alive | dying | dead
        self.transcript: list[dict[str, Any]] = []
        self.tool_log: list[dict[str, Any]] = []
        self.tool_calls_blocked = False

    def turn(self, msg: str, tools: list[str] | None = None) -> str:
        if self.state == "dead":
            raise RuntimeError(f"Kernel {self.id} is dead.")
        self.last_turn_at = time.time()
        self.cache_expires_at = self.last_turn_at + self.seat.cache_window

        self.transcript.append({"role": "user", "content": msg})
        active_tools = [] if self.tool_calls_blocked else tools

        resp_content, t_logs, p_tok, c_tok = self.harness.run_turn(
            seat=self.seat,
            system_prompt=self.brief,
            messages=self.transcript,
            tools_allowed=active_tools,
        )

        self.tool_log.extend(t_logs)
        self.transcript.append({"role": "assistant", "content": resp_content})
        self.prefix_tokens += p_tok + c_tok
        return resp_content

    def checkpoint(self) -> None:
        """Atomic safe point reached before dying."""
        pass

    def trace(self, reason: str) -> EvidenceEnvelope:
        return EvidenceEnvelope(
            kernel_id=self.id,
            identity=self.identity,
            role=self.role,
            seat=self.seat,
            transcript=list(self.transcript),
            tool_log=list(self.tool_log),
            ended_by=reason,
            tokens_used=self.prefix_tokens,
        )

    def write_report(self, reason: str) -> str:
        """Solicits first-person account with one last tool-free turn."""
        return self.turn(f"{REPORT_PROMPT} (Trigger: {reason})", tools=[])

    def die(self, reason: str, store: Store, archivist: Any, ledger: SeatLedger) -> tuple[str | None, Any]:
        """Transitions to dying, reserves turn, reports, and enqueues archivist."""
        self.state = "dying"
        self.tool_calls_blocked = True
        self.checkpoint()

        report: str | None = None
        if ledger.reserve(self.seat, purpose="report_turn"):
            try:
                report = self.write_report(reason)
            except Exception:
                report = None

        env = self.trace(reason=reason)
        self.state = "dead"

        # Record own report or absence
        if report:
            store.write_record(
                tier=self.role.tier,
                record=Record(tier=self.role.tier, kind="own_report", body=report, by=self.id),
            )
        else:
            store.write_record(
                tier=self.role.tier,
                record=Record(tier=self.role.tier, kind="absence", body=f"REPORT_ABSENT: {reason}", by=self.id),
            )

        entry = archivist.enqueue(env=env, report=report, reason=reason, store=store)
        return report, entry
