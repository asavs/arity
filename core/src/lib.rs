//! Arity's core. The CLI and the Mac app are both thin front ends over the
//! `Arity` object below: the CLI calls it as plain Rust, the app calls the same
//! methods from Swift through bindings UniFFI generates.
//!
//! Everything lives in one data folder (`~/Library/Application Support/Arity`,
//! or `$ARITY_HOME`), so both front ends see the same conversations and board.

mod agent;
mod codex_auth;
mod journal;
mod model;
mod tools;

use std::path::PathBuf;
use std::sync::Arc;

use journal::{Entry, Journal};
use model::tool_calls_of;

uniffi::setup_scaffolding!();

/// One line in a conversation as a person reads it. An assistant entry that
/// calls two tools becomes three messages: its text and one per call.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Message {
    /// Unique within a conversation: "<entry>.<part>".
    pub id: String,
    /// The journal entry this came from. Fork from here to branch.
    pub entry: i64,
    pub kind: MessageKind,
    pub text: String,
    /// Which model wrote it, for assistant text and tool calls.
    pub model: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum MessageKind {
    User,
    Assistant,
    ToolCall,
    ToolResult,
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct Conversation {
    pub name: String,
    pub head: i64,
    /// When the last entry was written, UTC, "YYYY-MM-DD HH:MM:SS".
    pub updated: String,
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct BoardTask {
    pub id: i64,
    pub title: String,
    pub done: bool,
}

#[derive(Debug, thiserror::Error, uniffi::Error)]
#[uniffi(flat_error)]
pub enum ArityError {
    #[error("{0}")]
    Failed(String),
}

impl From<anyhow::Error> for ArityError {
    fn from(e: anyhow::Error) -> Self {
        ArityError::Failed(format!("{e:#}"))
    }
}

type Result<T> = std::result::Result<T, ArityError>;

/// Models offered by default. Any `mlx:<id>` or `codex:<id>` also works.
const SUGGESTED_MODELS: &[&str] = &[
    "mlx:mlx-community/Qwen3.5-4B-MLX-4bit",
    "mlx:mlx-community/gemma-4-e4b-it-4bit",
    "codex:gpt-6.1-terra",
    "codex:gpt-6.2-sol",
];

#[derive(uniffi::Object)]
pub struct Arity {
    dir: PathBuf,
}

#[uniffi::export]
impl Arity {
    /// Open the data folder, or the default one when `data_dir` is None.
    #[uniffi::constructor]
    pub fn new(data_dir: Option<String>) -> Arc<Self> {
        let dir = data_dir.map(PathBuf::from).unwrap_or_else(default_dir);
        Arc::new(Self { dir })
    }

    pub fn data_dir(&self) -> String {
        self.dir.display().to_string()
    }

    pub fn suggested_models(&self) -> Vec<String> {
        SUGGESTED_MODELS.iter().map(|s| s.to_string()).collect()
    }

    pub fn conversations(&self) -> Result<Vec<Conversation>> {
        Ok(self
            .journal()?
            .conversations()?
            .into_iter()
            .map(|(name, head, updated)| Conversation { name, head, updated })
            .collect())
    }

    pub fn messages(&self, conversation: String) -> Result<Vec<Message>> {
        Ok(self.journal()?.chain(&conversation)?.iter().flat_map(messages_of).collect())
    }

    /// The model the conversation last used, so a front end can default to it.
    pub fn last_model(&self, conversation: String) -> Result<Option<String>> {
        Ok(self.journal()?.chain(&conversation)?.into_iter().rev().find_map(|e| e.model))
    }

    pub fn tasks(&self) -> Result<Vec<BoardTask>> {
        Ok(self
            .journal()?
            .tasks()?
            .into_iter()
            .map(|(id, title, done)| BoardTask { id, title, done })
            .collect())
    }

    /// Changes whenever anything is written, by this process or another.
    pub fn revision(&self) -> Result<String> {
        Ok(self.journal()?.revision()?)
    }

    /// Add the user's message and run the model until it stops calling tools.
    /// Blocks; call it off the main thread.
    pub fn send(&self, conversation: String, model: String, text: String) -> Result<()> {
        self.send_with(&conversation, &model, &text, &mut |_| {})
    }

    /// Carry on a conversation that stopped part way, for example after a failed model call.
    pub fn resume(&self, conversation: String, model: String) -> Result<()> {
        self.resume_with(&conversation, &model, &mut |_| {})
    }

    /// A new conversation sharing history up to and including `entry`.
    pub fn fork(&self, entry: i64, new_conversation: String) -> Result<()> {
        Ok(self.journal()?.fork(entry, &new_conversation)?)
    }

    /// Opens the browser and waits for the ChatGPT sign-in. Returns the account id.
    pub fn sign_in_chatgpt(&self) -> Result<String> {
        Ok(codex_auth::login(&self.dir.join("codex-auth.json"))?)
    }

    pub fn chatgpt_signed_in(&self) -> bool {
        self.dir.join("codex-auth.json").exists()
    }
}

/// Rust-only variants that report each new message as it is written. The CLI
/// prints with these; the app instead watches `revision`.
impl Arity {
    pub fn send_with(&self, conv: &str, model: &str, text: &str, on: &mut dyn FnMut(&Message)) -> Result<()> {
        let db = self.journal()?;
        db.append(conv, "user", &serde_json::json!({ "text": text }), None)?;
        agent::run(&db, &self.dir, conv, model, on)?;
        Ok(())
    }

    pub fn resume_with(&self, conv: &str, model: &str, on: &mut dyn FnMut(&Message)) -> Result<()> {
        Ok(agent::run(&self.journal()?, &self.dir, conv, model, on)?)
    }

    fn journal(&self) -> anyhow::Result<Journal> {
        Journal::open(&self.dir.join("arity.sqlite"))
    }
}

fn default_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("ARITY_HOME") {
        return dir.into();
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join("Library/Application Support/Arity")
}

pub(crate) fn messages_of(e: &Entry) -> Vec<Message> {
    let msg = |part: usize, kind, text: String| Message {
        id: format!("{}.{part}", e.id),
        entry: e.id,
        kind,
        text,
        model: e.model.clone(),
    };
    let text = |key: &str| e.body[key].as_str().unwrap_or_default().to_string();
    match e.kind.as_str() {
        "user" => vec![msg(0, MessageKind::User, text("text"))],
        "assistant" => {
            let mut out = Vec::new();
            if !text("text").trim().is_empty() {
                out.push(msg(0, MessageKind::Assistant, text("text")));
            }
            for (i, c) in tool_calls_of(&e.body).into_iter().enumerate() {
                out.push(msg(i + 1, MessageKind::ToolCall, format!("{}({})", c.name, c.arguments)));
            }
            out
        }
        _ => vec![msg(0, MessageKind::ToolResult, text("output"))],
    }
}
