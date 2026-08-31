"""Arity — a small, composable trial kernel for autonomous agent harnesses.

The ``arity`` import namespace is retained for compatibility.
"""
from ._version import __version__
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
    UnsupportedEvaluationSchema,
    UnsupportedEvidenceContractSchema,
    UnsupportedEvidenceSchema,
    UnsupportedResolutionSchema,
    evaluate_bundle,
    factual_eligibility,
    resolve_bundle,
)
from .pulse import CacheEconomics, CadenceModel, PulseAction, PulseEngine
from .transports import RedphoneInbox, RedphoneMessage, WebhookTransport
from .orchestrator import ArityOrchestrator, ArityOrchestrator, OrchestrationResponse
from .runtime import Runtime
from .record_readers import (
    JsonlRecordReader,
    RecordChanged,
    RecordCorruption,
    RecordNotFound,
    RecordReadError,
    SqliteRecordReader,
    StoreSpec,
    configured_store_spec,
    open_record_reader,
)
from .seams import (
    ModelProvider,
    Observer,
    RecordReader,
    RecordStore,
    ToolRunner,
    Transport,
)
from .transition import transition
from .trial_events import (
    TrialEvent,
    TrialJournal,
    TrialReplay,
    UnsupportedTrialEventSchema,
    replay_trial,
)
from .inspection import (
    InspectionIssue,
    TrialCatalog,
    TrialInspection,
    TrialNotFound,
    TrialSummary,
    inspect_trial,
    inspect_trials,
)
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

__all__ = [
    "__version__",
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
    "RecordReader",
    "Transport",
    "Observer",
    # Handlers
    "OpenAIModelProvider",
    "LocalToolRunner",
    "JsonlRecordStore",
    "ConsoleTransport",
    "MetricsObserver",
    # Query-only record readers
    "JsonlRecordReader",
    "SqliteRecordReader",
    "RecordReadError",
    "RecordNotFound",
    "RecordCorruption",
    "RecordChanged",
    "StoreSpec",
    "configured_store_spec",
    "open_record_reader",
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
    "UnsupportedEvidenceContractSchema",
    "UnsupportedEvidenceSchema",
    "UnsupportedEvaluationSchema",
    "UnsupportedResolutionSchema",
    "Resolution",
    "ResolutionKind",
    "evaluate_bundle",
    "factual_eligibility",
    "resolve_bundle",
    # Trial event observation and replay
    "TrialEvent",
    "TrialJournal",
    "TrialReplay",
    "UnsupportedTrialEventSchema",
    "replay_trial",
    "InspectionIssue",
    "TrialCatalog",
    "TrialInspection",
    "TrialNotFound",
    "TrialSummary",
    "inspect_trial",
    "inspect_trials",
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
    "ArityOrchestrator",
    "OrchestrationResponse",
]
