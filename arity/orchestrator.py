"""Arity orchestrator — the end-to-end engine integrating all seven parts.

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
from .handlers import ConsoleTransport, JsonlRecordStore, default_record_store
from .ledger import Seat, SeatLedger
from .pulse import PulseAction, PulseEngine
from .roles import BUILDER_ROLE, Role, RoleRegistry, SECRETARY_ROLE, VOICE_ROLE
from .runtime import Runtime
from .scorecard import Scorecard
from .seams import ModelProvider, RecordStore, Transport
from .terrarium import TaskRecord, TerrariumCandidateResult, TerrariumDispatcher
from .tiers import BriefCompiler, PredecessorAccounts
from .tools import SandboxToolRunner, resolve_arity
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
    session_state: State
    delegated_task: Optional[TaskRecord] = None
    winning_candidate: Optional[TerrariumCandidateResult] = None
    archivist_entries: list[ArchivistEntry] = field(default_factory=list)

    @property
    def voice_state(self) -> State:
        """Backward-compatible alias for session_state."""
        return self.session_state

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
        self.store = store or default_record_store()
        self.ledger = ledger or SeatLedger()
        self.roles = roles or RoleRegistry()
        self.scorecard = scorecard or Scorecard(store=self.store)
        self.compiler = compiler or BriefCompiler(scorecard=self.scorecard)
        self.archivist = archivist or ImpartialArchivist(scorecard=self.scorecard, store=self.store)
        self.composer = CastingComposer(ledger=self.ledger, scorecard=self.scorecard)
        self.pulse = pulse or PulseEngine()
        self.inbox = inbox or RedphoneInbox(store=self.store)
        self.transport = transport or ConsoleTransport()
        self.base_workspace = Path(base_workspace) if base_workspace else Path(".terrarium")

        self.terrarium = TerrariumDispatcher(
            ledger=self.ledger,
            store=self.store,
            compiler=self.compiler,
            base_workspace=self.base_workspace,
            model_factory=model_factory,
        )

        self._last_session: Optional[State] = None
        self._last_turn_time: float = time.time()
        self._last_predecessors: dict[str, PredecessorAccounts] = {}

    def handle_message(
        self,
        user_text: str,
        sender: str = "user",
        channel: str = "main",
        candidates_per_task: Optional[int] = None,
        now: Optional[float] = None,
    ) -> OrchestrationResponse:
        """Process an incoming message through the unified kernel runtime."""
        curr_time = now if now is not None else time.time()
        self._last_turn_time = curr_time

        # API argument > ARITY > legacy ARITY_CONCURRENCY > unary chat.
        effective_arity = resolve_arity(candidates_per_task, default=1)

        # 1. Post to red phone public address (Axiom 10)
        self.inbox.post(channel=channel, sender=sender, text=user_text)

        # 2. Resolve target role (default: Secretary / Voice)
        target_role = self.roles.resolve(user_text)

        # 3. Cast candidate seat(s) based on role aptitude, quota, and evidence (Axiom 3)
        casting = self.composer.cast(
            role=target_role,
            task=user_text,
            candidates_count=effective_arity,
            now=curr_time,
        )

        # 4. Multi-candidate trial or direct single-kernel turn
        is_direct_chat = target_role.name in ("secretary", "voice") and effective_arity == 1
        if not is_direct_chat:
            task_record = TaskRecord(
                from_role="user" if target_role.name in ("secretary", "voice") else "secretary",
                to_role=target_role.name,
                brief=user_text,
                predecessor=self._last_predecessors.get(target_role.name),
            )

            candidate_results = self.terrarium.dispatch_parallel(
                task=task_record,
                candidate_seats=casting.candidates,
                role=target_role,
            )
            winner, archivist_entries = self.archivist.evaluate_trial(candidate_results)

            if winner and winner.self_report:
                self._last_predecessors[target_role.name] = PredecessorAccounts(
                    self_report=winner.self_report,
                    archivist_entry=archivist_entries[0].entry_text if archivist_entries else None,
                )

            output_text = winner.output if (winner and winner.output) else "(no output)"
            session_state = winner.final_state if winner else State(session_id=f"sess_{target_role.name}")
            self._last_session = session_state

            return OrchestrationResponse(
                reply_text=output_text,
                session_state=session_state,
                delegated_task=task_record,
                winning_candidate=winner,
                archivist_entries=archivist_entries,
            )

        # 5. Direct single conversational kernel (Tier 0)
        primary_seat = casting.primary_seat

        def route_peer_message(to_peer: str, text_msg: str) -> str:
            peer_role = self.roles.resolve(to_peer)
            peer_task = TaskRecord(from_role=target_role.name, to_role=peer_role.name, brief=text_msg)
            peer_casting = self.composer.cast(role=peer_role, task=text_msg, candidates_count=1, now=curr_time)
            peer_res = self.terrarium.dispatch_single(
                task=peer_task, candidate_or_seat=peer_casting.primary_seat, role=peer_role
            )
            return peer_res.output or f"[{peer_role.name} replied with no output]"

        tool_runner = SandboxToolRunner(role=target_role, message_router=route_peer_message)
        brief = self.compiler.assemble(
            role=target_role,
            task=user_text,
            provider=primary_seat.provider,
            endpoint=primary_seat.endpoint,
            model=primary_seat.model,
            session_id=f"{target_role.name}_main",
            all_tools=tool_runner.get_schemas(),
        )

        model_provider = self.terrarium._model_factory(primary_seat)
        runtime = Runtime(
            model_provider=model_provider,
            tool_runner=tool_runner,
            store=self.store,
            transport=self.transport,
        )

        state = State(
            session_id=f"{target_role.name}_main",
            role=target_role.name,
            system_prompt=brief.system_prompt,
            active_tools=brief.filtered_tools,
        )
        final_state = runtime.run(state, initial_event=UserMessage(text=user_text, sender=sender))
        self._last_session = final_state
        return OrchestrationResponse(
            reply_text=final_state.output or "(no output)",
            session_state=final_state,
        )
    def tick_pulse(self, now: Optional[float] = None) -> list[PulseAction]:
        """Execute a pulse cycle (keepalives + expiring quota checks)."""
        curr_time = now if now is not None else time.time()
        actions: list[PulseAction] = []

        # 1. Evaluate Secretary session keepalive
        if self._last_session:
            idle_seconds = curr_time - self._last_turn_time
            available_seats = self.ledger.list_available(now=curr_time)
            if available_seats:
                seat = available_seats[0]
                action = self.pulse.evaluate_session(
                    session_id=self._last_session.session_id,
                    seat=seat,
                    seconds_idle=idle_seconds,
                    prefix_tokens=2000,
                )
                actions.append(action)

        # 2. Check for expiring subscription quotas
        quota_actions = self.pulse.scan_expiring_seats(self.ledger, now=curr_time)
        actions.extend(quota_actions)

        return actions


# Compatibility: the pre-Arity public class name remains importable.
ArityOrchestrator = ArityOrchestrator
