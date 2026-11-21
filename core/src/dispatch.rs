//! The dispatcher: turns tasks for an agent into runs, and finished runs into
//! reports. `work_once` does one unit of work and returns; whoever hosts the
//! dispatcher (the app while it is open, or `arity work`) calls it in a loop.
//!
//! Claims are leases on the trial row, taken in a write transaction, so two
//! dispatchers never run the same task, and a run whose dispatcher died is
//! picked up again from the journal once its lease runs out, or as soon as its
//! conversation's turn lease lapses, which takes seconds rather than minutes.
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
use crate::{agent, config};

/// What is said about a failing run before it is rated. Nothing in it hints at the model.
pub const FAILING: &str = "The last attempt failed; it will be tried again";
const RETRYING: &str = "failed; retrying in a minute";
const ABANDONED: &str = "could not reach its model; the task will be picked again";

/// What one call did, for logging.
pub enum Did {
    Nothing,
    /// A result was passed on. `rater` is the conversation woken to rate it, if any.
    Reported { task: i64, rater: Option<String> },
    Ran { task: i64, trial: i64 },
    Failed { task: i64, trial: i64, error: String },
}

impl Did {
    /// A line describing it, as `arity work` prints it. None for nothing done.
    pub fn describe(&self) -> Option<String> {
        Some(match self {
            Did::Nothing => return None,
            Did::Ran { task, trial } => format!("task {task}: run {trial} finished"),
            Did::Failed { task, trial, error } => format!("task {task}: run {trial} {error}"),
            Did::Reported { task, rater: Some(conv) } => format!("task {task}: reported to {conv} to rate"),
            Did::Reported { task, rater: None } => format!("task {task}: waiting for your rating"),
        })
    }
}

pub fn work_once(db: &Journal, data_dir: &Path) -> Result<Did> {
    work_once_with(db, data_dir, &|| crate::candidates(data_dir))
}

/// `reachable` lists the models that can be reached now; it is asked only when
/// there is work, since it may check local servers.
pub fn work_once_with(db: &Journal, data_dir: &Path, reachable: &dyn Fn() -> Result<Vec<Candidate>>) -> Result<Did> {
    if let Some(did) = deliver_report(db, data_dir)? {
        return Ok(did);
    }
    let waiting: bool = db.conn().query_row(
        "SELECT EXISTS (SELECT 1 FROM tasks WHERE assignee = 'agent' AND state = 'ready')
             OR EXISTS (SELECT 1 FROM trials WHERE ended IS NULL)",
        [],
        |r| r.get(0),
    )?;
    if !waiting {
        return Ok(Did::Nothing);
    }
    let reach = reachable()?;
    let Some((trial, task, conv, model)) = db.immediate(|c| claim(c, data_dir, &reach))? else {
        return Ok(Did::Nothing);
    };
    // Renew the lease after every step, so a long run is not taken over.
    let mut renew = |_: crate::Event| {
        let _ = db.conn().execute("UPDATE trials SET lease_until = ?2 WHERE id = ?1", params![trial, now() + LEASE_SECS]);
    };
    let ran = agent::run(db, data_dir, &conv, &model, &mut renew)
        .and_then(|ran| if ran { Ok(()) } else { Err(anyhow::anyhow!("another turn is running in {conv}")) });
    match ran {
        Ok(()) => {
            let result = db
                .chain(&conv)?
                .iter()
                .rev()
                .find(|e| e.kind == "assistant")
                .and_then(|e| e.body["text"].as_str().map(String::from))
                .unwrap_or_default();
            db.immediate(|c| {
                c.execute(
                    "UPDATE trials SET ended = datetime('now'), result = ?2, error = NULL WHERE id = ?1",
                    params![trial, result],
                )?;
                // Reported work waits in gates; the rating is the one gate for now.
                c.execute("UPDATE tasks SET state = 'in_gates', edits = edits + 1 WHERE id = ?1", params![task])?;
                Ok(())
            })?;
            Ok(Did::Ran { task, trial })
        }
        Err(e) => {
            let detail = format!("{e:#}");
            if !reach.iter().any(|c| c.model == model) {
                // Its model is out of reach: end this run unscored and let the task be picked again.
                db.immediate(|c| {
                    c.execute(
                        "UPDATE trials SET ended = datetime('now'), error = ?2, abandoned = 1, reported = 1 WHERE id = ?1",
                        params![trial, detail],
                    )?;
                    c.execute("UPDATE tasks SET state = 'open', edits = edits + 1 WHERE id = ?1", params![task])?;
                    crate::board::refresh_ready(c)
                })?;
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

/// Take over a run whose dispatcher died, or start a run for the most pressing ready task.
/// A run is taken over once its lease runs out, or sooner when it has not been
/// renewed for a little while and no turn is running in its conversation: the
/// conversation's own lease lapses within seconds of its process dying.
fn claim(c: &Connection, data_dir: &Path, reachable: &[Candidate]) -> Result<Option<(i64, i64, String, String)>> {
    let stale: Option<(i64, i64, String, String)> = c
        .query_row(
            "SELECT id, task, conversation, model FROM trials tr
             WHERE ended IS NULL AND (lease_until < ?1 OR (
                 lease_until < ?1 + ?2 - ?3
                 AND NOT EXISTS (SELECT 1 FROM turns WHERE conversation = tr.conversation AND lease_until >= ?1)))
             ORDER BY id LIMIT 1",
            params![now(), LEASE_SECS, crate::turn::LEASE_SECS],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()?;
    if let Some((trial, ..)) = &stale {
        c.execute("UPDATE trials SET lease_until = ?2 WHERE id = ?1", params![trial, now() + LEASE_SECS])?;
        return Ok(stale);
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
    Ok(Some((trial, task, conv, model)))
}

/// Pass on a finished result. It already waits in the task's own thread for a
/// rating. The chat the task was filed in gets a note of it, as plain
/// information. Only an agent that delegated on its own is woken, to rate it,
/// and only if the model it would rate with is not the one that did the run.
fn deliver_report(db: &Journal, data_dir: &Path) -> Result<Option<Did>> {
    let found = db.immediate(|c| {
        let row: Option<(i64, i64, String, String, Option<String>, String, String)> = c
            .query_row(
                "SELECT tr.id, t.id, t.title, t.created_by, t.origin, ifnull(tr.result, ''), tr.model
                 FROM trials tr JOIN tasks t ON t.id = tr.task
                 WHERE tr.ended IS NOT NULL AND tr.reported = 0 ORDER BY tr.id LIMIT 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?)),
            )
            .optional()?;
        let Some((trial, task, title, created_by, origin, result, model)) = row else { return Ok(None) };
        c.execute("UPDATE trials SET reported = 1 WHERE id = ?1", params![trial])?;
        let author = created_by.strip_prefix("conversations/");
        let rater = match author {
            Some(conv) => chain(c, conv)?.iter().rev().find_map(|e| e.model.clone()).filter(|m| *m != model),
            None => None,
        };
        let to = author.map(String::from).or(origin);
        if let Some(conv) = &to {
            let ask = match rater {
                Some(_) => format!("\n\nRate it with the rate tool: task {task}, a score from 1 to 5, and a short note."),
                None => String::new(),
            };
            let text = format!("Task {task}, \"{title}\", is finished. The result:\n\n{result}{ask}");
            insert(c, conv, "report", &json!({ "text": text, "task": task, "trial": trial }), None)?;
        }
        Ok(Some((task, to.zip(rater))))
    })?;
    let Some((task, rater)) = found else { return Ok(None) };
    if let Some((conv, model)) = &rater {
        // A turn already running there reads the report as its next input.
        agent::run(db, data_dir, conv, model, &mut |_| {})?;
    }
    Ok(Some(Did::Reported { task, rater: rater.map(|(conv, _)| conv) }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::board::{self, TaskFields};

    #[test]
    fn the_users_result_waits_for_the_user_and_wakes_no_model() -> Result<()> {
        let dir = std::env::temp_dir().join(format!("arity-deliver-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Journal::open(&dir.join("arity.sqlite"))?;
        let c = db.conn();
        // A chat on some model files a task for an agent, and a run of it finishes.
        db.append("chat", "assistant", &json!({ "text": "on it" }), Some("codex:x"))?;
        let f = TaskFields { title: "write a poem".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(c, &f, Some("chat"))?;
        c.execute(
            "INSERT INTO trials (task, conversation, model, ended, result) VALUES (?1, 'task-1', 'mlx:m', datetime('now'), 'roses')",
            params![task],
        )?;
        board::set_state(c, task, board::TaskState::InGates)?;
        // Waking the chat would call its model, which does not exist, and fail.
        assert!(matches!(deliver_report(&db, &dir)?, Some(Did::Reported { rater: None, .. })));
        let chat = db.chain("chat")?;
        let note = chat.last().unwrap();
        assert_eq!(note.kind, "report");
        assert!(note.body["text"].as_str().unwrap().contains("roses") && !note.body["text"].as_str().unwrap().contains("Rate"));
        assert_eq!(board::task(c, task)?.run_state, Some(board::RunState::NeedsRating));
        assert!(deliver_report(&db, &dir)?.is_none(), "delivered once");
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
        assert_eq!(claim(c, &dir, &[])?.map(|r| r.0), Some(1));
        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }
}
