//! A turn in progress. One row in `turns` per conversation a loop is running
//! in: who holds it, until when, the reply's text so far, and whether someone
//! asked it to stop. It lives in the same SQLite file as the journal, so every
//! process sees it by polling, the way it already sees new entries.
//!
//! The row is a lease. A loop takes it before its first step and renews it
//! many times a second from a thread of its own, so a lease whose process died runs
//! out in a few seconds and the next sender takes over. A message sent while
//! the lease is held waits in `queued` and is moved into the journal between
//! replies, so two loops never run on one conversation.
//!
//! The journal still records only finished entries; this table is cleared in
//! the same transaction that commits the reply.

use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::Result;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::board::now;
use crate::journal::{Journal, insert};

pub const LEASE_SECS: i64 = 15;
const BEAT: Duration = Duration::from_millis(80);

pub const TABLES: &str = "
    CREATE TABLE IF NOT EXISTS turns (
        conversation TEXT PRIMARY KEY,
        owner        TEXT NOT NULL,
        model        TEXT,
        lease_until  INTEGER NOT NULL,
        partial      TEXT NOT NULL DEFAULT '',
        stop         INTEGER NOT NULL DEFAULT 0,
        seq          INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS queued (
        id           INTEGER PRIMARY KEY,
        conversation TEXT NOT NULL,
        text         TEXT NOT NULL
    );";

/// What the loop and its heartbeat share: the text so far, and the stop flag.
#[derive(Default)]
pub struct Live {
    text: Mutex<String>,
    stop: AtomicBool,
}

impl Live {
    pub fn push(&self, s: &str) {
        self.text.lock().unwrap().push_str(s);
    }
    pub fn stopped(&self) -> bool {
        self.stop.load(Relaxed)
    }
}

pub struct Lease {
    conv: String,
    owner: String,
    path: String,
    pub live: Arc<Live>,
    done: Arc<AtomicBool>,
    beat: Option<std::thread::JoinHandle<()>>,
    released: bool,
}

impl Lease {
    /// Take the conversation, unless a turn whose lease has not run out holds it.
    pub fn take(db: &Journal, conv: &str, model: &str) -> Result<Option<Lease>> {
        let owner = format!("{}-{}", std::process::id(), rand::random::<u32>());
        let took = db.immediate(|c| {
            let held: Option<i64> = c
                .query_row("SELECT lease_until FROM turns WHERE conversation = ?1", params![conv], |r| r.get(0))
                .optional()?;
            if held.is_some_and(|until| until >= now()) {
                return Ok(false);
            }
            c.execute(
                "INSERT OR REPLACE INTO turns (conversation, owner, model, lease_until) VALUES (?1, ?2, ?3, ?4)",
                params![conv, owner, model, now() + LEASE_SECS],
            )?;
            Ok(true)
        })?;
        if !took {
            return Ok(None);
        }
        let live = Arc::new(Live::default());
        let done = Arc::new(AtomicBool::new(false));
        let path = db.conn().path().unwrap_or_default().to_string();
        let beat = {
            let (conv, owner, live, done) = (conv.to_string(), owner.clone(), live.clone(), done.clone());
            let path = path.clone();
            std::thread::spawn(move || heartbeat(&path, &conv, &owner, &live, &done))
        };
        Ok(Some(Lease { conv: conv.into(), owner, path, live, done, beat: Some(beat), released: false }))
    }

    /// Move messages sent during the turn into the journal. Only between replies,
    /// never between a reply's tool calls and their results.
    pub fn drain(&self, db: &Journal) -> Result<usize> {
        db.immediate(|c| drain(c, &self.conv))
    }

    /// Append the finished reply and clear the text so far, in one transaction,
    /// so a window never shows both or neither.
    pub fn commit(&self, db: &Journal, body: &Value, model: &str) -> Result<i64> {
        let mut text = self.live.text.lock().unwrap();
        let id = db.immediate(|c| {
            c.execute("UPDATE turns SET partial = '', seq = seq + 1 WHERE conversation = ?1 AND owner = ?2", params![
                self.conv, self.owner
            ])?;
            insert(c, &self.conv, "assistant", body, Some(model))
        })?;
        text.clear();
        Ok(id)
    }

    /// End the turn. Returns false, keeping the lease, if a message was queued
    /// meanwhile and the turn should answer it. A stopped turn ends regardless;
    /// what was queued stays in the journal for the next turn.
    pub fn release(&mut self, db: &Journal, stopped: bool) -> Result<bool> {
        let end = db.immediate(|c| {
            if drain(c, &self.conv)? > 0 && !stopped {
                return Ok(false);
            }
            c.execute("DELETE FROM turns WHERE conversation = ?1 AND owner = ?2", params![self.conv, self.owner])?;
            Ok(true)
        })?;
        self.released = end;
        Ok(end)
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        self.done.store(true, Relaxed);
        if let Some(beat) = self.beat.take() {
            let _ = beat.join();
        }
        // A turn that failed part way: let go, and keep anything queued.
        if !self.released {
            if let Ok(c) = open(&self.path) {
                let _ = c.execute_batch("BEGIN IMMEDIATE");
                let _ = drain(&c, &self.conv);
                let _ = c.execute("DELETE FROM turns WHERE conversation = ?1 AND owner = ?2", params![
                    self.conv, self.owner
                ]);
                let _ = c.execute_batch("COMMIT");
            }
        }
    }
}

fn open(path: &str) -> rusqlite::Result<Connection> {
    let c = Connection::open(path)?;
    c.busy_timeout(Duration::from_secs(5))?;
    c.execute_batch("PRAGMA synchronous = NORMAL")?;
    Ok(c)
}

/// A few times a second: publish the text so far, renew the lease, and pick up a stop.
/// Losing the row (someone took over a lease that ran out) also stops the turn.
fn heartbeat(path: &str, conv: &str, owner: &str, live: &Live, done: &AtomicBool) {
    let Ok(c) = open(path) else { return };
    while !done.load(Relaxed) {
        {
            let text = live.text.lock().unwrap();
            let stop: rusqlite::Result<Option<bool>> = c
                .query_row(
                    "UPDATE turns SET seq = seq + (partial IS NOT ?1), partial = ?1, lease_until = ?2
                     WHERE conversation = ?3 AND owner = ?4 RETURNING stop",
                    params![*text, now() + LEASE_SECS, conv, owner],
                    |r| r.get(0),
                )
                .optional();
            if matches!(stop, Ok(Some(true) | None)) {
                live.stop.store(true, Relaxed);
            }
        }
        std::thread::sleep(BEAT);
    }
}

fn drain(c: &Connection, conv: &str) -> Result<usize> {
    let mut stmt = c.prepare("SELECT id, text FROM queued WHERE conversation = ?1 ORDER BY id")?;
    let waiting: Vec<(i64, String)> = stmt.query_map(params![conv], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
    for (id, text) in &waiting {
        insert(c, conv, "user", &json!({ "text": text }), None)?;
        c.execute("DELETE FROM queued WHERE id = ?1", params![id])?;
    }
    Ok(waiting.len())
}

/// The user's message. Goes straight into the journal when no turn is running,
/// otherwise waits for the running turn to take it between replies.
pub fn send(db: &Journal, conv: &str, text: &str) -> Result<()> {
    db.immediate(|c| {
        let running: bool = c.query_row(
            "SELECT count(*) > 0 FROM turns WHERE conversation = ?1 AND lease_until >= ?2",
            params![conv, now()],
            |r| r.get(0),
        )?;
        if running {
            c.execute("INSERT INTO queued (conversation, text) VALUES (?1, ?2)", params![conv, text])?;
        } else {
            drain(c, conv)?;
            insert(c, conv, "user", &json!({ "text": text }), None)?;
        }
        Ok(())
    })
}

/// Ask the turn running in `conv` to stop, from any process. False if none is running.
pub fn stop(c: &Connection, conv: &str) -> Result<bool> {
    Ok(c.execute("UPDATE turns SET stop = 1, seq = seq + 1 WHERE conversation = ?1 AND lease_until >= ?2", params![
        conv,
        now()
    ])? > 0)
}

/// Conversations with a turn running in some process.
pub fn running(c: &Connection) -> Result<Vec<String>> {
    let mut stmt = c.prepare("SELECT conversation FROM turns WHERE lease_until >= ?1")?;
    Ok(stmt.query_map(params![now()], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?)
}

/// The reply so far and who is writing it, if a turn is running in `conv`.
pub fn partial(c: &Connection, conv: &str) -> Result<Option<(String, Option<String>)>> {
    Ok(c.query_row(
        "SELECT partial, model FROM turns WHERE conversation = ?1 AND lease_until >= ?2 AND partial != ''",
        params![conv, now()],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )
    .optional()?)
}

/// Messages waiting for the running turn, oldest first.
pub fn queued(c: &Connection, conv: &str) -> Result<Vec<(i64, String)>> {
    let mut stmt = c.prepare("SELECT id, text FROM queued WHERE conversation = ?1 ORDER BY id")?;
    Ok(stmt.query_map(params![conv], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?)
}
