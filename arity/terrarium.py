"""arity terrarium — Multi-kernel parallel execution and task delegation.

Axiom 3 Corollary: Many kernels per task (run candidates side-by-side for evidence).
Axiom 1: One voice, a staff, and structured handoffs between them.
Axiom 2: Denial-set enforcement per role in isolated sandboxes.
"""
from __future__ import annotations

import concurrent.futures
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .handlers import (
    ConsoleTransport,
    JsonlRecordStore,
    MetricsObserver,
    create_model_provider,
)
from .tools import SandboxToolRunner
from .ledger import Seat, SeatLedger
from .roles import Role
from .runtime import Runtime
from .seams import ModelProvider, RecordStore
from .tiers import BriefCompiler, CompiledBrief, PredecessorAccounts
from .types import (
    CallModel,
    Event,
    ModelCompleted,
    State,
    Status,
    StoreRecord,
    UserMessage,
)


@dataclass
class TaskRecord:
    """A structured task handoff record (Axiom 1 & Axiom 3)."""
    id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    from_role: str = "voice"
    to_role: str = "builder"
    brief: str = ""
    budget: float = 1.0  # USD or reference token budget
    depth: int = 0  # Recursion depth limit
    max_depth: int = 3
    predecessor: Optional[PredecessorAccounts] = None
    task_context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_depth_exceeded(self) -> bool:
        return self.depth >= self.max_depth


@dataclass
class TerrariumCandidateResult:
    """The execution outcome of a single kernel candidate in a trial."""
    candidate_id: str
    task_id: str
    seat: Seat
    role: Role
    final_state: State
    output: Optional[str]
    self_report: Optional[str]
    tokens_used: int
    duration_seconds: float
    workspace_path: Path
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "completed"
    error: Optional[str] = None


class TerrariumDispatcher:
    """Dispatches tasks across candidate kernels in isolated parallel sandboxes."""

    def __init__(
        self,
        ledger: SeatLedger,
        store: Optional[RecordStore] = None,
        compiler: Optional[BriefCompiler] = None,
        base_workspace: Optional[Path] = None,
        model_factory: Optional[Callable[[Seat], ModelProvider]] = None,
    ):
        self.ledger = ledger
        self.store = store or JsonlRecordStore()
        self.compiler = compiler or BriefCompiler()
        self.base_workspace = Path(base_workspace) if base_workspace else Path(".terrarium")
        self.base_workspace.mkdir(parents=True, exist_ok=True)
        self._model_factory = model_factory or self._default_model_factory

    def _default_model_factory(self, seat: Seat) -> ModelProvider:
        return create_model_provider(seat)

    def dispatch_single(
        self,
        task: TaskRecord,
        seat: Seat,
        role: Role,
    ) -> TerrariumCandidateResult:
        """Run a single candidate kernel in an isolated sandbox."""
        start_time = time.time()
        candidate_id = f"cand_{seat.id}_{uuid.uuid4().hex[:6]}"
        workspace = self.base_workspace / task.id / candidate_id
        workspace.mkdir(parents=True, exist_ok=True)

        if task.is_depth_exceeded():
            return TerrariumCandidateResult(
                candidate_id=candidate_id,
                task_id=task.id,
                seat=seat,
                role=role,
                final_state=State(session_id=candidate_id, status=Status.HALTED),
                output=f"Error: Maximum recursion depth ({task.max_depth}) exceeded.",
                self_report=None,
                tokens_used=0,
                duration_seconds=0.0,
                workspace_path=workspace,
                status="depth_exceeded",
                error="Max depth exceeded",
            )

        # 1. Setup isolated sandbox tool runner with AST validation and path confinement
        tool_runner = SandboxToolRunner(workspace_root=workspace, role=role)
        metrics = MetricsObserver()

        # Register deploy_subagent tool for multi-level delegation (depth bounded)
        def deploy_subagent(role_name: str, brief: str) -> str:
            if task.depth + 1 >= task.max_depth:
                return f"Error: Maximum subagent depth limit ({task.max_depth}) reached."
            from .roles import RoleRegistry
            roles = RoleRegistry()
            child_role = roles.resolve(role_name)
            child_task = TaskRecord(
                from_role=role.name,
                to_role=child_role.name,
                brief=brief,
                depth=task.depth + 1,
                max_depth=task.max_depth,
            )
            child_res = self.dispatch_single(task=child_task, seat=seat, role=child_role)
            return child_res.output or f"[{child_role.name} completed with no output]"

        tool_runner.register(
            name="deploy_subagent",
            description="Deploy a specialized subagent (e.g. 'scout', 'tester', 'python_developer') to run a scoped subtask.",
            parameters={
                "type": "object",
                "properties": {
                    "role_name": {"type": "string", "description": "Specialist role to spawn (e.g. scout, tester, python_developer)"},
                    "brief": {"type": "string", "description": "Clear instruction brief for the subagent"},
                },
                "required": ["role_name", "brief"],
            },
            func=deploy_subagent,
        )
        compiled_brief = self.compiler.assemble(
            role=role,
            task=task.brief,
            predecessor=task.predecessor,
            task_context=task.task_context,
            provider=seat.provider,
            endpoint=seat.endpoint,
            model=seat.model,
            workspace=str(workspace),
            session_id=candidate_id,
            all_tools=tool_runner.get_schemas(),
        )

        # 3. Instantiate candidate runtime
        model_provider = self._model_factory(seat)
        runtime = Runtime(
            model_provider=model_provider,
            tool_runner=tool_runner,
            store=self.store,
            transport=ConsoleTransport(bot_name=f"{role.name}@{seat.id}"),
            observers=[metrics],
        )

        initial_state = State(
            session_id=candidate_id,
            role=role.name,
            system_prompt=compiled_brief.system_prompt,
            active_tools=compiled_brief.filtered_tools,
        )

        # 4. Execute the multi-turn agent loop
        try:
            final_state = runtime.run(
                initial_state,
                initial_event=UserMessage(text=compiled_brief.user_prompt, sender=task.from_role),
            )
            output = final_state.output
            error = None
            status = "completed" if final_state.status == Status.IDLE else final_state.status.value
        except Exception as e:
            output = None
            error = str(e)
            status = "failed"
            final_state = initial_state
            final_state.status = Status.HALTED

        duration = time.time() - start_time
        total_tokens = metrics.total_prompt_tokens + metrics.total_completion_tokens

        # 5. Meter tokens in ledger
        self.ledger.meter(seat.id, total_tokens)

        # 6. Extract or generate self-report
        self_report = f"Candidate {seat.id} executed brief in {duration:.2f}s ({total_tokens} tokens). Output: {output}"

        # 7. Collect tool audit records
        tool_records = self.store.query("tool_result", session_id=candidate_id) if hasattr(self.store, "query") else []

        # 8. Record trial entry in store
        if self.store:
            try:
                self.store.append(
                    StoreRecord(
                        kind="terrarium_trial",
                        record={
                            "task_id": task.id,
                            "candidate_id": candidate_id,
                            "seat_id": seat.id,
                            "role": role.name,
                            "status": status,
                            "tokens_used": total_tokens,
                            "duration_seconds": duration,
                            "output_preview": output[:200] if output else "",
                            "error": error,
                        },
                    )
                )
            except Exception:
                pass

        return TerrariumCandidateResult(
            candidate_id=candidate_id,
            task_id=task.id,
            seat=seat,
            role=role,
            final_state=final_state,
            output=output,
            self_report=self_report,
            tokens_used=total_tokens,
            duration_seconds=duration,
            workspace_path=workspace,
            tool_events=tool_records,
            status=status,
            error=error,
        )

    def dispatch_parallel(
        self,
        task: TaskRecord,
        candidate_seats: list[Seat],
        role: Role,
        max_workers: int = 4,
    ) -> list[TerrariumCandidateResult]:
        """Dispatch task concurrently across all candidate seats (A/B testing)."""
        if not candidate_seats:
            return []

        results: list[TerrariumCandidateResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(candidate_seats), max_workers)) as executor:
            future_to_seat = {
                executor.submit(self.dispatch_single, task, seat, role): seat
                for seat in candidate_seats
            }
            for future in concurrent.futures.as_completed(future_to_seat):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    seat = future_to_seat[future]
                    results.append(
                        TerrariumCandidateResult(
                            candidate_id=f"cand_{seat.id}_err",
                            task_id=task.id,
                            seat=seat,
                            role=role,
                            final_state=State(session_id=f"err_{seat.id}", status=Status.HALTED),
                            output=None,
                            self_report=None,
                            tokens_used=0,
                            duration_seconds=0.0,
                            workspace_path=self.base_workspace / task.id,
                            status="failed",
                            error=str(e),
                        )
                    )

        return results
