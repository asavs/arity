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

/// How many times in a row a run or a woken chat is taken over from a process
/// that died, with nothing written in between, before Arity stops trying.
pub const RESTART_LIMIT: i64 = 5;
const CRASH_LOOP: &str = "stopped after 5 restarts in a row without progress";

/// How long a run that failed `retries` times in a row waits before the next
/// try: 15 s, doubling each time, at most 5 minutes.
pub(crate) fn backoff_secs(retries: i64) -> i64 {
    (15i64 << (retries - 1).clamp(0, 5)).min(300)
}

/// Leave a run that failed open, to be tried again after its backoff; the
/// journal knows where it stopped. Returns how long it waits, in seconds.
pub(crate) fn retry_later(c: &Connection, trial: i64, error: &str) -> Result<i64> {
    let retries: i64 = c.query_row(
        "UPDATE trials SET retries = retries + 1, error = ?2, lease_until = 0 WHERE id = ?1 RETURNING retries",
        params![trial, error],
        |r| r.get(0),
    )?;
    let wait = backoff_secs(retries);
    c.execute("UPDATE trials SET retry_at = ?2 WHERE id = ?1", params![trial, now() + wait])?;
    Ok(wait)
}

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
    /// Someone stopped the run part way; it waits to be continued.
    Paused { task: i64, trial: i64 },
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
            Did::Paused { task, trial } => format!("task {task}: run {trial} was stopped; continue it to carry on"),
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
    /// A run or chat that kept dying was given up on instead of taken over again.
    GaveUp(Did),
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
             OR EXISTS (SELECT 1 FROM trials WHERE ended IS NULL AND waiting = 0 AND paused = 0 AND retry_at <= ?1)
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
        Work::GaveUp(did) => Ok(did),
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
                    let wait = retry_later(db.conn(), trial, &detail)?;
                    Ok(Did::Failed { task, trial, error: format!("failed; retrying in {wait} s") })
                }
            }
        }
        Work::Chat { conv, model } => {
            let mut renew = |_: crate::Event| {
                let _ = db.conn().execute("UPDATE conversations SET lease_until = ?2 WHERE name = ?1", params![conv, now() + LEASE_SECS]);
            };
            match agent::run(db, data_dir, &conv, &model, &mut renew) {
                Ok(agent::Outcome::Busy) => {
                    db.conn().execute("UPDATE conversations SET lease_until = 0 WHERE name = ?1", params![conv])?;
                    Ok(Did::Nothing)
                }
                Ok(_) => {
                    crate::board::clear_wake(db.conn(), &conv)?;
                    Ok(Did::Woke { conv })
                }
                Err(_) => {
                    if !reach.iter().any(|c| c.model == model) {
                        crate::board::clear_wake(db.conn(), &conv)?;
                        return Ok(Did::WakeFailed { conv, error: "model unreachable; result left in thread".into() });
                    }
                    db.conn().execute(
                        "UPDATE conversations SET wake_at = ?2, lease_until = 0 WHERE name = ?1",
                        params![conv, now() + 60],
                    )?;
                    Ok(Did::WakeFailed { conv, error: RETRYING.into() })
                }
            }
        }
    }
}

/// End a run that kept dying with a failed result, so its parent hears of it
/// instead of the run being taken over forever.
fn give_up_run(c: &Connection, trial: i64, task: i64) -> Result<()> {
    let result = format!("Failed: {CRASH_LOOP}. Its process kept dying before it got anywhere.");
    c.execute(
        "UPDATE trials SET ended = datetime('now'), outcome = 'failed', result = ?2, error = NULL, waiting = 0,
             lease_until = 0, restarts = 0, reported = 0 WHERE id = ?1",
        params![trial, result],
    )?;
    c.execute("UPDATE tasks SET state = 'in_gates', edits = edits + 1 WHERE id = ?1 AND state != 'done'", params![task])?;
    Ok(())
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
        agent::Outcome::Ended | agent::Outcome::Stopped if ended_before => {
            db.conn().execute("UPDATE trials SET error = NULL WHERE id = ?1", params![trial])?;
            Ok(Did::Nothing)
        }
        // Stopped part way: what it wrote so far is not a result. It waits, unclaimed, to be continued.
        agent::Outcome::Stopped => {
            db.conn().execute(
                "UPDATE trials SET paused = 1, waiting = 0, lease_until = 0, retry_at = 0, retries = 0, error = NULL
                 WHERE id = ?1 AND ended IS NULL",
                params![trial],
            )?;
            Ok(Did::Paused { task, trial })
        }
        agent::Outcome::Waiting => {
            db.conn().execute(
                "UPDATE trials SET waiting = 1, lease_until = 0, retries = 0, error = NULL WHERE id = ?1",
                params![trial],
            )?;
            Ok(Did::Waiting { task, trial })
        }
        agent::Outcome::Unfinished => {
            abandon(db, trial, task, UNFINISHED)?;
            Ok(Did::Failed { task, trial, error: UNFINISHED.into() })
        }
        outcome => {
            // Finished with a report; or a turn that ended with the last reply all there is.
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
                    "UPDATE trials SET ended = coalesce(ended, datetime('now')), result = ?2, error = NULL, waiting = 0, paused = 0,
                         retries = 0, reported = 0 WHERE id = ?1",
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
    // A run stopped by the user, or waiting out its backoff, is left alone.
    let stale: Option<(i64, i64, String, String, i64, i64)> = c
        .query_row(
            "SELECT id, task, conversation, model, lease_until, restarts FROM trials tr
             WHERE ended IS NULL AND waiting = 0 AND paused = 0 AND retry_at <= ?1 AND (lease_until < ?1 OR (
                 lease_until < ?1 + ?2 - ?3
                 AND NOT EXISTS (SELECT 1 FROM turns WHERE conversation = tr.conversation AND lease_until >= ?1)))
             ORDER BY id LIMIT 1",
            params![now(), LEASE_SECS, crate::turn::LEASE_SECS],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?)),
        )
        .optional()?;
    if let Some((trial, task, conv, model, lease, restarts)) = stale {
        // Every clean end lets the lease go; one still held means its process died.
        let restarts = restarts + (lease > 0) as i64;
        if restarts >= RESTART_LIMIT {
            give_up_run(c, trial, task)?;
            return Ok(Some(Work::GaveUp(Did::Failed { task, trial, error: CRASH_LOOP.into() })));
        }
        c.execute("UPDATE trials SET lease_until = ?2, restarts = ?3 WHERE id = ?1", params![trial, now() + LEASE_SECS, restarts])?;
        return Ok(Some(Work::Trial { trial, task, conv, model }));
    }
    // An idle parent conversation woken by returning child reports. `lease_until`
    // is a dispatcher's hold on it while it runs; it lapses quickly once no turn runs there.
    let mut stmt = c.prepare(
        "SELECT name, lease_until, restarts FROM conversations
         WHERE wake_at > 0 AND wake_at <= ?1 AND lease_until < ?1 + ?2 - ?3
           AND NOT EXISTS (SELECT 1 FROM turns WHERE conversation = name AND lease_until >= ?1)
         ORDER BY wake_at, name",
    )?;
    let woken: Vec<(String, i64, i64)> = stmt
        .query_map(params![now(), LEASE_SECS, crate::turn::LEASE_SECS], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
        .collect::<rusqlite::Result<_>>()?;
    for (conv, lease, restarts) in woken {
        if let Some(model) = crate::board::parent_model(c, &conv)? {
            let restarts = restarts + (lease > 0) as i64;
            if restarts >= RESTART_LIMIT {
                c.execute("UPDATE conversations SET wake_at = 0, lease_until = 0, restarts = 0 WHERE name = ?1", params![conv])?;
                let text = format!("Arity {CRASH_LOOP}: waking this conversation kept failing. Continue it to try again.");
                insert(c, &conv, "notice", &json!({ "text": text }), None)?;
                return Ok(Some(Work::GaveUp(Did::WakeFailed { conv, error: CRASH_LOOP.into() })));
            }
            c.execute(
                "UPDATE conversations SET lease_until = ?2, restarts = ?3 WHERE name = ?1",
                params![conv, now() + LEASE_SECS, restarts],
            )?;
            return Ok(Some(Work::Chat { conv, model }));
        }
        crate::board::clear_wake(c, &conv)?;
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
        let row: Option<(i64, i64, String, String, Option<String>, String, String, String)> = c
            .query_row(
                "SELECT tr.id, t.id, t.title, t.created_by, t.origin, ifnull(tr.result, ''), t.report_when, tr.outcome
                 FROM trials tr JOIN tasks t ON t.id = tr.task
                 WHERE tr.ended IS NOT NULL AND tr.reported = 0 ORDER BY tr.id LIMIT 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?, r.get(7)?)),
            )
            .optional()?;
        let Some((trial, task, title, created_by, origin, result, report_when, outcome)) = row else { return Ok(None) };
        c.execute("UPDATE trials SET reported = 1 WHERE id = ?1", params![trial])?;
        let text = if outcome == "failed" {
            format!("Task {task}, \"{title}\", failed without a result:\n\n{result}")
        } else {
            format!("Task {task}, \"{title}\", is finished. The result:\n\n{result}")
        };
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
        Work::GaveUp(did) => Ok(did),
        Work::Trial { trial, task, conv, model } => {
            let mut renew = |_: crate::Event| {
                let _ = db.conn().execute("UPDATE trials SET lease_until = ?2 WHERE id = ?1", params![trial, now() + LEASE_SECS]);
            };
            match agent::run_with(db, &conv, &model, complete, &mut renew) {
                Ok(agent::Outcome::Busy) => anyhow::bail!("another turn is running in {conv}"),
                Ok(outcome) => settle(db, trial, task, &conv, false, outcome),
                Err(e) => {
                    let wait = retry_later(db.conn(), trial, &format!("{e:#}"))?;
                    Ok(Did::Failed { task, trial, error: format!("failed; retrying in {wait} s") })
                }
            }
        }
        Work::Chat { conv, model } => {
            let mut renew = |_: crate::Event| {
                let _ = db.conn().execute("UPDATE conversations SET lease_until = ?2 WHERE name = ?1", params![conv, now() + LEASE_SECS]);
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
            usage: None,
        };
        let model = |_: &str, chain: &[crate::journal::Entry], _: &[crate::tools::ToolSpec], _: &mut crate::model::Sink| {
            let last = chain.iter().rev().find(|e| e.kind != "system").unwrap();
            if last.body["hook"] == "rate" {
                return Ok(call(crate::hooks::SUBMIT_RATING, json!({ "score": 4, "note": "useful" })));
            }
            Ok(match (last.kind.as_str(), last.body["name"].as_str()) {
                ("user", _) => call("add_task", json!({ "title": "find trains", "assignee": "agent", "brief": "List two." })),
                ("tool", Some("add_task")) => Reply { text: "Waiting for the trains.".into(), tool_calls: vec![], interrupted: false, usage: None },
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

    fn scratch(name: &str) -> Result<(std::path::PathBuf, Journal)> {
        let dir = std::env::temp_dir().join(format!("arity-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Journal::open(&dir.join("arity.sqlite"))?;
        std::fs::write(dir.join("config.json"), json!({ "workers": [] }).to_string())?;
        Ok((dir, db))
    }

    fn trial_row(c: &Connection, trial: i64) -> Result<(Option<String>, i64, i64, i64, i64, i64)> {
        Ok(c.query_row(
            "SELECT ended, lease_until, retries, retry_at, restarts, paused FROM trials WHERE id = ?1",
            params![trial],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?)),
        )?)
    }

    #[test]
    fn a_failing_run_backs_off_longer_each_time() -> Result<()> {
        assert_eq!((1..=7).map(backoff_secs).collect::<Vec<_>>(), [15, 30, 60, 120, 240, 300, 300]);
        let (dir, db) = scratch("backoff")?;
        let c = db.conn();
        let task = board::add_task(c, &TaskFields { title: "t".into(), assignee: board::AGENT.into(), ..Default::default() }, None)?;
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (?1, 'task-1', 'lab:m')", params![task])?;
        insert(c, "task-1", "user", &json!({ "text": "t" }), None)?;
        let broken = |_: &str, _: &[crate::journal::Entry], _: &[crate::tools::ToolSpec], _: &mut crate::model::Sink| -> Result<crate::model::Reply> {
            anyhow::bail!("the tool server crashed")
        };
        for (n, wait) in [(1, 15), (2, 30), (3, 60)] {
            let did = work_once_scripted(&db, &dir, &[], &broken)?;
            assert_eq!(did.describe().unwrap(), format!("task 1: run 1 failed; retrying in {wait} s"));
            let (ended, lease, retries, retry_at, restarts, _) = trial_row(c, 1)?;
            assert_eq!((ended, lease, retries, restarts), (None, 0, n, 0), "open, let go, and not a crash");
            assert!((retry_at - now() - wait).abs() <= 1);
            assert!(claim(c, &dir, &[])?.is_none(), "not before its backoff");
            assert_eq!(board::task(c, task)?.run_state, Some(board::RunState::Failing));
            c.execute("UPDATE trials SET retry_at = 0", [])?;
        }
        // It works again: the count of failures starts over.
        let fine = |_: &str, _: &[crate::journal::Entry], _: &[crate::tools::ToolSpec], _: &mut crate::model::Sink| {
            let finish = crate::model::ToolCall { id: "f".into(), name: "finish".into(), arguments: json!({ "report": "done" }).to_string() };
            Ok(crate::model::Reply { text: String::new(), tool_calls: vec![finish], interrupted: false, usage: None })
        };
        assert!(matches!(work_once_scripted(&db, &dir, &[], &fine)?, Did::Ran { task: 1, trial: 1 }));
        assert_eq!(trial_row(c, 1)?.2, 0);
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// A run whose process keeps dying is taken over four times, then given
    /// up on with a failed result that reaches its parent. Writing anything
    /// real in between starts the count over.
    #[test]
    fn a_run_that_keeps_dying_is_given_up_after_the_restart_limit() -> Result<()> {
        let (dir, db) = scratch("crash-loop")?;
        let c = db.conn();
        db.append("chat", "assistant", &json!({ "text": "on it" }), Some("chat:m"))?;
        let f = TaskFields { title: "flaky".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(c, &f, Some("chat"))?;
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (?1, 'task-1', 'lab:m')", params![task])?;
        insert(c, "task-1", "user", &json!({ "text": "flaky" }), None)?;
        board::set_state(c, task, board::TaskState::Assigned)?;
        // Its dispatcher died holding the lease: last renewed 30 s ago, and no turn runs.
        let die = || c.execute("UPDATE trials SET lease_until = ?1 WHERE id = 1", params![now() + LEASE_SECS - 30]);

        // The first claim is not a restart: nothing held it.
        assert!(matches!(claim(c, &dir, &[])?, Some(Work::Trial { trial: 1, .. })));
        assert_eq!(trial_row(c, 1)?.4, 0);
        for n in 1..RESTART_LIMIT - 1 {
            die()?;
            assert!(matches!(claim(c, &dir, &[])?, Some(Work::Trial { trial: 1, .. })));
            assert_eq!(trial_row(c, 1)?.4, n);
        }
        // Progress: the run wrote a reply before dying. The count starts over.
        insert(c, "task-1", "assistant", &json!({ "text": "", "tool_calls": [] }), Some("lab:m"))?;
        assert_eq!(trial_row(c, 1)?.4, 0, "progress resets the count");
        insert(c, "task-1", "notice", &json!({ "text": "a reminder" }), None)?;
        for n in 1..RESTART_LIMIT {
            die()?;
            assert!(matches!(claim(c, &dir, &[])?, Some(Work::Trial { trial: 1, .. })));
            assert_eq!(trial_row(c, 1)?.4, n, "a notice is not progress");
        }
        // The fifth death in a row: given up on, not taken over again.
        die()?;
        let Some(Work::GaveUp(did)) = claim(c, &dir, &[])? else { panic!("expected to give up") };
        assert_eq!(did.describe().unwrap(), format!("task 1: run 1 {CRASH_LOOP}"));
        let (ended, lease, ..) = trial_row(c, 1)?;
        assert!(ended.is_some() && lease == 0);
        assert!(claim(c, &dir, &[])?.is_none(), "never taken over again");
        let t = board::task(c, task)?;
        assert_eq!((t.state, t.run_state), (board::TaskState::InGates, Some(board::RunState::NeedsRating)));
        assert!(t.result.unwrap().starts_with("Failed: stopped after 5 restarts"));
        // Its parent hears that it failed, and is woken to deal with it.
        assert!(matches!(deliver_report(&db)?, Some(Did::Reported { task: 1, parent: Some(ref p) }) if p == "chat"));
        let report = db.chain("chat")?.last().unwrap().clone();
        assert_eq!(report.kind, "report");
        let text = report.body["text"].as_str().unwrap();
        assert!(text.contains("failed without a result") && text.contains("5 restarts in a row"), "{text}");
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// A woken chat whose dispatcher keeps dying stops being woken, and says why.
    #[test]
    fn a_woken_chat_that_keeps_dying_stops_being_woken() -> Result<()> {
        let (dir, db) = scratch("crash-loop-chat")?;
        let c = db.conn();
        db.append("chat", "assistant", &json!({ "text": "on it" }), Some("chat:m"))?;
        db.append("chat", "report", &json!({ "text": "Task 1 is finished." }), None)?;
        board::wake(c, "chat")?;
        assert!(matches!(claim(c, &dir, &[])?, Some(Work::Chat { ref conv, .. }) if conv == "chat"));
        let held = |c: &Connection| -> (i64, i64, i64) {
            c.query_row("SELECT wake_at, lease_until, restarts FROM conversations WHERE name = 'chat'", [], |r| {
                Ok((r.get(0)?, r.get(1)?, r.get(2)?))
            })
            .unwrap()
        };
        assert_eq!((held(c).0, held(c).2), (1, 0), "still to wake, held, not a restart");
        assert!(claim(c, &dir, &[])?.is_none(), "held while it runs");
        for n in 1..RESTART_LIMIT {
            c.execute("UPDATE conversations SET lease_until = ?1 WHERE name = 'chat'", params![now() + LEASE_SECS - 30])?;
            assert!(matches!(claim(c, &dir, &[])?, Some(Work::Chat { .. })));
            assert_eq!(held(c).2, n);
        }
        c.execute("UPDATE conversations SET lease_until = ?1 WHERE name = 'chat'", params![now() + LEASE_SECS - 30])?;
        assert!(matches!(claim(c, &dir, &[])?, Some(Work::GaveUp(Did::WakeFailed { .. }))));
        assert_eq!(held(c), (0, 0, 0), "no longer woken");
        let notice = db.chain("chat")?.last().unwrap().clone();
        assert_eq!(notice.kind, "notice");
        assert!(notice.body["text"].as_str().unwrap().contains(CRASH_LOOP));
        assert!(claim(c, &dir, &[])?.is_none());
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// A run the user stopped is not finished: it hands nothing to its parent,
    /// and waits, unclaimed, until someone continues it.
    #[test]
    fn a_stopped_run_is_paused_not_reported() -> Result<()> {
        let (dir, db) = scratch("stopped-run")?;
        let c = db.conn();
        db.append("chat", "assistant", &json!({ "text": "on it" }), Some("chat:m"))?;
        let f = TaskFields { title: "essay".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(c, &f, Some("chat"))?;
        c.execute("INSERT INTO trials (task, conversation, model, lease_until) VALUES (?1, 'task-1', 'lab:m', ?2)", params![task, now() + LEASE_SECS])?;
        board::set_state(c, task, board::TaskState::Assigned)?;
        db.append("task-1", "user", &json!({ "text": "essay" }), None)?;
        db.append("task-1", "assistant", &json!({ "text": "The first half of", "interrupted": true }), Some("lab:m"))?;
        assert!(matches!(settle(&db, 1, task, "task-1", false, agent::Outcome::Stopped)?, Did::Paused { task: 1, trial: 1 }));
        let (ended, lease, .., paused) = trial_row(c, 1)?;
        assert_eq!((ended, lease, paused), (None, 0, 1));
        let t = board::task(c, task)?;
        assert_eq!((t.state, t.run_state, t.result), (board::TaskState::Assigned, Some(board::RunState::Paused), None));
        assert!(deliver_report(&db)?.is_none(), "nothing half done goes to the parent");
        assert!(claim(c, &dir, &[])?.is_none(), "not taken over");
        board::wake(c, "task-1")?;
        assert!(claim(c, &dir, &[])?.is_none(), "not woken either");
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }

    /// Stopping a chat's subtree stops its runs, their runs, and theirs: turns
    /// running are stopped, open runs paused, wakes cleared, and tasks not yet
    /// started go back to the user. Nothing in it starts again on its own;
    /// work outside it carries on.
    #[test]
    fn stopping_a_subtree_stops_every_run_below_and_none_wakes_again() -> Result<()> {
        let (dir, db) = scratch("subtree")?;
        let c = db.conn();
        let agent = |t: &str| TaskFields { title: t.into(), assignee: board::AGENT.into(), ..Default::default() };
        db.append("chat-1", "assistant", &json!({ "text": "on it" }), Some("chat:m"))?;
        // chat-1 → task 1 (running a turn now) → task 2 (waiting) → task 3 (about to be claimed);
        // task 1 also filed task 4, not started. Task 5 is the user's own, outside the subtree.
        let run = |task: i64, waiting: i64| {
            c.execute(
                "INSERT INTO trials (task, conversation, model, waiting, lease_until) VALUES (?1, 'task-' || ?1, 'lab:m', ?2, 0)",
                params![task, waiting],
            )
        };
        board::add_task(c, &agent("top"), Some("chat-1"))?;
        run(1, 0)?;
        c.execute("UPDATE trials SET lease_until = ?1 WHERE id = 1", params![now() + LEASE_SECS])?;
        c.execute("INSERT INTO turns (conversation, owner, lease_until) VALUES ('task-1', 'x', ?1)", params![now() + 10])?;
        board::add_task(c, &agent("middle"), Some("task-1"))?;
        run(2, 1)?;
        // Below the floor `add_task` allows today; the walk does not depend on it.
        c.execute("INSERT INTO tasks (title, assignee, created_by, depth) VALUES ('bottom', 'agent', 'conversations/task-2', 3)", [])?;
        run(3, 0)?;
        board::add_task(c, &agent("later"), Some("task-1"))?;
        board::add_task(c, &agent("elsewhere"), None)?;
        run(5, 0)?;
        for t in [1, 2, 3, 5] {
            board::set_state(c, t, board::TaskState::Assigned)?;
        }
        board::wait_for_children(c, "chat-1")?;
        assert_eq!(board::descendants(c, "chat-1")?, ["task-1", "task-2", "task-3"]);
        assert_eq!(board::active_children(c, "chat-1")?, 4, "three runs and a task about to start");
        assert_eq!(board::active_children(c, "task-2")?, 1);

        let (stopped, taken_back) = db.immediate(|c| board::stop_subtree(c, "chat-1"))?;
        assert_eq!((stopped, taken_back), (4, 1), "chat-1 and its three runs; task 4 back to the user");
        let stop: i64 = c.query_row("SELECT stop FROM turns WHERE conversation = 'task-1'", [], |r| r.get(0))?;
        assert_eq!(stop, 1, "the turn running in task-1 was asked to stop");
        for trial in [1, 2, 3] {
            let (ended, lease, .., paused) = trial_row(c, trial)?;
            assert_eq!((ended, lease, paused), (None, 0, 1), "run {trial} paused, holding nothing");
        }
        assert_eq!(trial_row(c, 4)?.5, 0, "task 5's run is outside the subtree");
        assert_eq!(board::task(c, 4)?.fields.assignee, board::me());
        let wake: i64 = c.query_row("SELECT wake_at FROM conversations WHERE name = 'chat-1'", [], |r| r.get(0))?;
        assert_eq!(wake, 0);
        assert_eq!(board::active_children(c, "chat-1")?, 0);

        // The turn in task-1 ends; nothing in the subtree is claimed or woken, only task 5.
        c.execute("DELETE FROM turns", [])?;
        db.immediate(wake_settled)?;
        board::wake(c, "task-2")?;
        assert!(matches!(claim(c, &dir, &[])?, Some(Work::Trial { task: 5, .. })));
        assert!(claim(c, &dir, &[])?.is_none());
        assert!(board::tasks(c)?.iter().filter(|t| t.id <= 3).all(|t| t.run_state == Some(board::RunState::Paused)));
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }
}
