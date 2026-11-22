//! The board: tasks, and for tasks given to an agent, the trials that did them
//! and the ratings those trials got.
//!
//! A task follows the wiki's states: open → ready → assigned → in gates → done.
//! Ready is decided by blockers and recomputed after every write. A task whose
//! assignee is `agent` wakes the dispatcher once it is ready: it claims the
//! task by creating a trial, which picks a model blind and runs a worker in its
//! own conversation. The finished result waits in gates until whoever wrote the
//! task rates it; the rating is the only gate for now. The model stays hidden
//! until then.
//!
//! A task filed in a conversation the user is talking in is the user's, and
//! `origin` remembers the conversation. Only a task an agent files from its own
//! run is written by a conversation, and only those are rated by a model.

use anyhow::{Result, anyhow, bail};
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

pub use crate::leaderboard::Choice;

/// How long a claim on a trial lasts before another dispatcher may take it over.
pub const LEASE_SECS: i64 = 600;

/// The assignee that wakes the dispatcher: any agent, picked blind.
pub const AGENT: &str = "agent";

/// How many levels of tasks for agents can stack up. A task filed in a chat is
/// level 1; one its run hands to an agent is level 2. A run at the floor is not
/// offered delegation: a task it writes is its own todo (see `tools::specs`).
pub const MAX_DEPTH: i64 = 2;

/// The user's page: `people/<login>`.
pub fn me() -> String {
    let user = std::env::var("USER")
        .ok()
        .filter(|u| !u.is_empty())
        .or_else(|| std::env::var("HOME").ok().and_then(|h| h.rsplit('/').next().map(String::from)))
        .unwrap_or_else(|| "me".into());
    format!("people/{user}")
}

pub fn conversation_page(conv: &str) -> String {
    format!("conversations/{conv}")
}

/// Who a task filed in this conversation belongs to: the agent whose run the
/// conversation is, or else the user, who is the one talking in it.
pub fn author(conn: &Connection, conv: &str) -> Result<String> {
    Ok(if trial_of_conversation(conn, conv)?.is_some() { conversation_page(conv) } else { me() })
}

pub fn migrate(conn: &Connection) -> Result<()> {
    // Rebuilding `tasks` drops a table that trials point at. SQLite's way is to
    // turn foreign keys off around the transaction and check them before commit.
    conn.execute_batch("PRAGMA foreign_keys = OFF")?;
    let result = migrate_tables(conn);
    conn.execute_batch("PRAGMA foreign_keys = ON")?;
    result
}

fn migrate_tables(conn: &Connection) -> Result<()> {
    let tx = conn.unchecked_transaction()?;
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS task_blockers (
             task    INTEGER NOT NULL REFERENCES tasks(id),
             blocker INTEGER NOT NULL REFERENCES tasks(id),
             PRIMARY KEY (task, blocker)
         );
         CREATE TABLE IF NOT EXISTS trials (
             id           INTEGER PRIMARY KEY,
             task         INTEGER NOT NULL REFERENCES tasks(id),
             conversation TEXT NOT NULL,
             harness      TEXT NOT NULL DEFAULT 'arity',
             model        TEXT NOT NULL,
             started      TEXT NOT NULL DEFAULT (datetime('now')),
             ended        TEXT,
             result       TEXT,
             error        TEXT,
             lease_until  INTEGER NOT NULL DEFAULT 0,
             reported     INTEGER NOT NULL DEFAULT 0,
             abandoned    INTEGER NOT NULL DEFAULT 0, -- its model could not be reached; another run is picked
             waiting      INTEGER NOT NULL DEFAULT 0  -- its turn ended with sub-tasks out; woken when they report
         );
         CREATE TABLE IF NOT EXISTS ratings (
             trial    INTEGER PRIMARY KEY REFERENCES trials(id),
             score    INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
             note     TEXT NOT NULL,
             rated_by TEXT NOT NULL,              -- people/<login>, or conversations/<name>
             rater_model TEXT,                    -- the model that rated; NULL for a person
             rated_at TEXT NOT NULL DEFAULT (datetime('now'))
         );",
    )?;
    let columns = |table: &str| -> Result<Vec<String>> {
        let mut stmt = tx.prepare("SELECT name FROM pragma_table_info(?1)")?;
        let names = stmt.query_map([table], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        Ok(names)
    };
    let cols = columns("tasks")?;
    if cols.is_empty() {
        create_tasks(&tx, "tasks")?;
    } else if !cols.iter().any(|c| c == "assignee") {
        migrate_v0(&tx, &cols)?;
        let broken: i64 = tx.query_row("SELECT count(*) FROM pragma_foreign_key_check", [], |r| r.get(0))?;
        anyhow::ensure!(broken == 0, "migrating tasks would break {broken} links; nothing was changed");
    } else {
        if !cols.iter().any(|c| c == "origin") {
            tx.execute_batch("ALTER TABLE tasks ADD COLUMN origin TEXT")?;
        }
        if !columns("tasks")?.iter().any(|c| c == "choice") {
            migrate_choice(&tx)?;
            let broken: i64 = tx.query_row("SELECT count(*) FROM pragma_foreign_key_check", [], |r| r.get(0))?;
            anyhow::ensure!(broken == 0, "migrating tasks would break {broken} links; nothing was changed");
        }
    }
    if !columns("trials")?.iter().any(|c| c == "abandoned") {
        tx.execute_batch("ALTER TABLE trials ADD COLUMN abandoned INTEGER NOT NULL DEFAULT 0")?;
    }
    if !columns("trials")?.iter().any(|c| c == "waiting") {
        tx.execute_batch("ALTER TABLE trials ADD COLUMN waiting INTEGER NOT NULL DEFAULT 0")?;
    }
    let task_cols = columns("tasks")?;
    if !task_cols.iter().any(|c| c == "report_when") {
        tx.execute_batch("ALTER TABLE tasks ADD COLUMN report_when TEXT NOT NULL DEFAULT 'after_turn'")?;
        // An earlier column for the same thing, left in place.
        if task_cols.iter().any(|c| c == "when_done") {
            tx.execute_batch("UPDATE tasks SET report_when = 'next_step' WHERE when_done = 'interrupt'")?;
        }
    }
    if !columns("ratings")?.iter().any(|c| c == "rater_model") {
        migrate_authorship(&tx)?;
    }
    tx.commit()?;
    Ok(())
}

fn create_tasks(c: &Connection, name: &str) -> Result<()> {
    c.execute_batch(&format!(
        "CREATE TABLE {name} (
             id         INTEGER PRIMARY KEY,
             title      TEXT NOT NULL,
             brief      TEXT,
             assignee   TEXT NOT NULL,            -- people/<login>, or agent
             created_by TEXT NOT NULL,            -- people/<login>, or conversations/<name> for an agent's run
             origin     TEXT,                     -- the conversation it was filed in, if any
             state      TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open', 'ready', 'assigned', 'in_gates', 'done')),
             choice     TEXT NOT NULL DEFAULT 'smart' CHECK (choice IN ('smart', 'fast', 'free', 'private')),
             depth      INTEGER NOT NULL DEFAULT 1, -- 1 filed by a person or chat, 2 by that task's run, ...
             report_when TEXT NOT NULL DEFAULT 'after_turn', -- when its result joins the run that filed it
             priority   INTEGER NOT NULL DEFAULT 1, -- 0 low, 1 normal, 2 high, 3 urgent
             due        TEXT,                     -- YYYY-MM-DD
             tags       TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
             created    TEXT NOT NULL DEFAULT (datetime('now')),
             edits      INTEGER NOT NULL DEFAULT 0  -- bumped on every change, for polling
         )"
    ))?;
    Ok(())
}

/// The first board (done, for_agent, created_by as a bare conversation name)
/// rebuilt as the wiki's task. Ids are kept, so trials still point at their tasks.
fn migrate_v0(c: &Connection, cols: &[String]) -> Result<()> {
    let col = |name: &str, fallback: &str| if cols.iter().any(|c| c == name) { name.to_string() } else { fallback.to_string() };
    let (details, for_agent, created_by) = (col("details", "NULL"), col("for_agent", "0"), col("created_by", "NULL"));
    create_tasks(c, "tasks_new")?;
    c.execute(
        &format!(
            "INSERT INTO tasks_new (id, title, brief, assignee, created_by, state)
             SELECT t.id, t.title, {details},
                    CASE WHEN {for_agent} != 0 THEN 'agent' ELSE ?1 END,
                    CASE WHEN ifnull({created_by}, '') = '' THEN ?1 ELSE 'conversations/' || {created_by} END,
                    CASE
                        WHEN tr.id IS NULL THEN CASE WHEN t.done != 0 THEN 'done' ELSE 'open' END
                        WHEN tr.ended IS NULL THEN 'assigned'
                        WHEN r.trial IS NULL THEN 'in_gates'
                        ELSE 'done'
                    END
             FROM tasks t
             LEFT JOIN trials tr ON tr.id = (SELECT max(id) FROM trials WHERE task = t.id)
             LEFT JOIN ratings r ON r.trial = tr.id"
        ),
        params![me()],
    )?;
    c.execute_batch("DROP TABLE tasks; ALTER TABLE tasks_new RENAME TO tasks;")?;
    refresh_ready(c)?;
    Ok(())
}

/// Tasks used to carry a clearance, any or local; now they carry one of the four
/// choices, and `local` is what `private` means. The table is rebuilt because
/// the old column's check allows only the old values. Ids are kept.
fn migrate_choice(c: &Connection) -> Result<()> {
    create_tasks(c, "tasks_new")?;
    c.execute_batch(
        "INSERT INTO tasks_new (id, title, brief, assignee, created_by, origin, state, choice, priority, due, tags, created, edits)
         SELECT id, title, brief, assignee, created_by, origin, state,
                CASE clearance WHEN 'local' THEN 'private' ELSE 'smart' END,
                priority, due, tags, created, edits
         FROM tasks;
         DROP TABLE tasks;
         ALTER TABLE tasks_new RENAME TO tasks;",
    )?;
    Ok(())
}

/// Tasks used to be written by the conversation they were filed in, which then
/// rated the result with whatever model it last used. Now a task filed in a
/// chat is the user's and the chat is only its origin; every rating records the
/// rater's model; and a rating a model gave its own run is void, so the result
/// waits for the user again.
fn migrate_authorship(c: &Connection) -> Result<()> {
    c.execute_batch("ALTER TABLE ratings ADD COLUMN rater_model TEXT")?;
    c.execute(
        "UPDATE tasks SET origin = substr(created_by, 15), created_by = ?1
         WHERE created_by LIKE 'conversations/%' AND substr(created_by, 15) NOT IN (SELECT conversation FROM trials)",
        params![me()],
    )?;
    // The rater's model is on the assistant entry that called `rate` for the task.
    c.execute_batch(
        "UPDATE ratings SET rater_model = (
             SELECT e.model FROM entries e, json_each(e.body, '$.tool_calls') call
             WHERE e.kind = 'assistant' AND json_extract(call.value, '$.name') = 'rate'
               AND json_valid(json_extract(call.value, '$.arguments'))
               AND json_extract(json_extract(call.value, '$.arguments'), '$.task')
                   = (SELECT task FROM trials WHERE id = ratings.trial)
             ORDER BY e.id DESC LIMIT 1)
         WHERE rated_by != 'user';
         UPDATE tasks SET state = 'in_gates' WHERE state = 'done' AND id IN (
             SELECT tr.task FROM ratings r JOIN trials tr ON tr.id = r.trial WHERE r.rater_model = tr.model);
         DELETE FROM ratings WHERE rater_model = (SELECT model FROM trials WHERE id = ratings.trial);",
    )?;
    c.execute("UPDATE ratings SET rated_by = ?1 WHERE rated_by = 'user'", params![me()])?;
    Ok(())
}

/// Open and ready swap as blockers open and close. Call after every board write.
pub fn refresh_ready(c: &Connection) -> Result<()> {
    c.execute(
        "UPDATE tasks SET state = new, edits = edits + 1 FROM (
             SELECT t.id AS tid, CASE WHEN EXISTS (
                 SELECT 1 FROM task_blockers b JOIN tasks o ON o.id = b.blocker
                 WHERE b.task = t.id AND o.state != 'done'
             ) THEN 'open' ELSE 'ready' END AS new
             FROM tasks t WHERE t.state IN ('open', 'ready')
         ) WHERE tasks.id = tid AND tasks.state != new",
        [],
    )?;
    Ok(())
}

pub fn now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs() as i64
}

// Fields

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, uniffi::Enum)]
pub enum Priority {
    Low,
    Normal,
    High,
    Urgent,
}

const PRIORITIES: [(Priority, &str); 4] =
    [(Priority::Low, "low"), (Priority::Normal, "normal"), (Priority::High, "high"), (Priority::Urgent, "urgent")];

impl Priority {
    pub fn name(self) -> &'static str {
        PRIORITIES.iter().find(|(p, _)| *p == self).unwrap().1
    }
    fn rank(self) -> i64 {
        PRIORITIES.iter().position(|(p, _)| *p == self).unwrap() as i64
    }
    fn from_rank(n: i64) -> Self {
        PRIORITIES.get(n.clamp(0, 3) as usize).unwrap().0
    }
}

/// What a person or a model fills in for a task. The same fields go into
/// `add_task`, `update_task`, `propose_task` and the draft card.
#[derive(Debug, Clone, PartialEq, uniffi::Record)]
pub struct TaskFields {
    pub title: String,
    /// Everything whoever does it needs to know. Empty for none.
    pub brief: String,
    /// `people/<login>`, or `agent` for any agent.
    pub assignee: String,
    /// Which models may do it and how they rank: private keeps it on this computer.
    pub choice: Choice,
    pub priority: Priority,
    /// YYYY-MM-DD.
    pub due: Option<String>,
    /// Tasks that must be done before this one is ready.
    pub blocked_by: Vec<i64>,
    pub tags: Vec<String>,
}

impl Default for TaskFields {
    fn default() -> Self {
        TaskFields {
            title: String::new(),
            brief: String::new(),
            assignee: me(),
            choice: Choice::Smart,
            priority: Priority::Normal,
            due: None,
            blocked_by: vec![],
            tags: vec![],
        }
    }
}

impl TaskFields {
    pub fn to_json(&self) -> Value {
        json!({
            "title": self.title,
            "brief": self.brief,
            "assignee": self.assignee,
            "choice": self.choice.name(),
            "priority": self.priority.name(),
            "due": self.due,
            "blocked_by": self.blocked_by,
            "tags": self.tags,
        })
    }

    /// Set the fields present in `v`, leaving the rest. Accepts what a model
    /// is likely to write: "user" or "me" for the user, "high" for a priority.
    pub fn merge(&mut self, v: &Value) -> Result<()> {
        let Some(obj) = v.as_object() else { bail!("fields must be an object") };
        for (key, val) in obj {
            let s = || val.as_str().map(str::trim).unwrap_or_default().to_string();
            match key.as_str() {
                "title" => self.title = s(),
                "brief" | "details" => self.brief = s(),
                "assignee" | "for" => self.assignee = normalize_assignee(&s())?,
                "choice" => {
                    let c = s().to_lowercase();
                    self.choice = if c.is_empty() {
                        Choice::Smart
                    } else {
                        Choice::parse(&c).ok_or_else(|| anyhow!("choice must be smart, fast, free or private, not {c:?}"))?
                    }
                }
                // What the field was called before; old task cards in the journal still say it.
                "clearance" => {
                    self.choice = match s().to_lowercase().as_str() {
                        "local" | "local only" => Choice::Private,
                        _ => Choice::Smart,
                    }
                }
                "priority" => {
                    self.priority = match val {
                        Value::Number(n) => Priority::from_rank(n.as_i64().unwrap_or(1)),
                        _ => {
                            let p = s().to_lowercase();
                            PRIORITIES
                                .iter()
                                .find(|(_, name)| *name == p)
                                .map(|(p, _)| *p)
                                .ok_or_else(|| anyhow!("priority must be low, normal, high or urgent, not {p:?}"))?
                        }
                    }
                }
                "due" => {
                    self.due = match s() {
                        d if d.is_empty() || d == "null" => None,
                        d if is_date(&d) => Some(d),
                        d => bail!("due must be a date as YYYY-MM-DD, not {d:?}"),
                    }
                }
                "blocked_by" => {
                    self.blocked_by = match val {
                        Value::Null => vec![],
                        Value::Array(a) => a
                            .iter()
                            .map(|x| x.as_i64().or_else(|| x.as_str().and_then(|s| s.trim().parse().ok())))
                            .collect::<Option<_>>()
                            .ok_or_else(|| anyhow!("blocked_by must be a list of task ids"))?,
                        Value::Number(n) => vec![n.as_i64().unwrap_or_default()],
                        _ => bail!("blocked_by must be a list of task ids"),
                    }
                }
                "tags" => {
                    self.tags = match val {
                        Value::Null => vec![],
                        Value::Array(a) => a.iter().filter_map(|t| t.as_str()).map(String::from).collect(),
                        Value::String(s) => s.split(',').map(String::from).collect(),
                        _ => bail!("tags must be a list of strings"),
                    };
                    self.tags = clean_tags(&self.tags);
                }
                "id" => {}
                other => bail!("unknown field {other}"),
            }
        }
        Ok(())
    }

    pub fn from_json(v: &Value) -> Self {
        let mut f = TaskFields::default();
        // Drafts were written by `to_json`, so this cannot fail on them in practice.
        let _ = f.merge(v);
        f
    }

    /// One line per field that is set, for a model or the CLI to read.
    pub fn describe(&self) -> String {
        let mut out = vec![format!("title: {}", self.title)];
        if !self.brief.is_empty() {
            out.push(format!("brief: {}", self.brief));
        }
        out.push(format!("assignee: {}", self.assignee));
        out.push(format!("choice: {}", self.choice.name()));
        out.push(format!("priority: {}", self.priority.name()));
        if let Some(d) = &self.due {
            out.push(format!("due: {d}"));
        }
        if !self.blocked_by.is_empty() {
            let ids: Vec<String> = self.blocked_by.iter().map(|i| i.to_string()).collect();
            out.push(format!("blocked by: {}", ids.join(", ")));
        }
        if !self.tags.is_empty() {
            out.push(format!("tags: {}", self.tags.join(", ")));
        }
        out.join("\n")
    }
}

fn normalize_assignee(a: &str) -> Result<String> {
    Ok(match a.to_lowercase().as_str() {
        "agent" | "an agent" | "any agent" => AGENT.into(),
        "me" | "user" | "the user" | "you" | "" => me(),
        _ if a.starts_with("people/") && a.len() > 7 => a.into(),
        // A run's own todo, which it does itself.
        _ if a.starts_with("conversations/") && a.len() > 14 => a.into(),
        _ => bail!("assignee must be \"agent\" or \"user\", not {a:?}"),
    })
}

/// A real calendar date as YYYY-MM-DD: 2026-02-31 is not one.
fn is_date(d: &str) -> bool {
    let b = d.as_bytes();
    let shaped = b.len() == 10
        && b[4] == b'-'
        && b[7] == b'-'
        && b.iter().enumerate().all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit());
    if !shaped {
        return false;
    }
    let (y, m, day): (u32, u32, u32) = (d[..4].parse().unwrap(), d[5..7].parse().unwrap(), d[8..].parse().unwrap());
    let leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    let days = match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => return false,
    };
    (1..=days).contains(&day)
}

/// Tags trimmed, without empties or repeats (ignoring case), in their first order.
fn clean_tags(tags: &[String]) -> Vec<String> {
    let mut seen: Vec<String> = vec![];
    for t in tags.iter().map(|t| t.trim().to_string()).filter(|t| !t.is_empty()) {
        if !seen.iter().any(|s| s.eq_ignore_ascii_case(&t)) {
            seen.push(t);
        }
    }
    seen
}

/// The longest title a task may have; the rest belongs in the brief.
pub const MAX_TITLE: usize = 200;

// Tasks

#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum TaskState {
    /// Waiting on blockers.
    Open,
    /// No open blockers. A ready task for an agent wakes the dispatcher.
    Ready,
    /// A person took it, or an agent run was started for it.
    Assigned,
    /// The work is reported and waits on its gates. For now the one gate is a rating.
    InGates,
    Done,
}

impl TaskState {
    pub fn name(self) -> &'static str {
        match self {
            TaskState::Open => "open",
            TaskState::Ready => "ready",
            TaskState::Assigned => "assigned",
            TaskState::InGates => "in_gates",
            TaskState::Done => "done",
        }
    }
    fn parse(s: &str) -> Self {
        match s {
            "ready" => TaskState::Ready,
            "assigned" => TaskState::Assigned,
            "in_gates" => TaskState::InGates,
            "done" => TaskState::Done,
            _ => TaskState::Open,
        }
    }
}

/// How the latest run of a task is going. Shown next to the task, apart from its state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum RunState {
    Running,
    /// The run handed part of its task on and waits for those results.
    Waiting,
    /// The last attempt failed; it will be retried.
    Failing,
    NeedsRating,
    Rated,
}

#[derive(Debug, Clone)]
pub struct TaskRow {
    pub id: i64,
    pub fields: TaskFields,
    pub created_by: String,
    pub origin: Option<String>,
    pub state: TaskState,
    pub created: String,
    pub trial: Option<i64>,
    pub run_state: Option<RunState>,
    pub conversation: Option<String>,
    pub result: Option<String>,
    pub error: Option<String>,
    pub score: Option<i64>,
    pub note: Option<String>,
    /// Which model did the latest run. Front ends see it only once it is rated.
    pub model: Option<String>,
}

/// Every task, the ones still to do first, then by priority, due date and age.
pub fn tasks(conn: &Connection) -> Result<Vec<TaskRow>> {
    let blockers = all_blockers(conn)?;
    let mut stmt = conn.prepare(
        "SELECT t.id, t.title, ifnull(t.brief, ''), t.assignee, t.created_by, t.state, t.choice,
                t.priority, t.due, t.tags,
                tr.id, tr.conversation, tr.ended, tr.result, tr.error, r.score,
                t.origin, t.created, r.note, tr.model, tr.waiting
         FROM tasks t
         LEFT JOIN trials tr ON tr.id = (SELECT max(id) FROM trials WHERE task = t.id AND abandoned = 0)
         LEFT JOIN ratings r ON r.trial = tr.id
         ORDER BY t.state = 'done', t.priority DESC, t.due IS NULL, t.due, t.id",
    )?;
    let rows = stmt.query_map([], |r| {
        let id: i64 = r.get(0)?;
        let tags: String = r.get(9)?;
        let trial: Option<i64> = r.get(10)?;
        let ended: Option<String> = r.get(12)?;
        let error: Option<String> = r.get(14)?;
        let score: Option<i64> = r.get(15)?;
        let waiting: Option<bool> = r.get(20)?;
        let run_state = trial.map(|_| match (ended.is_some(), error.is_some(), score.is_some()) {
            (false, _, _) if waiting == Some(true) => RunState::Waiting,
            (false, true, _) => RunState::Failing,
            (false, false, _) => RunState::Running,
            (true, _, false) => RunState::NeedsRating,
            (true, _, true) => RunState::Rated,
        });
        Ok(TaskRow {
            id,
            fields: TaskFields {
                title: r.get(1)?,
                brief: r.get(2)?,
                assignee: r.get(3)?,
                choice: Choice::parse(&r.get::<_, String>(6)?).unwrap_or(Choice::Smart),
                priority: Priority::from_rank(r.get(7)?),
                due: r.get(8)?,
                blocked_by: blockers.iter().filter(|(t, _)| *t == id).map(|(_, b)| *b).collect(),
                tags: serde_json::from_str(&tags).unwrap_or_default(),
            },
            created_by: r.get(4)?,
            origin: r.get(16)?,
            state: TaskState::parse(&r.get::<_, String>(5)?),
            created: r.get(17)?,
            trial,
            run_state,
            conversation: r.get(11)?,
            result: r.get(13)?,
            error,
            score,
            note: r.get(18)?,
            model: r.get(19)?,
        })
    })?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

pub fn task(conn: &Connection, id: i64) -> Result<TaskRow> {
    tasks(conn)?.into_iter().find(|t| t.id == id).ok_or_else(|| anyhow!("no task {id}"))
}

fn all_blockers(conn: &Connection) -> Result<Vec<(i64, i64)>> {
    let mut stmt = conn.prepare("SELECT task, blocker FROM task_blockers ORDER BY task, blocker")?;
    let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

fn check(conn: &Connection, id: Option<i64>, f: &TaskFields) -> Result<()> {
    if f.title.trim().is_empty() {
        bail!("a task needs a title");
    }
    if f.title.trim().chars().count() > MAX_TITLE {
        bail!("a title is at most {MAX_TITLE} characters; put the rest in the brief");
    }
    if let Some(d) = f.due.as_deref().filter(|d| !is_date(d)) {
        bail!("due must be a date as YYYY-MM-DD, not {d:?}");
    }
    normalize_assignee(&f.assignee)?;
    for &b in &f.blocked_by {
        if Some(b) == id {
            bail!("a task cannot block itself");
        }
        let exists: bool = conn.query_row("SELECT count(*) FROM tasks WHERE id = ?1", params![b], |r| r.get::<_, i64>(0))? > 0;
        if !exists {
            bail!("blocked_by: no task {b}");
        }
        if let Some(id) = id {
            // Would `b` (transitively) wait on this task? Then this would be a cycle.
            let cycle: bool = conn.query_row(
                "WITH RECURSIVE up(t) AS (SELECT ?1 UNION SELECT blocker FROM task_blockers JOIN up ON task = up.t)
                 SELECT count(*) FROM up WHERE t = ?2",
                params![b, id],
                |r| r.get::<_, i64>(0),
            )? > 0;
            if cycle {
                bail!("task {b} already waits on task {id}");
            }
        }
    }
    Ok(())
}

fn write_links(conn: &Connection, id: i64, f: &TaskFields) -> Result<()> {
    conn.execute("DELETE FROM task_blockers WHERE task = ?1", params![id])?;
    for b in &f.blocked_by {
        conn.execute("INSERT OR IGNORE INTO task_blockers (task, blocker) VALUES (?1, ?2)", params![id, b])?;
    }
    Ok(())
}

/// A task filed in the conversation `origin`, or by the user directly when
/// there is none. Who it belongs to follows from that; see `author`.
pub fn add_task(conn: &Connection, f: &TaskFields, origin: Option<&str>) -> Result<i64> {
    check(conn, None, f)?;
    let created_by = match origin {
        Some(conv) => author(conn, conv)?,
        None => me(),
    };
    let (depth, parent) = parent_of(conn, origin)?;
    // Private work stays private all the way down: a private run's tasks are private too.
    let choice = if parent == Some(Choice::Private) { Choice::Private } else { f.choice };
    // The tools never offer delegation at the floor; this only guards the rule.
    if depth > MAX_DEPTH && normalize_assignee(&f.assignee)? == AGENT {
        bail!("a run at the floor cannot hand work to an agent");
    }
    conn.execute(
        "INSERT INTO tasks (title, brief, assignee, created_by, choice, priority, due, tags, origin, depth)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
        params![
            f.title.trim(),
            Some(f.brief.trim()).filter(|b| !b.is_empty()),
            normalize_assignee(&f.assignee)?,
            created_by,
            choice.name(),
            f.priority.rank(),
            f.due,
            serde_json::to_string(&clean_tags(&f.tags))?,
            origin,
            depth,
        ],
    )?;
    let id = conn.last_insert_rowid();
    write_links(conn, id, f)?;
    refresh_ready(conn)?;
    Ok(id)
}

/// The level of the task whose run `conv` is, or None for a conversation that is not a run.
pub fn run_depth(conn: &Connection, conv: &str) -> Result<Option<i64>> {
    Ok(conn
        .query_row("SELECT t.depth FROM trials tr JOIN tasks t ON t.id = tr.task WHERE tr.conversation = ?1", params![conv], |r| r.get(0))
        .optional()?)
}

/// The level a task filed in `origin` would be at, one below the task whose run
/// `origin` is (1 when it is filed by a person or a chat), and that task's choice.
fn parent_of(conn: &Connection, origin: Option<&str>) -> Result<(i64, Option<Choice>)> {
    let Some(conv) = origin else { return Ok((1, None)) };
    let parent: Option<(i64, String)> = conn
        .query_row(
            "SELECT t.depth, t.choice FROM trials tr JOIN tasks t ON t.id = tr.task WHERE tr.conversation = ?1",
            params![conv],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    Ok(match parent {
        Some((d, choice)) => (d + 1, Choice::parse(&choice)),
        None => (1, None),
    })
}

/// Replace a task's fields. The assignee can change only before work starts:
/// that is how a task is handed to an agent, or taken back.
pub fn update_task(conn: &Connection, id: i64, f: &TaskFields) -> Result<()> {
    let old = task(conn, id)?;
    check(conn, Some(id), f)?;
    let assignee = normalize_assignee(&f.assignee)?;
    if assignee != old.fields.assignee && matches!(old.state, TaskState::Assigned | TaskState::InGates) {
        bail!("task {id} is already being worked on; its assignee cannot change now");
    }
    conn.execute(
        "UPDATE tasks SET title = ?2, brief = ?3, assignee = ?4, choice = ?5, priority = ?6, due = ?7, tags = ?8,
             edits = edits + 1
         WHERE id = ?1",
        params![
            id,
            f.title.trim(),
            Some(f.brief.trim()).filter(|b| !b.is_empty()),
            assignee,
            f.choice.name(),
            f.priority.rank(),
            f.due,
            serde_json::to_string(&clean_tags(&f.tags))?,
        ],
    )?;
    write_links(conn, id, f)?;
    refresh_ready(conn)?;
    Ok(())
}

pub fn set_state(conn: &Connection, id: i64, state: TaskState) -> Result<()> {
    if conn.execute("UPDATE tasks SET state = ?2, edits = edits + 1 WHERE id = ?1", params![id, state.name()])? == 0 {
        bail!("no task {id}");
    }
    refresh_ready(conn)
}

/// Tick a task off, or reopen it. Not while an agent is still on it.
pub fn set_done(conn: &Connection, id: i64, done: bool) -> Result<()> {
    let t = task(conn, id)?;
    if done && matches!(t.run_state, Some(RunState::Running | RunState::Failing | RunState::Waiting)) {
        bail!("an agent is still working on task {id}");
    }
    set_state(conn, id, if done { TaskState::Done } else { TaskState::Open })
}

/// Is this conversation a worker doing a task? Workers may not hand work on yet.
pub fn trial_of_conversation(conn: &Connection, conv: &str) -> Result<Option<i64>> {
    Ok(conn
        .query_row("SELECT id FROM trials WHERE conversation = ?1", params![conv], |r| r.get(0))
        .optional()?)
}

pub fn run_conversations(conn: &Connection) -> Result<Vec<String>> {
    let mut stmt = conn.prepare("SELECT conversation FROM trials")?;
    let rows = stmt.query_map([], |r| r.get(0))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

pub fn is_rated(conn: &Connection, trial: i64) -> Result<bool> {
    Ok(conn.query_row("SELECT count(*) FROM ratings WHERE trial = ?1", params![trial], |r| r.get::<_, i64>(0))? > 0)
}

/// Rate a finished run. The rating is the task's one gate, so it is done after.
/// `rater_model` is the model rating, or None for a person. A model never
/// rates a run it did itself.
/// Returns the score it replaced, if the run was already rated.
pub fn rate(conn: &Connection, trial: i64, score: i64, note: &str, rated_by: &str, rater_model: Option<&str>) -> Result<Option<i64>> {
    if !(1..=5).contains(&score) {
        bail!("score must be 1 to 5");
    }
    let found: Option<(i64, Option<String>, String)> = conn
        .query_row("SELECT task, CASE WHEN abandoned = 0 THEN ended END, model FROM trials WHERE id = ?1", params![trial], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })
        .optional()?;
    let task = match found {
        None => bail!("no run {trial}"),
        Some((_, None, _)) => bail!("run {trial} has not finished"),
        Some((_, _, model)) if rater_model == Some(&model) => bail!("run {trial} is not yours to rate"),
        Some((task, ..)) => task,
    };
    let before: Option<i64> = conn.query_row("SELECT score FROM ratings WHERE trial = ?1", params![trial], |r| r.get(0)).optional()?;
    conn.execute(
        "INSERT INTO ratings (trial, score, note, rated_by, rater_model) VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(trial) DO UPDATE SET score = excluded.score, note = excluded.note,
             rated_by = excluded.rated_by, rater_model = excluded.rater_model, rated_at = datetime('now')",
        params![trial, score, note, rated_by, rater_model],
    )?;
    conn.execute("UPDATE tasks SET state = 'done', edits = edits + 1 WHERE id = ?1 AND state = 'in_gates'", params![task])?;
    refresh_ready(conn)?;
    Ok(before)
}

/// Finished, reported runs of tasks this conversation wrote that nobody has
/// rated and that `model`, the one it is running on, may rate: not its own.
/// The rest are left for the user. Each is (task, run).
pub fn unrated_for(conn: &Connection, conv: &str, model: &str) -> Result<Vec<(i64, i64)>> {
    let mut stmt = conn.prepare(
        "SELECT tr.task, tr.id FROM trials tr JOIN tasks t ON t.id = tr.task
         WHERE t.created_by = ?1 AND tr.ended IS NOT NULL AND tr.reported = 1 AND tr.abandoned = 0 AND tr.model != ?2
           AND NOT EXISTS (SELECT 1 FROM ratings r WHERE r.trial = tr.id)
         ORDER BY tr.id",
    )?;
    let rows = stmt.query_map(params![conversation_page(conv), model], |r| Ok((r.get(0)?, r.get(1)?)))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// Tasks this run handed to an agent whose results have not reached it yet:
/// not started, running, or finished but not yet passed on. Ids, oldest first.
pub fn children_out(conn: &Connection, conv: &str) -> Result<Vec<i64>> {
    let mut stmt = conn.prepare(
        "SELECT t.id FROM tasks t
         WHERE t.created_by = ?1 AND t.assignee = 'agent'
           AND (t.state IN ('open', 'ready', 'assigned')
                OR EXISTS (SELECT 1 FROM trials tr WHERE tr.task = t.id AND tr.ended IS NOT NULL
                               AND tr.reported = 0 AND tr.abandoned = 0))
         ORDER BY t.id",
    )?;
    let rows = stmt.query_map(params![conversation_page(conv)], |r| r.get(0))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// A run's own todos that are not done, as (id, title).
pub fn open_todos(conn: &Connection, conv: &str) -> Result<Vec<(i64, String)>> {
    let mut stmt = conn.prepare("SELECT id, title FROM tasks WHERE assignee = ?1 AND state != 'done' ORDER BY id")?;
    let rows = stmt.query_map(params![conversation_page(conv)], |r| Ok((r.get(0)?, r.get(1)?)))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

#[derive(Debug, Clone)]
pub struct RunRow {
    pub trial: i64,
    pub task: i64,
    pub title: String,
    pub conversation: String,
    pub model: String,
    pub started: String,
    pub ended: Option<String>,
    pub score: Option<i64>,
    pub note: Option<String>,
}

pub fn runs(conn: &Connection) -> Result<Vec<RunRow>> {
    let mut stmt = conn.prepare(
        "SELECT tr.id, tr.task, t.title, tr.conversation, tr.model, tr.started, tr.ended, r.score, r.note
         FROM trials tr JOIN tasks t ON t.id = tr.task LEFT JOIN ratings r ON r.trial = tr.id
         WHERE tr.abandoned = 0
         ORDER BY tr.id DESC",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok(RunRow {
            trial: r.get(0)?,
            task: r.get(1)?,
            title: r.get(2)?,
            conversation: r.get(3)?,
            model: r.get(4)?,
            started: r.get(5)?,
            ended: r.get(6)?,
            score: r.get(7)?,
            note: r.get(8)?,
        })
    })?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// Per model: rated runs and their average score. Unrated runs are left out
/// entirely: counting them per model would give away who did them.
pub fn scores(conn: &Connection) -> Result<Vec<(String, i64, f64)>> {
    let mut stmt = conn.prepare(
        "SELECT tr.model, count(*), avg(r.score)
         FROM trials tr JOIN ratings r ON r.trial = tr.id
         GROUP BY tr.model ORDER BY avg(r.score) DESC, tr.model",
    )?;
    let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
    Ok(rows.collect::<rusqlite::Result<_>>()?)
}

/// Which model did a run, and why it failed if it did. Failures name the
/// model or its provider, so they are only told here.
pub fn reveal(conn: &Connection, trial: i64) -> Result<String> {
    let (model, error): (String, Option<String>) = conn
        .query_row("SELECT model, error FROM trials WHERE id = ?1", params![trial], |r| Ok((r.get(0)?, r.get(1)?)))
        .optional()?
        .ok_or_else(|| anyhow!("no run {trial}"))?;
    Ok(match error {
        Some(e) => format!("{model} (failed: {e})"),
        None => model,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!("arity-{name}-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        path
    }

    fn fields(title: &str) -> TaskFields {
        TaskFields { title: title.into(), ..Default::default() }
    }

    #[test]
    fn task_fields_are_checked() -> Result<()> {
        let db = crate::journal::Journal::open(&temp("checks"))?;
        let c = db.conn();
        let mut f = fields("t");
        assert!(f.merge(&json!({ "due": "2026-02-31" })).is_err(), "no 31 February");
        assert!(f.merge(&json!({ "due": "2026-13-01" })).is_err());
        f.merge(&json!({ "due": "2028-02-29" }))?;
        assert!(f.merge(&json!({ "due": "2027-02-29" })).is_err(), "2027 is not a leap year");
        // The core checks fields however they arrive, not only through merge.
        assert!(add_task(c, &TaskFields { due: Some("2026-04-31".into()), ..fields("t") }, None).is_err());
        f.merge(&json!({ "tags": ["fun", "Fun", " fun ", "work"] }))?;
        assert_eq!(f.tags, ["fun", "work"]);
        let long = "x".repeat(MAX_TITLE + 1);
        assert!(add_task(c, &fields(&long), None).unwrap_err().to_string().contains("at most"));
        add_task(c, &fields(&"x".repeat(MAX_TITLE)), None)?;
        let id = add_task(c, &TaskFields { tags: vec!["a".into(), "A".into()], ..fields("t") }, None)?;
        assert_eq!(task(c, id)?.fields.tags, ["a"]);
        Ok(())
    }

    #[test]
    fn blockers_decide_ready() -> Result<()> {
        let path = temp("ready");
        let db = crate::journal::Journal::open(&path)?;
        let c = db.conn();
        let a = add_task(c, &fields("a"), None)?;
        let b = add_task(c, &TaskFields { blocked_by: vec![a], ..fields("b") }, None)?;
        assert_eq!(task(c, a)?.state, TaskState::Ready);
        assert_eq!(task(c, b)?.state, TaskState::Open, "a is not done yet");
        set_done(c, a, true)?;
        assert_eq!(task(c, b)?.state, TaskState::Ready, "its blocker is done");
        set_done(c, a, false)?;
        assert_eq!(task(c, a)?.state, TaskState::Ready, "reopened with no blockers of its own");
        assert_eq!(task(c, b)?.state, TaskState::Open, "blocked again");
        // Dropping the blocker frees it.
        update_task(c, b, &fields("b"))?;
        assert_eq!(task(c, b)?.state, TaskState::Ready);
        // No cycles, no self-blocking, no unknown tasks.
        update_task(c, b, &TaskFields { blocked_by: vec![a], ..fields("b") })?;
        assert!(update_task(c, a, &TaskFields { blocked_by: vec![b], ..fields("a") }).is_err());
        assert!(update_task(c, a, &TaskFields { blocked_by: vec![a], ..fields("a") }).is_err());
        assert!(add_task(c, &TaskFields { blocked_by: vec![99], ..fields("c") }, None).is_err());
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn migrates_the_first_board_in_place() -> Result<()> {
        let path = temp("migrate");
        {
            let c = Connection::open(&path)?;
            // The schema and rows as they were before this change, plus a task
            // with a rated run and one with a run still going.
            c.execute_batch(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                     details TEXT, for_agent INTEGER NOT NULL DEFAULT 0, created_by TEXT);
                 INSERT INTO tasks VALUES (1, 'try the Arity app', 1, NULL, 0, NULL);
                 INSERT INTO tasks VALUES (2, 'pick the next step', 0, NULL, 0, NULL);
                 INSERT INTO tasks VALUES (3, 'write a poem', 0, NULL, 0, 'hello');
                 INSERT INTO tasks VALUES (4, 'summarise', 1, 'the readme', 1, 'hello');
                 INSERT INTO tasks VALUES (5, 'translate', 0, NULL, 1, NULL);
                 CREATE TABLE trials (id INTEGER PRIMARY KEY, task INTEGER NOT NULL REFERENCES tasks(id),
                     conversation TEXT NOT NULL, harness TEXT NOT NULL DEFAULT 'arity', model TEXT NOT NULL,
                     started TEXT NOT NULL DEFAULT (datetime('now')), ended TEXT, result TEXT, error TEXT,
                     lease_until INTEGER NOT NULL DEFAULT 0, reported INTEGER NOT NULL DEFAULT 0);
                 CREATE TABLE ratings (trial INTEGER PRIMARY KEY REFERENCES trials(id), score INTEGER NOT NULL,
                     note TEXT NOT NULL, rated_by TEXT NOT NULL, rated_at TEXT NOT NULL DEFAULT (datetime('now')));
                 INSERT INTO trials (id, task, conversation, model, ended, result, reported)
                     VALUES (1, 4, 'task-4', 'mlx:m', datetime('now'), 'short', 1);
                 INSERT INTO ratings (trial, score, note, rated_by) VALUES (1, 4, 'ok', 'hello');
                 INSERT INTO trials (id, task, conversation, model) VALUES (2, 5, 'task-5', 'mlx:m');",
            )?;
        }
        let db = crate::journal::Journal::open(&path)?;
        let c = db.conn();
        let me = me();
        let ts = tasks(c)?;
        let get = |id: i64| ts.iter().find(|t| t.id == id).unwrap();
        assert_eq!((get(1).state, get(1).fields.assignee.as_str(), get(1).created_by.as_str()), (TaskState::Done, &*me, &*me));
        assert_eq!((get(2).state, get(2).fields.assignee.as_str()), (TaskState::Ready, &*me));
        assert_eq!((get(3).state, &*get(3).created_by, get(3).origin.as_deref()), (TaskState::Ready, &*me, Some("hello")));
        assert_eq!((get(4).state, get(4).fields.assignee.as_str()), (TaskState::Done, AGENT));
        assert_eq!((get(4).fields.brief.as_str(), get(4).run_state, get(4).score), ("the readme", Some(RunState::Rated), Some(4)));
        assert_eq!((get(5).state, get(5).run_state), (TaskState::Assigned, Some(RunState::Running)));
        assert_eq!(get(3).fields.priority, Priority::Normal);
        // Opening again changes nothing.
        drop(db);
        let db = crate::journal::Journal::open(&path)?;
        assert_eq!(tasks(db.conn())?.len(), 5);
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    /// A run of `task` by `model` that has finished and been reported.
    fn finished_run(c: &Connection, task: i64, model: &str) -> Result<i64> {
        c.execute(
            "INSERT INTO trials (task, conversation, model, ended, result, reported)
             VALUES (?1, 'task-' || ?1, ?2, datetime('now'), 'the result', 1)",
            params![task, model],
        )?;
        set_state(c, task, TaskState::InGates)?;
        Ok(c.last_insert_rowid())
    }

    fn for_agent(title: &str) -> TaskFields {
        TaskFields { assignee: AGENT.into(), ..fields(title) }
    }

    #[test]
    fn a_task_filed_in_a_chat_is_the_users() -> Result<()> {
        let path = temp("author");
        let db = crate::journal::Journal::open(&path)?;
        let c = db.conn();
        // The model in a chat adds a task for an agent: the user's, from that chat.
        let out = crate::tools::execute(c, "chat", "codex:x", "add_task", r#"{"title": "write a poem", "assignee": "agent"}"#);
        assert!(out.starts_with("added task 1"), "{out}");
        let t = task(c, 1)?;
        assert_eq!((&*t.created_by, t.origin.as_deref()), (&*me(), Some("chat")));
        // So no model is asked to rate it, whichever one the chat is on.
        let run = finished_run(c, 1, "mlx:m")?;
        assert!(unrated_for(c, "chat", "codex:x")?.is_empty());
        rate(c, run, 5, "lovely", &me(), None)?;
        assert_eq!(task(c, 1)?.state, TaskState::Done);
        // Written directly, there is no origin. Filed from an agent's own run, it is that run's.
        assert_eq!(task(c, add_task(c, &fields("direct"), None)?)?.origin, None);
        let delegated = add_task(c, &for_agent("delegated"), Some("task-1"))?;
        assert_eq!(task(c, delegated)?.created_by, "conversations/task-1");
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn a_model_never_rates_its_own_run() -> Result<()> {
        let path = temp("own-run");
        let db = crate::journal::Journal::open(&path)?;
        let c = db.conn();
        let parent = add_task(c, &for_agent("parent"), None)?;
        finished_run(c, parent, "mlx:a")?;
        // The parent's run delegates; the same model happens to do the child.
        let child = add_task(c, &for_agent("child"), Some("task-1"))?;
        let run = finished_run(c, child, "mlx:a")?;
        assert!(unrated_for(c, "task-1", "mlx:a")?.is_empty(), "not offered its own run");
        assert!(rate(c, run, 5, "great", "conversations/task-1", Some("mlx:a")).is_err());
        let out = crate::tools::execute(c, "task-1", "mlx:a", "rate", &format!(r#"{{"task": {child}, "score": 5, "note": "great"}}"#));
        assert!(out.starts_with("error"), "{out}");
        assert_eq!(task(c, child)?.run_state, Some(RunState::NeedsRating), "still waiting, for the user");
        // Another model may, and is recorded as the rater.
        assert_eq!(unrated_for(c, "task-1", "mlx:b")?, vec![(child, run)]);
        let out = crate::tools::execute(c, "task-1", "mlx:b", "rate", &format!(r#"{{"task": {child}, "score": 3, "note": "ok"}}"#));
        assert_eq!(out, format!("rated task {child}: 3/5"));
        let rater: Option<String> = c.query_row("SELECT rater_model FROM ratings WHERE trial = ?1", params![run], |r| r.get(0))?;
        assert_eq!(rater.as_deref(), Some("mlx:b"));
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn migrates_clearance_to_choice_in_place() -> Result<()> {
        let path = temp("choice");
        {
            // The board with clearance, any or local, behind a check that allows only those.
            let c = Connection::open(&path)?;
            c.execute_batch(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL, brief TEXT, assignee TEXT NOT NULL,
                     created_by TEXT NOT NULL, origin TEXT, state TEXT NOT NULL DEFAULT 'open',
                     clearance TEXT NOT NULL DEFAULT 'any' CHECK (clearance IN ('any', 'local')),
                     priority INTEGER NOT NULL DEFAULT 1, due TEXT, tags TEXT NOT NULL DEFAULT '[]',
                     created TEXT NOT NULL DEFAULT (datetime('now')), edits INTEGER NOT NULL DEFAULT 0);
                 CREATE TABLE trials (id INTEGER PRIMARY KEY, task INTEGER NOT NULL REFERENCES tasks(id),
                     conversation TEXT NOT NULL, harness TEXT NOT NULL DEFAULT 'arity', model TEXT NOT NULL,
                     started TEXT NOT NULL DEFAULT (datetime('now')), ended TEXT, result TEXT, error TEXT,
                     lease_until INTEGER NOT NULL DEFAULT 0, reported INTEGER NOT NULL DEFAULT 0);
                 CREATE TABLE ratings (trial INTEGER PRIMARY KEY REFERENCES trials(id), score INTEGER NOT NULL,
                     note TEXT NOT NULL, rated_by TEXT NOT NULL, rater_model TEXT,
                     rated_at TEXT NOT NULL DEFAULT (datetime('now')));
                 INSERT INTO tasks (id, title, assignee, created_by, origin, state, clearance, priority, due, tags) VALUES
                     (3, 'private notes', 'agent', 'people/x', 'chat-1', 'done', 'local', 2, '2026-12-01', '[\"a\"]'),
                     (7, 'anything', 'agent', 'people/x', NULL, 'ready', 'any', 1, NULL, '[]');
                 INSERT INTO trials (id, task, conversation, model, ended, result, reported) VALUES
                     (1, 3, 'task-3', 'mlx:qwen', datetime('now'), 'done', 1);
                 INSERT INTO ratings (trial, score, note, rated_by) VALUES (1, 4, 'ok', 'people/x');",
            )?;
        }
        for _ in 0..2 {
            // Twice: opening again changes nothing.
            let db = crate::journal::Journal::open(&path)?;
            let c = db.conn();
            let a = task(c, 3)?;
            assert_eq!((a.fields.choice, a.fields.priority, a.fields.due.as_deref()), (Choice::Private, Priority::High, Some("2026-12-01")));
            assert_eq!((a.origin.as_deref(), a.fields.tags.clone(), a.score), (Some("chat-1"), vec!["a".to_string()], Some(4)), "the run still links");
            assert_eq!(task(c, 7)?.fields.choice, Choice::Smart);
            let depth: i64 = c.query_row("SELECT depth FROM tasks WHERE id = 7", [], |r| r.get(0))?;
            assert_eq!(depth, 1);
        }
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn migrates_authorship_in_place() -> Result<()> {
        let path = temp("authorship");
        {
            // The board as it was: tasks written by the chat they were filed in,
            // one of them rated there by the model that had also done the run.
            let c = Connection::open(&path)?;
            c.execute_batch(
                "CREATE TABLE entries (id INTEGER PRIMARY KEY, parent INTEGER, kind TEXT NOT NULL, body TEXT NOT NULL,
                     model TEXT, created TEXT NOT NULL DEFAULT (datetime('now')));
                 CREATE TABLE conversations (name TEXT PRIMARY KEY, head INTEGER NOT NULL);
                 CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL, brief TEXT, assignee TEXT NOT NULL,
                     created_by TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', clearance TEXT NOT NULL DEFAULT 'any',
                     priority INTEGER NOT NULL DEFAULT 1, due TEXT, tags TEXT NOT NULL DEFAULT '[]',
                     created TEXT NOT NULL DEFAULT (datetime('now')), edits INTEGER NOT NULL DEFAULT 0);
                 CREATE TABLE trials (id INTEGER PRIMARY KEY, task INTEGER NOT NULL REFERENCES tasks(id),
                     conversation TEXT NOT NULL, harness TEXT NOT NULL DEFAULT 'arity', model TEXT NOT NULL,
                     started TEXT NOT NULL DEFAULT (datetime('now')), ended TEXT, result TEXT, error TEXT,
                     lease_until INTEGER NOT NULL DEFAULT 0, reported INTEGER NOT NULL DEFAULT 0);
                 CREATE TABLE ratings (trial INTEGER PRIMARY KEY REFERENCES trials(id), score INTEGER NOT NULL,
                     note TEXT NOT NULL, rated_by TEXT NOT NULL, rated_at TEXT NOT NULL DEFAULT (datetime('now')));
                 INSERT INTO tasks (id, title, assignee, created_by, state) VALUES
                     (1, 'write a poem', 'agent', 'conversations/hello', 'done'),
                     (2, 'summarise', 'agent', 'conversations/hello', 'done'),
                     (3, 'translate', 'agent', 'people/x', 'done'),
                     (4, 'child', 'agent', 'conversations/task-3', 'ready');
                 INSERT INTO trials (id, task, conversation, model, ended, result, reported) VALUES
                     (1, 1, 'task-1', 'mlx:qwen', datetime('now'), 'a poem', 1),
                     (2, 2, 'task-2', 'mlx:gemma', datetime('now'), 'short', 1),
                     (3, 3, 'task-3', 'mlx:qwen', datetime('now'), 'hola', 1);
                 INSERT INTO ratings (trial, score, note, rated_by) VALUES
                     (1, 5, 'beautiful', 'conversations/hello'),
                     (2, 4, 'fine', 'conversations/hello'),
                     (3, 2, 'stiff', 'user');",
            )?;
            let call = |task: i64| json!({"text": "", "tool_calls": [{"id": "c", "name": "rate",
                "arguments": json!({"task": task, "score": 5, "note": "n"}).to_string()}]})
            .to_string();
            c.execute("INSERT INTO entries (id, kind, body, model) VALUES (1, 'assistant', ?1, 'mlx:qwen')", params![call(1)])?;
            c.execute("INSERT INTO entries (id, parent, kind, body, model) VALUES (2, 1, 'assistant', ?1, 'mlx:qwen')", params![call(2)])?;
            c.execute("INSERT INTO conversations VALUES ('hello', 2)", [])?;
        }
        for _ in 0..2 {
            // Twice: opening again changes nothing.
            let db = crate::journal::Journal::open(&path)?;
            let c = db.conn();
            let ts = tasks(c)?;
            let get = |id: i64| ts.iter().find(|t| t.id == id).unwrap();
            // Filed in a chat: the user's now, with the chat kept as the origin.
            assert_eq!((&*get(1).created_by, get(1).origin.as_deref()), (&*me(), Some("hello")));
            // Qwen rated its own poem: void, and waiting for the user.
            assert_eq!((get(1).state, get(1).run_state, get(1).score), (TaskState::InGates, Some(RunState::NeedsRating), None));
            // Qwen rated Gemma's run: kept, with the rater's model recorded.
            assert_eq!((get(2).state, get(2).score), (TaskState::Done, Some(4)));
            let raters: Vec<(i64, String, Option<String>)> = c
                .prepare("SELECT trial, rated_by, rater_model FROM ratings ORDER BY trial")?
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                .collect::<rusqlite::Result<_>>()?;
            assert_eq!(raters, vec![(2, "conversations/hello".into(), Some("mlx:qwen".into())), (3, me(), None)]);
            // A task an agent's run delegated is still that run's.
            assert_eq!((&*get(4).created_by, get(4).origin.as_deref()), ("conversations/task-3", None));
        }
        let _ = std::fs::remove_file(&path);
        Ok(())
    }
}
