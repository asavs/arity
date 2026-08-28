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
    REVIEWER_ROLE,
    VOICE_ROLE,
    DenialSet,
    Role,
    RoleRegistry,
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
from .terrarium import TaskRecord, TerrariumCandidateResult, TerrariumDispatcher
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

__version__ = "0.1.1"

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
    "VOICE_ROLE",
    "ARCHITECT_ROLE",
    "BUILDER_ROLE",
    "REVIEWER_ROLE",
    # Tiers & Briefs
    "TierLevel",
    "PredecessorAccounts",
    "CompiledBrief",
    "BriefCompiler",
    "BriefRefusalError",
    "compute_identity",
    # Ledger & Casting
    "Seat",
    "SeatLedger",
    "CastingComposer",
    "CastingDecision",
    # Terrarium & Handoffs
    "TaskRecord",
    "TerrariumCandidateResult",
    "TerrariumDispatcher",
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
