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
mod endpoint;
mod journal;
mod keys;
mod leaderboard;
mod model;
mod tools;
mod usage;
mod turn;

use std::path::PathBuf;
use std::sync::Arc;

pub use board::{Priority, RunState, TaskFields, TaskState};
pub use turn::Delivery;
pub use catalog::KeyCheck;
pub use leaderboard::{Choice, Figures, Pick, Standing};
pub use usage::{Limit, Usage};
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

/// Somewhere you have models: OpenAI, OpenCode, Nous, this Mac. It holds at
/// most one key, shared by its plans. The key itself is never shown.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Account {
    pub name: String,
    pub title: String,
    /// The provider to give the key to, if any plan needs one.
    pub key_for: Option<String>,
    pub has_key: bool,
    /// Where to get a key.
    pub key_page: Option<String>,
    /// Added by the user, so it can be removed.
    pub removable: bool,
    pub plans: Vec<Plan>,
}

/// One way of reaching an account's models, with its own models and limits.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Plan {
    /// The model prefix: a provider in config.json, or `codex` for the ChatGPT plan.
    pub provider: String,
    pub title: String,
    /// What it costs, in a few words.
    pub note: String,
    pub access: Access,
    /// Where to get it, for a plan that needs a subscription.
    pub page: Option<String>,
    pub usage: Usage,
}

#[derive(Debug, Clone, PartialEq, uniffi::Enum)]
pub enum Access {
    /// Nothing to set up.
    Open,
    /// The account's key.
    Key,
    /// The ChatGPT sign-in; `account` is who is signed in.
    SignIn { account: Option<String> },
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

    /// The model a choice comes to among those the user can reach now, and why.
    /// Fails, saying what is missing, when the choice has no candidates. Blocks
    /// briefly: it checks that local servers are up.
    pub fn pick(&self, choice: Choice) -> Result<Pick> {
        let candidates = candidates(&self.dir)?;
        let board = self.standings(&candidates)?;
        Ok(leaderboard::choose(choice, &candidates, &board)?)
    }

    /// Every model the user can reach or that anything is known about: public
    /// figures, Arity's own, and what the pick makes of them together.
    pub fn leaderboard(&self) -> Result<Vec<Standing>> {
        let candidates = candidates(&self.dir)?;
        self.standings(&candidates)
    }

    /// The models tasks for an agent are picked from, from config.json.
    pub fn worker_models(&self) -> Result<Vec<String>> {
        Ok(config::load(&self.dir)?.workers)
    }

    /// Add a model to the workers in config.json, or take it out.
    pub fn set_worker(&self, model: String, on: bool) -> Result<()> {
        Ok(config::set_worker(&self.dir, &model, on)?)
    }

    /// Every account and its plans: the built-in ones first, then those the user added.
    pub fn accounts(&self) -> Result<Vec<Account>> {
        let mut out: Vec<Account> = vec![];
        for p in config::load(&self.dir)?.providers {
            let i = match out.iter().position(|a| a.name == p.account) {
                Some(i) => i,
                None => {
                    out.push(Account {
                        name: p.account.clone(),
                        title: p.account_title.clone(),
                        key_for: None,
                        has_key: false,
                        key_page: None,
                        removable: !p.builtin,
                        plans: vec![],
                    });
                    out.len() - 1
                }
            };
            let a = &mut out[i];
            if p.needs_key && a.key_for.is_none() {
                a.has_key = keys::get(&self.dir, &p.key_name).is_some();
                a.key_for = Some(p.key_name.clone());
            }
            // The key's own provider says where to get it; a plan on a borrowed key links to subscribing.
            if p.needs_key && p.key_name == p.name {
                a.key_page = p.page.clone();
            }
            let borrowed = p.key_name != p.name;
            let usage = match (p.usage_headers, p.needs_key) {
                (true, _) => usage::read(&self.dir, &p.name),
                (false, false) => Usage::Unlimited,
                (false, true) => Usage::NotReported,
            };
            a.plans.push(Plan {
                usage,
                access: if p.needs_key { Access::Key } else { Access::Open },
                page: if borrowed { p.page } else { None },
                provider: p.name,
                title: p.title,
                note: p.note,
            });
        }
        if let Some(a) = out.iter_mut().find(|a| a.name == "openai") {
            a.plans.insert(0, Plan {
                provider: "codex".into(),
                title: "ChatGPT".into(),
                note: "Your Plus or Pro plan, through OpenAI's sign-in for apps".into(),
                access: Access::SignIn { account: codex_auth::account(&self.dir.join("codex-auth.json")) },
                page: None,
                usage: Usage::NotReported,
            });
        }
        let rank = |a: &Account| config::ACCOUNT_ORDER.iter().position(|n| *n == a.name).unwrap_or(usize::MAX);
        out.sort_by(|a, b| rank(a).cmp(&rank(b)).then(a.name.cmp(&b.name)));
        Ok(out)
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

    /// Whether a provider takes its key, checked without running a model. Blocks for a few seconds.
    pub fn check_provider(&self, name: String) -> Result<KeyCheck> {
        Ok(catalog::check(&self.dir, &name)?)
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
        // A run's own todos are its scratchpad, shown in its thread rather than listed.
        for t in board::tasks(db.conn())?.into_iter().filter(|t| !t.fields.assignee.starts_with("conversations/")) {
            let name = t.conversation.clone().unwrap_or_else(|| format!("task-{}", t.id));
            let updated = conversations.iter().find(|c| c.0 == name).map_or(&t.created, |c| &c.1).clone();
            let group = match (t.state, t.run_state) {
                (TaskState::Done, _) => ThreadGroup::Done,
                (_, Some(RunState::NeedsRating)) => ThreadGroup::NeedsYou,
                (_, Some(RunState::Running | RunState::Failing | RunState::Waiting)) => ThreadGroup::Running,
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
        // Before its first reply lands, the turn running in it knows the model.
        match db.chain(&conversation)?.into_iter().rev().find_map(|e| e.model) {
            Some(m) => Ok(Some(m)),
            None => Ok(turn::model(db.conn(), &conversation)?),
        }
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

    /// The user's message, delivered the way any participant's is: `how` says
    /// when it joins a turn running there. With no turn running, every timing
    /// but inbox starts one and blocks until it ends; inbox only leaves the
    /// message there for the conversation's next turn, waking nothing.
    pub fn send_as(&self, conversation: String, model: String, text: String, how: Delivery) -> Result<()> {
        self.send_as_with(&conversation, &model, &text, how, &mut |_| {})?;
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
        Ok(dispatch::work_once(&self.journal()?, &self.dir)?.describe())
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
    /// Returns the score it replaced, if the run was already rated.
    pub fn rate(&self, run: i64, score: i64, note: String) -> Result<Option<i64>> {
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
        self.send_as_with(conv, model, text, Delivery::Steer, on)
    }

    /// As `send_as`. Inbox runs nothing and returns true.
    pub fn send_as_with(&self, conv: &str, model: &str, text: &str, how: Delivery, on: &mut dyn FnMut(Event)) -> Result<bool> {
        let db = self.journal()?;
        turn::send_as(&db, conv, text, how)?;
        if how == Delivery::Inbox {
            return Ok(true);
        }
        board::wake(db.conn(), conv)?;
        self.resume_with(conv, model, on)
    }

    pub fn resume_with(&self, conv: &str, model: &str, on: &mut dyn FnMut(Event)) -> Result<bool> {
        let db = self.journal()?;
        let model = resolve_model(&db, conv, model)?;
        self.run_resolved(&db, conv, &model, None, on)
    }

    pub(crate) fn run_resolved(
        &self,
        db: &Journal,
        conv: &str,
        model: &str,
        scripted: Option<&agent::Complete>,
        on: &mut dyn FnMut(Event),
    ) -> Result<bool> {
        let trial = board::trial_state(db.conn(), conv)?;
        let hidden = hidden(db, conv)?;
        let mut on_event = |e: Event| {
            if let Some((tr_id, _, _, false)) = &trial {
                let _ = db.conn().execute(
                    "UPDATE trials SET lease_until = ?2 WHERE id = ?1",
                    rusqlite::params![tr_id, board::now() + board::LEASE_SECS],
                );
            }
            match e {
                Event::Message(m) if hidden => on(Event::Message(&Message { model: None, ..m.clone() })),
                e => on(e),
            }
        };
        let ran = match scripted {
            Some(complete) => agent::run_with(db, conv, model, complete, &mut on_event),
            None => agent::run(db, &self.dir, conv, model, &mut on_event),
        };
        match ran {
            Ok(agent::Outcome::Busy) => Ok(false),
            Ok(outcome) => {
                if let Some((tr_id, task_id, _, ended_before)) = trial {
                    if matches!(dispatch::settle(db, tr_id, task_id, conv, ended_before, outcome)?, dispatch::Did::Ran { .. }) {
                        let _ = dispatch::deliver_report(db)?;
                    }
                }
                Ok(true)
            }
            Err(e) => {
                if let Some((tr_id, _, _, ended_before)) = trial {
                    let detail = format!("{e:#}");
                    if ended_before {
                        let _ = db.conn().execute("UPDATE trials SET error = ?2 WHERE id = ?1", rusqlite::params![tr_id, detail]);
                    } else {
                        let _ = db.conn().execute(
                            "UPDATE trials SET error = ?2, lease_until = ?3 WHERE id = ?1",
                            rusqlite::params![tr_id, detail, board::now() + 60],
                        );
                    }
                }
                if hidden {
                    return Err(ArityError::Failed(
                        "could not reach the model for this task; rate first or use reveal to see why".into(),
                    ));
                }
                Err(e.into())
            }
        }
    }

    fn standings(&self, candidates: &[leaderboard::Candidate]) -> Result<Vec<Standing>> {
        let own = leaderboard::own(self.journal()?.conn())?;
        let names: Vec<String> = candidates.iter().map(|c| c.model.clone()).collect();
        Ok(leaderboard::standings(&leaderboard::public(), &own, &names))
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
            // The detail names the model or its provider; `reveal` tells it.
            error: t.error.map(|_| dispatch::FAILING.to_string()),
            model: t.score.and(t.model),
            score: t.score,
            note: t.note,
        }
    }
}

/// The models the user can reach now, and how: everything the usable providers
/// offer, less local servers that are not running and providers whose key was
/// refused when last checked.
pub(crate) fn candidates(dir: &std::path::Path) -> anyhow::Result<Vec<leaderboard::Candidate>> {
    use leaderboard::{Candidate, Reach};
    let providers = config::load(&dir)?.providers;
    let rejected = catalog::rejected(&dir);
    let mut up: std::collections::HashMap<String, bool> = Default::default();
    let quota_left = |provider: &str| match usage::read(&dir, provider) {
        Usage::Limits { limits, .. } => limits.iter().all(|l| l.remaining > 0),
        _ => true, // not reported: assume there is some
    };
    let mut out = vec![];
    for model in catalog::models(&dir, false)? {
        let Some((name, id)) = model.split_once(':') else { continue };
        let reach = if name == "codex" {
            Reach::Subscription { quota_left: true }
        } else {
            let Some(p) = providers.iter().find(|p| p.name == name) else { continue };
            if rejected.contains(&p.name) {
                continue;
            }
            if p.local() {
                let alive = *up.entry(p.name.clone()).or_insert_with(|| {
                    let client = reqwest::blocking::Client::builder().timeout(std::time::Duration::from_millis(800)).build();
                    client.is_ok_and(|c| c.get(format!("{}/models", p.url)).send().is_ok())
                });
                if !alive {
                    continue;
                }
                Reach::Local
            } else if p.subscription || p.free_suffix.as_deref().is_some_and(|s| id.ends_with(s)) {
                Reach::Subscription { quota_left: quota_left(&p.name) }
            } else {
                Reach::Pay
            }
        };
        out.push(Candidate { model, reach });
    }
    Ok(out)
}

/// Which model `conv` runs on: a task's run always keeps the model its trial
/// picked (resolved inside the core so an unrated run stays blind); a chat uses
/// `given`, or the model it last spoke with when `given` is empty or `-`.
fn resolve_model(db: &Journal, conv: &str, given: &str) -> anyhow::Result<String> {
    if let Some((_, _, model, _)) = board::trial_state(db.conn(), conv)? {
        return Ok(model);
    }
    if !given.is_empty() && given != "-" {
        return Ok(given.to_string());
    }
    if let Some(m) = db.chain(conv)?.into_iter().rev().find_map(|e| e.model) {
        return Ok(m);
    }
    if let Some(m) = turn::model(db.conn(), conv)? {
        return Ok(m);
    }
    anyhow::bail!("{conv} has no model yet; name one")
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
        "report" | "message" => vec![msg(0, MessageKind::Report, text("text"))],
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
                let why = if e.body["cut_for_message"] == true { "Cut off by a new message." } else { "Stopped." };
                out.push(Message { model: None, ..msg(1, MessageKind::Notice, why.into()) });
            }
            out
        }
        "tool" => vec![msg(0, MessageKind::ToolResult, text("output"))],
        _ => vec![],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Reply, ToolCall};
    use serde_json::json;

    #[test]
    fn talking_to_a_worker_directly_keeps_it_blind_until_rated() -> anyhow::Result<()> {
        let dir = std::env::temp_dir().join(format!("arity-worker-talk-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let arity = Arity::new(Some(dir.to_string_lossy().into_owned()));
        let db = arity.journal()?;

        // A chat delegates task 1; a blindly picked worker ("secret:worker-7b") finishes blocked.
        db.append("chat-1", "assistant", &json!({ "text": "Handed off." }), Some("chat:front"))?;
        let f = TaskFields { title: "start the server".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(db.conn(), &f, Some("chat-1"))?;
        db.conn().execute(
            "INSERT INTO trials (task, conversation, model, lease_until) VALUES (?1, 'task-1', 'secret:worker-7b', ?2)",
            rusqlite::params![task, board::now() + 60],
        )?;
        db.append("task-1", "user", &json!({ "text": "start the server" }), None)?;
        board::set_state(db.conn(), task, TaskState::Assigned)?;

        let scripted = |_: &str, chain: &[Entry], _: &[tools::ToolSpec], out: &mut model::Sink| -> anyhow::Result<Reply> {
            let last = chain.last().unwrap();
            let said = last.body["text"].as_str().unwrap_or_default();
            if said == "start the server" {
                Ok(Reply {
                    text: String::new(),
                    tool_calls: vec![ToolCall {
                        id: "f1".into(),
                        name: "finish".into(),
                        arguments: json!({ "report": "which port?", "outcome": "blocked" }).to_string(),
                    }],
                    interrupted: false,
                })
            } else if said.contains("why") {
                out("Ports below 1024 need root.");
                Ok(Reply { text: "Ports below 1024 need root.".into(), tool_calls: vec![], interrupted: false })
            } else {
                Ok(Reply {
                    text: String::new(),
                    tool_calls: vec![ToolCall {
                        id: "f2".into(),
                        name: "finish".into(),
                        arguments: json!({ "report": "Listening on 8080.", "outcome": "done" }).to_string(),
                    }],
                    interrupted: false,
                })
            }
        };

        let mut seen_models: Vec<Option<String>> = vec![];
        let send_blind = |text: &str, complete: &agent::Complete, seen: &mut Vec<Option<String>>| -> Result<bool> {
            turn::send_as(&db, "task-1", text, Delivery::Steer)?;
            board::wake(db.conn(), "task-1")?;
            let resolved = resolve_model(&db, "task-1", "-")?;
            arity.run_resolved(&db, "task-1", &resolved, Some(complete), &mut |e| {
                if let Event::Message(m) = e {
                    seen.push(m.model.clone());
                }
            })
        };

        // Initial run finishes blocked and reports to chat-1's inbox.
        arity.run_resolved(&db, "task-1", "secret:worker-7b", Some(&scripted), &mut |e| {
            if let Event::Message(m) = e {
                seen_models.push(m.model.clone());
            }
        })?;
        assert_eq!(arity.last_model("task-1".into())?, None);

        // 1) Plain follow-up question after the run: answers directly with no finish nudges, stays NeedsRating.
        send_blind("why not pick a default?", &scripted, &mut seen_models)?;
        let notices = arity.messages("task-1".into())?.into_iter().filter(|m| m.kind == MessageKind::Notice).count();
        assert_eq!(notices, 0, "a finished worker answers follow-ups without finish nudges");
        let speeds: i64 = db.conn().query_row("SELECT count(*) FROM speeds", [], |r| r.get(0))?;
        assert_eq!(speeds, 0, "worker replies never record speed before or after finishing");

        // 2) Unblock the worker: it calls finish again, replacing the blocked report and updating chat-1's inbox.
        send_blind("use 8080", &scripted, &mut seen_models)?;
        let t = arity.threads()?.into_iter().find(|t| t.name == "task-1").unwrap().task.unwrap();
        assert_eq!((t.run_state, t.result.as_deref(), t.model), (Some(RunState::NeedsRating), Some("Listening on 8080."), None));
        assert!(arity.messages("chat-1".into())?.last().unwrap().text.contains("Listening on 8080."));

        // 3) Failing call while unrated scrubs the model/provider from the surfaced error, keeping it for reveal.
        let failing = |_: &str, _: &[Entry], _: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            anyhow::bail!("secret:worker-7b refused connection on 127.0.0.1:9999")
        };
        let err = send_blind("one more thing", &failing, &mut seen_models).unwrap_err().to_string();
        assert!(!err.contains("secret") && !err.contains("worker-7b"), "{err}");
        assert!(arity.reveal(1)?.contains("secret:worker-7b (failed: secret:worker-7b refused"));

        // Sweep every public read surface before rating: zero mention of secret:worker-7b.
        assert!(seen_models.iter().all(Option::is_none), "{seen_models:?}");
        let public = format!(
            "{:?} {:?} {:?} {:?} {:?}",
            arity.threads()?,
            arity.messages("task-1".into())?,
            arity.messages("chat-1".into())?,
            arity.runs()?,
            arity.scores()?,
        );
        assert!(!public.contains("secret") && !public.contains("worker-7b"), "{public}");

        // Once rated, the model opens everywhere.
        arity.rate(1, 5, "clean fix".into())?;
        assert_eq!(arity.last_model("task-1".into())?.as_deref(), Some("secret:worker-7b"));
        assert!(arity.messages("task-1".into())?.iter().any(|m| m.model.as_deref() == Some("secret:worker-7b")));
        assert_eq!(arity.scores()?[0].model, "secret:worker-7b");

        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// The whole loop on scripted models: a chat delegates, a blindly picked
    /// worker hands part on and waits, the sub-task's run is killed after a
    /// committed tool call and taken over from the journal without repeating
    /// it, the parent rates the sub-task and finishes, and the result waits in
    /// the chat for the user. No model name shows before the user rates, except
    /// through `reveal`.
    #[test]
    fn scripted_end_to_end_survives_a_crash_and_stays_blind_until_rated() -> anyhow::Result<()> {
        use crate::dispatch::{Did, work_once_scripted};
        use crate::leaderboard::{Candidate, Reach};
        use std::cell::Cell;
        use std::panic::{AssertUnwindSafe, catch_unwind};

        let dir = std::env::temp_dir().join(format!("arity-e2e-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir)?;
        // The two workers; the local server points nowhere, so `leaderboard` stays offline.
        std::fs::write(
            dir.join("config.json"),
            json!({ "workers": ["secret-a:alpha", "secret-b:beta"], "providers": { "mlx": { "url": "http://127.0.0.1:9/v1" } } })
                .to_string(),
        )?;
        let arity = Arity::new(Some(dir.to_string_lossy().into_owned()));
        let db = arity.journal()?;
        let only = |m: &str| vec![Candidate { model: m.into(), reach: Reach::Local }];

        let call = |name: &str, args: serde_json::Value| Reply {
            text: String::new(),
            tool_calls: vec![ToolCall { id: format!("{name}-{}", rand::random::<u32>()), name: name.into(), arguments: args.to_string() }],
            interrupted: false,
        };
        let says = |text: &str| Reply { text: text.into(), tool_calls: vec![], interrupted: false };
        let last_tool = |chain: &[Entry]| {
            let e = chain.last().unwrap();
            (e.kind.clone(), e.body["name"].as_str().unwrap_or_default().to_string(), e.body["output"].as_str().unwrap_or_default().to_string())
        };

        // The chat's model: hands the work to an agent, then says so.
        let chat_calls = Cell::new(0);
        let chat = |_: &str, chain: &[Entry], _: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            chat_calls.set(chat_calls.get() + 1);
            Ok(match last_tool(chain).0.as_str() {
                "user" => call("add_task", json!({ "title": "Check the sources", "brief": "Check every citation in draft.md.", "assignee": "agent" })),
                "tool" => says("Handed to an agent."),
                other => panic!("chat-1 woken by {other}"),
            })
        };

        // Both workers, told apart by their brief. Worker 2 is killed on its second call, once.
        let crash = Cell::new(true);
        let worker_2_calls = Cell::new(0);
        let workers = |_: &str, chain: &[Entry], _: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            let brief = chain[0].body["text"].as_str().unwrap_or_default();
            let (kind, name, output) = last_tool(chain);
            if brief.starts_with("Check the sources") {
                return Ok(match (kind.as_str(), name.as_str()) {
                    ("user", _) => call(
                        "add_task",
                        json!({ "title": "Verify footnote 3", "brief": "Does footnote 3 match its source?", "assignee": "agent" }),
                    ),
                    ("tool", "add_task") => says("Waiting for the footnote check."),
                    // Tries to finish before rating the sub-task's result.
                    ("report", _) => call("finish", json!({ "report": "Sources verified." })),
                    ("tool", "finish") if output.starts_with("error") => call("rate", json!({ "task": 2, "score": 4, "note": "solid" })),
                    ("tool", "rate") => call("finish", json!({ "report": "All sources verified; footnote 3 holds." })),
                    other => panic!("task-1 did not expect {other:?}"),
                });
            }
            worker_2_calls.set(worker_2_calls.get() + 1);
            Ok(match (kind.as_str(), name.as_str()) {
                ("user", _) => call("add_task", json!({ "title": "Note the page number" })),
                ("tool", "add_task") if crash.replace(false) => panic!("the dispatcher's process is killed"),
                ("tool", "add_task") => call("complete_task", json!({ "id": 3 })),
                ("tool", "complete_task") => call("finish", json!({ "report": "Footnote 3 matches page 12." })),
                other => panic!("task-2 did not expect {other:?}"),
            })
        };

        // 1. chat-1 delegates task 1; the dispatcher claims it onto task-1 with a blind pick.
        turn::send_as(&db, "chat-1", "Please check the sources in draft.md.", Delivery::Steer)?;
        arity.run_resolved(&db, "chat-1", "chat:front", Some(&chat), &mut |_| {})?;
        assert_eq!(chat_calls.get(), 2);
        let t1 = board::task(db.conn(), 1)?;
        assert_eq!((t1.fields.assignee.as_str(), t1.origin.as_deref()), ("agent", Some("chat-1")));
        let did = work_once_scripted(&db, &dir, &only("secret-a:alpha"), &workers)?;
        assert!(matches!(did, Did::Waiting { task: 1, trial: 1 }), "{:?}", did.describe());

        // 2. Worker 1 filed task 2 and ended its turn: it waits, holding no lease.
        let thread = |name: &str| arity.threads().unwrap().into_iter().find(|t| t.name == name).unwrap();
        assert_eq!(thread("task-1").task.unwrap().run_state, Some(RunState::Waiting));
        let lease = |conv: &str| -> i64 {
            db.conn().query_row("SELECT lease_until FROM trials WHERE conversation = ?1", [conv], |r| r.get(0)).unwrap()
        };
        assert_eq!(lease("task-1"), 0);
        assert_eq!(board::task(db.conn(), 2)?.created_by, "conversations/task-1");

        // 3. Worker 2 claims task 2, at the floor, commits one tool call, and its process dies.
        let killed = catch_unwind(AssertUnwindSafe(|| work_once_scripted(&db, &dir, &only("secret-b:beta"), &workers)));
        assert!(killed.is_err(), "the run was killed mid-turn");
        assert_eq!(board::run_depth(db.conn(), "task-2")?, Some(board::MAX_DEPTH));
        assert_eq!(worker_2_calls.get(), 2);
        // A killed process leaves its turn lease behind, and its run's lease, last renewed 30 s ago, minutes to go.
        db.conn().execute(
            "INSERT OR REPLACE INTO turns (conversation, owner, lease_until) VALUES ('task-2', 'dead', ?1)",
            rusqlite::params![board::now() + 10],
        )?;
        db.conn().execute(
            "UPDATE trials SET lease_until = ?1 WHERE conversation = 'task-2'",
            rusqlite::params![board::now() + board::LEASE_SECS - 30],
        )?;
        assert!(matches!(work_once_scripted(&db, &dir, &only("secret-b:beta"), &workers)?, Did::Nothing), "alive until its turn lease lapses");
        db.conn().execute("UPDATE turns SET lease_until = ?1 WHERE conversation = 'task-2'", rusqlite::params![board::now() - 1])?;
        assert!(lease("task-2") > board::now() + 60, "the run's own lease still has minutes left");
        // Taken over from the journal: the committed call is not made again.
        let did = work_once_scripted(&db, &dir, &[], &workers)?;
        assert!(matches!(did, Did::Ran { task: 2, trial: 2 }), "{:?}", did.describe());
        assert_eq!(worker_2_calls.get(), 4, "two calls after the restart, not three");
        let chain2 = db.chain("task-2")?;
        assert_eq!(chain2.iter().filter(|e| e.kind == "tool" && e.body["name"] == "add_task").count(), 1);
        let todos: i64 = db.conn().query_row("SELECT count(*) FROM tasks WHERE title = 'Note the page number'", [], |r| r.get(0))?;
        assert_eq!(todos, 1, "the todo was filed once");
        let trials: i64 = db.conn().query_row("SELECT count(*) FROM trials WHERE task = 2", [], |r| r.get(0))?;
        assert_eq!(trials, 1, "the same run carried on");

        // 4. The report wakes task-1, which is turned back from finishing until it rates task 2.
        let did = work_once_scripted(&db, &dir, &[], &workers)?;
        assert!(matches!(&did, Did::Reported { task: 2, rater: Some(r) } if r == "task-1"), "{:?}", did.describe());
        let did = work_once_scripted(&db, &dir, &[], &workers)?;
        assert!(matches!(did, Did::Ran { task: 1, trial: 1 }), "{:?}", did.describe());
        let chain1 = db.chain("task-1")?;
        let refused = chain1.iter().find(|e| e.kind == "tool" && e.body["name"] == "finish").unwrap();
        assert!(refused.body["output"].as_str().unwrap().contains("rate the result of task 2"), "{:?}", refused.body);
        let (score, note, by): (i64, String, String) =
            db.conn().query_row("SELECT score, note, rated_by FROM ratings WHERE trial = 2", [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        assert_eq!((score, note.as_str(), by.as_str()), (4, "solid", "conversations/task-1"));
        // The final report lands in chat-1's inbox and wakes nothing.
        let did = work_once_scripted(&db, &dir, &[], &workers)?;
        assert!(matches!(did, Did::Reported { task: 1, rater: None }), "{:?}", did.describe());
        assert!(matches!(work_once_scripted(&db, &dir, &[], &workers)?, Did::Nothing));
        assert_eq!(chat_calls.get(), 2, "chat-1's model was not called again");
        assert!(arity.running()?.is_empty());
        let note = arity.messages("chat-1".into())?.pop().unwrap();
        assert_eq!(note.kind, MessageKind::Report);
        assert!(note.text.contains("All sources verified; footnote 3 holds."), "{}", note.text);
        let t1 = thread("task-1");
        assert_eq!((t1.group, t1.task.as_ref().unwrap().run_state), (ThreadGroup::NeedsYou, Some(RunState::NeedsRating)));

        // 5. Before the user rates, nothing public names task 1's model.
        let sweep = || -> anyhow::Result<String> {
            Ok(format!(
                "{:?} {:?} {:?} {:?} {:?} {:?} {:?}",
                arity.threads()?,
                arity.messages("chat-1".into())?,
                arity.messages("task-1".into())?,
                arity.runs()?,
                arity.scores()?,
                arity.last_model("task-1".into())?,
                arity.leaderboard()?,
            ))
        };
        let public = sweep()?;
        assert!(!public.contains("secret-a") && !public.contains("alpha"), "{public}");
        assert_eq!(arity.last_model("task-1".into())?, None);
        // Run 2 is rated, by task-1's agent, so its model is open: the rule is "rated", not "rated by the user".
        let runs = arity.runs()?;
        assert_eq!(runs.iter().find(|r| r.id == 2).unwrap().model.as_deref(), Some("secret-b:beta"));
        assert_eq!(runs.iter().find(|r| r.id == 1).unwrap().model, None);
        assert_eq!(arity.scores()?.iter().map(|s| (s.model.as_str(), s.rated)).collect::<Vec<_>>(), [("secret-b:beta", 1)]);
        assert!(arity.leaderboard()?.iter().any(|s| format!("{s:?}").contains("secret-b:beta")));
        // reveal tells, and rates nothing.
        assert_eq!(arity.reveal(1)?, "secret-a:alpha");
        assert_eq!(thread("task-1").task.unwrap().run_state, Some(RunState::NeedsRating));
        assert!(!sweep()?.contains("alpha"));

        // 6. The user's rating opens it everywhere.
        arity.rate(1, 5, "great".into())?;
        let t1 = thread("task-1");
        assert_eq!(t1.group, ThreadGroup::Done);
        assert_eq!(t1.task.unwrap().model.as_deref(), Some("secret-a:alpha"));
        assert!(arity.messages("task-1".into())?.iter().any(|m| m.model.as_deref() == Some("secret-a:alpha")));
        assert_eq!(arity.runs()?.iter().find(|r| r.id == 1).unwrap().model.as_deref(), Some("secret-a:alpha"));
        assert_eq!(arity.last_model("task-1".into())?.as_deref(), Some("secret-a:alpha"));
        assert!(arity.scores()?.iter().any(|s| s.model == "secret-a:alpha" && s.rated == 1 && s.average == 5.0));

        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }
}
