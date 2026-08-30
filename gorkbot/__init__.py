"""Arity — a small, composable trial kernel for autonomous agent harnesses.

Version: 0.3.0

The ``gorkbot`` import namespace is retained for compatibility.
"""
from .handlers import (
    ConsoleTransport,
    JsonlRecordStore,
    LocalToolRunner,
    MetricsObserver,
    OpenAIModelProvider,
)
from .roles import (
    ARCHITECT_ROLE,
    BUILDER_ROLE,
    ENGINEER_ROLE,
    PYTHON_DEVELOPER_ROLE,
    REVIEWER_ROLE,
    SCOUT_ROLE,
    SECRETARY_ROLE,
    TESTER_ROLE,
    VOICE_ROLE,
    DenialSet,
    Role,
    RoleRegistry,
    TypePack,
)
from .skills import (
    FIRECRAWL_SKILL,
    PYTHON_DEVELOPER_SKILL,
    PYTEST_TDD_SKILL,
    SCOUT_RECON_SKILL,
    TEST_ENGINEERING_SKILL,
    Skill,
    SkillRegistry,
)
from .tiers import (
    BriefCompiler,
    BriefRefusalError,
    CompiledBrief,
    PredecessorAccounts,
    TierLevel,
    compute_identity,
)
from .ledger import Seat, SeatLedger
from .composer import CastingComposer, CastingDecision
from .terrarium import (
    CONTEXT_MODES,
    HIDDEN_TESTS_DIR,
    CandidateSpec,
    ContextAdapter,
    ContextEnvelope,
    TaskRecord,
    TerrariumCandidateResult,
    TerrariumDispatcher,
    normalize_harness,
    normalize_tool_runner,
    run_sandbox_verification,
)
from .tasks import RaceTask, TaskBank
from .tools import (
    McpToolAdapter,
    PathTraversalError,
    SandboxToolRunner,
    SyntaxValidationError,
    resolve_sandbox_path,
)
from .scorecard import Scorecard, ScorecardRecord
from .archivist import ArchivistEntry, ImpartialArchivist
from .evidence import (
    ArtifactEvidence,
    CandidateEvidence,
    Evaluation,
    EvidenceBundle,
    Resolution,
    ResolutionKind,
    TrialEvaluator,
    evaluate_bundle,
    resolve_bundle,
)
from .pulse import CacheEconomics, CadenceModel, PulseAction, PulseEngine
from .transports import RedphoneInbox, RedphoneMessage, WebhookTransport
from .orchestrator import ArityOrchestrator, GorkbotOrchestrator, OrchestrationResponse
from .runtime import Runtime
from .seams import (
    ModelProvider,
    Observer,
    RecordStore,
    ToolRunner,
    Transport,
)
from .transition import transition
from .types import (
    CallModel,
    Effect,
    EmitMessage,
    Event,
    ExecuteTool,
    Halt,
    HandoffCompleted,
    HandoffRequested,
    Interrupt,
    ModelCompleted,
    ModelFailed,
    PulseTick,
    SchedulePulse,
    SpawnHandoff,
    State,
    Status,
    StoreRecord,
    ToolCompleted,
    UserMessage,
)

__version__ = "0.3.0"

__all__ = [
    # Core types
    "Status",
    "State",
    "Event",
    "Effect",
    "UserMessage",
    "ModelCompleted",
    "ModelFailed",
    "ToolCompleted",
    "PulseTick",
    "HandoffRequested",
    "HandoffCompleted",
    "Interrupt",
    "CallModel",
    "ExecuteTool",
    "EmitMessage",
    "StoreRecord",
    "SpawnHandoff",
    "SchedulePulse",
    "Halt",
    # Transition
    "transition",
    # Runtime
    "Runtime",
    # Seams
    "ModelProvider",
    "ToolRunner",
    "RecordStore",
    "Transport",
    "Observer",
    # Handlers
    "OpenAIModelProvider",
    "LocalToolRunner",
    "JsonlRecordStore",
    "ConsoleTransport",
    "MetricsObserver",
    # Roles & Denial Sets
    "Role",
    "DenialSet",
    "RoleRegistry",
    "SECRETARY_ROLE",
    "VOICE_ROLE",
    "ENGINEER_ROLE",
    "PYTHON_DEVELOPER_ROLE",
    "BUILDER_ROLE",
    "REVIEWER_ROLE",
    "SCOUT_ROLE",
    # Memory & Briefs
    "PredecessorAccounts",
    "CompiledBrief",
    "BriefCompiler",
    "BriefRefusalError",
    "compute_identity",
    "Seat",
    "SeatLedger",
    "CastingComposer",
    "CastingDecision",
    # Terrarium & Multi-Dimensional Trials
    "CandidateSpec",
    "ContextAdapter",
    "ContextEnvelope",
    "TaskRecord",
    "TerrariumCandidateResult",
    "TerrariumDispatcher",
    "run_sandbox_verification",
    # Tools & Sandbox
    "SandboxToolRunner",
    "McpToolAdapter",
    "PathTraversalError",
    "SyntaxValidationError",
    "resolve_sandbox_path",
    # Archivist & Scorecard
    "Scorecard",
    "ScorecardRecord",
    "ArchivistEntry",
    "ImpartialArchivist",
    # Frozen evidence, evaluation, and resolution
    "ArtifactEvidence",
    "CandidateEvidence",
    "EvidenceBundle",
    "Evaluation",
    "TrialEvaluator",
    "Resolution",
    "ResolutionKind",
    "evaluate_bundle",
    "resolve_bundle",
    # Pulse & Keepalive
    "PulseEngine",
    "PulseAction",
    "CacheEconomics",
    "CadenceModel",
    # Transports & Red Phone
    "RedphoneInbox",
    "RedphoneMessage",
    "WebhookTransport",
    # Orchestrator
    "ArityOrchestrator",
    "GorkbotOrchestrator",
    "OrchestrationResponse",
]
