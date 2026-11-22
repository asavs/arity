//! The dispatcher: turns tasks for an agent into runs, delivers finished
//! results back to their parent conversation, and wakes idle parents (chats or
//! runs) once their results are ready. `work_once` does one unit of work and
//! returns; whoever hosts the dispatcher (the app while it is open, or
//! `arity work`) calls it in a loop.
//!
//! Claims are leases on the trial or conversation row, taken in a write
//! transaction, so two dispatchers never run the same work, and a run whose
//! dispatcher died is picked up again from the journal once its lease runs out,
//! or as soon as its conversation's turn lease lapses, which takes seconds
//! rather than minutes.
//!
//! Recursively, a conversation that delegates work (whether a chat or a worker
//! run) is that task's parent. Each child's finished result is delivered to its
//! parent like any message (`report_when`: after its turn by default, or steer).
//! An idle or waiting parent is woken once all its delegated sub-tasks have
//! reported (or immediately on a steer). After the parent integrates the result
//! in its turn, the harness's post-use hook (`hooks::after_turn`) grades the
//! child run.
//!
//! A run whose model cannot be reached (signed out, key gone, server down) is
//! abandoned: it ends without a result, is never reported or scored, and the
//! task is picked again among models that can be reached. Failures are told
//! only through `reveal`, since they name the model or its provider.

use std::path::Path;

use anyhow::Result;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::json;

use crate::board::{LEASE_SECS, now};
use crate::journal::{Journal, chain, insert};
use crate::leaderboard::{self, Candidate, Choice};
use crate::turn::{self, Delivery};
use crate::{agent, config};

/// What is said about a failing run before it is rated. Nothing in it hints at the model.
pub const FAILING: &str = "The last attempt failed; it will be tried again";
const RETRYING: &str = "failed; retrying in a minute";
const ABANDONED: &str = "could not reach its model; the task will be picked again";
const UNFINISHED: &str = "stopped without finishing; the task will be picked again";

/// What one call did, for logging.
pub enum Did {
    Nothing,
    /// A result was passed on. `parent` is the conversation that will handle it, if any.
    Reported { task: i64, parent: Option<String> },
    Ran { task: i64, trial: i64 },
    /// An idle parent conversation (such as a chat) was woken to handle returning results.
    Woke { conv: String },
    /// The run stopped to wait for its sub-tasks.
    Waiting { task: i64, trial: i64 },
    Failed { task: i64, trial: i64, error: String },
    WakeFailed { conv: String, error: String },
}

impl Did {
    /// A line describing it, as `arity work` prints it. None for nothing done.
    pub fn describe(&self) -> Option<String> {
        Some(match self {
            Did::Nothing => return None,
            Did::Ran { task, trial } => format!("task {task}: run {trial} finished"),
            Did::Woke { conv } => format!("{conv}: handled returning results"),
            Did::Waiting { task, trial } => format!("task {task}: run {trial} waits for its sub-tasks"),
            Did::Failed { task, trial, error } => format!("task {task}: run {trial} {error}"),
            Did::WakeFailed { conv, error } => format!("{conv}: {error}"),
            Did::Reported { task, parent: Some(conv) } => format!("task {task}: reported to {conv}"),
            Did::Reported { task, parent: None } => format!("task {task}: waiting for your rating"),
        })
    }
}

enum Work {
    Trial { trial: i64, task: i64, conv: String, model: String },
    Chat { conv: String, model: String },
}

pub fn work_once(db: &Journal, data_dir: &Path) -> Result<Did> {
    work_once_with(db, data_dir, &|| crate::candidates(data_dir))
}

/// `reachable` lists the models that can be reached now; it is asked only when
/// there is work, since it may check local servers.
pub fn work_once_with(db: &Journal, data_dir: &Path, reachable: &dyn Fn() -> Result<Vec<Candidate>>) -> Result<Did> {
    if let Some(did) = deliver_report(db)? {
        return Ok(did);
    }
    db.immediate(wake_settled)?;
    let waiting: bool = db.conn().query_row(
        "SELECT EXISTS (SELECT 1 FROM tasks WHERE assignee = 'agent' AND state = 'ready')
             OR EXISTS (SELECT 1 FROM trials WHERE ended IS NULL AND waiting = 0)
             OR EXISTS (SELECT 1 FROM conversations WHERE wake_at > 0 AND wake_at <= ?1)",
        params![now()],
        |r| r.get(0),
    )?;
    if !waiting {
        return Ok(Did::Nothing);
    }
    let reach = reachable()?;
    let Some(work) = db.immediate(|c| claim(c, data_dir, &reach))? else {
        return Ok(Did::Nothing);
    };
    match work {
        Work::Trial { trial, task, conv, model } => {
            // Renew the lease after every step, so a long run is not taken over.
            let mut renew = |_: crate::Event| {
                let _ = db.conn().execute("UPDATE trials SET lease_until = ?2 WHERE id = ?1", params![trial, now() + LEASE_SECS]);
            };
            let ran = agent::run(db, data_dir, &conv, &model, &mut renew).and_then(|outcome| match outcome {
                agent::Outcome::Busy => Err(anyhow::anyhow!("another turn is running in {conv}")),
                outcome => Ok(outcome),
            });
            match ran {
                Ok(outcome) => settle(db, trial, task, &conv, false, outcome),
                Err(e) => {
                    let detail = format!("{e:#}");
                    if !reach.iter().any(|c| c.model == model) {
                        // Its model is out of reach: end this run unscored and let the task be picked again.
                        abandon(db, trial, task, &detail)?;
                        return Ok(Did::Failed { task, trial, error: ABANDONED.into() });
                    }
                    // Leave the run open and retry it in a minute; the journal knows where it stopped.
                    db.conn().execute(
                        "UPDATE trials SET error = ?2, lease_until = ?3 WHERE id = ?1",
                        params![trial, detail, now() + 60],
                    )?;
                    Ok(Did::Failed { task, trial, error: RETRYING.into() })
                }
            }
        }
        Work::Chat { conv, model } => {
            let mut renew = |_: crate::Event| {
                let _ = db.conn().execute("UPDATE conversations SET wake_at = ?2 WHERE name = ?1", params![conv, now() + LEASE_SECS]);
            };
            match agent::run(db, data_dir, &conv, &model, &mut renew) {
                Ok(agent::Outcome::Busy) => Ok(Did::Nothing),
                Ok(_) => {
                    crate::board::clear_wake(db.conn(), &conv)?;
                    Ok(Did::Woke { conv })
                }
                Err(_) => {
                    if !reach.iter().any(|c| c.model == model) {
                        crate::board::clear_wake(db.conn(), &conv)?;
                        return Ok(Did::WakeFailed { conv, error: "model unreachable; result left in thread".into() });
                    }
                    db.conn().execute("UPDATE conversations SET wake_at = ?2 WHERE name = ?1", params![conv, now() + 60])?;
                    Ok(Did::WakeFailed { conv, error: RETRYING.into() })
                }
            }
        }
    }
}

fn abandon(db: &Journal, trial: i64, task: i64, error: &str) -> Result<()> {
    db.immediate(|c| {
        c.execute(
            "UPDATE trials SET ended = datetime('now'), error = ?2, abandoned = 1, reported = 1 WHERE id = ?1",
            params![trial, error],
        )?;
        c.execute("UPDATE tasks SET state = 'open', edits = edits + 1 WHERE id = ?1", params![task])?;
        crate::board::refresh_ready(c)
    })
}

/// Record how a turn of `conv`'s trial ended, whether run by the dispatcher or
/// when the user messages a worker directly.
pub(crate) fn settle(db: &Journal, trial: i64, task: i64, conv: &str, ended_before: bool, outcome: agent::Outcome) -> Result<Did> {
    match outcome {
        agent::Outcome::Busy => Ok(Did::Nothing),
        // Follow-up reply after a finished run, without replacing its report.
        agent::Outcome::Ended if ended_before => {
            db.conn().execute("UPDATE trials SET error = NULL WHERE id = ?1", params![trial])?;
            Ok(Did::Nothing)
        }
        agent::Outcome::Waiting => {
            db.conn().execute("UPDATE trials SET waiting = 1, lease_until = 0, error = NULL WHERE id = ?1", params![trial])?;
            Ok(Did::Waiting { task, trial })
        }
        agent::Outcome::Unfinished => {
            abandon(db, trial, task, UNFINISHED)?;
            Ok(Did::Failed { task, trial, error: UNFINISHED.into() })
        }
        outcome => {
            // Finished with a report; or stopped by someone, when the last reply is all there is.
            let result = match outcome {
                // The outcome heads the result, so every place that shows it says so.
                agent::Outcome::Finished(report) => {
                    let said: String = db.conn().query_row("SELECT outcome FROM trials WHERE id = ?1", params![trial], |r| r.get(0))?;
                    match said.as_str() {
                        "blocked" => format!("Blocked: {report}"),
                        "needs_review" => format!("Needs review: {report}"),
                        _ => report,
                    }
                }
                _ => db
                    .chain(conv)?
                    .iter()
                    .rev()
                    .find(|e| e.kind == "assistant")
                    .and_then(|e| e.body["text"].as_str().map(String::from))
                    .unwrap_or_default(),
            };
            db.immediate(|c| {
                c.execute(
                    "UPDATE trials SET ended = coalesce(ended, datetime('now')), result = ?2, error = NULL, waiting = 0, reported = 0 WHERE id = ?1",
                    params![trial, result],
                )?;
                // Reported work waits in gates; the rating is the one gate for now.
                c.execute("UPDATE tasks SET state = 'in_gates', edits = edits + 1 WHERE id = ?1 AND state != 'done'", params![task])?;
                Ok(())
            })?;
            Ok(Did::Ran { task, trial })
        }
    }
}

/// Take over a run whose dispatcher died, wake an idle parent conversation, or
/// start a run for the most pressing ready task.
/// A run is taken over once its lease runs out, or sooner when it has not been
/// renewed for a little while and no turn is running in its conversation: the
/// conversation's own lease lapses within seconds of its process dying.
fn claim(c: &Connection, data_dir: &Path, reachable: &[Candidate]) -> Result<Option<Work>> {
    let stale: Option<(i64, i64, String, String)> = c
        .query_row(
            "SELECT id, task, conversation, model FROM trials tr
             WHERE ended IS NULL AND waiting = 0 AND (lease_until < ?1 OR (
                 lease_until < ?1 + ?2 - ?3
                 AND NOT EXISTS (SELECT 1 FROM turns WHERE conversation = tr.conversation AND lease_until >= ?1)))
             ORDER BY id LIMIT 1",
            params![now(), LEASE_SECS, crate::turn::LEASE_SECS],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()?;
    if let Some((trial, task, conv, model)) = stale {
        c.execute("UPDATE trials SET lease_until = ?2 WHERE id = ?1", params![trial, now() + LEASE_SECS])?;
        return Ok(Some(Work::Trial { trial, task, conv, model }));
    }
    // An idle parent conversation woken by returning child reports.
    let mut stmt = c.prepare(
        "SELECT name FROM conversations
         WHERE wake_at > 0 AND (wake_at <= ?1 OR (
             wake_at < ?1 + ?2 - ?3
             AND NOT EXISTS (SELECT 1 FROM turns WHERE conversation = name AND lease_until >= ?1)))
           AND NOT EXISTS (SELECT 1 FROM turns WHERE conversation = name AND lease_until >= ?1)
         ORDER BY wake_at, name",
    )?;
    let woken: Vec<String> = stmt
        .query_map(params![now(), LEASE_SECS, crate::turn::LEASE_SECS], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    for conv in woken {
        if let Some(model) = crate::board::parent_model(c, &conv)? {
            c.execute("UPDATE conversations SET wake_at = ?2 WHERE name = ?1", params![conv, now() + LEASE_SECS])?;
            return Ok(Some(Work::Chat { conv, model }));
        }
        c.execute("UPDATE conversations SET wake_at = 0 WHERE name = ?1", params![conv])?;
    }
    // Ready tasks for an agent, most pressing first. The blind pick draws among
    // the top three reachable workers the task's choice allows (an empty
    // `workers` list allows every reachable model). A task no worker suits is
    // skipped rather than blocking the rest.
    let workers = config::load(data_dir)?.workers;
    let pool: Vec<Candidate> = reachable.iter().filter(|c| workers.is_empty() || workers.contains(&c.model)).cloned().collect();
    let names: Vec<String> = pool.iter().map(|c| c.model.clone()).collect();
    let board = leaderboard::standings(&leaderboard::public(), &leaderboard::own(c)?, &names);
    let mut stmt = c.prepare(
        "SELECT id, title, brief, choice FROM tasks
         WHERE assignee = 'agent' AND state = 'ready'
         ORDER BY priority DESC, due IS NULL, due, id",
    )?;
    let waiting: Vec<(i64, String, Option<String>, String)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
        .collect::<rusqlite::Result<_>>()?;
    let picked = waiting.into_iter().find_map(|(task, title, brief, choice)| {
        let choice = Choice::parse(&choice).unwrap_or(Choice::Smart);
        let model = leaderboard::blind(choice, &pool, &board, &mut |n| rand::random_range(0..n)).ok()?;
        Some((task, title, brief, model))
    });
    let Some((task, title, details, model)) = picked else { return Ok(None) };
    // Each run has its own conversation; a task reopened and run again gets a fresh one.
    let earlier: i64 = c.query_row("SELECT count(*) FROM trials WHERE task = ?1", params![task], |r| r.get(0))?;
    let conv = if earlier == 0 { format!("task-{task}") } else { format!("task-{task}-{}", earlier + 1) };
    c.execute(
        "INSERT INTO trials (task, conversation, model, lease_until) VALUES (?1, ?2, ?3, ?4)",
        params![task, conv, model, now() + LEASE_SECS],
    )?;
    let trial = c.last_insert_rowid();
    c.execute("UPDATE tasks SET state = 'assigned', edits = edits + 1 WHERE id = ?1", params![task])?;
    let brief = match details {
        Some(d) if !d.trim().is_empty() => format!("{title}\n\n{d}"),
        _ => title,
    };
    insert(c, &conv, "user", &json!({ "text": brief }), None)?;
    Ok(Some(Work::Trial { trial, task, conv, model }))
}

/// Deliver one finished result back to its parent conversation (chat or worker
/// run) as a standard dispatch completion. No rating instructions are injected:
/// once the parent integrates the result in its turn, `hooks::after_turn` runs
/// the rating hook automatically.
///
/// If the parent has no model to wake (a finished run, an empty chat, or a task
/// filed directly by the user outside any conversation), the result waits for
/// the user to rate.
pub(crate) fn deliver_report(db: &Journal) -> Result<Option<Did>> {
    db.immediate(|c| {
        let row: Option<(i64, i64, String, String, Option<String>, String, String)> = c
            .query_row(
                "SELECT tr.id, t.id, t.title, t.created_by, t.origin, ifnull(tr.result, ''), t.report_when
                 FROM trials tr JOIN tasks t ON t.id = tr.task
                 WHERE tr.ended IS NOT NULL AND tr.reported = 0 ORDER BY tr.id LIMIT 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?)),
            )
            .optional()?;
        let Some((trial, task, title, created_by, origin, result, report_when)) = row else { return Ok(None) };
        c.execute("UPDATE trials SET reported = 1 WHERE id = ?1", params![trial])?;
        let text = format!("Task {task}, \"{title}\", is finished. The result:\n\n{result}");
        let body = json!({ "text": text, "task": task, "trial": trial });
        let parent = created_by.strip_prefix("conversations/").or(origin.as_deref());
        let Some(parent) = parent else {
            return Ok(Some(Did::Reported { task, parent: None }));
        };
        if crate::board::parent_model(c, parent)?.is_none() {
            // Finished run or empty chat with no model: leave quietly in inbox, wait for user.
            turn::deliver(c, parent, "report", &body, Delivery::Inbox)?;
            return Ok(Some(Did::Reported { task, parent: None }));
        }
        let how = Delivery::parse(&report_when).unwrap_or(Delivery::AfterTurn);
        let straight = turn::deliver(c, parent, "report", &body, how)?;
        if straight {
            if how != Delivery::AfterTurn || crate::board::children_out(c, parent)?.is_empty() {
                crate::board::wake(c, parent)?;
            } else {
                crate::board::wait_for_children(c, parent)?;
            }
        }
        Ok(Some(Did::Reported { task, parent: Some(parent.to_string()) }))
    })
}

/// Wake runs and chats that are waiting with nothing left out: their sub-tasks
/// have all reported, or were closed without a result.
fn wake_settled(c: &Connection) -> Result<()> {
    let mut stmt = c.prepare("SELECT id, conversation FROM trials WHERE waiting = 1 AND ended IS NULL")?;
    let waiting: Vec<(i64, String)> = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
    for (trial, conv) in waiting {
        if !crate::board::children_out(c, &conv)?.is_empty() {
            continue;
        }
        // A result is what it waited for; if none came, say so, so it does not stop again at once.
        if chain(c, &conv)?.last().is_none_or(|e| e.kind != "report") {
            let text = "Your sub-tasks are settled, and not all of them reported a result. Use what you have and call finish.";
            insert(c, &conv, "notice", &json!({ "text": text }), None)?;
        }
        c.execute("UPDATE trials SET waiting = 0, lease_until = 0 WHERE id = ?1", params![trial])?;
    }
    let mut stmt = c.prepare("SELECT name FROM conversations WHERE wake_at = -1")?;
    let chats: Vec<String> = stmt.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    for conv in chats {
        if crate::board::children_out(c, &conv)?.is_empty() {
            c.execute("UPDATE conversations SET wake_at = 1 WHERE name = ?1", params![conv])?;
        }
    }
    Ok(())
}

/// `work_once_with`, with runs and woken chats on a scripted model instead of a
/// real one. For tests that drive the whole dispatcher without a network.
#[cfg(test)]
pub(crate) fn work_once_scripted(db: &Journal, data_dir: &Path, reach: &[Candidate], complete: &agent::Complete) -> Result<Did> {
    if let Some(did) = deliver_report(db)? {
        return Ok(did);
    }
    db.immediate(wake_settled)?;
    let Some(work) = db.immediate(|c| claim(c, data_dir, reach))? else {
        return Ok(Did::Nothing);
    };
    match work {
        Work::Trial { trial, task, conv, model } => {
            let mut renew = |_: crate::Event| {
                let _ = db.conn().execute("UPDATE trials SET lease_until = ?2 WHERE id = ?1", params![trial, now() + LEASE_SECS]);
            };
            match agent::run_with(db, &conv, &model, complete, &mut renew)? {
                agent::Outcome::Busy => anyhow::bail!("another turn is running in {conv}"),
                outcome => settle(db, trial, task, &conv, false, outcome),
            }
        }
        Work::Chat { conv, model } => {
            let mut renew = |_: crate::Event| {
                let _ = db.conn().execute("UPDATE conversations SET wake_at = ?2 WHERE name = ?1", params![conv, now() + LEASE_SECS]);
            };
            match agent::run_with(db, &conv, &model, complete, &mut renew)? {
                agent::Outcome::Busy => anyhow::bail!("another turn is running in {conv}"),
                _ => {
                    crate::board::clear_wake(db.conn(), &conv)?;
                    Ok(Did::Woke { conv })
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::board::{self, TaskFields};

    /// A task filed outside any conversation (or in an empty chat with no model)
    /// waits for the user and wakes nothing. A task filed in a chat with a model
    /// reports back to the chat with no rating instructions injected, and wakes it.
    #[test]
    fn report_delivery_wakes_a_parent_chat_and_leaves_direct_tasks_for_the_user() -> Result<()> {
        let dir = std::env::temp_dir().join(format!("arity-deliver-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Journal::open(&dir.join("arity.sqlite"))?;
        let c = db.conn();
        // 1. Direct task outside any conversation: waits for the user, wakes nothing.
        let f = TaskFields { title: "direct poem".into(), assignee: board::AGENT.into(), ..Default::default() };
        let direct = board::add_task(c, &f, None)?;
        c.execute("INSERT INTO trials (task, conversation, model, ended, result) VALUES (?1, 'task-1', 'mlx:m', datetime('now'), 'violets')", params![direct])?;
        board::set_state(c, direct, board::TaskState::InGates)?;
        assert!(matches!(deliver_report(&db)?, Some(Did::Reported { task: 1, parent: None })));
        assert_eq!(board::task(c, direct)?.run_state, Some(board::RunState::NeedsRating));

        // 2. Task filed in a chat that has a model: delivers cleanly (no "Rate it" text) and marks the chat to wake.
        db.append("chat", "assistant", &json!({ "text": "on it" }), Some("codex:x"))?;
        let g = TaskFields { title: "write a poem".into(), assignee: board::AGENT.into(), ..Default::default() };
        let from_chat = board::add_task(c, &g, Some("chat"))?;
        c.execute(
            "INSERT INTO trials (task, conversation, model, ended, result) VALUES (?1, 'task-2', 'mlx:m', datetime('now'), 'roses')",
            params![from_chat],
        )?;
        board::set_state(c, from_chat, board::TaskState::InGates)?;
        assert!(matches!(deliver_report(&db)?, Some(Did::Reported { task: 2, parent: Some(ref p) }) if p == "chat"));
        let chat = db.chain("chat")?;
        let note = chat.last().unwrap();
        assert_eq!(note.kind, "report");
        assert!(note.body["text"].as_str().unwrap().contains("roses") && !note.body["text"].as_str().unwrap().contains("Rate"));
        let wake_at: i64 = c.query_row("SELECT wake_at FROM conversations WHERE name = 'chat'", [], |r| r.get(0))?;
        assert_eq!(wake_at, 1);
        assert!(deliver_report(&db)?.is_none(), "delivered once");
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// A run whose model is out of reach ends, unscored, and the task is picked
    /// again; nothing said about it before a rating names the model.
    #[test]
    fn an_unreachable_model_gives_the_task_back_without_naming_it() -> Result<()> {
        let dir = std::env::temp_dir().join(format!("arity-unreachable-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Journal::open(&dir.join("arity.sqlite"))?;
        let c = db.conn();
        std::fs::write(dir.join("config.json"), json!({ "workers": ["codex:gone", "lab:here"] }).to_string())?;
        let f = TaskFields { title: "write a poem".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(c, &f, None)?;
        // It was picked while ChatGPT was signed in; the sign-in is gone now, and its dispatcher died.
        c.execute(
            "INSERT INTO trials (task, conversation, model, lease_until) VALUES (?1, 'task-1', 'codex:gone', 0)",
            params![task],
        )?;
        insert(c, "task-1", "user", &json!({ "text": "write a poem" }), None)?;
        board::set_state(c, task, board::TaskState::Assigned)?;
        let nothing = || Ok(vec![]);
        let did = work_once_with(&db, &dir, &nothing)?;
        let said = did.describe().unwrap();
        let Did::Failed { error, trial, .. } = did else { panic!("expected a failure") };
        assert_eq!(error, ABANDONED);
        let stored = crate::BoardTask::from(board::task(c, task)?);
        for said in [said, stored.error.unwrap_or_default()] {
            assert!(!said.contains("codex") && !said.contains("ChatGPT") && !said.contains("signed"), "{said}");
        }
        let t = board::task(c, task)?;
        assert_eq!((t.state, t.run_state, t.trial), (board::TaskState::Ready, None, None), "back to be picked again");
        assert!(board::reveal(c, trial)?.starts_with("codex:gone (failed: "), "the detail is kept for reveal");
        assert!(board::runs(c)?.is_empty() && board::scores(c)?.is_empty(), "not a run, not scored");
        assert!(board::rate(c, trial, 5, "", &board::me(), None).is_err());
        // Nothing reachable: the task waits instead of retrying the same model.
        assert!(matches!(work_once_with(&db, &dir, &nothing)?, Did::Nothing));
        let tried: i64 = c.query_row("SELECT count(*) FROM trials", [], |r| r.get(0))?;
        assert_eq!(tried, 1);
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// A run whose dispatcher died is taken over once its conversation's turn
    /// lease has lapsed, not ten minutes later.
    #[test]
    fn a_dead_dispatchers_run_is_taken_over_quickly() -> Result<()> {
        let dir = std::env::temp_dir().join(format!("arity-deadrun-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Journal::open(&dir.join("arity.sqlite"))?;
        let c = db.conn();
        std::fs::write(dir.join("config.json"), json!({ "workers": [] }).to_string())?;
        let f = TaskFields { title: "t".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(c, &f, None)?;
        // Last renewed 30 s ago, lease still has minutes to run.
        c.execute(
            "INSERT INTO trials (task, conversation, model, lease_until) VALUES (?1, 'task-1', 'lab:m', ?2)",
            params![task, now() + LEASE_SECS - 30],
        )?;
        // While a turn runs in its conversation it is alive.
        c.execute("INSERT INTO turns (conversation, owner, lease_until) VALUES ('task-1', 'x', ?1)", params![now() + 10])?;
        assert!(claim(c, &dir, &[])?.is_none());
        // Its process died: the turn lease lapsed.
        c.execute("UPDATE turns SET lease_until = ?1", params![now() - 1])?;
        assert!(matches!(claim(c, &dir, &[])?, Some(Work::Trial { trial: 1, .. })));
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// A run that hands part of its task on and stops waits for the result,
    /// holding no lease. The result is delivered with no rating instructions;
    /// once nothing is left out it is woken, finishes using the result, and the
    /// post-use parent hook grades the child automatically.
    /// A sub-task that asked to steer wakes it at once.
    #[test]
    fn a_run_waits_for_its_sub_task_then_rates_it_and_finishes() -> Result<()> {
        use crate::agent::{Outcome, run_with};
        use crate::model::{Reply, ToolCall};
        let dir = std::env::temp_dir().join(format!("arity-wait-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Journal::open(&dir.join("arity.sqlite"))?;
        let c = db.conn();
        let f = TaskFields { title: "plan a trip".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(c, &f, None)?;
        c.execute("INSERT INTO trials (task, conversation, model, lease_until) VALUES (?1, 'task-1', 'a', ?2)", params![task, now() + 60])?;
        db.append("task-1", "user", &json!({ "text": "plan a trip" }), None)?;
        let call = |name: &str, args: serde_json::Value| Reply {
            text: String::new(),
            tool_calls: vec![ToolCall { id: format!("{name}-{}", rand::random::<u32>()), name: name.into(), arguments: args.to_string() }],
            interrupted: false,
        };
        let model = |_: &str, chain: &[crate::journal::Entry], _: &[crate::tools::ToolSpec], _: &mut crate::model::Sink| {
            let last = chain.iter().rev().find(|e| e.kind != "system").unwrap();
            if last.body["hook"] == "rate" {
                return Ok(call(crate::hooks::SUBMIT_RATING, json!({ "score": 4, "note": "useful" })));
            }
            Ok(match (last.kind.as_str(), last.body["name"].as_str()) {
                ("user", _) => call("add_task", json!({ "title": "find trains", "assignee": "agent", "brief": "List two." })),
                ("tool", Some("add_task")) => Reply { text: "Waiting for the trains.".into(), tool_calls: vec![], interrupted: false },
                ("report", _) => call("finish", json!({ "report": "Take the 9:10." })),
                other => panic!("unexpected {other:?}"),
            })
        };
        assert_eq!(run_with(&db, "task-1", "a", &model, &mut |_| {})?, Outcome::Waiting);
        c.execute("UPDATE trials SET waiting = 1, lease_until = 0 WHERE id = 1", [])?;
        assert_eq!(board::task(c, task)?.run_state, Some(board::RunState::Waiting));
        assert!(db.immediate(|c| claim(c, &dir, &[]))?.is_none(), "a waiting run is not taken over");
        // Another model does the sub-task.
        c.execute("INSERT INTO trials (task, conversation, model, ended, result) VALUES (2, 'task-2', 'b', datetime('now'), '9:10 and 11:40')", [])?;
        board::set_state(c, 2, board::TaskState::InGates)?;
        assert!(matches!(deliver_report(&db)?, Some(Did::Reported { parent: Some(ref p), .. }) if p == "task-1"));
        let report = db.chain("task-1")?.last().unwrap().clone();
        assert_eq!(report.kind, "report");
        assert!(!report.body["text"].as_str().unwrap().contains("Rate it"), "reports carry no rating instructions");
        db.immediate(wake_settled)?;
        assert!(matches!(db.immediate(|c| claim(c, &dir, &[]))?, Some(Work::Trial { trial: 1, .. })));
        assert_eq!(run_with(&db, "task-1", "a", &model, &mut |_| {})?, Outcome::Finished("Take the 9:10.".into()));
        let (score, by, rater): (i64, String, String) =
            c.query_row("SELECT score, rated_by, rater_model FROM ratings WHERE trial = 2", [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        assert_eq!((score, by.as_str(), rater.as_str()), (4, "conversations/task-1", "a"));

        // A sub-task that asked to steer wakes its waiting run at once, though another is still out.
        let g = TaskFields { title: "two halves".into(), assignee: board::AGENT.into(), ..Default::default() };
        board::add_task(c, &g, None)?;
        c.execute("INSERT INTO trials (task, conversation, model, waiting) VALUES (3, 'task-3', 'a', 1)", [])?;
        let half = |t: &str| TaskFields { title: t.into(), assignee: board::AGENT.into(), ..Default::default() };
        board::add_task(c, &half("first half"), Some("task-3"))?;
        board::add_task(c, &half("second half"), Some("task-3"))?;
        c.execute("UPDATE tasks SET report_when = 'steer' WHERE id = 4", [])?;
        c.execute("INSERT INTO trials (task, conversation, model, ended, result) VALUES (4, 'task-4', 'b', datetime('now'), 'half')", [])?;
        deliver_report(&db)?;
        let waiting: bool = c.query_row("SELECT waiting FROM trials WHERE conversation = 'task-3'", [], |r| r.get(0))?;
        assert!(!waiting, "woken at once while task 5 is still out");
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }
}
