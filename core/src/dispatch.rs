//! The dispatcher: turns tasks for an agent into runs, and finished runs into
//! reports. `work_once` does one unit of work and returns; whoever hosts the
//! dispatcher (the app while it is open, or `arity work`) calls it in a loop.
//!
//! Claims are leases on the trial row, taken in a write transaction, so two
//! dispatchers never run the same task, and a run whose dispatcher died is
//! picked up again from the journal once its lease runs out.

use std::path::Path;

use anyhow::Result;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::json;

use crate::board::{LEASE_SECS, now};
use crate::journal::{Journal, insert};
use crate::{agent, config};

/// What one call did, for logging.
pub enum Did {
    Nothing,
    Reported { task: i64, to: Option<String> },
    Ran { task: i64, trial: i64 },
    Failed { task: i64, trial: i64, error: String },
}

pub fn work_once(db: &Journal, data_dir: &Path) -> Result<Did> {
    if let Some(did) = deliver_report(db, data_dir)? {
        return Ok(did);
    }
    let Some((trial, task, conv, model)) = db.immediate(|c| claim(c, data_dir))? else {
        return Ok(Did::Nothing);
    };
    // Renew the lease after every step, so a long run is not taken over.
    let mut renew = |_: &crate::Message| {
        let _ = db.conn().execute("UPDATE trials SET lease_until = ?2 WHERE id = ?1", params![trial, now() + LEASE_SECS]);
    };
    match agent::run(db, data_dir, &conv, &model, &mut renew) {
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
            // Leave the run open and retry it in a minute; the journal knows where it stopped.
            let error = format!("{e:#}");
            db.conn().execute(
                "UPDATE trials SET error = ?2, lease_until = ?3 WHERE id = ?1",
                params![trial, error, now() + 60],
            )?;
            Ok(Did::Failed { task, trial, error })
        }
    }
}

/// Take over an abandoned run, or start a run for the most pressing ready task.
fn claim(c: &Connection, data_dir: &Path) -> Result<Option<(i64, i64, String, String)>> {
    let stale: Option<(i64, i64, String, String)> = c
        .query_row(
            "SELECT id, task, conversation, model FROM trials
             WHERE ended IS NULL AND lease_until < ?1 ORDER BY id LIMIT 1",
            params![now()],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()?;
    if let Some((trial, ..)) = &stale {
        c.execute("UPDATE trials SET lease_until = ?2 WHERE id = ?1", params![trial, now() + LEASE_SECS])?;
        return Ok(stale);
    }
    // Ready tasks for an agent, most pressing first. A task whose clearance no
    // configured worker meets is skipped rather than blocking the rest.
    let workers = config::load(data_dir)?.workers;
    let mut stmt = c.prepare(
        "SELECT id, title, brief, clearance FROM tasks
         WHERE assignee = 'agent' AND state = 'ready'
         ORDER BY priority DESC, due IS NULL, due, id",
    )?;
    let waiting: Vec<(i64, String, Option<String>, String)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
        .collect::<rusqlite::Result<_>>()?;
    let picked = waiting.into_iter().find_map(|(task, title, brief, clearance)| {
        let cleared: Vec<&String> = workers.iter().filter(|m| clearance != "local" || m.starts_with("mlx:")).collect();
        // The blind pick: uniform among the workers cleared for the task.
        (!cleared.is_empty()).then(|| (task, title, brief, cleared[rand::random_range(0..cleared.len())].clone()))
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

/// Hand a finished result to whoever wrote the task. A conversation gets a
/// report entry and is woken to read and rate it; a person sees it on the board.
fn deliver_report(db: &Journal, data_dir: &Path) -> Result<Option<Did>> {
    let found = db.immediate(|c| {
        let row: Option<(i64, i64, String, String, String)> = c
            .query_row(
                "SELECT tr.id, t.id, t.title, t.created_by, ifnull(tr.result, '')
                 FROM trials tr JOIN tasks t ON t.id = tr.task
                 WHERE tr.ended IS NOT NULL AND tr.reported = 0 ORDER BY tr.id LIMIT 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
            )
            .optional()?;
        let Some((trial, task, title, created_by, result)) = row else { return Ok(None) };
        c.execute("UPDATE trials SET reported = 1 WHERE id = ?1", params![trial])?;
        // A conversation gets the report; a person rates it on the board.
        let writer = created_by.strip_prefix("conversations/").map(String::from);
        if let Some(writer) = &writer {
            let text = format!(
                "Report on task {task}, \"{title}\":\n\n{result}\n\n\
                 Rate it with the rate tool: task {task}, a score from 1 to 5, and a short note."
            );
            insert(c, writer, "report", &json!({ "text": text, "task": task, "trial": trial }), None)?;
        }
        Ok(Some((task, writer)))
    })?;
    let Some((task, writer)) = found else { return Ok(None) };
    // Wake the writer with the model it last used. Without one, the report waits
    // for the next message in that conversation.
    if let Some(conv) = &writer {
        let model = db.chain(conv)?.iter().rev().find_map(|e| e.model.clone());
        if let Some(model) = model {
            agent::run(db, data_dir, conv, &model, &mut |_| {})?;
        }
    }
    Ok(Some(Did::Reported { task, to: writer }))
}
