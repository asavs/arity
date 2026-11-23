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
mod approvals;
mod board;
mod catalog;
mod codex_auth;
mod coding;
mod compact;
mod config;
mod dispatch;
mod draft;
mod endpoint;
mod files;
mod hooks;
mod journal;
mod keys;
mod leaderboard;
mod model;
mod papercut;
mod prompt;
mod tools;
mod usage;
mod turn;
mod worktree;

use std::path::PathBuf;
use std::sync::Arc;

pub use agent::Harness;
pub use board::{Priority, RunState, TaskFields, TaskState};
pub use turn::{Delivery, FollowUp, Live};
pub use catalog::KeyCheck;
pub use config::{HookConfig, HookEvent};
pub use hooks::{BeforeTool, Hook, HookCtx, HookRegistry, YieldAction};
pub use leaderboard::{Choice, Figures, Pick, Standing};
pub use model::{Reply, Sink, TokenUsage, ToolCall};
pub use tools::{Caller, Execution, Replay, Tool, ToolOutput, ToolRegistry, ToolSpec, ToolVisibility};
pub use usage::{Limit, Usage};
pub use journal::{Entry, Journal};
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
    /// A post-use rating recorded by the parent hook.
    Rating,
    /// The person opened, edited, added or dismissed the task card.
    TaskCard,
    /// The model's context was compacted into a summary, or reset; the first line says which.
    Compaction,
}

/// Tokens a conversation used, as its providers reported them. Replies from
/// providers that do not report usage are not counted.
#[derive(Debug, Clone, Default, PartialEq, Eq, uniffi::Record)]
pub struct ConversationUsage {
    /// Prompt tokens summed over every reply in the whole thread, before and after any compact or reset.
    /// Each reply re-reads its context, so this is what was paid for, not how full the context is.
    pub input_tokens: u64,
    /// The part of `input_tokens` served from providers' prompt caches.
    pub cached_tokens: u64,
    /// Output tokens summed over every reply in the whole thread.
    pub output_tokens: u64,
    /// Prompt tokens of the latest reply in the active context: how full the context is now.
    /// 0 right after a compact or reset, until the next reply.
    pub last_input_tokens: u64,
    /// The part of `last_input_tokens` served from the prompt cache.
    pub last_cached_tokens: u64,
    /// Replies that reported usage.
    pub replies_with_usage: u32,
    /// Entries before the newest compact or reset that the model no longer reads; 0 if there is none.
    pub compacted_entries: u32,
}

/// A built-in hook registered in Arity's default harness.
#[derive(Debug, Clone, uniffi::Record)]
pub struct BuiltinHookInfo {
    pub name: String,
    pub event: HookEvent,
    pub tool: Option<String>,
    pub description: String,
}

/// A tool registered in Arity's harness, and whether it is available for a conversation.
#[derive(Debug, Clone, uniffi::Record)]
pub struct ToolInfo {
    pub name: String,
    /// "atomic", "external/safe", or "external/unsafe".
    pub execution: String,
    pub available: bool,
    pub description: String,
}

/// One row of the one list both front ends show.
#[derive(Debug, Clone, uniffi::Record)]
pub struct Thread {
    /// The conversation's name. A task nobody has started is `task-<id>`,
    /// which is also what its first run's conversation will be called.
    pub name: String,
    /// The task's title, or how the chat began.
    pub title: String,
    /// The attached source folder, shared by a task and its runs.
    pub folder: Option<String>,
    pub group: ThreadGroup,
    /// When something last happened in it, UTC, "YYYY-MM-DD HH:MM:SS".
    pub updated: String,
    pub task: Option<BoardTask>,
    /// The task card being drafted in it, if one is open.
    pub card: Option<TaskFields>,
    /// Nothing runs in it, and it stopped short: a reply cut off, a message or
    /// report not answered yet, or a run stopped or failing. See `continue_turn`.
    pub can_continue: bool,
    /// Runs below it that are going or about to start, at any depth. See `stop_subtree`.
    pub active_children: u32,
    /// Tool calls in it waiting for the user to allow or deny. See `pending_approvals`.
    pub pending_approvals: u32,
}

/// A tool call a hook parked until the user allows or denies it.
#[derive(Debug, Clone, uniffi::Record)]
pub struct PendingApproval {
    pub id: i64,
    pub conversation: String,
    pub call_id: String,
    pub tool: String,
    /// The call's arguments, as JSON.
    pub args: String,
    /// The hook that asked.
    pub hook: String,
    pub reason: String,
    /// When it was asked, UTC, as Unix seconds.
    pub created_at: i64,
}

impl From<approvals::Approval> for PendingApproval {
    fn from(a: approvals::Approval) -> Self {
        Self {
            id: a.id,
            conversation: a.conversation,
            call_id: a.call_id,
            tool: a.tool,
            args: a.args,
            hook: a.hook,
            reason: a.reason,
            created_at: a.created_at,
        }
    }
}

/// Where a thread sits in the list, most pressing first.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, uniffi::Enum)]
pub enum ThreadGroup {
    /// A result to rate, a tool call to allow or deny, or a task card left open.
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

/// One file a run changed in its isolated worktree.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct DiffFileInfo {
    pub path: String,
    /// "added", "modified", "deleted" or "renamed".
    pub status: String,
    pub insertions: u32,
    pub deletions: u32,
}

/// What a run changed in its own git worktree, for review before it is applied.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct WorktreeDiffInfo {
    pub conversation: String,
    /// The user's real checkout, where `apply_worktree` lands the changes.
    pub repo_root: String,
    /// The run's isolated folder, which its file tools read and write.
    pub work_dir: String,
    pub base_commit: String,
    /// "active", "applied" or "discarded". Applied and discarded worktrees are
    /// gone, so they come with no files and no patch.
    pub status: String,
    pub files_changed: u32,
    pub insertions: u32,
    pub deletions: u32,
    pub files: Vec<DiffFileInfo>,
    /// A unified diff against the commit the worktree was made from.
    pub patch: String,
    /// The patch was cut short; `files` is still complete.
    pub truncated: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct ApplyResult {
    pub files_applied: u32,
    pub summary: String,
}

impl From<worktree::WorktreeDiff> for WorktreeDiffInfo {
    fn from(d: worktree::WorktreeDiff) -> Self {
        WorktreeDiffInfo {
            conversation: d.conversation,
            repo_root: d.repo_root,
            work_dir: d.work_dir,
            base_commit: d.base_commit,
            status: d.status,
            files_changed: d.files_changed,
            insertions: d.insertions,
            deletions: d.deletions,
            files: d
                .files
                .into_iter()
                .map(|f| DiffFileInfo { path: f.path, status: f.status, insertions: f.insertions, deletions: f.deletions })
                .collect(),
            patch: d.patch,
            truncated: d.truncated,
        }
    }
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
        let folders = board::attached_folders(db.conn())?;
        // An attachment is enough to locate a new chat before its first journal entry.
        let listed: std::collections::HashSet<_> = conversations.iter().map(|c| c.0.clone()).collect();
        conversations.extend(folders.keys().filter(|name| !listed.contains(*name)).map(|name| (name.clone(), String::new())));
        let running = turn::running(db.conn())?;
        let mut out = Vec::new();
        // A run's own todos are its scratchpad, shown in its thread rather than listed.
        for t in board::tasks(db.conn())?.into_iter().filter(|t| !t.fields.assignee.starts_with("conversations/")) {
            let name = t.conversation.clone().unwrap_or_else(|| format!("task-{}", t.id));
            let updated = conversations.iter().find(|c| c.0 == name).map_or(&t.created, |c| &c.1).clone();
            let pending_approvals = pending_count(&db, &name)?;
            let group = match (t.state, t.run_state) {
                (_, _) if pending_approvals > 0 => ThreadGroup::NeedsYou,
                (TaskState::Done, _) => ThreadGroup::Done,
                (_, Some(RunState::NeedsRating | RunState::Paused | RunState::NeedsApproval)) => ThreadGroup::NeedsYou,
                (_, Some(RunState::Running | RunState::Failing | RunState::Waiting)) => ThreadGroup::Running,
                _ => ThreadGroup::Open,
            };
            let can_continue = t.conversation.is_some() && can_continue(&db, &name, &db.chain(&name)?, &running)?;
            let active_children = board::active_children(db.conn(), &name)?;
            out.push(Thread {
                folder: folders.get(&name).cloned().or_else(|| t.fields.dir.clone()),
                name,
                title: t.fields.title.clone(),
                group,
                updated,
                task: Some(t.into()),
                card: None,
                can_continue,
                active_children,
                pending_approvals,
            });
        }
        // Whatever is left is a chat. Earlier runs of a task that was run again are not listed.
        let runs = board::run_conversations(db.conn())?;
        conversations.retain(|c| !runs.contains(&c.0) && !out.iter().any(|t| t.name == c.0));
        for (name, updated) in conversations {
            let chain = db.chain(&name)?;
            let card = draft::current(&chain);
            let said = chain.iter().find(|e| e.kind == "user").and_then(|e| e.body["text"].as_str());
            let title = said.and_then(|s| s.lines().next()).map_or(name.clone(), |l| l.chars().take(60).collect());
            let pending_approvals = pending_count(&db, &name)?;
            let group = if card.is_some() || pending_approvals > 0 { ThreadGroup::NeedsYou } else { ThreadGroup::Chat };
            let can_continue = can_continue(&db, &name, &chain, &running)?;
            let active_children = board::active_children(db.conn(), &name)?;
            out.push(Thread {
                folder: folders.get(&name).cloned(),
                name,
                title: if updated.is_empty() { "New chat".into() } else { title },
                group,
                updated,
                task: None,
                card,
                can_continue,
                active_children,
                pending_approvals,
            });
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

    /// Route a composer follow-up to existing work. Idle conversations use the normal send path.
    pub fn follow_up(&self, conversation: String, text: String, interrupt: Option<String>) -> Result<FollowUp> {
        Ok(turn::follow_up(&self.journal()?, &conversation, &text, interrupt.as_deref())?)
    }

    /// Stop the turn running in a conversation, in whichever process runs it.
    /// The reply so far is kept. False if nothing was running.
    pub fn stop(&self, conversation: String) -> Result<bool> {
        Ok(turn::stop(self.journal()?.conn(), &conversation)?)
    }

    /// Stop a conversation and everything it handed out, at any depth: turns
    /// running are stopped, open runs are paused until continued, nothing is
    /// woken again, and tasks for an agent no run has picked up yet go back to
    /// the user. Returns how many conversations were stopped.
    pub fn stop_subtree(&self, conversation: String) -> Result<u32> {
        let db = self.journal()?;
        Ok(db.immediate(|c| board::stop_subtree(c, &conversation))?.0)
    }

    // Approvals

    /// Tool calls waiting for the user to allow or deny, oldest first: in one
    /// conversation, or everywhere when None.
    pub fn pending_approvals(&self, conversation: Option<String>) -> Result<Vec<PendingApproval>> {
        let db = self.journal()?;
        let pending = match conversation {
            Some(conv) => approvals::pending_for(db.conn(), &conv)?,
            None => approvals::list_pending(db.conn())?,
        };
        Ok(pending.into_iter().map(PendingApproval::from).collect())
    }

    /// Allow a parked tool call. A run is woken for the dispatcher to carry on;
    /// a chat is not woken, and is carried on with `continue_turn` (see
    /// `can_continue`), which runs the call. `note` is kept with the decision.
    pub fn approve_tool(&self, id: i64, note: Option<String>) -> Result<PendingApproval> {
        self.decide_tool(id, true, note)
    }

    /// Deny a parked tool call: the model is told "Tool call denied by user",
    /// with `note` after it if there is one, once the conversation carries on
    /// as after `approve_tool`.
    pub fn deny_tool(&self, id: i64, note: Option<String>) -> Result<PendingApproval> {
        self.decide_tool(id, false, note)
    }

    /// Take back the user's messages still waiting for the turn running in a
    /// conversation. Reports from sub-tasks are never taken back. Returns how many were.
    pub fn discard_queued(&self, conversation: String) -> Result<u32> {
        Ok(turn::discard_queued_user(self.journal()?.conn(), &conversation)? as u32)
    }

    /// Whether `continue_turn` has something to carry on in a conversation; see `Thread::can_continue`.
    pub fn can_continue(&self, conversation: String) -> Result<bool> {
        let db = self.journal()?;
        Ok(can_continue(&db, &conversation, &db.chain(&conversation)?, &turn::running(db.conn())?)?)
    }

    /// Carry on a conversation that stopped short (see `can_continue`) from
    /// where it is, without writing anything to ask for it: a reply cut off is
    /// asked for again, an unanswered message or report is answered, a stopped
    /// or failing run goes on. `model` is for a chat; None uses its last one,
    /// and a run always keeps its own. Blocks. Returns how the turn ended.
    pub fn continue_turn(&self, conversation: String, model: Option<String>) -> Result<String> {
        self.continue_with(&conversation, model.as_deref(), &mut |_| {})
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

    /// Tokens the conversation used, and how full its context is now.
    pub fn usage(&self, conversation: String) -> Result<ConversationUsage> {
        Ok(conversation_usage(&self.journal()?.chain(&conversation)?))
    }

    /// Replace what the model reads in a conversation with a summary the model writes, keeping every
    /// entry in the journal. `instructions` say what the summary should keep. Returns the summary.
    /// Blocks on a model call. Refused while a turn runs there.
    pub fn compact(&self, conversation: String, instructions: Option<String>) -> Result<String> {
        self.compact_with(&self.journal()?, &conversation, instructions.as_deref(), None)
    }

    /// Start the model afresh in a conversation: it reads nothing before this point, though the whole
    /// thread stays visible and can still be forked. `note` is shown with the marker.
    pub fn reset_context(&self, conversation: String, note: Option<String>) -> Result<()> {
        let db = self.journal()?;
        if db.chain_for_model(&conversation)?.iter().all(|e| e.kind == "system") {
            return Err(ArityError::Failed("Nothing to reset yet: the model's context is already empty".into()));
        }
        let Some(mut lease) = turn::Lease::take(&db, &conversation, "")? else {
            return Err(busy(&conversation));
        };
        compact::reset(&db, &conversation, note.as_deref())?;
        lease.release(&db, true)?;
        Ok(())
    }

    /// Attach a folder to a conversation, or with None take it away. Its file
    /// tools read only inside it, and tasks filed there take it with them. The
    /// folder must exist; it can be set before the first message.
    pub fn set_folder(&self, conversation: String, dir: Option<String>) -> Result<()> {
        Ok(board::set_folder(self.journal()?.conn(), &conversation, dir.as_deref())?)
    }

    /// The folder attached to a conversation, or for a run, to its task.
    pub fn folder(&self, conversation: String) -> Result<Option<String>> {
        let db = self.journal()?;
        Ok(board::folder_of(db.conn(), &conversation)?.map(|p| p.to_string_lossy().into_owned()))
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

    /// A papercut from the user: a note that something got in the way, filed as
    /// a low-priority task on their own list tagged `papercut`. `conversation`
    /// is the thread it came up in, if any; `about` is what got in the way.
    pub fn file_papercut(&self, conversation: Option<String>, note: String, about: Option<String>) -> Result<i64> {
        Ok(papercut::file(self.journal()?.conn(), conversation.as_deref(), &note, about.as_deref())?)
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

    // Worktrees: review and apply a run's changes

    /// What the run in `conversation` changed in its isolated git worktree, as
    /// files with line counts and a unified patch. None if it has no worktree
    /// (its folder is not in a git repo). After `apply_worktree` or
    /// `discard_worktree` it still answers, with that `status` and nothing to review.
    pub fn worktree_diff(&self, conversation: String) -> Result<Option<WorktreeDiffInfo>> {
        Ok(worktree::diff(self.journal()?.conn(), &conversation)?.map(Into::into))
    }

    /// Apply the run's reviewed changes to the user's real checkout, then remove
    /// its worktree. Refused, with nothing changed, if they conflict with the
    /// checkout's own edits or while the run is still going.
    pub fn apply_worktree(&self, conversation: String) -> Result<ApplyResult> {
        let db = self.journal()?;
        refuse_while_running(&db, &conversation, "apply")?;
        let out = worktree::apply(db.conn(), &conversation)?;
        Ok(ApplyResult { files_applied: out.files_applied, summary: out.summary })
    }

    /// Throw the run's worktree away without applying it. False if there was
    /// nothing active to discard. Refused while the run is still going.
    pub fn discard_worktree(&self, conversation: String) -> Result<bool> {
        let db = self.journal()?;
        refuse_while_running(&db, &conversation, "discard")?;
        Ok(worktree::discard(db.conn(), &conversation)?)
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

    // Hooks & tools

    /// Built-in hooks that run automatically in Arity's harness.
    pub fn builtin_hooks(&self) -> Vec<BuiltinHookInfo> {
        vec![
            BuiltinHookInfo {
                name: "safety_guard".into(),
                event: HookEvent::BeforeTool,
                tool: Some("write_file,edit_file,bash".into()),
                description: "Blocks .git and .env edits, rm -rf of / or ~, fork bombs, and git force-push. \
                              Asks before git push, sudo, chmod 777, curl | sh, and other rm -rf."
                    .into(),
            },
            BuiltinHookInfo {
                name: "unfinished_run".into(),
                event: HookEvent::OnYield,
                tool: None,
                description: "Nudges a worker run that stops without calling finish (up to 2 times).".into(),
            },
            BuiltinHookInfo {
                name: "rate".into(),
                event: HookEvent::AfterTurn,
                tool: None,
                description: "Rates completed child runs after the parent integrates their report.".into(),
            },
        ]
    }

    /// User-configured hooks from `config.json`.
    pub fn hooks(&self) -> Result<Vec<HookConfig>> {
        Ok(config::load(&self.dir)?.hooks)
    }

    /// Add or replace a hook in `config.json`.
    pub fn save_hook(&self, hook: HookConfig) -> Result<()> {
        Ok(config::save_hook(&self.dir, &hook)?)
    }

    /// Remove a hook by name from `config.json`. Returns true if one was removed.
    pub fn remove_hook(&self, name: String) -> Result<bool> {
        Ok(config::remove_hook(&self.dir, &name)?)
    }

    /// Enable or disable a hook by name in `config.json`.
    pub fn set_hook_enabled(&self, name: String, enabled: bool) -> Result<()> {
        Ok(config::set_hook_enabled(&self.dir, &name, enabled)?)
    }

    /// Prompt token threshold for automatic pre-turn compaction (`None` if disabled).
    pub fn auto_compact_tokens(&self) -> Result<Option<u64>> {
        Ok(config::load(&self.dir)?.auto_compact_tokens)
    }

    /// Set or disable automatic pre-turn compaction in `config.json`.
    pub fn set_auto_compact_tokens(&self, tokens: Option<u64>) -> Result<()> {
        Ok(config::set_auto_compact_tokens(&self.dir, tokens)?)
    }

    /// Every tool registered in the default harness and whether it is offered to `conversation`
    /// (or to a chat with no folder when `conversation` is None).
    pub fn tools(&self, conversation: Option<String>) -> Result<Vec<ToolInfo>> {
        let vis = match conversation {
            Some(ref conv) => {
                let db = self.journal()?;
                tools::ToolVisibility {
                    caller: tools::caller(db.conn(), conv)?,
                    folder: board::folder_of(db.conn(), conv)?,
                }
            }
            None => tools::ToolVisibility { caller: tools::Caller::Chat, folder: None },
        };
        let fallback_vis = tools::ToolVisibility {
            caller: vis.caller,
            folder: Some(std::path::PathBuf::from(".")),
        };
        let reg = tools::ToolRegistry::default();
        Ok(reg
            .all()
            .iter()
            .map(|t| {
                let spec = t.spec(&vis);
                let available = spec.is_some();
                let description = spec
                    .or_else(|| t.spec(&fallback_vis))
                    .or_else(|| t.spec(&tools::ToolVisibility { caller: tools::Caller::Run, folder: Some(std::path::PathBuf::from(".")) }))
                    .map(|s| s.description.to_string())
                    .unwrap_or_default();
                let execution = match t.execution() {
                    tools::Execution::Atomic => "atomic",
                    tools::Execution::External { replay: tools::Replay::Safe } => "external/safe",
                    tools::Execution::External { replay: tools::Replay::Unsafe } => "external/unsafe",
                };
                ToolInfo { name: t.name().into(), execution: execution.into(), available, description }
            })
            .collect())
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
        board::clear_wake(db.conn(), conv)?;
        self.run_resolved(&db, conv, &model, None, on)
    }

    fn decide_tool(&self, id: i64, approve: bool, note: Option<String>) -> Result<PendingApproval> {
        let db = self.journal()?;
        let note = note.unwrap_or_default();
        let decided = db.immediate(|c| {
            let decided = approvals::decide(c, id, approve, note.trim())?;
            // A run goes back to the dispatcher. A chat is left to whoever decided, so the
            // dispatcher does not race them for the turn; `can_continue` says it may go on.
            if board::trial_state(c, &decided.conversation)?.is_some_and(|(.., ended)| !ended) {
                board::wake(c, &decided.conversation)?;
            }
            Ok(decided)
        })?;
        Ok(decided.into())
    }

    /// As `continue_turn`, reporting text and messages as they come.
    pub fn continue_with(&self, conv: &str, model: Option<&str>, on: &mut dyn FnMut(Event)) -> Result<String> {
        self.continue_in(&self.journal()?, conv, model, None, on)
    }

    pub(crate) fn continue_in(
        &self,
        db: &Journal,
        conv: &str,
        model: Option<&str>,
        scripted: Option<&agent::Complete>,
        on: &mut dyn FnMut(Event),
    ) -> Result<String> {
        let running = turn::running(db.conn())?;
        if running.iter().any(|r| r == conv) {
            // Another front end, or the dispatcher, already took up a decided call.
            if approvals::has_decided(db.conn(), conv)? {
                return Ok("a turn is already running".into());
            }
            return Err(busy(conv));
        }
        if approvals::has_pending(db.conn(), conv)? {
            return Err(ArityError::Failed(format!(
                "{conv} waits for you to allow or deny a tool call: see `arity approvals {conv}`"
            )));
        }
        if !can_continue(db, conv, &db.chain(conv)?, &running)? {
            return Err(ArityError::Failed(format!("Nothing to continue in {conv}: it is not waiting on anything")));
        }
        let model = resolve_model(db, conv, model.unwrap_or("-"))?;
        board::clear_wake(db.conn(), conv)?;
        Ok(match self.run_outcome(db, conv, &model, scripted, true, on)? {
            None | Some(agent::Outcome::Busy) => "a turn is already running",
            Some(agent::Outcome::Ended) => "answered",
            Some(agent::Outcome::Stopped) => "stopped",
            Some(agent::Outcome::Finished(_)) => "finished",
            Some(agent::Outcome::Waiting) => "waiting on its sub-tasks",
            Some(agent::Outcome::Unfinished) => "stopped without finishing",
            Some(agent::Outcome::NeedsApproval) => "waiting for your approval",
        }
        .to_string())
    }

    pub(crate) fn run_resolved(
        &self,
        db: &Journal,
        conv: &str,
        model: &str,
        scripted: Option<&agent::Complete>,
        on: &mut dyn FnMut(Event),
    ) -> Result<bool> {
        Ok(self.run_outcome(db, conv, model, scripted, false, on)?.is_some())
    }

    /// Run a turn and settle its run, if it is one. None when another loop holds the conversation.
    fn run_outcome(
        &self,
        db: &Journal,
        conv: &str,
        model: &str,
        scripted: Option<&agent::Complete>,
        continuing: bool,
        on: &mut dyn FnMut(Event),
    ) -> Result<Option<agent::Outcome>> {
        let trial = board::trial_state(db.conn(), conv)?;
        if trial.is_some() {
            // A run in a git repo works in its own worktree, made now if it has none (or lost it).
            dispatch::isolate(db.conn(), &self.dir, conv)?;
        }
        let hidden = hidden(db, conv)?;
        // Someone asked for it: a run stopped or waiting out a backoff goes on now.
        if let Some((tr_id, _, _, false)) = &trial {
            db.conn()
                .execute("UPDATE trials SET paused = 0, retry_at = 0 WHERE id = ?1", rusqlite::params![tr_id])
                .map_err(anyhow::Error::from)?;
        }
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
            Some(complete) => {
                agent::run_turn(db, conv, model, &agent::Harness::for_data_dir(&self.dir), complete, continuing, &mut on_event)
            }
            None => agent::run_from(db, &self.dir, conv, model, continuing, &mut on_event),
        };
        match ran {
            Ok(agent::Outcome::Busy) => Ok(None),
            Ok(outcome) => {
                if let Some((tr_id, task_id, _, ended_before)) = trial {
                    if matches!(dispatch::settle(db, tr_id, task_id, conv, ended_before, outcome.clone())?, dispatch::Did::Ran { .. }) {
                        let _ = dispatch::deliver_report(db)?;
                    }
                }
                Ok(Some(outcome))
            }
            Err(e) => {
                if let Some((tr_id, _, _, ended_before)) = trial {
                    let detail = format!("{e:#}");
                    if ended_before {
                        let _ = db.conn().execute("UPDATE trials SET error = ?2 WHERE id = ?1", rusqlite::params![tr_id, detail]);
                    } else {
                        let _ = dispatch::retry_later(db.conn(), tr_id, &detail);
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

    /// As `compact`, on `scripted` instead of the conversation's model when given.
    pub(crate) fn compact_with(
        &self,
        db: &Journal,
        conv: &str,
        instructions: Option<&str>,
        scripted: Option<&agent::Complete>,
    ) -> Result<String> {
        let view = db.chain_for_model(conv)?;
        if compact::replies(&view) < 2 {
            return Err(ArityError::Failed("Nothing to compact yet: the context holds fewer than two replies".into()));
        }
        let model = match resolve_model(db, conv, "-") {
            Ok(m) => m,
            Err(_) => self.pick(Choice::Smart)?.model,
        };
        let real = match scripted {
            Some(_) => None,
            None => Some(model::Model::parse(&model, &self.dir, &journal::session_id(db.conn(), conv)?)?),
        };
        let call = |system: &str, chain: &[Entry], tools: &[ToolSpec], out: &mut Sink| real.as_ref().expect("a model").complete(system, chain, tools, out);
        let complete: &agent::Complete = scripted.unwrap_or(&call);

        // Holding the turn's lease keeps a turn from starting meanwhile; messages sent wait for it.
        let Some(mut lease) = turn::Lease::take(db, conv, &model)? else {
            return Err(busy(conv));
        };
        let live = lease.live.clone();
        let summary = compact::compact(db, conv, complete, instructions, false, &|| live.stopped() && !live.cut_for_steer());
        let answer = !lease.release(db, summary.is_err())?;
        drop(lease);
        let summary = match summary {
            Ok(s) => s,
            Err(_) if hidden(db, conv)? => {
                return Err(ArityError::Failed("could not reach the model for this task; rate first or use reveal to see why".into()));
            }
            Err(e) => return Err(e.into()),
        };
        // Someone wrote while it was summarizing: answer them now, as the turn would have.
        if answer {
            self.run_resolved(db, conv, &model, scripted, &mut |_| {})?;
        }
        Ok(summary)
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

/// A worktree is not applied or discarded out from under a worker that is
/// using it: a turn running there, or a run still going and not stopped.
fn refuse_while_running(db: &Journal, conv: &str, what: &str) -> Result<()> {
    let working: bool = turn::running(db.conn())?.iter().any(|r| r == conv)
        || db
            .conn()
            .query_row(
                "SELECT count(*) > 0 FROM trials WHERE conversation = ?1 AND ended IS NULL AND abandoned = 0
                   AND paused = 0 AND waiting = 0",
                [conv],
                |r| r.get(0),
            )
            .map_err(anyhow::Error::from)?;
    if working {
        return Err(ArityError::Failed(format!("The run in {conv} is still going; stop it before you {what} its changes")));
    }
    Ok(())
}

fn busy(conv: &str) -> ArityError {
    ArityError::Failed(format!("A turn is running in {conv}; wait for it to end or stop it first"))
}

/// Sums every reply's usage in `chain`, and reads the latest one in the model's active context.
fn conversation_usage(chain: &[Entry]) -> ConversationUsage {
    let mut out = ConversationUsage::default();
    for u in chain.iter().filter(|e| e.kind == "assistant").filter_map(|e| model::TokenUsage::from_json(&e.body["usage"])) {
        out.input_tokens += u.input;
        out.cached_tokens += u.cached;
        out.output_tokens += u.output;
        out.replies_with_usage += 1;
    }
    let view = journal::model_view(chain.to_vec());
    if let Some(u) = compact::last_usage(&view) {
        out.last_input_tokens = u.input;
        out.last_cached_tokens = u.cached;
    }
    if let Some(i) = journal::head_marker(chain) {
        let read: std::collections::HashSet<i64> = view.iter().map(|e| e.id).collect();
        out.compacted_entries = chain[..i].iter().filter(|e| !read.contains(&e.id)).count() as u32;
    }
    out
}

/// Whether `conv`, whose chain is `chain`, stopped short and nothing else will
/// carry it on: no turn runs there (`running`), the dispatcher is not about to
/// wake it, and it is a run the user stopped or that is failing, a chat whose
/// tool call the user just allowed or denied, or its chain ends on a cut-off
/// reply or something not answered yet. Never while a tool call waits for the user.
fn can_continue(db: &Journal, conv: &str, chain: &[Entry], running: &[String]) -> anyhow::Result<bool> {
    if running.iter().any(|r| r == conv) || approvals::has_pending(db.conn(), conv)? {
        return Ok(false);
    }
    let open: Option<(bool, bool)> = db
        .conn()
        .query_row(
            "SELECT paused, error IS NOT NULL FROM trials WHERE conversation = ?1 AND ended IS NULL ORDER BY id DESC LIMIT 1",
            [conv],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .map(Some)
        .or_else(|e| if e == rusqlite::Error::QueryReturnedNoRows { Ok(None) } else { Err(e) })?;
    // An open run is the dispatcher's to carry on, unless it was stopped or keeps failing.
    if let Some((paused, failing)) = open {
        return Ok(paused || failing);
    }
    // Deciding a call wakes a chat too; whoever decided it may carry it on at once.
    if approvals::has_decided(db.conn(), conv)? {
        return Ok(true);
    }
    let waking: bool = db
        .conn()
        .query_row("SELECT count(*) > 0 FROM conversations WHERE name = ?1 AND wake_at > 0", [conv], |r| r.get(0))?;
    Ok(!waking && (agent::cut_short(chain) || agent::owes_a_step(chain)))
}

fn pending_count(db: &Journal, conv: &str) -> anyhow::Result<u32> {
    Ok(approvals::pending_for(db.conn(), conv)?.len() as u32)
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
        "rating" => {
            let task = e.body["task"].as_i64().unwrap_or_default();
            let score = e.body["score"].as_i64().unwrap_or_default();
            let note = text("note");
            let summary = if note.is_empty() { format!("Rated task {task}: {score}/5") } else { format!("Rated task {task}: {score}/5 — {note}") };
            vec![Message { model: None, ..msg(0, MessageKind::Rating, summary) }]
        }
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
        "compact" => {
            let n = e.body["compacted_count"].as_u64().unwrap_or_default();
            let how = if e.body["continue"] == true { "Context compacted automatically" } else { "Context compacted" };
            let head = format!("{how}: the model now reads this summary instead of the {n} entries before it.");
            vec![Message { model: None, ..msg(0, MessageKind::Compaction, format!("{head}\n\n{}", text("summary"))) }]
        }
        "reset" => {
            let mut t = "Context reset: the model starts fresh from here. Everything above stays here, and can be forked.".to_string();
            if !text("note").is_empty() {
                t.push_str(&format!("\n\n{}", text("note")));
            }
            vec![Message { model: None, ..msg(0, MessageKind::Compaction, t) }]
        }
        _ => vec![],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Reply, ToolCall};
    use serde_json::json;
    use std::path::Path;

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
            let last = chain.iter().rev().find(|e| e.kind != "system").unwrap();
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
                    usage: None,
                })
            } else if said.contains("why") {
                out("Ports below 1024 need root.");
                Ok(Reply { text: "Ports below 1024 need root.".into(), tool_calls: vec![], interrupted: false, usage: None })
            } else {
                Ok(Reply {
                    text: String::new(),
                    tool_calls: vec![ToolCall {
                        id: "f2".into(),
                        name: "finish".into(),
                        arguments: json!({ "report": "Listening on 8080.", "outcome": "done" }).to_string(),
                    }],
                    interrupted: false,
                    usage: None,
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

    #[test]
    fn compact_and_reset_change_what_the_model_reads_and_nothing_else() -> anyhow::Result<()> {
        let dir = std::env::temp_dir().join(format!("arity-compact-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let arity = Arity::new(Some(dir.to_string_lossy().into_owned()));
        let db = arity.journal()?;
        let conv = "chat-1";
        let seen = std::cell::RefCell::new(Vec::<Vec<Entry>>::new());
        let model = |system: &str, chain: &[Entry], _: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            if system == compact::SUMMARIZER_SYSTEM {
                let asked = chain[0].body["text"].as_str().unwrap();
                assert!(asked.contains("Also: keep the port number"), "instructions reach the summarizer");
                return Ok(Reply { text: "## Goal\nServe on port 8080.".into(), tool_calls: vec![], interrupted: false, usage: None });
            }
            seen.borrow_mut().push(chain.to_vec());
            let n = seen.borrow().len() as u64;
            let usage = model::TokenUsage { input: 1000 * n, cached: 600 * n, output: 10, total: 1000 * n + 10 };
            Ok(Reply { text: format!("reply {n}"), tool_calls: vec![], interrupted: false, usage: Some(usage) })
        };
        let say = |text: &str| -> anyhow::Result<()> {
            turn::send_as(&db, conv, text, Delivery::Steer)?;
            arity.run_resolved(&db, conv, "test:m", Some(&model), &mut |_| {})?;
            Ok(())
        };

        say("serve it on 8080")?;
        let early = arity.compact_with(&db, conv, None, Some(&model)).unwrap_err().to_string();
        assert!(early.contains("Nothing to compact yet"), "{early}");
        say("and log requests")?;
        let u = arity.usage(conv.into())?;
        assert_eq!(
            u,
            ConversationUsage {
                input_tokens: 3000,
                cached_tokens: 1800,
                output_tokens: 20,
                last_input_tokens: 2000,
                last_cached_tokens: 1200,
                replies_with_usage: 2,
                compacted_entries: 0
            }
        );

        // Refused while a turn holds the conversation.
        let lease = turn::Lease::take(&db, conv, "test:m")?.unwrap();
        assert!(arity.compact_with(&db, conv, None, Some(&model)).unwrap_err().to_string().contains("A turn is running"));
        assert!(arity.reset_context(conv.into(), None).unwrap_err().to_string().contains("A turn is running"));
        drop(lease);

        let before = db.chain(conv)?;
        let summary = arity.compact_with(&db, conv, Some("keep the port number"), Some(&model))?;
        assert_eq!(summary, "## Goal\nServe on port 8080.");
        let after = db.chain(conv)?;
        assert_eq!(after.len(), before.len() + 1, "one entry appended");
        assert!(before.iter().zip(&after).all(|(a, b)| a.id == b.id && a.body == b.body), "nothing rewritten");
        let marker = after.last().unwrap();
        assert_eq!((marker.kind.as_str(), marker.body["compacted_count"].as_u64()), ("compact", Some(4)));
        assert!(turn::running(db.conn())?.is_empty(), "the lease is let go");

        // The transcript shows everything and the marker; usage counts the whole thread, but the context is empty for now.
        let msgs = arity.messages(conv.into())?;
        assert_eq!(msgs.iter().filter(|m| m.kind == MessageKind::User).count(), 2);
        let shown = msgs.iter().find(|m| m.kind == MessageKind::Compaction).unwrap();
        assert!(shown.text.starts_with("Context compacted: ") && shown.text.ends_with("Serve on port 8080."));
        let u = arity.usage(conv.into())?;
        assert_eq!((u.input_tokens, u.last_input_tokens, u.compacted_entries), (3000, 0, 5));

        // The next turn reads the summary, the new message, and the sections sent again.
        say("what port?")?;
        let view = seen.borrow().last().unwrap().clone();
        assert_eq!(view.iter().map(|e| e.kind.as_str()).collect::<Vec<_>>(), ["compact", "user", "system"]);
        assert!(view[2].body["text"].as_str().unwrap().starts_with("Today is"));
        assert_eq!(arity.usage(conv.into())?.last_input_tokens, 3000);

        // A reset: the model starts from nothing but the sections, and the thread still shows it all.
        arity.reset_context(conv.into(), Some("new topic".into()))?;
        say("hello again")?;
        let view = seen.borrow().last().unwrap().clone();
        assert_eq!(view.iter().map(|e| e.kind.as_str()).collect::<Vec<_>>(), ["user", "system"]);
        assert_eq!(view[0].body["text"], "hello again");
        let msgs = arity.messages(conv.into())?;
        assert!(msgs.iter().any(|m| m.kind == MessageKind::Compaction && m.text.starts_with("Context reset") && m.text.ends_with("new topic")));
        assert_eq!(msgs.iter().filter(|m| m.kind == MessageKind::User).count(), 4);
        assert!(arity.reset_context("nobody".into(), None).unwrap_err().to_string().contains("Nothing to reset"));

        // Forking from before the compact gets the raw history back, as the model reads it too.
        let first_reply = after.iter().find(|e| e.kind == "assistant").unwrap().id;
        arity.fork(first_reply, "chat-1-fork".into())?;
        assert_eq!(db.chain_for_model("chat-1-fork")?.iter().filter(|e| e.kind != "system").count(), 2);
        assert_eq!(arity.usage("chat-1-fork".into())?.compacted_entries, 0);

        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    fn scratch_arity(name: &str) -> anyhow::Result<(PathBuf, Arc<Arity>, Journal)> {
        let dir = std::env::temp_dir().join(format!("arity-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let arity = Arity::new(Some(dir.to_string_lossy().into_owned()));
        let db = arity.journal()?;
        Ok((dir, arity, db))
    }

    fn says(text: &str) -> Reply {
        Reply { text: text.into(), tool_calls: vec![], interrupted: false, usage: None }
    }

    /// Stop while a sub-task's report and a user's message wait for the turn:
    /// the user may take their message back, the report is never dropped, and
    /// it lands unanswered in the journal, where `continue_turn` answers it
    /// without anything written to ask.
    #[test]
    fn a_report_waiting_at_stop_survives_and_continue_answers_it() -> anyhow::Result<()> {
        let (dir, arity, db) = scratch_arity("stop-report")?;
        db.append("chat", "user", &json!({ "text": "check the sources" }), None)?;
        db.append("chat", "assistant", &json!({ "text": "Handed off." }), Some("test:m"))?;
        let mut lease = turn::Lease::take(&db, "chat", "test:m")?.unwrap();
        let report = json!({ "text": "Task 1, \"sources\", is finished. The result:\n\nAll fine.", "task": 1, "trial": 1 });
        assert!(!turn::deliver(db.conn(), "chat", "report", &report, Delivery::AfterTurn)?);
        turn::send_as(&db, "chat", "never mind", Delivery::AfterTurn)?;
        assert!(arity.stop("chat".into())?);
        assert_eq!(arity.discard_queued("chat".into())?, 1, "the user's message is taken back");
        let reports: i64 = db.conn().query_row("SELECT count(*) FROM queued WHERE kind = 'report'", [], |r| r.get(0))?;
        assert_eq!(reports, 1, "the report is not");
        assert!(lease.release(&db, true)?, "a stopped turn ends");
        drop(lease);
        let chain = db.chain("chat")?;
        assert_eq!(chain.last().unwrap().kind, "report", "kept, in the journal, unanswered");
        assert!(arity.can_continue("chat".into())?);
        assert!(arity.threads()?.iter().any(|t| t.name == "chat" && t.can_continue));

        let model = |_: &str, view: &[Entry], _: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            let last = view.iter().rev().find(|e| e.kind != "system").unwrap();
            assert_eq!(last.kind, "report");
            Ok(says("The sources check out."))
        };
        assert_eq!(arity.continue_in(&db, "chat", None, Some(&model), &mut |_| {})?, "answered");
        let kinds: Vec<String> = db.chain("chat")?.into_iter().filter(|e| e.kind != "system").map(|e| e.kind).collect();
        assert_eq!(kinds, ["user", "assistant", "report", "assistant"], "no message was written to ask");
        assert!(!arity.can_continue("chat".into())?);
        let nothing = arity.continue_in(&db, "chat", None, Some(&model), &mut |_| {}).unwrap_err().to_string();
        assert!(nothing.contains("Nothing to continue"), "{nothing}");
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// A small git repo with one commit: `keep.txt` and `sub/nested.txt`.
    fn git_repo(name: &str) -> PathBuf {
        let repo = std::env::temp_dir().join(format!("arity-wt-repo-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&repo);
        std::fs::create_dir_all(repo.join("sub")).unwrap();
        std::fs::write(repo.join("keep.txt"), "base\n").unwrap();
        std::fs::write(repo.join("sub/nested.txt"), "nested\n").unwrap();
        let git = |args: &[&str]| {
            let out = std::process::Command::new("git").arg("-C").arg(&repo).args(args).output().unwrap();
            assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
        };
        git(&["init"]);
        git(&["config", "user.email", "arity@example.invalid"]);
        git(&["config", "user.name", "Arity Test"]);
        git(&["add", "."]);
        git(&["commit", "-m", "base"]);
        std::fs::canonicalize(&repo).unwrap()
    }

    /// A worker that writes `file` with `text`, edits `keep.txt`, and finishes.
    fn coder(file: &'static str, text: &'static str) -> impl Fn(&str, &[Entry], &[tools::ToolSpec], &mut model::Sink) -> anyhow::Result<Reply> {
        move |_, chain, tools, _| {
            assert!(["write_file", "edit_file", "bash"].iter().all(|n| tools.iter().any(|t| t.name == *n)), "coding tools offered");
            let last = chain.iter().rev().find(|e| e.kind != "system").unwrap();
            let call = |name: &str, args: serde_json::Value| Reply {
                text: String::new(),
                tool_calls: vec![ToolCall { id: format!("{name}-{file}"), name: name.into(), arguments: args.to_string() }],
                interrupted: false,
                usage: None,
            };
            Ok(match (last.kind.as_str(), last.body["name"].as_str()) {
                ("user", _) => call("write_file", json!({ "path": file, "content": text })),
                ("tool", Some("write_file")) => call("edit_file", json!({ "path": "keep.txt", "old_text": "base", "new_text": format!("base+{file}") })),
                ("tool", Some("edit_file")) => call("finish", json!({ "report": format!("wrote {file}") })),
                other => panic!("coder did not expect {other:?}"),
            })
        }
    }

    /// A coding task filed in a chat attached to a git repo runs in its own
    /// worktree: the checkout stays untouched while it runs and while it waits
    /// for a rating, the exact diff is there to review, and applying it lands
    /// the changes in the checkout. A sub-task it files inherits the real repo.
    #[test]
    fn a_coding_run_works_in_its_own_worktree_until_its_diff_is_applied() -> anyhow::Result<()> {
        use crate::dispatch::{Did, work_once_scripted};
        use crate::leaderboard::{Candidate, Reach};
        let (dir, arity, db) = scratch_arity("worktree-apply")?;
        std::fs::write(dir.join("config.json"), json!({ "workers": [] }).to_string())?;
        let repo = git_repo("apply");
        let only = vec![Candidate { model: "lab:coder".into(), reach: Reach::Local }];
        arity.set_folder("chat-1".into(), Some(repo.to_string_lossy().into_owned()))?;
        let f = TaskFields { title: "add hello".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(db.conn(), &f, Some("chat-1"))?;
        assert_eq!(board::task(db.conn(), task)?.fields.dir.as_deref(), repo.to_str());
        assert!(arity.worktree_diff("task-1".into())?.is_none(), "nothing before it is claimed");

        let worker = coder("hello.txt", "hi\n");
        let did = work_once_scripted(&db, &dir, &only, &worker)?;
        assert!(matches!(did, Did::Ran { task: 1, trial: 1 }), "{:?}", did.describe());

        // It worked in an isolated worktree under the data folder; the checkout is untouched.
        let work_dir = std::fs::canonicalize(arity.folder("task-1".into())?.unwrap())?;
        assert!(work_dir.starts_with(std::fs::canonicalize(&dir)?.join("worktrees")), "{}", work_dir.display());
        assert_eq!(std::fs::read_to_string(work_dir.join("hello.txt"))?, "hi\n");
        assert_eq!(std::fs::read_to_string(work_dir.join("keep.txt"))?, "base+hello.txt\n");
        assert_eq!(std::fs::read_to_string(repo.join("keep.txt"))?, "base\n");
        assert!(!repo.join("hello.txt").exists());
        assert_eq!(arity.folder("chat-1".into())?.as_deref(), repo.to_str(), "the chat still reads the real repo");
        let status = std::process::Command::new("git").arg("-C").arg(&repo).args(["status", "--porcelain"]).output()?;
        assert!(status.stdout.is_empty(), "the checkout is clean while the run waits for a rating");
        assert_eq!(board::task(db.conn(), task)?.run_state, Some(RunState::NeedsRating));

        // A sub-task it files inherits the real repo, so its own worktree is a sibling and applies there.
        let sub = board::add_task(db.conn(), &TaskFields { title: "sub".into(), assignee: board::AGENT.into(), ..Default::default() }, Some("task-1"))?;
        assert_eq!(board::task(db.conn(), sub)?.fields.dir.as_deref(), repo.to_str());

        // The exact diff, for review before rating.
        let d = arity.worktree_diff("task-1".into())?.unwrap();
        assert_eq!((d.status.as_str(), d.files_changed, d.insertions, d.deletions), ("active", 2, 2, 1));
        assert_eq!(Path::new(&d.repo_root), repo);
        let kinds: Vec<(&str, &str)> = d.files.iter().map(|f| (f.path.as_str(), f.status.as_str())).collect();
        assert!(kinds.contains(&("hello.txt", "added")) && kinds.contains(&("keep.txt", "modified")), "{kinds:?}");
        assert!(d.patch.contains("+hi") && d.patch.contains("+base+hello.txt") && d.patch.contains("-base"), "{}", d.patch);
        assert!(!d.truncated);

        // Applying lands it in the checkout, removes the worktree, and the status stays readable.
        let applied = arity.apply_worktree("task-1".into())?;
        assert_eq!(applied.files_applied, 2);
        assert_eq!(std::fs::read_to_string(repo.join("hello.txt"))?, "hi\n");
        assert_eq!(std::fs::read_to_string(repo.join("keep.txt"))?, "base+hello.txt\n");
        assert!(!work_dir.exists());
        let after = arity.worktree_diff("task-1".into())?.unwrap();
        assert_eq!((after.status.as_str(), after.files_changed, after.patch.as_str()), ("applied", 0, ""));
        assert!(arity.apply_worktree("task-1".into()).is_err(), "not twice");
        assert!(!arity.discard_worktree("task-1".into())?);
        assert_eq!(arity.folder("task-1".into())?.as_deref(), repo.to_str(), "the folder is the repo's again");
        // Rating still works afterwards.
        arity.rate(1, 5, "fine".into())?;

        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&repo);
        Ok(())
    }

    /// Two runs on one repo get distinct worktrees and never see each other's
    /// files; discarding one leaves the checkout and the other alone.
    #[test]
    fn parallel_runs_get_separate_worktrees_and_discarding_one_touches_nothing() -> anyhow::Result<()> {
        use crate::dispatch::{Did, work_once_scripted};
        use crate::leaderboard::{Candidate, Reach};
        let (dir, arity, db) = scratch_arity("worktree-parallel")?;
        std::fs::write(dir.join("config.json"), json!({ "workers": [] }).to_string())?;
        let repo = git_repo("parallel");
        let only = vec![Candidate { model: "lab:coder".into(), reach: Reach::Local }];
        for title in ["first", "second"] {
            let f = TaskFields {
                title: title.into(),
                assignee: board::AGENT.into(),
                dir: Some(repo.to_string_lossy().into_owned()),
                ..Default::default()
            };
            board::add_task(db.conn(), &f, None)?;
        }
        // Both are open and neither is rated at once: the second starts while the first's worktree lives.
        let first = coder("one.txt", "1\n");
        let second = coder("two.txt", "2\n");
        assert!(matches!(work_once_scripted(&db, &dir, &only, &first)?, Did::Ran { task: 1, .. }));
        // The first result is delivered to the user's inbox before the next task is claimed.
        assert!(matches!(work_once_scripted(&db, &dir, &only, &second)?, Did::Reported { task: 1, parent: None }));
        assert!(matches!(work_once_scripted(&db, &dir, &only, &second)?, Did::Ran { task: 2, .. }));

        let d1 = arity.worktree_diff("task-1".into())?.unwrap();
        let d2 = arity.worktree_diff("task-2".into())?.unwrap();
        assert_ne!(d1.work_dir, d2.work_dir);
        assert!(Path::new(&d1.work_dir).is_dir() && Path::new(&d2.work_dir).is_dir());
        assert_eq!(d1.files.iter().map(|f| f.path.as_str()).collect::<Vec<_>>(), ["keep.txt", "one.txt"]);
        assert_eq!(d2.files.iter().map(|f| f.path.as_str()).collect::<Vec<_>>(), ["keep.txt", "two.txt"]);
        assert!(!Path::new(&d1.work_dir).join("two.txt").exists() && !Path::new(&d2.work_dir).join("one.txt").exists());
        assert_eq!(d1.base_commit, d2.base_commit);

        // Discarding the first leaves the checkout and the second as they were.
        assert!(arity.discard_worktree("task-1".into())?);
        assert!(!Path::new(&d1.work_dir).exists());
        assert_eq!(arity.worktree_diff("task-1".into())?.unwrap().status, "discarded");
        assert_eq!(std::fs::read_to_string(repo.join("keep.txt"))?, "base\n");
        assert!(!repo.join("one.txt").exists() && !repo.join("two.txt").exists());
        let d2 = arity.worktree_diff("task-2".into())?.unwrap();
        assert_eq!((d2.status.as_str(), d2.files_changed), ("active", 2));

        // The second still applies, alone.
        arity.apply_worktree("task-2".into())?;
        assert_eq!(std::fs::read_to_string(repo.join("two.txt"))?, "2\n");
        assert!(!repo.join("one.txt").exists());
        assert_eq!(std::fs::read_to_string(repo.join("keep.txt"))?, "base+two.txt\n");

        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&repo);
        Ok(())
    }

    /// Folders outside git (and repos with no commits) are worked in directly.
    #[test]
    fn a_run_in_a_plain_folder_has_no_worktree() -> anyhow::Result<()> {
        use crate::dispatch::{Did, work_once_scripted};
        use crate::leaderboard::{Candidate, Reach};
        let (dir, arity, db) = scratch_arity("worktree-plain")?;
        std::fs::write(dir.join("config.json"), json!({ "workers": [] }).to_string())?;
        let plain = std::fs::canonicalize({
            let p = std::env::temp_dir().join(format!("arity-wt-plain-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&p);
            std::fs::create_dir_all(&p)?;
            std::fs::write(p.join("keep.txt"), "base\n")?;
            p
        })?;
        let only = vec![Candidate { model: "lab:coder".into(), reach: Reach::Local }];
        let f = TaskFields { title: "t".into(), assignee: board::AGENT.into(), dir: Some(plain.to_string_lossy().into_owned()), ..Default::default() };
        board::add_task(db.conn(), &f, None)?;
        let did = work_once_scripted(&db, &dir, &only, &coder("a.txt", "a\n"))?;
        assert!(matches!(did, Did::Ran { .. }), "{:?}", did.describe());
        assert_eq!(arity.folder("task-1".into())?.as_deref(), plain.to_str());
        assert_eq!(std::fs::read_to_string(plain.join("a.txt"))?, "a\n");
        assert!(arity.worktree_diff("task-1".into())?.is_none());
        assert!(!arity.discard_worktree("task-1".into())?);
        assert!(arity.apply_worktree("task-1".into()).is_err());
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&plain);
        Ok(())
    }

    /// A worker run the user stopped mid-reply waits as paused; continuing it
    /// carries on the cut-off reply, and only its real finish reaches the parent.
    #[test]
    fn a_stopped_run_continues_to_its_real_finish() -> anyhow::Result<()> {
        let (dir, arity, db) = scratch_arity("continue-run")?;
        db.append("chat-1", "assistant", &json!({ "text": "Handed off." }), Some("chat:front"))?;
        let f = TaskFields { title: "write an essay".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(db.conn(), &f, Some("chat-1"))?;
        db.conn().execute("INSERT INTO trials (task, conversation, model) VALUES (?1, 'task-1', 'secret:w')", [task])?;
        board::set_state(db.conn(), task, TaskState::Assigned)?;
        db.append("task-1", "user", &json!({ "text": "write an essay" }), None)?;
        db.append("task-1", "assistant", &json!({ "text": "The first half", "interrupted": true }), Some("secret:w"))?;
        dispatch::settle(&db, 1, task, "task-1", false, agent::Outcome::Stopped)?;
        let thread = |name: &str| arity.threads().unwrap().into_iter().find(|t| t.name == name).unwrap();
        let t = thread("task-1");
        assert_eq!((t.group, t.task.as_ref().unwrap().run_state, t.can_continue), (ThreadGroup::NeedsYou, Some(RunState::Paused), true));
        assert_eq!(thread("chat-1").active_children, 0, "a paused run is not going");

        let worker = |_: &str, view: &[Entry], _: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            assert_eq!(view.last().unwrap().kind, "system", "told it was cut off");
            let finish = ToolCall { id: "f".into(), name: "finish".into(), arguments: json!({ "report": "The whole essay." }).to_string() };
            Ok(Reply { text: "and the second half.".into(), tool_calls: vec![finish], interrupted: false, usage: None })
        };
        assert_eq!(arity.continue_in(&db, "task-1", Some("ignored:model"), Some(&worker), &mut |_| {})?, "finished");
        let t = thread("task-1");
        assert_eq!((t.task.as_ref().unwrap().run_state, t.can_continue), (Some(RunState::NeedsRating), false));
        assert_eq!(t.task.unwrap().result.as_deref(), Some("The whole essay."));
        let report = db.chain("chat-1")?.last().unwrap().clone();
        assert!(report.kind == "report" && report.body["text"].as_str().unwrap().contains("The whole essay."));
        assert!(!report.body["text"].as_str().unwrap().contains("The first half"));
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    #[test]
    fn stop_subtree_counts_what_it_stopped_and_threads_show_active_children() -> anyhow::Result<()> {
        let (dir, arity, db) = scratch_arity("stop-subtree")?;
        db.append("chat-1", "assistant", &json!({ "text": "Handed off." }), Some("chat:front"))?;
        let agent = |t: &str| TaskFields { title: t.into(), assignee: board::AGENT.into(), ..Default::default() };
        board::add_task(db.conn(), &agent("parent"), Some("chat-1"))?;
        db.conn().execute("INSERT INTO trials (task, conversation, model) VALUES (1, 'task-1', 'w:a')", [])?;
        board::set_state(db.conn(), 1, TaskState::Assigned)?;
        board::add_task(db.conn(), &agent("child"), Some("task-1"))?;
        db.conn().execute("INSERT INTO trials (task, conversation, model) VALUES (2, 'task-2', 'w:b')", [])?;
        board::set_state(db.conn(), 2, TaskState::Assigned)?;
        let active = |name: &str| arity.threads().unwrap().into_iter().find(|t| t.name == name).unwrap().active_children;
        assert_eq!((active("chat-1"), active("task-1"), active("task-2")), (2, 1, 0));
        assert_eq!(arity.stop_subtree("chat-1".into())?, 2, "two runs; the idle chat had nothing to stop");
        assert_eq!((active("chat-1"), active("task-1")), (0, 0));
        assert_eq!(arity.stop_subtree("chat-1".into())?, 0, "nothing left to stop");
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    #[test]
    fn sidebar_folders_cover_unsent_chats_and_unstarted_tasks() -> anyhow::Result<()> {
        let (dir, arity, db) = scratch_arity("sidebar-folder")?;
        let path = dir.canonicalize()?.to_string_lossy().into_owned();
        arity.set_folder("chat-1".into(), Some(path.clone()))?;
        let threads = arity.threads()?;
        assert_eq!(threads.len(), 1);
        assert_eq!(threads[0].title, "New chat");
        assert_eq!(threads[0].folder.as_ref(), Some(&path));
        db.append("chat-1", "user", &json!({ "text": "Planning" }), None)?;
        board::add_task(db.conn(), &TaskFields { title: "Review".into(), ..Default::default() }, Some("chat-1"))?;
        arity.set_folder("chat-1".into(), None)?;
        let threads = arity.threads()?;
        assert_eq!(threads.iter().find(|t| t.name == "chat-1").unwrap().folder, None);
        assert_eq!(threads.iter().find(|t| t.title == "Review").unwrap().folder.as_ref(), Some(&path));
        Ok(())
    }

    /// A folder outside git for a conversation's shell tool, where `runs.txt`
    /// gets a line each time the scripted command actually runs.
    fn plain_folder(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("arity-shell-folder-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        std::fs::canonicalize(&p).unwrap()
    }

    fn runs_in(folder: &Path) -> usize {
        std::fs::read_to_string(folder.join("runs.txt")).map_or(0, |s| s.lines().count())
    }

    /// A chat model that runs `command` in bash when asked to "do it", and
    /// otherwise answers with what the last tool result said. Counts its calls.
    fn shell_chat<'a>(
        command: &'static str,
        calls: &'a std::cell::Cell<usize>,
    ) -> impl Fn(&str, &[Entry], &[tools::ToolSpec], &mut model::Sink) -> anyhow::Result<Reply> + 'a {
        move |_, view, _, _| {
            calls.set(calls.get() + 1);
            let last = view.iter().rev().find(|e| e.kind != "system").unwrap();
            if last.kind == "user" && last.body["text"] == "do it" {
                let call = ToolCall { id: "sh-1".into(), name: "bash".into(), arguments: json!({ "command": command }).to_string() };
                return Ok(Reply { text: String::new(), tool_calls: vec![call], interrupted: false, usage: None });
            }
            let tool = view.iter().rev().find(|e| e.kind == "tool").unwrap();
            Ok(says(&format!("Done: {}", tool.body["output"].as_str().unwrap_or_default().lines().next().unwrap_or_default())))
        }
    }

    /// Ask, park, approve and carry on, in a chat: the turn parks with its
    /// lease released and the command not run, then allowing it runs it once
    /// and the reply completes. A message sent while it waits joins after the
    /// tool result, never between the call and its result.
    fn approve_flow(arity: &Arity, db: &Journal, conv: &str, folder: &Path, command: &'static str, hook: &str) -> anyhow::Result<()> {
        arity.set_folder(conv.into(), Some(folder.to_string_lossy().into_owned()))?;
        let calls = std::cell::Cell::new(0);
        let model = shell_chat(command, &calls);
        let thread = |name: &str| arity.threads().unwrap().into_iter().find(|t| t.name == name).unwrap();

        db.append(conv, "user", &json!({ "text": "do it" }), None)?;
        let outcome = arity.run_outcome(db, conv, "test:m", Some(&model), false, &mut |_| {})?;
        assert_eq!(outcome, Some(agent::Outcome::NeedsApproval), "{:#?}", db.chain(conv)?);
        assert!(!turn::running(db.conn())?.contains(&conv.to_string()), "the lease is released");
        assert_eq!(runs_in(folder), 0, "the command has not run");
        assert!(db.chain(conv)?.iter().all(|e| e.kind != "tool"), "no tool entry while it waits");

        let pending = arity.pending_approvals(Some(conv.into()))?;
        assert_eq!(pending.len(), 1);
        let a = &pending[0];
        assert_eq!((a.conversation.as_str(), a.call_id.as_str(), a.tool.as_str(), a.hook.as_str()), (conv, "sh-1", "bash", hook));
        assert_eq!(serde_json::from_str::<serde_json::Value>(&a.args)?["command"], command);
        assert!(arity.pending_approvals(None)?.iter().any(|p| p.id == a.id));
        let t = thread(conv);
        assert_eq!((t.group, t.pending_approvals, t.can_continue), (ThreadGroup::NeedsYou, 1, false));
        let waits = arity.continue_in(db, conv, None, Some(&model), &mut |_| {}).unwrap_err().to_string();
        assert!(waits.contains("allow or deny"), "{waits}");

        // Sent while it waits: queued for the turn that carries on.
        turn::send_as(db, conv, "and thanks", Delivery::Steer)?;
        assert!(db.chain(conv)?.iter().all(|e| e.body["text"] != "and thanks"));

        let before = db.revision()?;
        let decided = arity.approve_tool(a.id, Some("ok".into()))?;
        assert_eq!(decided.id, a.id);
        assert_ne!(db.revision()?, before, "the app sees the decision");
        assert!(arity.pending_approvals(Some(conv.into()))?.is_empty());
        assert!(arity.approve_tool(a.id, None).is_err(), "decided once");
        assert!(arity.can_continue(conv.into())?, "a decided chat can be carried on");
        let wake: i64 = db.conn().query_row("SELECT wake_at FROM conversations WHERE name = ?1", [conv], |r| r.get(0))?;
        assert_eq!(wake, 0, "left to whoever decided, not raced by the dispatcher");

        assert_eq!(arity.continue_in(db, conv, None, Some(&model), &mut |_| {})?, "answered");
        assert_eq!(runs_in(folder), 1, "it ran exactly once");
        let kinds: Vec<String> = db.chain(conv)?.into_iter().filter(|e| !matches!(e.kind.as_str(), "system" | "tool_intent")).map(|e| e.kind).collect();
        assert_eq!(kinds, ["user", "assistant", "tool", "user", "assistant"]);
        let chain = db.chain(conv)?;
        let tool = chain.iter().find(|e| e.kind == "tool").unwrap();
        assert!(!tool.body["output"].as_str().unwrap().contains("denied"));
        assert!(chain.last().unwrap().body["text"].as_str().unwrap().starts_with("Done:"));
        assert_eq!(calls.get(), 2, "one call to ask for the tool, one to answer");
        let t = thread(conv);
        assert_eq!((t.group, t.pending_approvals, t.can_continue), (ThreadGroup::Chat, 0, false));
        assert!(!approvals::has_decided(db.conn(), conv)?, "the decision is consumed");
        Ok(())
    }

    #[test]
    fn a_chat_parks_on_the_safety_guard_and_an_allowed_push_runs_once() -> anyhow::Result<()> {
        let (dir, arity, db) = scratch_arity("approve-guard")?;
        let folder = plain_folder("guard");
        approve_flow(&arity, &db, "chat-1", &folder, "echo run >> runs.txt && git push origin main", "safety_guard")?;
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&folder);
        Ok(())
    }

    #[test]
    fn a_chat_parks_on_a_command_hook_that_asks_and_an_allowed_call_runs_once() -> anyhow::Result<()> {
        let (dir, arity, db) = scratch_arity("approve-hook")?;
        let folder = plain_folder("hook");
        arity.save_hook(HookConfig {
            name: "ask_bash".into(),
            event: HookEvent::BeforeTool,
            tool: Some("bash".into()),
            command: r#"printf '%s\n' '{"action":"ask","reason":"every shell command needs a look"}'"#.into(),
            enabled: true,
        })?;
        approve_flow(&arity, &db, "chat-1", &folder, "echo run >> runs.txt", "ask_bash")?;
        assert_eq!(db.chain("chat-1")?.iter().filter(|e| e.kind == "tool").count(), 1);
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&folder);
        Ok(())
    }

    #[test]
    fn a_denied_call_never_runs_and_the_model_hears_why() -> anyhow::Result<()> {
        let (dir, arity, db) = scratch_arity("approve-deny")?;
        let folder = plain_folder("deny");
        arity.set_folder("chat-1".into(), Some(folder.to_string_lossy().into_owned()))?;
        let calls = std::cell::Cell::new(0);
        let model = shell_chat("echo run >> runs.txt; sudo true", &calls);
        db.append("chat-1", "user", &json!({ "text": "do it" }), None)?;
        assert_eq!(arity.run_outcome(&db, "chat-1", "test:m", Some(&model), false, &mut |_| {})?, Some(agent::Outcome::NeedsApproval));
        let a = arity.pending_approvals(Some("chat-1".into()))?.remove(0);
        assert!(a.reason.contains("sudo"), "{}", a.reason);

        arity.deny_tool(a.id, Some("  not with sudo ".into()))?;
        assert!(arity.can_continue("chat-1".into())?);
        assert_eq!(arity.continue_in(&db, "chat-1", None, Some(&model), &mut |_| {})?, "answered");
        assert_eq!(runs_in(&folder), 0, "a denied call never runs");
        let chain = db.chain("chat-1")?;
        let tool = chain.iter().find(|e| e.kind == "tool").unwrap();
        assert_eq!((tool.body["output"].as_str(), tool.body["is_error"].as_bool()), (Some("Tool call denied by user: not with sudo"), Some(true)));
        assert_eq!(chain.last().unwrap().body["text"], "Done: Tool call denied by user: not with sudo");
        assert!(chain.iter().all(|e| e.kind != "tool_intent"));
        assert!(!arity.can_continue("chat-1".into())?);
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&folder);
        Ok(())
    }

    /// A worker run that asks parks as waiting, is not woken by the dispatcher
    /// or anything else while it waits, and allowing the call wakes it for the
    /// next pass, which runs the call once and finishes.
    #[test]
    fn a_worker_run_parks_until_allowed_and_then_finishes() -> anyhow::Result<()> {
        use crate::dispatch::{Did, work_once_scripted};
        use crate::leaderboard::{Candidate, Reach};
        let (dir, arity, db) = scratch_arity("approve-worker")?;
        std::fs::write(dir.join("config.json"), json!({ "workers": [] }).to_string())?;
        let folder = plain_folder("worker");
        let only = vec![Candidate { model: "lab:worker".into(), reach: Reach::Local }];
        let f = TaskFields { title: "push it".into(), assignee: board::AGENT.into(), dir: Some(folder.to_string_lossy().into_owned()), ..Default::default() };
        let task = board::add_task(db.conn(), &f, None)?;
        let worker = |_: &str, view: &[Entry], _: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            let last = view.iter().rev().find(|e| e.kind != "system").unwrap();
            let (name, args) = match last.kind.as_str() {
                "user" => ("bash", json!({ "command": "echo run >> runs.txt && git push origin main" })),
                "tool" => ("finish", json!({ "report": "pushed" })),
                other => panic!("the worker did not expect {other}"),
            };
            let call = ToolCall { id: format!("{name}-1"), name: name.into(), arguments: args.to_string() };
            Ok(Reply { text: String::new(), tool_calls: vec![call], interrupted: false, usage: None })
        };
        let trial_flags = || -> anyhow::Result<(bool, i64)> {
            Ok(db.conn().query_row("SELECT waiting, lease_until FROM trials WHERE id = 1", [], |r| Ok((r.get(0)?, r.get(1)?)))?)
        };
        let thread = || arity.threads().unwrap().into_iter().find(|t| t.name == "task-1").unwrap();

        let did = work_once_scripted(&db, &dir, &only, &worker)?;
        assert!(matches!(did, Did::NeedsApproval { task: 1, trial: 1 }), "{:?}", did.describe());
        assert_eq!(trial_flags()?, (true, 0));
        assert_eq!(board::task(db.conn(), task)?.run_state, Some(RunState::NeedsApproval));
        let t = thread();
        assert_eq!((t.group, t.pending_approvals, t.can_continue), (ThreadGroup::NeedsYou, 1, false));
        assert_eq!(runs_in(&folder), 0);
        assert!(!turn::running(db.conn())?.contains(&"task-1".to_string()));
        assert!(board::set_done(db.conn(), task, true).is_err(), "an agent is still on it");

        // Nothing wakes it while it waits: not the dispatcher, not a report's wake.
        assert!(matches!(work_once_scripted(&db, &dir, &only, &worker)?, Did::Nothing));
        board::wake(db.conn(), "task-1")?;
        assert_eq!(trial_flags()?, (true, 0));
        assert!(matches!(work_once_scripted(&db, &dir, &only, &worker)?, Did::Nothing));
        assert!(db.chain("task-1")?.iter().all(|e| e.kind != "notice"), "not told its sub-tasks settled");

        let a = arity.pending_approvals(Some("task-1".into()))?.remove(0);
        assert_eq!(a.hook, "safety_guard");
        arity.approve_tool(a.id, None)?;
        assert!(!trial_flags()?.0, "allowing it wakes the run");
        assert!(!arity.can_continue("task-1".into())?, "the dispatcher carries it on");

        let did = work_once_scripted(&db, &dir, &only, &worker)?;
        assert!(matches!(did, Did::Ran { task: 1, trial: 1 }), "{:?}", did.describe());
        assert_eq!(runs_in(&folder), 1, "it ran exactly once");
        assert_eq!(board::task(db.conn(), task)?.run_state, Some(RunState::NeedsRating));
        assert_eq!(board::task(db.conn(), task)?.result.as_deref(), Some("pushed"));
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&folder);
        Ok(())
    }

    #[test]
    fn stopping_a_subtree_denies_the_calls_waiting_below_it() -> anyhow::Result<()> {
        let (dir, arity, db) = scratch_arity("approve-stop")?;
        db.append("chat-1", "assistant", &json!({ "text": "Handed off." }), Some("chat:front"))?;
        let agent = |t: &str| TaskFields { title: t.into(), assignee: board::AGENT.into(), ..Default::default() };
        board::add_task(db.conn(), &agent("parent"), Some("chat-1"))?;
        db.conn().execute("INSERT INTO trials (task, conversation, model) VALUES (1, 'task-1', 'w:a')", [])?;
        board::set_state(db.conn(), 1, TaskState::Assigned)?;
        board::add_task(db.conn(), &agent("child"), Some("task-1"))?;
        db.conn().execute("INSERT INTO trials (task, conversation, model, waiting) VALUES (2, 'task-2', 'w:b', 1)", [])?;
        board::set_state(db.conn(), 2, TaskState::Assigned)?;
        let ask = json!({ "command": "git push" });
        approvals::request(db.conn(), "task-2", "c-2", "bash", &ask, "safety_guard", "git push")?;
        approvals::request(db.conn(), "elsewhere", "c-3", "bash", &ask, "safety_guard", "git push")?;
        assert_eq!(board::task(db.conn(), 2)?.run_state, Some(RunState::NeedsApproval));

        assert_eq!(arity.stop_subtree("task-1".into())?, 2);
        assert_eq!(approvals::check(db.conn(), "task-2", "c-2")?, approvals::PriorDecision::Denied("stopped by the user".into()));
        let left: Vec<String> = arity.pending_approvals(None)?.into_iter().map(|a| a.conversation).collect();
        assert_eq!(left, ["elsewhere"], "only the subtree's calls are denied");
        assert_eq!(board::task(db.conn(), 2)?.run_state, Some(RunState::Paused));
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// The whole recursive loop on scripted models: a chat delegates, a blindly
    /// picked worker hands part on and waits, the sub-task's run is killed
    /// after a committed tool call and taken over from the journal without
    /// repeating it, the parent worker integrates the result and finishes while
    /// the post-use hook grades the sub-task, and then the dispatcher wakes the
    /// chat to integrate the final result and grade the worker via the same
    /// hook. No model name shows before rating, except through `reveal`.
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
            usage: None,
        };
        let says = |text: &str| Reply { text: text.into(), tool_calls: vec![], interrupted: false, usage: None };
        let last_tool = |chain: &[Entry]| {
            let e = chain.iter().rev().find(|e| e.kind != "system").unwrap();
            (e.kind.clone(), e.body["name"].as_str().unwrap_or_default().to_string())
        };

        // The chat's model: hands the work to an agent, later wakes to present the result,
        // and answers the post-use rating hook.
        let chat_calls = Cell::new(0);
        let chat = |_: &str, chain: &[Entry], tools: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            chat_calls.set(chat_calls.get() + 1);
            let last = chain.iter().rev().find(|e| e.kind != "system").unwrap();
            if last.body["hook"] == "rate" {
                assert_eq!(tools.iter().map(|t| t.name).collect::<Vec<_>>(), [hooks::SUBMIT_RATING]);
                return Ok(call(hooks::SUBMIT_RATING, json!({ "score": 5, "note": "thorough" })));
            }
            assert!(!tools.iter().any(|t| t.name == "rate"), "rate is never in the normal toolset");
            Ok(match last.kind.as_str() {
                "user" => call("add_task", json!({ "title": "Check the sources", "brief": "Check every citation in draft.md.", "assignee": "agent" })),
                "tool" => says("Handed to an agent."),
                "report" => says("All sources verified; footnote 3 holds."),
                other => panic!("chat-1 did not expect {other}"),
            })
        };

        // Both workers, told apart by their brief. Worker 2 is killed on its second call, once.
        let crash = Cell::new(true);
        let worker_2_calls = Cell::new(0);
        let workers = |_: &str, chain: &[Entry], tools: &[tools::ToolSpec], _: &mut model::Sink| -> anyhow::Result<Reply> {
            let last = chain.iter().rev().find(|e| e.kind != "system").unwrap();
            if last.body["hook"] == "rate" {
                assert_eq!(tools.iter().map(|t| t.name).collect::<Vec<_>>(), [hooks::SUBMIT_RATING]);
                return Ok(call(hooks::SUBMIT_RATING, json!({ "score": 4, "note": "solid" })));
            }
            assert!(!tools.iter().any(|t| t.name == "rate"), "rate is never in the normal toolset");
            let brief = chain[0].body["text"].as_str().unwrap_or_default();
            let (kind, name) = last_tool(chain);
            if brief.starts_with("Check the sources") {
                return Ok(match (kind.as_str(), name.as_str()) {
                    ("user", _) => call(
                        "add_task",
                        json!({ "title": "Verify footnote 3", "brief": "Does footnote 3 match its source?", "assignee": "agent" }),
                    ),
                    ("tool", "add_task") => says("Waiting for the footnote check."),
                    ("report", _) => call("finish", json!({ "report": "All sources verified; footnote 3 holds." })),
                    other => panic!("task-1 did not expect {other:?}"),
                });
            }
            worker_2_calls.set(worker_2_calls.get() + 1);
            Ok(match (kind.as_str(), name.as_str()) {
                ("user", _) => call("list_tasks", json!({})),
                ("tool", "list_tasks") if crash.replace(false) => panic!("the dispatcher's process is killed"),
                ("tool", "list_tasks") => call("finish", json!({ "report": "Footnote 3 matches page 12." })),
                other => panic!("task-2 did not expect {other:?}"),
            })
        };

        // 1. chat-1 delegates task 1; the dispatcher claims it onto task-1 with a blind pick.
        turn::send_as(&db, "chat-1", "Please check the sources in draft.md.", Delivery::Steer)?;
        arity.run_resolved(&db, "chat-1", "chat:front", Some(&chat), &mut |_| {})?;
        assert_eq!(chat_calls.get(), 2);
        let t1 = board::task(db.conn(), 1)?;
        assert_eq!((t1.fields.assignee.as_str(), t1.created_by.as_str(), t1.origin.as_deref()), ("agent", "conversations/chat-1", Some("chat-1")));
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
        assert_eq!(worker_2_calls.get(), 3, "one call after the restart, not two");
        let chain2 = db.chain("task-2")?;
        assert_eq!(chain2.iter().filter(|e| e.kind == "tool" && e.body["name"] == "list_tasks").count(), 1);
        let trials: i64 = db.conn().query_row("SELECT count(*) FROM trials WHERE task = 2", [], |r| r.get(0))?;
        assert_eq!(trials, 1, "the same run carried on");

        // 4. The report wakes task-1, which finishes cleanly; the post-use hook grades task 2.
        let did = work_once_scripted(&db, &dir, &[], &workers)?;
        assert!(matches!(&did, Did::Reported { task: 2, parent: Some(p) } if p == "task-1"), "{:?}", did.describe());
        let did = work_once_scripted(&db, &dir, &[], &workers)?;
        assert!(matches!(did, Did::Ran { task: 1, trial: 1 }), "{:?}", did.describe());
        let (score, note, by): (i64, String, String) =
            db.conn().query_row("SELECT score, note, rated_by FROM ratings WHERE trial = 2", [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        assert_eq!((score, note.as_str(), by.as_str()), (4, "solid", "conversations/task-1"));
        assert!(arity.messages("task-1".into())?.iter().any(|m| m.kind == MessageKind::Rating && m.text.contains("4/5")));

        // 5. The report lands in chat-1, marked to wake. Before chat-1 runs its turn and hook,
        //    nothing public names task 1's model.
        let did = work_once_scripted(&db, &dir, &[], &chat)?;
        assert!(matches!(&did, Did::Reported { task: 1, parent: Some(p) } if p == "chat-1"), "{:?}", did.describe());
        let t1 = thread("task-1");
        assert_eq!((t1.group, t1.task.as_ref().unwrap().run_state), (ThreadGroup::NeedsYou, Some(RunState::NeedsRating)));

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
        // Run 2 is rated, by task-1's post-use hook, so its model is open.
        let runs = arity.runs()?;
        assert_eq!(runs.iter().find(|r| r.id == 2).unwrap().model.as_deref(), Some("secret-b:beta"));
        assert_eq!(runs.iter().find(|r| r.id == 1).unwrap().model, None);
        assert_eq!(arity.scores()?.iter().map(|s| (s.model.as_str(), s.rated)).collect::<Vec<_>>(), [("secret-b:beta", 1)]);
        assert!(arity.leaderboard()?.iter().any(|s| format!("{s:?}").contains("secret-b:beta")));
        // reveal tells, and rates nothing.
        assert_eq!(arity.reveal(1)?, "secret-a:alpha");
        assert_eq!(thread("task-1").task.unwrap().run_state, Some(RunState::NeedsRating));
        assert!(!sweep()?.contains("alpha"));

        // 6. The dispatcher wakes chat-1; chat-1 presents the result, and its post-use hook grades task 1.
        let did = work_once_scripted(&db, &dir, &[], &chat)?;
        assert!(matches!(&did, Did::Woke { conv } if conv == "chat-1"), "{:?}", did.describe());
        assert!(matches!(work_once_scripted(&db, &dir, &[], &chat)?, Did::Nothing));
        assert_eq!(chat_calls.get(), 4, "reply + post-use rating hook");
        assert!(arity.running()?.is_empty());
        let chat_msgs = arity.messages("chat-1".into())?;
        assert!(chat_msgs.iter().any(|m| m.kind == MessageKind::Assistant && m.text.contains("All sources verified")));
        assert!(chat_msgs.iter().any(|m| m.kind == MessageKind::Rating && m.text.contains("5/5")));

        // Task 1 is now done and rated by chat-1; its model opens everywhere.
        let t1 = thread("task-1");
        assert_eq!(t1.group, ThreadGroup::Done);
        assert_eq!(t1.task.unwrap().model.as_deref(), Some("secret-a:alpha"));
        assert!(arity.messages("task-1".into())?.iter().any(|m| m.model.as_deref() == Some("secret-a:alpha")));
        assert_eq!(arity.runs()?.iter().find(|r| r.id == 1).unwrap().model.as_deref(), Some("secret-a:alpha"));
        assert_eq!(arity.last_model("task-1".into())?.as_deref(), Some("secret-a:alpha"));
        assert!(arity.scores()?.iter().any(|s| s.model == "secret-a:alpha" && s.rated == 1 && s.average == 5.0));

        // The user can still override a rating directly.
        assert_eq!(arity.rate(1, 4, "good".into())?, Some(5));
        assert!(arity.scores()?.iter().any(|s| s.model == "secret-a:alpha" && s.rated == 1 && s.average == 4.0));

        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }
}
