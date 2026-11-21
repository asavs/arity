//! Arity's core. The CLI and the Mac app are both thin front ends over the
//! `Arity` object below: the CLI calls it as plain Rust, the app calls the same
//! methods from Swift through bindings UniFFI generates.
//!
//! Everything lives in one data folder (`~/Library/Application Support/Arity`,
//! or `$ARITY_HOME`), so both front ends see the same conversations and board.

mod agent;
mod board;
mod codex_auth;
mod config;
mod dispatch;
mod draft;
mod journal;
mod model;
mod tools;

use std::path::PathBuf;
use std::sync::Arc;

pub use board::{Clearance, Priority, RunState, TaskFields, TaskState};
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
    /// Which model wrote it. None while the conversation is an unrated run.
    pub model: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum MessageKind {
    User,
    Assistant,
    ToolCall,
    ToolResult,
    /// A finished task's result, delivered to the conversation that asked for it.
    Report,
    /// Arity speaking, for example a reminder to rate.
    Notice,
    /// The person opened, edited, added or dismissed the task card.
    TaskCard,
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
    pub fields: TaskFields,
    /// The page that wrote it: `people/<login>` or `conversations/<name>`.
    pub created_by: String,
    pub state: TaskState,
    /// The latest run, for tasks given to an agent, and how it is going.
    pub run: Option<i64>,
    pub run_state: Option<RunState>,
    pub conversation: Option<String>,
    pub result: Option<String>,
    pub error: Option<String>,
    pub score: Option<i64>,
}

/// One attempt at a task by a blindly picked model.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Run {
    pub id: i64,
    pub task: i64,
    pub title: String,
    pub conversation: String,
    /// Hidden (None) until the run is rated. `reveal` shows it regardless.
    pub model: Option<String>,
    pub started: String,
    pub ended: Option<String>,
    pub score: Option<i64>,
    pub note: Option<String>,
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct Score {
    pub model: String,
    pub rated: i64,
    pub average: f64,
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

/// Models offered for talking to directly. Any `mlx:<id>` or `codex:<id>` also works.
const SUGGESTED_MODELS: &[&str] = &[
    "codex:gpt-6.1-terra",
    "codex:gpt-6.2-sol",
    "mlx:mlx-community/Qwen3.5-4B-MLX-4bit",
    "mlx:mlx-community/gemma-4-e4b-it-4bit",
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

    /// The models tasks for an agent are picked from, from config.json.
    pub fn worker_models(&self) -> Result<Vec<String>> {
        Ok(config::load(&self.dir)?.workers)
    }

    // Conversations

    pub fn conversations(&self) -> Result<Vec<Conversation>> {
        Ok(self
            .journal()?
            .conversations()?
            .into_iter()
            .map(|(name, head, updated)| Conversation { name, head, updated })
            .collect())
    }

    pub fn messages(&self, conversation: String) -> Result<Vec<Message>> {
        let db = self.journal()?;
        let hidden = match board::trial_of_conversation(db.conn(), &conversation)? {
            Some(trial) => !board::is_rated(db.conn(), trial)?,
            None => false,
        };
        let mut out: Vec<Message> = db.chain(&conversation)?.iter().flat_map(messages_of).collect();
        if hidden {
            out.iter_mut().for_each(|m| m.model = None);
        }
        Ok(out)
    }

    /// The model the conversation last used, so a front end can default to it.
    /// None for an unrated run, which would give the model away.
    pub fn last_model(&self, conversation: String) -> Result<Option<String>> {
        let db = self.journal()?;
        if let Some(trial) = board::trial_of_conversation(db.conn(), &conversation)? {
            if !board::is_rated(db.conn(), trial)? {
                return Ok(None);
            }
        }
        Ok(db.chain(&conversation)?.into_iter().rev().find_map(|e| e.model))
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

    // The board

    pub fn tasks(&self) -> Result<Vec<BoardTask>> {
        Ok(board::tasks(self.journal()?.conn())?.into_iter().map(BoardTask::from).collect())
    }

    /// The user's own page, `people/<login>`: the assignee for "Me".
    pub fn me(&self) -> String {
        board::me()
    }

    /// A task written by the user. With assignee `agent`, a blindly picked model does it.
    pub fn add_task(&self, fields: TaskFields) -> Result<i64> {
        Ok(board::add_task(self.journal()?.conn(), &fields, &board::me())?)
    }

    /// Replace a task's fields. Changing the assignee to `agent` hands it off.
    pub fn update_task(&self, id: i64, fields: TaskFields) -> Result<()> {
        Ok(board::update_task(self.journal()?.conn(), id, &fields)?)
    }

    pub fn set_task_done(&self, id: i64, done: bool) -> Result<()> {
        Ok(board::set_done(self.journal()?.conn(), id, done)?)
    }

    // Task cards: drafts bound to a conversation

    /// The task card open in a conversation, if any.
    pub fn draft(&self, conversation: String) -> Result<Option<TaskFields>> {
        Ok(draft::current(&self.journal()?.chain(&conversation)?))
    }

    /// Open an empty card. Creates the conversation if it is new.
    pub fn open_draft(&self, conversation: String) -> Result<()> {
        Ok(draft::open(self.journal()?.conn(), &conversation)?)
    }

    /// The person's edits to the card. The model sees them on its next turn.
    pub fn edit_draft(&self, conversation: String, fields: TaskFields) -> Result<()> {
        let db = self.journal()?;
        Ok(db.immediate(|c| draft::edit(c, &conversation, &fields))?)
    }

    /// Put the card on the board. Returns the new task's id.
    pub fn add_draft(&self, conversation: String) -> Result<i64> {
        let db = self.journal()?;
        Ok(db.immediate(|c| draft::add(c, &conversation))?)
    }

    pub fn dismiss_draft(&self, conversation: String) -> Result<()> {
        let db = self.journal()?;
        Ok(db.immediate(|c| draft::dismiss(c, &conversation))?)
    }

    // Runs, ratings, scores

    /// Do one piece of dispatcher work: deliver a report, or run a waiting task.
    /// Returns a line describing it, or None when there was nothing to do. Blocks.
    pub fn work_once(&self) -> Result<Option<String>> {
        use dispatch::Did;
        Ok(match dispatch::work_once(&self.journal()?, &self.dir)? {
            Did::Nothing => None,
            Did::Ran { task, trial } => Some(format!("task {task}: run {trial} finished")),
            Did::Failed { task, trial, error } => Some(format!("task {task}: run {trial} failed, will retry: {error}")),
            Did::Reported { task, to: Some(conv) } => Some(format!("task {task}: reported to {conv}")),
            Did::Reported { task, to: None } => Some(format!("task {task}: waiting for your rating on the board")),
        })
    }

    pub fn runs(&self) -> Result<Vec<Run>> {
        Ok(board::runs(self.journal()?.conn())?
            .into_iter()
            .map(|r| Run {
                id: r.trial,
                task: r.task,
                title: r.title,
                conversation: r.conversation,
                model: r.score.map(|_| r.model),
                started: r.started,
                ended: r.ended,
                score: r.score,
                note: r.note,
            })
            .collect())
    }

    /// The user rates a run. Replaces any earlier rating of it.
    pub fn rate(&self, run: i64, score: i64, note: String) -> Result<()> {
        Ok(board::rate(self.journal()?.conn(), run, score, &note, "user")?)
    }

    pub fn scores(&self) -> Result<Vec<Score>> {
        Ok(board::scores(self.journal()?.conn())?
            .into_iter()
            .map(|(model, rated, average)| Score { model, rated, average })
            .collect())
    }

    /// Which model did a run, rated or not.
    pub fn reveal(&self, run: i64) -> Result<String> {
        Ok(board::reveal(self.journal()?.conn(), run)?)
    }

    // ChatGPT

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

impl From<board::TaskRow> for BoardTask {
    fn from(t: board::TaskRow) -> Self {
        BoardTask {
            id: t.id,
            fields: t.fields,
            created_by: t.created_by,
            state: t.state,
            run: t.trial,
            run_state: t.run_state,
            conversation: t.conversation,
            result: t.result,
            error: t.error,
            score: t.score,
        }
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
        "report" => vec![msg(0, MessageKind::Report, text("text"))],
        "notice" => vec![msg(0, MessageKind::Notice, text("text"))],
        "draft" => draft::summary(&e.body).map(|t| msg(0, MessageKind::TaskCard, t)).into_iter().collect(),
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
        "tool" => vec![msg(0, MessageKind::ToolResult, text("output"))],
        _ => vec![],
    }
}
