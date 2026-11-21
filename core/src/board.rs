//! The board: tasks, and for tasks given to an agent, the trials that did them
//! and the ratings those trials got.
//!
//! A task follows the wiki's states: open → ready → assigned → in gates → done.
//! Ready is decided by blockers and recomputed after every write. A task whose
//! assignee is `agent` wakes the dispatcher once it is ready: it claims the
//! task by creating a trial, which picks a model blind and runs a worker in its
//! own conversation. The finished result goes back to whoever wrote the task
//! and waits in gates until it is rated; the rating is the only gate for now.
//! The model stays hidden until then.

use anyhow::{Result, anyhow, bail};
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

/// How long a claim on a trial lasts before another dispatcher may take it over.
pub const LEASE_SECS: i64 = 600;

/// The assignee that wakes the dispatcher: any agent, picked blind.
pub const AGENT: &str = "agent";

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
             reported     INTEGER NOT NULL DEFAULT 0
         );
         CREATE TABLE IF NOT EXISTS ratings (
             trial    INTEGER PRIMARY KEY REFERENCES trials(id),
             score    INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
             note     TEXT NOT NULL,
             rated_by TEXT NOT NULL,
             rated_at TEXT NOT NULL DEFAULT (datetime('now'))
         );",
    )?;
    let cols: Vec<String> = tx
        .prepare("SELECT name FROM pragma_table_info('tasks')")?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    if cols.is_empty() {
        create_tasks(&tx, "tasks")?;
    } else if !cols.iter().any(|c| c == "assignee") {
        migrate_v0(&tx, &cols)?;
        let broken: i64 = tx.query_row("SELECT count(*) FROM pragma_foreign_key_check", [], |r| r.get(0))?;
        anyhow::ensure!(broken == 0, "migrating tasks would break {broken} links; nothing was changed");
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
             created_by TEXT NOT NULL,            -- people/<login>, or conversations/<name>
             state      TEXT NOT NULL DEFAULT 'open'
                        CHECK (state IN ('open', 'ready', 'assigned', 'in_gates', 'done')),
             clearance  TEXT NOT NULL DEFAULT 'any' CHECK (clearance IN ('any', 'local')),
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum Clearance {
    /// Any configured model may do it.
    Any,
    /// Only models running on this computer (`mlx:`).
    Local,
}

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

impl Clearance {
    pub fn name(self) -> &'static str {
        match self {
            Clearance::Any => "any",
            Clearance::Local => "local",
        }
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
    pub clearance: Clearance,
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
            clearance: Clearance::Any,
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
            "clearance": self.clearance.name(),
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
                "clearance" => {
                    self.clearance = match s().to_lowercase().as_str() {
                        "any" | "" => Clearance::Any,
                        "local" | "local only" => Clearance::Local,
                        other => bail!("clearance must be any or local, not {other:?}"),
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
                    self.tags = self.tags.iter().map(|t| t.trim().to_string()).filter(|t| !t.is_empty()).collect();
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
        out.push(format!("clearance: {}", self.clearance.name()));
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
        _ => bail!("assignee must be \"agent\" or \"user\", not {a:?}"),
    })
}

fn is_date(d: &str) -> bool {
    let b = d.as_bytes();
    b.len() == 10
        && b[4] == b'-'
        && b[7] == b'-'
        && b.iter().enumerate().all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit())
}

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
    pub state: TaskState,
    pub trial: Option<i64>,
    pub run_state: Option<RunState>,
    pub conversation: Option<String>,
    pub result: Option<String>,
    pub error: Option<String>,
    pub score: Option<i64>,
}

/// Every task, the ones still to do first, then by priority, due date and age.
pub fn tasks(conn: &Connection) -> Result<Vec<TaskRow>> {
    let blockers = all_blockers(conn)?;
    let mut stmt = conn.prepare(
        "SELECT t.id, t.title, ifnull(t.brief, ''), t.assignee, t.created_by, t.state, t.clearance,
                t.priority, t.due, t.tags,
                tr.id, tr.conversation, tr.ended, tr.result, tr.error, r.score
         FROM tasks t
         LEFT JOIN trials tr ON tr.id = (SELECT max(id) FROM trials WHERE task = t.id)
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
        let run_state = trial.map(|_| match (ended.is_some(), error.is_some(), score.is_some()) {
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
                clearance: if r.get::<_, String>(6)? == "local" { Clearance::Local } else { Clearance::Any },
                priority: Priority::from_rank(r.get(7)?),
                due: r.get(8)?,
                blocked_by: blockers.iter().filter(|(t, _)| *t == id).map(|(_, b)| *b).collect(),
                tags: serde_json::from_str(&tags).unwrap_or_default(),
            },
            created_by: r.get(4)?,
            state: TaskState::parse(&r.get::<_, String>(5)?),
            trial,
            run_state,
            conversation: r.get(11)?,
            result: r.get(13)?,
            error,
            score,
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

/// `created_by` is a page id: `people/<login>` or `conversations/<name>`.
pub fn add_task(conn: &Connection, f: &TaskFields, created_by: &str) -> Result<i64> {
    check(conn, None, f)?;
    conn.execute(
        "INSERT INTO tasks (title, brief, assignee, created_by, clearance, priority, due, tags)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            f.title.trim(),
            Some(f.brief.trim()).filter(|b| !b.is_empty()),
            normalize_assignee(&f.assignee)?,
            created_by,
            f.clearance.name(),
            f.priority.rank(),
            f.due,
            serde_json::to_string(&f.tags)?,
        ],
    )?;
    let id = conn.last_insert_rowid();
    write_links(conn, id, f)?;
    refresh_ready(conn)?;
    Ok(id)
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
        "UPDATE tasks SET title = ?2, brief = ?3, assignee = ?4, clearance = ?5, priority = ?6, due = ?7, tags = ?8,
             edits = edits + 1
         WHERE id = ?1",
        params![
            id,
            f.title.trim(),
            Some(f.brief.trim()).filter(|b| !b.is_empty()),
            assignee,
            f.clearance.name(),
            f.priority.rank(),
            f.due,
            serde_json::to_string(&f.tags)?,
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
    if done && matches!(t.run_state, Some(RunState::Running | RunState::Failing)) {
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

pub fn is_rated(conn: &Connection, trial: i64) -> Result<bool> {
    Ok(conn.query_row("SELECT count(*) FROM ratings WHERE trial = ?1", params![trial], |r| r.get::<_, i64>(0))? > 0)
}

/// Rate a finished run. The rating is the task's one gate, so it is done after.
pub fn rate(conn: &Connection, trial: i64, score: i64, note: &str, rated_by: &str) -> Result<()> {
    if !(1..=5).contains(&score) {
        bail!("score must be 1 to 5");
    }
    let found: Option<(i64, Option<String>)> = conn
        .query_row("SELECT task, ended FROM trials WHERE id = ?1", params![trial], |r| Ok((r.get(0)?, r.get(1)?)))
        .optional()?;
    let task = match found {
        None => bail!("no run {trial}"),
        Some((_, None)) => bail!("run {trial} has not finished"),
        Some((task, Some(_))) => task,
    };
    conn.execute(
        "INSERT INTO ratings (trial, score, note, rated_by) VALUES (?1, ?2, ?3, ?4)
         ON CONFLICT(trial) DO UPDATE SET score = excluded.score, note = excluded.note,
             rated_by = excluded.rated_by, rated_at = datetime('now')",
        params![trial, score, note, rated_by],
    )?;
    conn.execute("UPDATE tasks SET state = 'done', edits = edits + 1 WHERE id = ?1 AND state = 'in_gates'", params![task])?;
    refresh_ready(conn)
}

/// Finished, reported runs of tasks this conversation wrote that nobody has rated.
pub fn unrated_for(conn: &Connection, conv: &str) -> Result<Vec<(i64, i64)>> {
    let mut stmt = conn.prepare(
        "SELECT tr.task, tr.id FROM trials tr JOIN tasks t ON t.id = tr.task
         WHERE t.created_by = ?1 AND tr.ended IS NOT NULL AND tr.reported = 1
           AND NOT EXISTS (SELECT 1 FROM ratings r WHERE r.trial = tr.id)
         ORDER BY tr.id",
    )?;
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

pub fn reveal(conn: &Connection, trial: i64) -> Result<String> {
    conn.query_row("SELECT model FROM trials WHERE id = ?1", params![trial], |r| r.get(0))
        .optional()?
        .ok_or_else(|| anyhow!("no run {trial}"))
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
    fn blockers_decide_ready() -> Result<()> {
        let path = temp("ready");
        let db = crate::journal::Journal::open(&path)?;
        let c = db.conn();
        let a = add_task(c, &fields("a"), &me())?;
        let b = add_task(c, &TaskFields { blocked_by: vec![a], ..fields("b") }, &me())?;
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
        assert!(add_task(c, &TaskFields { blocked_by: vec![99], ..fields("c") }, &me()).is_err());
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
        assert_eq!((get(3).state, get(3).created_by.as_str()), (TaskState::Ready, "conversations/hello"));
        assert_eq!((get(4).state, get(4).fields.assignee.as_str()), (TaskState::Done, AGENT));
        assert_eq!((get(4).fields.brief.as_str(), get(4).run_state, get(4).score), ("the readme", Some(RunState::Rated), Some(4)));
        assert_eq!((get(5).state, get(5).run_state), (TaskState::Assigned, Some(RunState::Running)));
        assert_eq!(get(3).fields.priority, Priority::Normal);
        // The rating gate still finds the conversation's runs, and opening again changes nothing.
        assert!(unrated_for(c, "hello")?.is_empty());
        drop(db);
        let db = crate::journal::Journal::open(&path)?;
        assert_eq!(tasks(db.conn())?.len(), 5);
        let _ = std::fs::remove_file(&path);
        Ok(())
    }
}
