//! Arity's core. The CLI and the Mac app are both thin front ends over the
//! `Arity` object below: the CLI calls it as plain Rust, the app calls the same
//! methods from Swift through bindings UniFFI generates.
//!
//! Everything lives in one data folder (`~/Library/Application Support/Arity`,
//! or `$ARITY_HOME`), so both front ends see the same threads.
//!
//! A thread is a conversation that may have a task attached. A plain chat is a
//! thread with no task; a run is the thread of a task an agent is doing; a task
//! nobody has started is a thread with nothing said in it yet.

mod agent;
mod board;
mod catalog;
mod codex_auth;
mod config;
mod dispatch;
mod draft;
mod journal;
mod keys;
mod model;
mod tools;
mod turn;

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

/// What a running turn reports to a Rust caller: the reply's text as it
/// arrives, and each message once it is written.
pub enum Event<'a> {
    Text(&'a str),
    Message(&'a Message),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum MessageKind {
    User,
    Assistant,
    ToolCall,
    ToolResult,
    /// A finished task's result, noted in the conversation it was filed in.
    Report,
    /// Arity speaking, for example a reminder to rate.
    Notice,
    /// The person opened, edited, added or dismissed the task card.
    TaskCard,
}

/// One row of the one list both front ends show.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Thread {
    /// The conversation's name. A task nobody has started is `task-<id>`,
    /// which is also what its first run's conversation will be called.
    pub name: String,
    /// The task's title, or how the chat began.
    pub title: String,
    pub group: ThreadGroup,
    /// When something last happened in it, UTC, "YYYY-MM-DD HH:MM:SS".
    pub updated: String,
    pub task: Option<BoardTask>,
    /// The task card being drafted in it, if one is open.
    pub card: Option<TaskFields>,
}

/// Where a thread sits in the list, most pressing first.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, uniffi::Enum)]
pub enum ThreadGroup {
    /// A result to rate, or a task card left open.
    NeedsYou,
    /// An agent is on it.
    Running,
    /// A task not started or not finished: the user's own, or waiting for an agent.
    Open,
    /// A chat with no task.
    Chat,
    Done,
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct BoardTask {
    pub id: i64,
    pub fields: TaskFields,
    /// Whose it is: `people/<login>`, or `conversations/<name>` when an agent delegated it from its run.
    pub created_by: String,
    /// The conversation it was filed in, if any.
    pub origin: Option<String>,
    pub state: TaskState,
    /// The latest run, for tasks given to an agent, and how it is going.
    pub run: Option<i64>,
    pub run_state: Option<RunState>,
    pub result: Option<String>,
    pub error: Option<String>,
    pub score: Option<i64>,
    pub note: Option<String>,
    /// Which model did the run. Hidden (None) until it is rated.
    pub model: Option<String>,
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

/// A place models are reached through, from config.json. Its key is never shown.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Provider {
    pub name: String,
    pub url: String,
    pub needs_key: bool,
    pub has_key: bool,
    /// One Arity comes with. It cannot be removed, only left without a key.
    pub builtin: bool,
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

    /// What the usable providers offer, as `<provider>:<id>`. Lists are cached
    /// for a day; `refresh` fetches them again. May block for a few seconds.
    pub fn models(&self, refresh: bool) -> Result<Vec<String>> {
        Ok(catalog::models(&self.dir, refresh)?)
    }

    /// The models tasks for an agent are picked from, from config.json.
    pub fn worker_models(&self) -> Result<Vec<String>> {
        Ok(config::load(&self.dir)?.workers)
    }

    /// Add a model to the workers in config.json, or take it out.
    pub fn set_worker(&self, model: String, on: bool) -> Result<()> {
        Ok(config::set_worker(&self.dir, &model, on)?)
    }

    pub fn providers(&self) -> Result<Vec<Provider>> {
        Ok(config::load(&self.dir)?
            .providers
            .into_iter()
            .map(|p| Provider {
                has_key: keys::get(&self.dir, &p.key_name).is_some(),
                name: p.name,
                url: p.url,
                needs_key: p.needs_key,
                builtin: p.builtin,
            })
            .collect())
    }

    /// Add an OpenAI-compatible endpoint to config.json, or change one.
    pub fn add_provider(&self, name: String, url: String, needs_key: bool) -> Result<()> {
        Ok(config::add_provider(&self.dir, &name, &url, needs_key)?)
    }

    /// Remove a provider the user added, and its key.
    pub fn remove_provider(&self, name: String) -> Result<()> {
        config::remove_provider(&self.dir, &name)?;
        Ok(keys::set(&self.dir, &name, "")?)
    }

    /// Store a provider's API key. An empty key removes it.
    pub fn set_key(&self, provider: String, key: String) -> Result<()> {
        let providers = config::load(&self.dir)?.providers;
        let Some(p) = providers.iter().find(|p| p.name == provider) else {
            return Err(ArityError::Failed(format!("no provider {provider} in config.json")));
        };
        Ok(keys::set(&self.dir, &p.key_name, &key)?)
    }

    // Threads

    /// Every thread, grouped by what needs attention: tasks in board order, chats newest first.
    pub fn threads(&self) -> Result<Vec<Thread>> {
        let db = self.journal()?;
        let mut conversations = db.conversations()?;
        let mut out = Vec::new();
        for t in board::tasks(db.conn())? {
            let name = t.conversation.clone().unwrap_or_else(|| format!("task-{}", t.id));
            let updated = conversations.iter().find(|c| c.0 == name).map_or(&t.created, |c| &c.1).clone();
            let group = match (t.state, t.run_state) {
                (TaskState::Done, _) => ThreadGroup::Done,
                (_, Some(RunState::NeedsRating)) => ThreadGroup::NeedsYou,
                (_, Some(RunState::Running | RunState::Failing)) => ThreadGroup::Running,
                _ => ThreadGroup::Open,
            };
            out.push(Thread { name, title: t.fields.title.clone(), group, updated, task: Some(t.into()), card: None });
        }
        // Whatever is left is a chat. Earlier runs of a task that was run again are not listed.
        let runs = board::run_conversations(db.conn())?;
        conversations.retain(|c| !runs.contains(&c.0) && !out.iter().any(|t| t.name == c.0));
        for (name, updated) in conversations {
            let chain = db.chain(&name)?;
            let card = draft::current(&chain);
            let said = chain.iter().find(|e| e.kind == "user").and_then(|e| e.body["text"].as_str());
            let title = said.and_then(|s| s.lines().next()).map_or(name.clone(), |l| l.chars().take(60).collect());
            let group = if card.is_some() { ThreadGroup::NeedsYou } else { ThreadGroup::Chat };
            out.push(Thread { name, title, group, updated, task: None, card });
        }
        out.sort_by_key(|t| t.group);
        Ok(out)
    }

    pub fn messages(&self, conversation: String) -> Result<Vec<Message>> {
        let db = self.journal()?;
        let hidden = hidden(&db, &conversation)?;
        let chain = db.chain(&conversation)?;
        let head = chain.last().map_or(0, |e| e.id);
        let mut out: Vec<Message> = chain.iter().flat_map(messages_of).collect();
        // A turn in progress, in this process or another: the reply so far, then what waits for it.
        if let Some((text, model)) = turn::partial(db.conn(), &conversation)? {
            out.push(Message { id: "live".into(), entry: head, kind: MessageKind::Assistant, text, model });
        }
        for (id, text) in turn::queued(db.conn(), &conversation)? {
            out.push(Message { id: format!("queued.{id}"), entry: head, kind: MessageKind::User, text, model: None });
        }
        if hidden {
            out.iter_mut().for_each(|m| m.model = None);
        }
        Ok(out)
    }

    /// The model the conversation last used, so a front end can default to it.
    /// None for an unrated run, which would give the model away.
    pub fn last_model(&self, conversation: String) -> Result<Option<String>> {
        let db = self.journal()?;
        if hidden(&db, &conversation)? {
            return Ok(None);
        }
        Ok(db.chain(&conversation)?.into_iter().rev().find_map(|e| e.model))
    }

    /// Changes whenever anything is written, by this process or another.
    pub fn revision(&self) -> Result<String> {
        Ok(self.journal()?.revision()?)
    }

    /// Add the user's message and run the model until it stops calling tools.
    /// Blocks; call it off the main thread. If a turn is already running in the
    /// conversation, in any process, the message waits for it and this returns at once.
    pub fn send(&self, conversation: String, model: String, text: String) -> Result<()> {
        self.send_with(&conversation, &model, &text, &mut |_| {})?;
        Ok(())
    }

    /// Stop the turn running in a conversation, in whichever process runs it.
    /// The reply so far is kept. False if nothing was running.
    pub fn stop(&self, conversation: String) -> Result<bool> {
        Ok(turn::stop(self.journal()?.conn(), &conversation)?)
    }

    /// Conversations a turn is running in, in any process.
    pub fn running(&self) -> Result<Vec<String>> {
        Ok(turn::running(self.journal()?.conn())?)
    }

    /// Carry on a conversation that stopped part way, for example after a failed model call.
    pub fn resume(&self, conversation: String, model: String) -> Result<()> {
        self.resume_with(&conversation, &model, &mut |_| {})?;
        Ok(())
    }

    /// A new conversation sharing history up to and including `entry`.
    pub fn fork(&self, entry: i64, new_conversation: String) -> Result<()> {
        Ok(self.journal()?.fork(entry, &new_conversation)?)
    }

    // Tasks

    /// The user's own page, `people/<login>`: the assignee for "Me".
    pub fn me(&self) -> String {
        board::me()
    }

    /// A task written by the user. With assignee `agent`, a blindly picked model does it.
    pub fn add_task(&self, fields: TaskFields) -> Result<i64> {
        Ok(board::add_task(self.journal()?.conn(), &fields, None)?)
    }

    /// Replace a task's fields. Changing the assignee to `agent` hands it off.
    pub fn update_task(&self, id: i64, fields: TaskFields) -> Result<()> {
        Ok(board::update_task(self.journal()?.conn(), id, &fields)?)
    }

    pub fn set_task_done(&self, id: i64, done: bool) -> Result<()> {
        Ok(board::set_done(self.journal()?.conn(), id, done)?)
    }

    // Task cards: drafts bound to a conversation

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
            Did::Reported { task, rater: Some(conv) } => Some(format!("task {task}: reported to {conv} to rate")),
            Did::Reported { task, rater: None } => Some(format!("task {task}: waiting for your rating")),
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
        Ok(board::rate(self.journal()?.conn(), run, score, &note, &board::me(), None)?)
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

    /// Opens the browser and waits for the ChatGPT sign-in. Returns the account's email.
    pub fn sign_in_chatgpt(&self) -> Result<String> {
        Ok(codex_auth::login(&self.dir.join("codex-auth.json"))?)
    }

    pub fn chatgpt_signed_in(&self) -> bool {
        codex_auth::signed_in(&self.dir.join("codex-auth.json"))
    }

    /// The signed-in ChatGPT account's email, if any.
    pub fn chatgpt_account(&self) -> Option<String> {
        codex_auth::account(&self.dir.join("codex-auth.json"))
    }

    /// Stop a ChatGPT sign-in that is waiting for the browser.
    pub fn cancel_chatgpt_sign_in(&self) {
        codex_auth::cancel()
    }

    pub fn sign_out_chatgpt(&self) -> Result<()> {
        Ok(codex_auth::logout(&self.dir.join("codex-auth.json"))?)
    }
}

/// Rust-only variants that report text and messages as they come. The CLI
/// prints with these; the app instead watches `revision`. Both return false
/// when another loop is running the conversation; a sent message then waits for it.
impl Arity {
    pub fn send_with(&self, conv: &str, model: &str, text: &str, on: &mut dyn FnMut(Event)) -> Result<bool> {
        turn::send(&self.journal()?, conv, text)?;
        self.resume_with(conv, model, on)
    }

    pub fn resume_with(&self, conv: &str, model: &str, on: &mut dyn FnMut(Event)) -> Result<bool> {
        let db = self.journal()?;
        let hidden = hidden(&db, conv)?;
        let mut masked = |e: Event| match e {
            Event::Message(m) if hidden => on(Event::Message(&Message { model: None, ..m.clone() })),
            e => on(e),
        };
        Ok(agent::run(&db, &self.dir, conv, model, &mut masked)?)
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
            origin: t.origin,
            state: t.state,
            run: t.trial,
            run_state: t.run_state,
            result: t.result,
            error: t.error,
            model: t.score.and(t.model),
            score: t.score,
            note: t.note,
        }
    }
}

/// An unrated run's thread, where the model stays hidden.
fn hidden(db: &Journal, conv: &str) -> anyhow::Result<bool> {
    Ok(match board::trial_of_conversation(db.conn(), conv)? {
        Some(trial) => !board::is_rated(db.conn(), trial)?,
        None => false,
    })
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
            if e.body["interrupted"] == true {
                out.push(Message { model: None, ..msg(1, MessageKind::Notice, "Stopped.".into()) });
            }
            out
        }
        "tool" => vec![msg(0, MessageKind::ToolResult, text("output"))],
        _ => vec![],
    }
}
