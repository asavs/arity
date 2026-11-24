//! Shared domain types and protocol invariants for Arity v0.0.1.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("stale writer epoch on thread '{thread_id}': writer holds epoch {writer_epoch}, thread is at epoch {current_epoch}")]
    StaleEpoch {
        thread_id: String,
        writer_epoch: i64,
        current_epoch: i64,
    },
    #[error("thread '{0}' not found")]
    ThreadNotFound(String),
    #[error("entry {0} not found")]
    EntryNotFound(i64),
    #[error("protocol violation: {0}")]
    ProtocolViolation(String),
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("{0}")]
    Other(#[from] anyhow::Error),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryMode {
    Steer,
    AfterTurn,
    Inbox,
}

impl DeliveryMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Steer => "steer",
            Self::AfterTurn => "after_turn",
            Self::Inbox => "inbox",
        }
    }

    pub fn parse(s: &str) -> anyhow::Result<Self> {
        match s.trim() {
            "steer" => Ok(Self::Steer),
            "after_turn" => Ok(Self::AfterTurn),
            "inbox" => Ok(Self::Inbox),
            other => anyhow::bail!("invalid delivery mode '{other}'; expected steer, after_turn, or inbox"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum EntryKind {
    #[serde(rename = "thread.created")]
    ThreadCreated,
    #[serde(rename = "thread.forked")]
    ThreadForked,
    #[serde(rename = "delivery.queued")]
    DeliveryQueued,
    #[serde(rename = "thread.stop")]
    ThreadStop,
    #[serde(rename = "workspace.applied")]
    WorkspaceApplied,
    #[serde(rename = "turn.claimed")]
    TurnClaimed,
    #[serde(rename = "delivery.accepted")]
    DeliveryAccepted,
    #[serde(rename = "model.completed")]
    ModelCompleted,
    #[serde(rename = "model.interrupted")]
    ModelInterrupted,
    #[serde(rename = "tool.call")]
    ToolCall,
    #[serde(rename = "tool.result")]
    ToolResult,
    #[serde(rename = "turn.finished")]
    TurnFinished,
}

impl EntryKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ThreadCreated => "thread.created",
            Self::ThreadForked => "thread.forked",
            Self::DeliveryQueued => "delivery.queued",
            Self::ThreadStop => "thread.stop",
            Self::WorkspaceApplied => "workspace.applied",
            Self::TurnClaimed => "turn.claimed",
            Self::DeliveryAccepted => "delivery.accepted",
            Self::ModelCompleted => "model.completed",
            Self::ModelInterrupted => "model.interrupted",
            Self::ToolCall => "tool.call",
            Self::ToolResult => "tool.result",
            Self::TurnFinished => "turn.finished",
        }
    }

    pub fn parse(s: &str) -> anyhow::Result<Self> {
        match s {
            "thread.created" => Ok(Self::ThreadCreated),
            "thread.forked" => Ok(Self::ThreadForked),
            "delivery.queued" => Ok(Self::DeliveryQueued),
            "thread.stop" => Ok(Self::ThreadStop),
            "workspace.applied" => Ok(Self::WorkspaceApplied),
            "turn.claimed" => Ok(Self::TurnClaimed),
            "delivery.accepted" => Ok(Self::DeliveryAccepted),
            "model.completed" => Ok(Self::ModelCompleted),
            "model.interrupted" => Ok(Self::ModelInterrupted),
            "tool.call" => Ok(Self::ToolCall),
            "tool.result" => Ok(Self::ToolResult),
            "turn.finished" => Ok(Self::TurnFinished),
            other => anyhow::bail!("unknown entry kind '{other}'"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Entry {
    pub id: i64,
    pub thread_id: String,
    pub parent_id: Option<i64>,
    pub epoch: i64,
    pub kind: EntryKind,
    pub request_id: Option<String>,
    pub actor: String,
    pub tree_sha: Option<String>,
    pub body: Value,
    pub created_ms: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ThreadStatus {
    Idle,
    Running,
    Halted,
    Applied,
}

impl ThreadStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Running => "running",
            Self::Halted => "halted",
            Self::Applied => "applied",
        }
    }

    pub fn parse(s: &str) -> anyhow::Result<Self> {
        match s {
            "idle" => Ok(Self::Idle),
            "running" => Ok(Self::Running),
            "halted" => Ok(Self::Halted),
            "applied" => Ok(Self::Applied),
            other => anyhow::bail!("unknown thread status '{other}'"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ThreadRow {
    pub id: String,
    pub epoch: i64,
    pub head_entry_id: Option<i64>,
    pub model: String,
    pub source_dir: Option<String>,
    pub base_commit: Option<String>,
    pub base_tree_sha: Option<String>,
    pub last_tree_sha: Option<String>,
    pub status: ThreadStatus,
    /// 0 = none, 1 = stop requested, 2 = steer pending.
    pub stop_requested: i64,
    pub updated_ms: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryState {
    Queued,
    Accepted,
}

impl DeliveryState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Accepted => "accepted",
        }
    }

    pub fn parse(s: &str) -> anyhow::Result<Self> {
        match s {
            "queued" => Ok(Self::Queued),
            "accepted" => Ok(Self::Accepted),
            other => anyhow::bail!("unknown delivery state '{other}'"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeliveryRow {
    pub entry_id: i64,
    pub thread_id: String,
    pub request_id: String,
    pub mode: DeliveryMode,
    pub text: String,
    pub model_override: Option<String>,
    pub state: DeliveryState,
    pub accepted_by: Option<i64>,
    pub created_ms: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    pub parameters: Value,
    pub mutating: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolCallItem {
    pub id: String,
    pub name: String,
    /// Guaranteed valid JSON object string (e.g. `"{}"` or `"{\"path\":\"a.txt\"}"`).
    pub arguments: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TokenUsage {
    pub input: u64,
    pub cached: u64,
    pub output: u64,
    pub total: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InterruptReason {
    Steer,
    Stop,
}

impl InterruptReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Steer => "steer",
            Self::Stop => "stop",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ModelStreamOutcome {
    Completed {
        text: String,
        tool_calls: Vec<ToolCallItem>,
        usage: Option<TokenUsage>,
        ttft_ms: Option<u64>,
        duration_ms: u64,
    },
    Interrupted {
        text: String,
        reason: InterruptReason,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolReceiptStatus {
    Ok,
    Error,
    Unknown,
    Aborted,
}

impl ToolReceiptStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Error => "error",
            Self::Unknown => "unknown",
            Self::Aborted => "aborted",
        }
    }

    pub fn parse(s: &str) -> anyhow::Result<Self> {
        match s {
            "ok" => Ok(Self::Ok),
            "error" => Ok(Self::Error),
            "unknown" => Ok(Self::Unknown),
            "aborted" => Ok(Self::Aborted),
            other => anyhow::bail!("unknown tool receipt status '{other}'"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WireCodec {
    ChatCompletions,
    Responses,
}

impl WireCodec {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ChatCompletions => "chat_completions",
            Self::Responses => "responses",
        }
    }

    pub fn parse(s: &str) -> anyhow::Result<Self> {
        match s.trim() {
            "chat_completions" => Ok(Self::ChatCompletions),
            "responses" => Ok(Self::Responses),
            other => anyhow::bail!("unknown wire codec '{other}'; expected chat_completions or responses"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderConfig {
    pub name: String,
    pub url: String,
    pub wire: WireCodec,
    pub needs_key: bool,
    pub key_name: String,
    pub session_header: Option<String>,
    pub models: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RateLimitRecord {
    pub provider: String,
    pub limit_name: String,
    pub limit_val: u64,
    pub remaining: u64,
    pub reset_secs: Option<u64>,
    pub observed_ms: i64,
}

pub fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64
}
