"""Arity terrarium — multi-kernel parallel execution and task delegation.

Axiom 3 Corollary: Many kernels per task (run candidates side-by-side for evidence).
Axiom 1: One voice, a staff, and structured handoffs between them.
Axiom 2: Denial-set enforcement per role in isolated sandboxes.
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Union

from .handlers import (
    default_record_store,
    CLIModelProvider,
    ConsoleTransport,
    JsonlRecordStore,
    LocalToolRunner,
    MetricsObserver,
    create_model_provider,
)
from .tools import USER_DELIVERY_MARKER, SandboxToolRunner, create_mcp_tool_runner
from .ledger import Seat, SeatLedger
from .roles import Role, BUILDER_ROLE, PYTHON_DEVELOPER_ROLE
from .runtime import Runtime
from .seams import ModelProvider, RecordStore, ToolRunner
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


class _NullTransport:
    def emit(self, effect: Any) -> None:
        pass


HIDDEN_TESTS_DIR = ".hidden_tests"
"""Directory inside a candidate sandbox where tester-authored tests are dropped after the build."""

# Files the archivist must not attribute to a candidate: verification side-effects.
PEERS_DIR = "peers"
"""Inside a conference-round sandbox: read-only copies of the other candidates' work, by letter."""

ARTIFACT_IGNORE_PARTS = ("__pycache__", ".pytest_cache", HIDDEN_TESTS_DIR, ".hypothesis", PEERS_DIR)


@dataclass
class TaskRecord:
    """A structured task handoff record (Axiom 1 & Axiom 3)."""
    id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    from_role: str = "secretary"
    to_role: str = "developer:python"
    brief: str = ""
    budget: float = 1.0  # USD or reference token budget
    depth: int = 0  # Recursion depth limit
    max_depth: int = 3
    predecessor: Optional[PredecessorAccounts] = None
    task_context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # Context-inheritance axis inputs. A "fork" candidate replays these verbatim
    # so the provider's prompt cache is hit (Axiom 7); other modes ignore them.
    parent_system_prompt: Optional[str] = None
    parent_messages: list[dict[str, Any]] = field(default_factory=list)
    # Tester-authored tests the builder never sees. Copied into the sandbox
    # only after the build finishes, then run alongside the builder's own tests.
    hidden_tests: dict[str, str] = field(default_factory=dict)  # relative filename -> source

    def is_depth_exceeded(self) -> bool:
        return self.depth >= self.max_depth


def _label(obj: Any) -> str:
    """Human-readable label for a harness or tool-runner axis value (str, class, instance, or callable)."""
    if isinstance(obj, str):
        return obj
    if hasattr(obj, "__name__"):
        return obj.__name__
    return obj.__class__.__name__


_TOOL_RUNNER_ALIASES = {
    "sandbox": "ast_tools", "ast": "ast_tools", "native": "ast_tools", "sandboxtoolrunner": "ast_tools",
    "mcp": "mcp_tools", "mcp_tools": "mcp_tools", "mcptooladapter": "mcp_tools", "create_mcp_tool_runner": "mcp_tools",
    "shell": "shell_tools", "local": "shell_tools", "shell_tools": "shell_tools", "localtoolrunner": "shell_tools",
}


def normalize_tool_runner(obj: Any) -> str:
    """Canonical scorecard name for a tool-runner axis value: ast_tools | mcp_tools | shell_tools | <custom>."""
    raw = _label(obj).lower()
    return _TOOL_RUNNER_ALIASES.get(raw, raw)


def normalize_harness(obj: Any) -> str:
    """Canonical scorecard name for a harness axis value."""
    raw = _label(obj).lower()
    return {"codex": "cli", "claude": "cli", "arity": "arity"}.get(raw, raw)


def skill_names(skills: list[Any]) -> list[str]:
    return [sk if isinstance(sk, str) else getattr(sk, "name", str(sk)) for sk in skills]


CONTEXT_MODES = ("fresh", "accounts", "fork")


@dataclass(frozen=True)
class ContextEnvelope:
    """The compiled context a strategy may transform immediately before execution."""

    system_prompt: str
    messages: tuple[dict[str, Any], ...]
    user_prompt: str


class ContextAdapter(Protocol):
    """A named, testable compaction/memory/context transform."""

    adapter_id: str

    def apply(self, envelope: ContextEnvelope) -> ContextEnvelope:
        ...


@dataclass
class CandidateSpec:
    """A multidimensional candidate specification across stack axes (Axiom 3).

    Axes:
      1. Model / Seat Axis: seat (model, provider, keys)
      2. Harness Axis: harness ("wire" | "cli" | "omp" | custom ModelProvider)
      3. Tool / MCP Axis: tool_runner_type ("sandbox"/"ast" | "mcp" | "shell"/"local" | custom)
      4. Skill / Brief Axis: skills (e.g. ["pytest-tdd"], ["firecrawl-developer-index"], [])
      5. Role Axis: role (e.g. PYTHON_DEVELOPER_ROLE, BUILDER_ROLE)
      6. Context Axis (prompt lever): context
           "fresh"    - brief only; predecessor accounts dropped
           "accounts" - brief + rendered predecessor self-report/archivist entry (default)
           "fork"     - parent's exact system prompt + message prefix replayed, brief appended
                        (prompt-cache hit; only meaningful when parent and child share a seat)
         An optional context_adapter performs one named transform after this built-in inheritance.
    """
    seat: Seat
    name: str = ""
    role: Optional[Role] = None
    harness: Union[str, ModelProvider] = "wire"
    tool_runner_type: Union[str, type[ToolRunner], ToolRunner, Callable[..., ToolRunner]] = "sandbox"
    skills: list[Union[str, Any]] = field(default_factory=list)
    context: str = "accounts"
    context_adapter: Optional[ContextAdapter] = None
    system_prompt_override: Optional[str] = None
    custom_model_provider: Optional[ModelProvider] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.context not in CONTEXT_MODES:
            raise ValueError(f"CandidateSpec.context must be one of {CONTEXT_MODES}, got {self.context!r}")
        if self.context_adapter is not None and not str(getattr(self.context_adapter, "adapter_id", "")).strip():
            raise ValueError("CandidateSpec.context_adapter must expose a stable adapter_id")
        if not self.name:
            self.name = f"{self.seat.id}:{_label(self.harness)}:{_label(self.tool_runner_type)}"

    @property
    def harness_name(self) -> str:
        return normalize_harness(self.harness)

    @property
    def tool_runner_name(self) -> str:
        return normalize_tool_runner(self.tool_runner_type)

    @property
    def skill_names(self) -> list[str]:
        return skill_names(self.skills)

    @property
    def context_adapter_id(self) -> Optional[str]:
        if self.context_adapter is None:
            return None
        return str(self.context_adapter.adapter_id)

    def signature(self, default_role: str = "builder") -> str:
        """Compute the unique multidimensional combo signature for scorecard standings.

        Format: role:model:harness:tool_runner[:skills][:ctx=<mode>]
        e.g. 'builder:gemini-3.6-flash:wire:ast_tools:pytest-tdd'
        The context segment is only emitted for non-default modes so existing keys stay stable.
        """
        r_name = self.role.key_name if self.role else default_role.replace(":", ".")
        parts = [r_name.lower(), self.seat.model.lower(), self.harness_name, self.tool_runner_name]
        if self.skill_names:
            parts.append(",".join(sorted(self.skill_names)))
        if self.context != "accounts":
            parts.append(f"ctx={self.context}")
        if self.context_adapter_id:
            parts.append(f"ctx_adapter={self.context_adapter_id}")
        return ":".join(parts)

    def display_tuple(self) -> tuple[str, str, str, str]:
        """Return (model, harness, tool_runner, skills_str) for display formatting."""
        s_str = ", ".join(self.skill_names) if self.skill_names else "baseline"
        return (self.seat.model, self.harness_name, self.tool_runner_name, s_str)


_EMPTY_TEST_RESULT: dict[str, Any] = {
    "has_tests": False, "passed": 0, "failed": 0, "total": 0, "exit_code": 0, "duration": 0.0, "output": "",
}


def _parse_test_output(raw_output: str, returncode: int) -> tuple[int, int]:
    """Extract (passed, failed) from pytest or unittest output."""
    passed = failed = 0
    m_pass = re.search(r"(\d+)\s+passed", raw_output)
    m_fail = re.search(r"(\d+)\s+failed", raw_output)
    m_err = re.search(r"(\d+)\s+error", raw_output)
    if m_pass:
        passed = int(m_pass.group(1))
    if m_fail:
        failed = int(m_fail.group(1))
    if m_err:
        failed += int(m_err.group(1))
    if not m_pass and not m_fail:
        m_ran = re.search(r"Ran (\d+) tests?", raw_output)
        if m_ran:
            total_ran = int(m_ran.group(1))
            if returncode == 0 and "OK" in raw_output:
                passed, failed = total_ran, 0
            else:
                m_f = re.search(r"failures=(\d+)", raw_output)
                m_e = re.search(r"errors=(\d+)", raw_output)
                failed = (int(m_f.group(1)) if m_f else 0) + (int(m_e.group(1)) if m_e else 0)
                passed = max(0, total_ran - failed)
    return passed, failed


def _run_tests(ws: Path, cmd: str, timeout: float, allow_unittest_fallback: bool) -> dict[str, Any]:
    start_time = time.time()
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{str(ws)}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(cmd, shell=True, cwd=str(ws), capture_output=True, text=True, timeout=timeout, env=env)
        raw_output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0 and "No module named pytest" in raw_output and allow_unittest_fallback:
            proc = subprocess.run(
                "python -m unittest discover -s . -p 'test_*.py'",
                shell=True, cwd=str(ws), capture_output=True, text=True, timeout=timeout, env=env,
            )
            raw_output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        passed, failed = _parse_test_output(raw_output, proc.returncode)
        return {
            "has_tests": True,
            "passed": passed,
            "failed": failed,
            "total": passed + failed,
            "exit_code": proc.returncode,
            "duration": time.time() - start_time,
            "output": raw_output.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"has_tests": True, "passed": 0, "failed": 1, "total": 1, "exit_code": -1,
                "duration": timeout, "output": f"Tests timed out after {timeout}s"}
    except Exception as e:
        return {"has_tests": True, "passed": 0, "failed": 1, "total": 1, "exit_code": 1,
                "duration": 0.0, "output": f"Verification error: {e}"}


PYTHON_VERIFY: dict[str, Any] = {
    "test_command": "python -m pytest -v -p no:cacheprovider",
    "test_globs": ["test_*.py", "*_test.py", "tests/**/test_*.py"],
    "hidden_dir": HIDDEN_TESTS_DIR,
    "hidden_command": f"python -m pytest {HIDDEN_TESTS_DIR} -v -p no:cacheprovider",
}
"""Default verification block; a role's TypePack (developer:python, developer:rust) overrides it."""


def run_sandbox_verification(
    workspace: Path,
    test_command: Optional[str] = None,
    timeout: float = 30.0,
    hidden_tests: Optional[dict[str, str]] = None,
    verify: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Execute unit tests in a candidate workspace to verify correctness empirically.

    Two layers, reported separately and merged in the top-level counts:
      - the candidate's own tests (test_*.py / *_test.py / tests/**), or `test_command`
      - `hidden_tests`: tester-authored files the candidate never saw, written to
        `.hidden_tests/` only now and run with the workspace on PYTHONPATH.
    Result keys: has_tests, passed, failed, total, exit_code, duration, output,
    plus `own` and `hidden` sub-results with the same shape.
    """
    ws = Path(workspace)
    v = {**PYTHON_VERIFY, **(verify or {})}
    test_files = [p for g in v["test_globs"] for p in ws.glob(g)]

    own = dict(_EMPTY_TEST_RESULT, output="No unit test files found in sandbox workspace.")
    if test_files or test_command:
        own = _run_tests(ws, test_command or v["test_command"], timeout,
                         allow_unittest_fallback=not test_command and v["test_command"].startswith("python -m pytest"))

    hidden = dict(_EMPTY_TEST_RESULT)
    if hidden_tests:
        hdir = ws / v["hidden_dir"]
        hdir.mkdir(parents=True, exist_ok=True)
        for rel, src in hidden_tests.items():
            target = hdir / Path(rel).name
            target.write_text(src, encoding="utf-8")
        hidden = _run_tests(ws, v["hidden_command"], timeout, allow_unittest_fallback=False)

    layers = [r for r in (own, hidden) if r["has_tests"]]
    if not layers:
        return dict(own, own=own, hidden=hidden)
    exit_codes = [r["exit_code"] for r in layers]
    return {
        "has_tests": True,
        "passed": sum(r["passed"] for r in layers),
        "failed": sum(r["failed"] for r in layers),
        "total": sum(r["total"] for r in layers),
        "exit_code": next((c for c in exit_codes if c != 0), 0),
        "duration": sum(r["duration"] for r in layers),
        "output": "\n\n".join(r["output"] for r in layers if r["output"]),
        "own": own,
        "hidden": hidden,
    }


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
    spec: Optional[CandidateSpec] = None
    signature: str = ""
    harness: str = "wire"
    tool_runner_name: str = "sandbox"
    skills_used: list[str] = field(default_factory=list)
    test_results: Optional[dict[str, Any]] = None
    tester_result: Optional["TerrariumCandidateResult"] = None
    fallbacks: int = 0  # wire -> CLI harness fallbacks during the run; >0 means the harness axis moved
    brief: str = ""  # the brief this candidate was given (archivist checks its hard numbers against own tests)
    task_metadata: dict[str, Any] = field(default_factory=dict)  # e.g. module/entrypoint from the task bank
    # Conference (phase 2) only: files changed vs. phase 1, and this phase's own cost
    # (tokens_used/duration_seconds are cumulative across phases for a phase-2 result).
    changed_files: list[str] = field(default_factory=list)
    phase_tokens: int = 0
    phase_seconds: float = 0.0


class TerrariumDispatcher:
    """Dispatches tasks across multi-dimensional candidate kernels in isolated parallel sandboxes."""

    def __init__(
        self,
        ledger: SeatLedger,
        store: Optional[RecordStore] = None,
        compiler: Optional[BriefCompiler] = None,
        base_workspace: Optional[Path] = None,
        model_factory: Optional[Callable[[Seat], ModelProvider]] = None,
        quiet: bool = False,
    ):
        self.ledger = ledger
        self.store = store or default_record_store()
        self.compiler = compiler or BriefCompiler()
        self.base_workspace = Path(base_workspace) if base_workspace else Path(".terrarium")
        self.base_workspace.mkdir(parents=True, exist_ok=True)
        self._model_factory = model_factory or self._default_model_factory
        self.quiet = quiet  # suppress per-candidate console chatter (machine-readable output modes)

    def _default_model_factory(self, seat: Seat) -> ModelProvider:
        return create_model_provider(seat)

    def _resolve_candidate_spec(
        self,
        spec_or_seat: Union[CandidateSpec, Seat],
        role: Optional[Role] = None,
    ) -> CandidateSpec:
        """Normalize Seat or CandidateSpec into a unified CandidateSpec."""
        if isinstance(spec_or_seat, CandidateSpec):
            if role and not spec_or_seat.role:
                spec_or_seat.role = role
            return spec_or_seat
        return CandidateSpec(
            seat=spec_or_seat,
            role=role or PYTHON_DEVELOPER_ROLE,
            harness="wire",
            tool_runner_type="sandbox",
        )

    def dispatch_single(
        self,
        task: TaskRecord,
        candidate_or_seat: Union[CandidateSpec, Seat],
        role: Optional[Role] = None,
        run_verification: bool = True,
        test_command: Optional[str] = None,
        workspace: Optional[Path] = None,
        candidate_id: Optional[str] = None,
        mailbox: Optional[dict[str, list[str]]] = None,
        peer_letter: Optional[str] = None,
    ) -> TerrariumCandidateResult:
        """Run a single candidate kernel in an isolated sandbox with multidimensional seams.

        `workspace`/`candidate_id` let a later phase (conference) wake a candidate up in the
        sandbox it already built. `mailbox`/`peer_letter` enable message(to="peer:B"): the note
        is queued for B's next round rather than spawning a new kernel.
        """
        spec = self._resolve_candidate_spec(candidate_or_seat, role)
        seat = spec.seat
        actual_role = spec.role or role or PYTHON_DEVELOPER_ROLE

        start_time = time.time()
        # Seat ids like "google:asa:gemini-3.6-flash" are not valid directory names on Windows.
        seat_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(seat.id)).strip("-")
        candidate_id = candidate_id or f"cand_{seat_slug}_{uuid.uuid4().hex[:6]}"
        workspace = Path(workspace) if workspace else self.base_workspace / task.id / candidate_id
        workspace.mkdir(parents=True, exist_ok=True)

        sig = spec.signature(default_role=actual_role.name)
        harness_name = spec.harness_name
        tool_name = spec.tool_runner_name
        skills_used = spec.skill_names

        if task.is_depth_exceeded():
            return TerrariumCandidateResult(
                candidate_id=candidate_id,
                task_id=task.id,
                seat=seat,
                role=actual_role,
                final_state=State(session_id=candidate_id, status=Status.HALTED),
                output=f"Error: Maximum recursion depth ({task.max_depth}) exceeded.",
                self_report=None,
                tokens_used=0,
                duration_seconds=0.0,
                workspace_path=workspace,
                status="depth_exceeded",
                error="Max depth exceeded",
                spec=spec,
                signature=sig,
                harness=harness_name,
                tool_runner_name=tool_name,
                skills_used=skills_used,
            )

        # 1. Peer message router for cross-kernel delegation
        def route_peer_message(to_peer: str, text_msg: str) -> str:
            target = to_peer.strip()
            if target.lower().startswith("peer:") and mailbox is not None:
                letter = target.split(":", 1)[1].strip().upper()
                if letter == (peer_letter or ""):
                    return "That is you. Address a different peer letter."
                if letter not in mailbox:
                    return f"No peer '{letter}' in this conference. Peers: {', '.join(sorted(k for k in mailbox if k != peer_letter))}."
                mailbox[letter].append(f"[from {peer_letter or '?'}] {text_msg}")
                return f"Queued for {letter}; delivered at the start of their next round."
            if task.depth + 1 >= task.max_depth:
                return f"Error: Maximum message hop limit ({task.max_depth}) reached."
            from .roles import RoleRegistry
            roles = RoleRegistry()
            target_peer = roles.resolve(to_peer)
            peer_task = TaskRecord(
                from_role=actual_role.name if hasattr(actual_role, 'name') else str(actual_role),
                to_role=target_peer.name,
                brief=text_msg,
                depth=task.depth + 1,
                max_depth=task.max_depth,
            )
            peer_spec = CandidateSpec(
                seat=seat,
                role=target_peer,
                harness=spec.harness,
                tool_runner_type=spec.tool_runner_type,
                skills=spec.skills,
                context_adapter=spec.context_adapter,
            )
            peer_res = self.dispatch_single(task=peer_task, candidate_or_seat=peer_spec)
            return peer_res.output or f"[{target_peer.name} replied with no output]"

        # 2. Setup Tool Runner Axis (Sandbox / AST vs. MCP vs. Shell vs. Custom)
        if spec.tool_runner_type in ("sandbox", "ast", "native") or spec.tool_runner_type is SandboxToolRunner:
            tool_runner = SandboxToolRunner(workspace_root=workspace, role=actual_role, message_router=route_peer_message)
        elif spec.tool_runner_type in ("mcp", "mcp_tools") or spec.tool_runner_type is create_mcp_tool_runner:
            tool_runner = create_mcp_tool_runner(workspace_root=workspace, role=actual_role)
        elif spec.tool_runner_type in ("shell", "local", "shell_tools") or spec.tool_runner_type is LocalToolRunner:
            tool_runner = LocalToolRunner(workspace_root=workspace)
        # ToolRunner is runtime_checkable, so isinstance() only asks whether execute and
        # get_schemas exist as attributes — true of a runner class as well as an instance.
        # Without the type guard a class takes this branch and is used uninstantiated.
        elif isinstance(spec.tool_runner_type, ToolRunner) and not isinstance(spec.tool_runner_type, type):
            tool_runner = spec.tool_runner_type
        elif callable(spec.tool_runner_type):
            try:
                tool_runner = spec.tool_runner_type(workspace, actual_role, route_peer_message)
            except TypeError:
                tool_runner = spec.tool_runner_type(workspace)
        else:
            tool_runner = SandboxToolRunner(workspace_root=workspace, role=actual_role, message_router=route_peer_message)

        # 3. Setup Brief Compiler & Skills Axis (+ Context Axis: what the child inherits)
        compiled_brief = self.compiler.assemble(
            role=actual_role,
            task=task.brief,
            predecessor=task.predecessor if spec.context == "accounts" else None,
            task_context=task.task_context,
            provider=seat.provider,
            endpoint=seat.endpoint,
            model=seat.model,
            workspace=str(workspace),
            session_id=candidate_id,
            all_tools=tool_runner.get_schemas(),
            extra_skills=spec.skills,
            system_prompt_override=spec.system_prompt_override,
        )

        # 4. Setup Model Provider / Harness Axis (Direct Wire vs. CLI vs. OMP vs. Custom)
        if spec.custom_model_provider:
            model_provider = spec.custom_model_provider
        elif isinstance(spec.harness, ModelProvider):
            model_provider = spec.harness
        elif spec.harness in ("cli", "codex", "claude"):
            cli_harness = spec.harness if spec.harness != "cli" else "codex"
            model_provider = CLIModelProvider(harness=cli_harness, model=seat.model)
        elif spec.harness == "omp":
            model_provider = CLIModelProvider(harness="omp", model=seat.model)
        else:
            # Default wire / factory provider
            model_provider = self._model_factory(seat)
            # A seat with no wire at all gets a bare CLI provider that cannot call tools; say so in
            # the record instead of filing it under "wire". (A wire that later falls back is handled below.)
            if hasattr(model_provider, "cwd") and not hasattr(model_provider, "primary"):  # a bare CLI, no wire
                harness_name = f"cli:{getattr(model_provider, 'harness', 'omp')}"

        # Any CLI harness in the chain (bare, or a wire's fallback) must act inside this sandbox.
        # TODO(kernel): a CLI's own tools still bypass the role's denial set; sandboxing by cwd is
        # containment, not enforcement. A leaf should run as a user that cannot see the repo.
        for cli in (model_provider, getattr(model_provider, "fallback", None)):
            if cli is not None and hasattr(cli, "cwd"):
                cli.cwd = str(workspace)

        metrics = MetricsObserver()
        runtime = Runtime(
            model_provider=model_provider,
            tool_runner=tool_runner,
            store=self.store,
            transport=(_NullTransport() if self.quiet else ConsoleTransport(bot_name=f"{actual_role.name}@{seat.id}")),
            observers=[metrics],
        )

        initial_state = State(
            session_id=candidate_id,
            role=actual_role.name,
            system_prompt=compiled_brief.system_prompt,
            active_tools=compiled_brief.filtered_tools,
        )
        if spec.context == "fork" and (task.parent_system_prompt or task.parent_messages):
            # Replay the parent's exact prefix so the provider cache is hit; the brief
            # arrives as the next user turn. Tool-call/result pairs are copied verbatim.
            initial_state.system_prompt = task.parent_system_prompt or compiled_brief.system_prompt
            initial_state.messages = [dict(m) for m in task.parent_messages]

        user_prompt = compiled_brief.user_prompt
        if spec.context_adapter is not None:
            adapted = spec.context_adapter.apply(
                ContextEnvelope(
                    system_prompt=initial_state.system_prompt,
                    messages=tuple(dict(message) for message in initial_state.messages),
                    user_prompt=user_prompt,
                )
            )
            if not isinstance(adapted, ContextEnvelope):
                raise TypeError("ContextAdapter.apply() must return ContextEnvelope")
            initial_state.system_prompt = adapted.system_prompt
            initial_state.messages = [dict(message) for message in adapted.messages]
            user_prompt = adapted.user_prompt

        # 5. Execute the multi-turn agent loop
        try:
            final_state = runtime.run(
                initial_state,
                initial_event=UserMessage(text=user_prompt, sender=task.from_role),
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

        # If the wire fell back to a CLI harness mid-run, the candidate did not run on the
        # harness its signature claims. Record it so the judge can refuse to attribute.
        fallbacks = int(getattr(model_provider, "fallback_count", 0) or 0)
        if fallbacks:
            fallback_name = getattr(getattr(model_provider, "fallback", None), "harness", "cli")
            harness_name = f"{harness_name}->{fallback_name}"

        # 6. Meter tokens in ledger
        self.ledger.meter(seat.id, total_tokens)

        # 7. In-sandbox Verification (Axiom 3 Empirical Proof)
        test_results = None
        if run_verification:
            test_results = run_sandbox_verification(
                workspace, test_command=test_command, hidden_tests=task.hidden_tests or None,
                verify=getattr(actual_role, "verify", None) or None,
            )

        # 8. Generate self-report
        test_summary = ""
        if test_results and test_results.get("has_tests"):
            test_summary = f" Unit tests: {test_results.get('passed')}/{test_results.get('total')} passed."
            hidden = test_results.get("hidden") or {}
            if hidden.get("has_tests"):
                test_summary += f" Hidden tests: {hidden.get('passed')}/{hidden.get('total')} passed."
        self_report = f"Candidate {spec.name} executed brief in {duration:.2f}s ({total_tokens} tokens).{test_summary} Output: {output}"

        # 9. Collect tool audit records
        tool_records = self.store.query("tool_result", session_id=candidate_id) if hasattr(self.store, "query") else []

        # A kernel that ends its turn by message(to="user") has spoken; that text is its output.
        # Claude in particular delivers rankings and reports this way and then stops with empty content.
        if not (output or "").strip():
            delivered = [m for m in final_state.messages if m.get("role") == "tool"
                         and str(m.get("content", "")).startswith(USER_DELIVERY_MARKER)]
            if delivered:
                output = str(delivered[-1]["content"]).split(f"{USER_DELIVERY_MARKER}: ", 1)[-1]
                self_report = f"Candidate {spec.name} executed brief in {duration:.2f}s ({total_tokens} tokens).{test_summary} Output: {output}"

        # 10. Record trial entry in store
        if self.store:
            try:
                self.store.append(
                    StoreRecord(
                        kind="terrarium_trial",
                        record={
                            "task_id": task.id,
                            "candidate_id": candidate_id,
                            "seat_id": seat.id,
                            "role": actual_role.name,
                            "signature": sig,
                            "harness": harness_name,
                            "fallbacks": fallbacks,
                            "tool_runner": tool_name,
                            "skills": skills_used,
                            "status": status,
                            "tokens_used": total_tokens,
                            "duration_seconds": duration,
                            "test_results": test_results,
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
            role=actual_role,
            final_state=final_state,
            output=output,
            self_report=self_report,
            tokens_used=total_tokens,
            duration_seconds=duration,
            workspace_path=workspace,
            tool_events=tool_records,
            status=status,
            error=error,
            spec=spec,
            signature=sig,
            harness=harness_name,
            tool_runner_name=tool_name,
            skills_used=skills_used,
            test_results=test_results,
            fallbacks=fallbacks,
            brief=task.brief,
            task_metadata=dict(task.metadata),
        )

    def dispatch_candidates(
        self,
        task: TaskRecord,
        candidates: list[CandidateSpec],
        max_workers: int = 4,
        run_verification: bool = True,
        test_command: Optional[str] = None,
    ) -> list[TerrariumCandidateResult]:
        """Dispatch task concurrently across multidimensional CandidateSpecs (A/B/C testing)."""
        if not candidates:
            return []

        # Legacy ARITY_CONCURRENCY remains a parallel-worker safety cap.
        from .tools import get_config_value
        cap = get_config_value("ARITY_CONCURRENCY")
        if cap and str(cap).isdigit() and int(cap) > 0:
            max_workers = min(max_workers, int(cap))

        # Execute concurrently but return declaration order.  Completion timing is an observed
        # metric, not a stable arm identity for evidence, evaluators, or replay.
        ordered_results: list[Optional[TerrariumCandidateResult]] = [None] * len(candidates)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(candidates), max_workers)) as executor:
            future_to_cand = {
                executor.submit(
                    self.dispatch_single,
                    task,
                    cand,
                    None,
                    run_verification,
                    test_command,
                ): (index, cand)
                for index, cand in enumerate(candidates)
            }
            for future in concurrent.futures.as_completed(future_to_cand):
                index, cand = future_to_cand[future]
                try:
                    ordered_results[index] = future.result()
                except Exception as e:
                    actual_role = cand.role or PYTHON_DEVELOPER_ROLE
                    ordered_results[index] = TerrariumCandidateResult(
                        candidate_id=f"cand_{cand.seat.id}_err",
                        task_id=task.id,
                        seat=cand.seat,
                        role=actual_role,
                        final_state=State(session_id=f"err_{cand.seat.id}", status=Status.HALTED),
                        output=None,
                        self_report=None,
                        tokens_used=0,
                        duration_seconds=0.0,
                        workspace_path=self.base_workspace / task.id,
                        status="failed",
                        error=str(e),
                        spec=cand,
                        signature=cand.signature(default_role=actual_role.name),
                    )

        return [result for result in ordered_results if result is not None]

    def dispatch_parallel(
        self,
        task: TaskRecord,
        candidate_seats: list[Seat],
        role: Role,
        max_workers: int = 4,
    ) -> list[TerrariumCandidateResult]:
        """Dispatch task concurrently across candidate seats (backwards-compatible API)."""
        specs = [
            CandidateSpec(seat=seat, role=role, harness="wire", tool_runner_type="sandbox")
            for seat in candidate_seats
        ]
        return self.dispatch_candidates(task=task, candidates=specs, max_workers=max_workers)

    def author_hidden_tests(
        self,
        task: TaskRecord,
        tester: CandidateSpec,
    ) -> tuple[dict[str, str], TerrariumCandidateResult]:
        """Run a tester candidate against the brief and harvest the test files it writes.

        The tester works in its own sandbox and never sees any builder output. Whatever
        `test_*.py` files it leaves behind become the hidden suite for the race.
        """
        from .roles import TESTER_ROLE
        tester_task = TaskRecord(
            from_role=task.from_role,
            to_role="tester",
            brief=(
                "Author the acceptance tests for the following task. You are NOT implementing it; "
                "another engineer will, without seeing your tests. Write only `test_*.py` files.\n\n"
                f"## Task under test\n{task.brief}"
            ),
            depth=task.depth,
            max_depth=task.max_depth,
            task_context=task.task_context,
        )
        if tester.role is None:
            tester.role = TESTER_ROLE
        res = self.dispatch_single(task=tester_task, candidate_or_seat=tester, run_verification=False)
        harvested: dict[str, str] = {}
        if res.workspace_path.exists():
            for p in sorted(res.workspace_path.rglob("test_*.py")):
                if any(part in ARTIFACT_IGNORE_PARTS for part in p.parts):
                    continue
                harvested[p.name] = p.read_text(encoding="utf-8")
        return harvested, res

    def teardown(self, results: list[TerrariumCandidateResult]) -> None:
        """Remove candidate sandboxes and empty task directories (tear-down half of tear-up/tear-down)."""
        for r in results:
            ws = Path(r.workspace_path)
            try:
                if ws.is_dir() and ws != self.base_workspace and self.base_workspace in ws.parents:
                    shutil.rmtree(ws, ignore_errors=True)
                    parent = ws.parent
                    if parent != self.base_workspace and parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()
            except Exception:
                pass

    def race(
        self,
        task: Union[TaskRecord, str],
        candidates: list[CandidateSpec],
        test_command: Optional[str] = None,
        max_workers: int = 4,
        archivist: Optional[Any] = None,
        tester: Optional[CandidateSpec] = None,
        teardown: bool = False,
    ) -> tuple[Optional[TerrariumCandidateResult], list[TerrariumCandidateResult], list[Any]]:
        """Execute an empirical race between multidimensional candidates, audit and rank them.

        Order of operations matters for impartiality:
          1. tester (if given) authors hidden tests, before any builder runs
          2. builders run in parallel sandboxes; each is verified against its own tests
             AND the hidden suite, which is only written into the sandbox after the build
          3. the archivist audits artifacts (excluding verification side-effects) and scores
          4. optionally, sandboxes are torn down
        """
        task_record = TaskRecord(brief=task) if isinstance(task, str) else task

        tester_result: Optional[TerrariumCandidateResult] = None
        if tester is not None:
            harvested, tester_result = self.author_hidden_tests(task_record, tester)
            merged = dict(task_record.hidden_tests)
            merged.update(harvested)
            task_record.hidden_tests = merged

        results = self.dispatch_candidates(
            task=task_record,
            candidates=candidates,
            max_workers=max_workers,
            run_verification=True,
            test_command=test_command,
        )

        if archivist is None:
            from .archivist import ImpartialArchivist
            archivist = ImpartialArchivist(store=self.store)

        winner, entries = archivist.evaluate_trial(results)
        for r in results:
            r.tester_result = tester_result

        if teardown:
            self.teardown(results + ([tester_result] if tester_result else []))
        return winner, results, entries

    def conference(
        self,
        task: TaskRecord,
        results: list[TerrariumCandidateResult],
        entries: Optional[list[Any]] = None,
        rounds: int = 2,
        max_workers: int = 4,
        test_command: Optional[str] = None,
    ) -> list[TerrariumCandidateResult]:
        """Wake the candidates back up, together, and let them sort out a final draft.

        Each round, every candidate is resumed in its own sandbox (context="fork": its phase-1
        transcript is replayed, so the provider cache is hit) with read-only copies of the
        others' work under peers/<letter>/, the archivist's axes for everyone (blind letters),
        and whatever notes peers sent it via message(to="peer:X"). Messages are queued between
        rounds - there is no live channel, so nothing can deadlock. Verification runs after the
        last round; the returned results are phase-2 results ready for the archivist.
        """
        if not results or rounds <= 0:
            return []
        letters = [chr(ord("A") + i) for i in range(len(results))]
        by_letter = dict(zip(letters, results))

        def snapshot(ws: Path) -> dict[str, str]:
            import hashlib
            out: dict[str, str] = {}
            if ws.is_dir():
                for p in ws.rglob("*"):
                    rel = p.relative_to(ws)
                    if p.is_file() and not any(seg in ARTIFACT_IGNORE_PARTS for seg in rel.parts):
                        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
            return out

        before = {L: snapshot(Path(r.workspace_path)) for L, r in by_letter.items()}
        entry_of = {e.candidate_id: e for e in (entries or [])}
        mailbox: dict[str, list[str]] = {L: [] for L in letters}
        current: dict[str, TerrariumCandidateResult] = dict(by_letter)
        # Each round returns a fresh result carrying only that round's usage, and replaces the
        # previous one. Totalling here is what keeps rounds 1..n-1 from vanishing from the bill.
        conf_tokens: dict[str, int] = {L: 0 for L in letters}
        conf_seconds: dict[str, float] = {L: 0.0 for L in letters}

        def facts(L: str) -> str:
            e = entry_of.get(by_letter[L].candidate_id)
            a = (e.axes if e else {}) or {}
            keep = ("tier", "hidden_rate", "own_rate", "loc", "test_count", "type_ignores", "brief_numbers_in_own_tests")
            return ", ".join(f"{k}={a[k]}" for k in keep if k in a) or (e.verdict if e else "unknown")

        def stage_peers(L: str) -> None:
            ws = Path(by_letter[L].workspace_path)
            peers_root = ws / PEERS_DIR
            shutil.rmtree(peers_root, ignore_errors=True)
            for M, other in by_letter.items():
                if M == L:
                    continue
                src = Path(other.workspace_path)
                if not src.is_dir():
                    continue
                for p in src.rglob("*"):
                    rel = p.relative_to(src)
                    if p.is_file() and not any(seg in ARTIFACT_IGNORE_PARTS for seg in rel.parts):
                        dst = peers_root / M / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, dst)

        def round_brief(L: str, rnd: int, inbox: list[str]) -> str:
            notes = "\n".join(f"- {m}" for m in inbox) if inbox else "- (none)"
            others = ", ".join(M for M in letters if M != L)
            return (
                f"# Conference round {rnd}/{rounds}\n"
                f"You are candidate {L}. You and {others} each built the brief below in isolation; all attempts are now "
                f"open. The other attempts are under `{PEERS_DIR}/<letter>/` in your workspace (read-only copies). "
                f"Archivist facts, by letter:\n" + "\n".join(f"- {M}: {facts(M)}" for M in letters) + "\n\n"
                f"Notes sent to you by peers:\n{notes}\n\n"
                f"Produce the final draft in YOUR workspace: keep what is best, borrow from peers with credit in a "
                f"comment, drop what is worse. You may send short notes to peers with message(to=\"peer:{others.split(', ')[0]}\") "
                f"- they arrive next round. Do not edit `{PEERS_DIR}/`. Finish with a closing report naming every file you changed.\n\n"
                f"## Original brief\n{task.brief}"
            )

        for rnd in range(1, rounds + 1):
            last = rnd == rounds
            for L in letters:
                stage_peers(L)
            # Notes sent last round are delivered now; notes sent this round wait for the next.
            deliveries = {L: list(mailbox[L]) for L in letters}
            for L in letters:
                mailbox[L] = []

            def run_one(L: str) -> tuple[str, TerrariumCandidateResult, bool]:
                prev = current[L]
                spec = prev.spec
                if spec is None:
                    return L, prev, False
                round_spec = CandidateSpec(
                    seat=spec.seat, name=spec.name, role=spec.role, harness=spec.harness,
                    tool_runner_type=spec.tool_runner_type, skills=spec.skills, context="fork",
                    context_adapter=spec.context_adapter,
                    system_prompt_override=spec.system_prompt_override, custom_model_provider=spec.custom_model_provider,
                    metadata=dict(spec.metadata),
                )
                round_task = TaskRecord(
                    id=task.id, from_role=task.from_role, to_role=task.to_role, brief=round_brief(L, rnd, deliveries[L]),
                    depth=task.depth, max_depth=task.max_depth, hidden_tests=task.hidden_tests, metadata=dict(task.metadata),
                    parent_system_prompt=prev.final_state.system_prompt, parent_messages=list(prev.final_state.messages),
                )
                res = self.dispatch_single(
                    round_task, round_spec, run_verification=False, test_command=test_command,
                    workspace=prev.workspace_path, candidate_id=f"{by_letter[L].candidate_id}_c{rnd}",
                    mailbox=mailbox, peer_letter=L,
                )
                res.brief = task.brief
                return L, res, True

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(letters), max_workers)) as ex:
                for L, res, ran in ex.map(run_one, letters):
                    current[L] = res
                    if ran:
                        conf_tokens[L] += res.tokens_used
                        conf_seconds[L] += res.duration_seconds

        # Verification only after the staged peer copies are gone: pytest would otherwise collect
        # peers/B/test_*.py and report import mismatches as failures.
        finals = []
        for L in letters:
            res = current[L]
            ws = Path(res.workspace_path)
            shutil.rmtree(ws / PEERS_DIR, ignore_errors=True)
            res.test_results = run_sandbox_verification(
                ws, test_command=test_command, hidden_tests=task.hidden_tests or None,
                verify=getattr(res.role, "verify", None) or None,
            )
            hidden = res.test_results.get("hidden") or {}
            # What the conference actually changed, and what the whole draft cost. Without the
            # cumulative cost a candidate that did nothing in phase 2 looks cheapest and wins the tie.
            after = snapshot(ws)
            res.changed_files = sorted(f for f in set(before[L]) | set(after) if before[L].get(f) != after.get(f))
            res.phase_tokens = conf_tokens[L]
            res.phase_seconds = conf_seconds[L]
            res.tokens_used = conf_tokens[L] + by_letter[L].tokens_used
            res.duration_seconds = conf_seconds[L] + by_letter[L].duration_seconds
            res.self_report = (
                f"Candidate {res.spec.name if res.spec else res.candidate_id} after conference: "
                f"own tests {res.test_results.get('own', {}).get('passed', 0)}/{res.test_results.get('own', {}).get('total', 0)}, "
                f"hidden tests {hidden.get('passed', 0)}/{hidden.get('total', 0)}; changed {len(res.changed_files)} file(s). "
                f"Output: {res.output}"
            )
            finals.append(res)
        return finals
