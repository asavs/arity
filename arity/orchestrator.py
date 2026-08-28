"""arity orchestrator — Master end-to-end engine integrating all 7 elemental parts.

Axiom 1: One voice, a staff, and a door to each.
Axiom 3: The model behind a bot is chosen per prompt, on evidence.
Axiom 3 Corollary: Many kernels per task (terrarium A/B testing).
Axiom 9: Impartial archivist audits kernel claims.
Axiom 11: The system has a pulse.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .archivist import ArchivistEntry, ImpartialArchivist
from .composer import CastingComposer, CastingDecision
from .handlers import ConsoleTransport, JsonlRecordStore
from .ledger import Seat, SeatLedger
from .pulse import PulseAction, PulseEngine
from .roles import BUILDER_ROLE, Role, RoleRegistry, VOICE_ROLE
from .runtime import Runtime
from .scorecard import Scorecard
from .seams import ModelProvider, RecordStore, Transport
from .terrarium import TaskRecord, TerrariumCandidateResult, TerrariumDispatcher
from .tiers import BriefCompiler, PredecessorAccounts
from .tools import SandboxToolRunner
from .transports import RedphoneInbox
from .types import (
    CallModel,
    EmitMessage,
    Event,
    ModelCompleted,
    State,
    Status,
    StoreRecord,
    UserMessage,
)


@dataclass
class OrchestrationResponse:
    """The complete response from an orchestrated interaction."""
    reply_text: str
    voice_state: State
    delegated_task: Optional[TaskRecord] = None
    winning_candidate: Optional[TerrariumCandidateResult] = None
    archivist_entries: list[ArchivistEntry] = field(default_factory=list)


class ArityOrchestrator:
    """The master coordinator uniting all 7 elemental parts."""

    def __init__(
        self,
        ledger: Optional[SeatLedger] = None,
        store: Optional[RecordStore] = None,
        roles: Optional[RoleRegistry] = None,
        compiler: Optional[BriefCompiler] = None,
        scorecard: Optional[Scorecard] = None,
        archivist: Optional[ImpartialArchivist] = None,
        pulse: Optional[PulseEngine] = None,
        inbox: Optional[RedphoneInbox] = None,
        transport: Optional[Transport] = None,
        base_workspace: Optional[Path] = None,
        model_factory: Optional[Callable[[Seat], ModelProvider]] = None,
    ):
        self.store = store or JsonlRecordStore()
        self.ledger = ledger or SeatLedger()
        self.roles = roles or RoleRegistry()
        self.compiler = compiler or BriefCompiler()
        self.scorecard = scorecard or Scorecard(store=self.store)
        self.archivist = archivist or ImpartialArchivist(scorecard=self.scorecard, store=self.store)
        self.composer = CastingComposer(ledger=self.ledger)
        self.pulse = pulse or PulseEngine()
        self.inbox = inbox or RedphoneInbox()
        self.transport = transport or ConsoleTransport()
        self.base_workspace = Path(base_workspace) if base_workspace else Path(".terrarium")

        self.terrarium = TerrariumDispatcher(
            ledger=self.ledger,
            store=self.store,
            compiler=self.compiler,
            base_workspace=self.base_workspace,
            model_factory=model_factory,
        )

        self._last_voice_session: Optional[State] = None
        self._last_turn_time: float = time.time()
        self._last_predecessors: dict[str, PredecessorAccounts] = {}

    def handle_message(
        self,
        user_text: str,
        sender: str = "user",
        channel: str = "main",
        candidates_per_task: int = 1,
        now: Optional[float] = None,
    ) -> OrchestrationResponse:
        """Process an incoming user message through the full end-to-end pipeline."""
        curr_time = now if now is not None else time.time()
        self._last_turn_time = curr_time

        # 1. Post to red phone inbox
        self.inbox.post(channel=channel, sender=sender, text=user_text)

        # 2. Check if this is a direct task delegation request (e.g. build, write, fix)
        target_role = self.roles.resolve(user_text)

        # If user asked for an engineering/building/review task, trigger terrarium delegation
        if target_role.tier > 0:
            task_record = TaskRecord(
                from_role="voice",
                to_role=target_role.name,
                brief=user_text,
                predecessor=self._last_predecessors.get(target_role.name),
            )

            # Cast candidate seats (Axiom 3)
            casting = self.composer.cast(
                role=target_role,
                task=user_text,
                candidates_count=candidates_per_task,
                now=curr_time,
            )

            # Run candidate kernels in parallel sandboxes (Axiom 3 Corollary)
            candidate_results = self.terrarium.dispatch_parallel(
                task=task_record,
                candidate_seats=casting.candidates,
                role=target_role,
            )

            # Archivist audits execution and picks verified winner (Axiom 9)
            winner, archivist_entries = self.archivist.evaluate_trial(candidate_results)

            if winner and winner.self_report:
                self._last_predecessors[target_role.name] = PredecessorAccounts(
                    self_report=winner.self_report,
                    archivist_entry=archivist_entries[0].entry_text if archivist_entries else None,
                )

            reply = (
                f"Task delegated to '{target_role.name}' ({winner.seat.model if winner else 'none'}). "
                f"Archivist verdict: {archivist_entries[0].verdict.upper() if archivist_entries else 'DONE'}. "
                f"Output: {winner.output if winner else 'No output'}"
            )

            voice_state = State(session_id="voice_main", role="voice", status=Status.IDLE)
            voice_state.output = reply

            return OrchestrationResponse(
                reply_text=reply,
                voice_state=voice_state,
                delegated_task=task_record,
                winning_candidate=winner,
                archivist_entries=archivist_entries,
            )

        # Otherwise, handle as direct conversation with the Voice (Tier 0)
        voice_brief = self.compiler.assemble(
            role=VOICE_ROLE,
            task=user_text,
            provider="openai",
            endpoint="https://api.openai.com/v1",
            model="gpt-4o",
            session_id="voice_main",
        )

        voice_runtime = Runtime(
            store=self.store,
            transport=self.transport,
        )

        voice_state = State(
            session_id="voice_main",
            role="voice",
            system_prompt=voice_brief.system_prompt,
        )
        final_state = voice_runtime.run(voice_state, initial_event=UserMessage(text=user_text, sender=sender))
        self._last_voice_session = final_state

        return OrchestrationResponse(
            reply_text=final_state.output or "(no output)",
            voice_state=final_state,
        )

    def tick_pulse(self, now: Optional[float] = None) -> list[PulseAction]:
        """Execute a pulse cycle (keepalives + expiring quota checks)."""
        curr_time = now if now is not None else time.time()
        actions: list[PulseAction] = []

        # 1. Evaluate Voice session keepalive
        if self._last_voice_session:
            idle_seconds = curr_time - self._last_turn_time
            # Get default voice seat
            voice_seats = self.ledger.list_available(now=curr_time)
            if voice_seats:
                seat = voice_seats[0]
                action = self.pulse.evaluate_session(
                    session_id=self._last_voice_session.session_id,
                    seat=seat,
                    seconds_idle=idle_seconds,
                    prefix_tokens=2000,
                )
                actions.append(action)

        # 2. Check for expiring subscription quotas
        quota_actions = self.pulse.scan_expiring_seats(self.ledger, now=curr_time)
        actions.extend(quota_actions)

        return actions
