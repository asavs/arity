"""gorkbot — A composable statechart chassis for autonomous AI agents.

Version: 0.0.1
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
)
from .skills import (
    FIRECRAWL_SKILL,
    PYTHON_DEVELOPER_SKILL,
    PYTEST_TDD_SKILL,
    SCOUT_RECON_SKILL,
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
from .terrarium import CandidateSpec, TaskRecord, TerrariumCandidateResult, TerrariumDispatcher, run_sandbox_verification
from .tools import (
    McpToolAdapter,
    PathTraversalError,
    SandboxToolRunner,
    SyntaxValidationError,
    resolve_sandbox_path,
)
from .scorecard import Scorecard, ScorecardRecord
from .archivist import ArchivistEntry, ImpartialArchivist
from .pulse import CacheEconomics, CadenceModel, PulseAction, PulseEngine
from .transports import RedphoneInbox, RedphoneMessage, WebhookTransport
from .orchestrator import GorkbotOrchestrator, OrchestrationResponse
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

__version__ = "0.2.0"

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
    "GorkbotOrchestrator",
    "OrchestrationResponse",
]
