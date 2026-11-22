//! A turn in progress. One row in `turns` per conversation a loop is running
//! in: who holds it, until when, the reply's text so far, and whether someone
//! asked it to stop. It lives in the same SQLite file as the journal, so every
//! process sees it by polling, the way it already sees new entries.
//!
//! The row is a lease. A loop takes it before its first step and renews it
//! many times a second from a thread of its own, so a lease whose process died runs
//! out in a few seconds and the next sender takes over. A message sent while
//! the lease is held waits in `queued`, so two loops never run on one
//! conversation. The sender says when it joins (see `Delivery`); a
//! conversation with no turn running gets it in the journal at once. The user's
//! messages, an agent's reports from its sub-tasks and notes alike go through
//! this one door, `deliver`.
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
        stop         INTEGER NOT NULL DEFAULT 0, -- 1 stop the turn; 2 cut the reply for a message sent now
        seq          INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS queued (
        id           INTEGER PRIMARY KEY,
        conversation TEXT NOT NULL,
        text         TEXT NOT NULL,
        kind         TEXT NOT NULL DEFAULT 'user',  -- the entry it becomes: user, or report
        body         TEXT,                          -- that entry's JSON body; NULL means {\"text\": text}
        mode         TEXT NOT NULL DEFAULT 'next_step' -- see Delivery
    );";

/// Bring a `queued` table from before deliveries had a kind and a mode up to date.
pub fn migrate(c: &Connection) -> Result<()> {
    let mut stmt = c.prepare("SELECT name FROM pragma_table_info('queued')")?;
    let cols: Vec<String> = stmt.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    if !cols.iter().any(|c| c == "mode") {
        c.execute_batch(
            "ALTER TABLE queued ADD COLUMN kind TEXT NOT NULL DEFAULT 'user';
             ALTER TABLE queued ADD COLUMN body TEXT;
             ALTER TABLE queued ADD COLUMN mode TEXT NOT NULL DEFAULT 'next_step';",
        )?;
    }
    // Earlier names for the same timings.
    c.execute_batch("UPDATE queued SET mode = 'next_step' WHERE mode = 'steer'")?;
    Ok(())
}

/// When something delivered to a conversation joins a turn running there. With
/// no turn running it goes into the journal at once, whatever the timing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum Delivery {
    /// At once: the reply being written is cut off (its text so far kept),
    /// and the turn takes it in and goes on.
    Now,
    /// Between the turn's replies (never between a reply's tool calls and
    /// their results): the turn takes it into the work in hand.
    NextStep,
    /// When the turn would end: the turn answers it then, instead of ending.
    AfterTurn,
    /// Read when the recipient is next ready: it asks for no answer, so it
    /// never keeps a turn going or wakes anyone. A running turn takes it in at
    /// the next step as context; an idle conversation has it there for its
    /// next turn. Work to be done is a task on the board, not this.
    Inbox,
}

impl Delivery {
    pub fn name(self) -> &'static str {
        match self {
            Delivery::Now => "now",
            Delivery::NextStep => "next_step",
            Delivery::AfterTurn => "after_turn",
            Delivery::Inbox => "inbox",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "now" => Delivery::Now,
            "next_step" => Delivery::NextStep,
            "after_turn" => Delivery::AfterTurn,
            "inbox" => Delivery::Inbox,
            _ => return None,
        })
    }
}

/// What the loop and its heartbeat share: the text so far, and the stop flag.
#[derive(Default)]
pub struct Live {
    text: Mutex<String>,
    stop: AtomicBool,
    /// The stop is for a message sent now: the turn goes on after it.
    now: AtomicBool,
}

impl Live {
    pub fn push(&self, s: &str) {
        self.text.lock().unwrap().push_str(s);
    }
    /// Whether the reply should be cut off: the turn was stopped, or a message was sent now.
    pub fn stopped(&self) -> bool {
        self.stop.load(Relaxed)
    }
    /// Whether that cut is for a message sent now, so the turn goes on.
    pub fn cut_for_now(&self) -> bool {
        self.now.load(Relaxed)
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

    /// Take in what may join between replies. Only between replies, never
    /// between a reply's tool calls and their results. Returns how many of
    /// them ask for an answer.
    pub fn drain(&self, db: &Journal) -> Result<usize> {
        db.immediate(|c| drain(c, &self.conv, false))
    }

    /// Take in everything waiting, what waits for the end of the turn too: the
    /// turn would end otherwise. Returns how many ask for an answer.
    pub fn drain_all(&self, db: &Journal) -> Result<usize> {
        db.immediate(|c| drain(c, &self.conv, true))
    }

    /// Go on after a cut for a message sent now: clear the flag here and in the
    /// row. Holds the text lock, which the heartbeat takes before reading the
    /// row, so it cannot set the flag again from a read made before the clear.
    pub fn go_on(&self, db: &Journal) -> Result<()> {
        let _text = self.live.text.lock().unwrap();
        db.conn().execute("UPDATE turns SET stop = 0 WHERE conversation = ?1 AND owner = ?2 AND stop = 2", params![
            self.conv, self.owner
        ])?;
        self.live.now.store(false, Relaxed);
        self.live.stop.store(false, Relaxed);
        Ok(())
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
            if drain(c, &self.conv, true)? > 0 && !stopped {
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
                let _ = drain(&c, &self.conv, true);
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
            let stop: rusqlite::Result<Option<i64>> = c
                .query_row(
                    "UPDATE turns SET seq = seq + (partial IS NOT ?1), partial = ?1, lease_until = ?2
                     WHERE conversation = ?3 AND owner = ?4 RETURNING stop",
                    params![*text, now() + LEASE_SECS, conv, owner],
                    |r| r.get(0),
                )
                .optional();
            match stop {
                Ok(Some(2)) => {
                    live.now.store(true, Relaxed);
                    live.stop.store(true, Relaxed);
                }
                Ok(Some(1) | None) => {
                    live.now.store(false, Relaxed);
                    live.stop.store(true, Relaxed);
                }
                _ => {}
            }
        }
        std::thread::sleep(BEAT);
    }
}

/// Move what waits for `conv` into the journal, oldest first: what may join
/// between replies, or everything when `all`. Returns how many ask for an answer.
fn drain(c: &Connection, conv: &str, all: bool) -> Result<usize> {
    let mut stmt = c.prepare(
        "SELECT id, text, kind, body, mode FROM queued
         WHERE conversation = ?1 AND (?2 OR mode != 'after_turn') ORDER BY id",
    )?;
    let waiting: Vec<(i64, String, String, Option<String>, String)> = stmt
        .query_map(params![conv, all], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))?
        .collect::<rusqlite::Result<_>>()?;
    for (id, text, kind, body, _) in &waiting {
        let body = body.as_deref().and_then(|b| serde_json::from_str(b).ok()).unwrap_or_else(|| json!({ "text": text }));
        insert(c, conv, kind, &body, None)?;
        c.execute("DELETE FROM queued WHERE id = ?1", params![id])?;
    }
    Ok(waiting.iter().filter(|w| w.4 != Delivery::Inbox.name()).count())
}

/// Deliver an entry to `conv`: into the journal at once when no turn is
/// running there, otherwise to the running turn, joining it when `how` says.
/// Returns true if it went straight into the journal.
pub fn deliver(c: &Connection, conv: &str, kind: &str, body: &Value, how: Delivery) -> Result<bool> {
    let running: bool = c.query_row(
        "SELECT count(*) > 0 FROM turns WHERE conversation = ?1 AND lease_until >= ?2",
        params![conv, now()],
        |r| r.get(0),
    )?;
    if running {
        let text = body["text"].as_str().unwrap_or_default();
        c.execute(
            "INSERT INTO queued (conversation, text, kind, body, mode) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![conv, text, kind, body.to_string(), how.name()],
        )?;
        if how == Delivery::Now {
            // Cut the reply in progress, unless the turn is being stopped anyway.
            c.execute("UPDATE turns SET stop = 2, seq = seq + 1 WHERE conversation = ?1 AND stop = 0", params![conv])?;
        }
        return Ok(false);
    }
    drain(c, conv, true)?;
    insert(c, conv, kind, body, None)?;
    Ok(true)
}

/// The user's message. A running turn takes it between replies.
pub fn send(db: &Journal, conv: &str, text: &str) -> Result<()> {
    send_as(db, conv, text, Delivery::NextStep)
}

/// The user's message, joining a running turn when `how` says.
pub fn send_as(db: &Journal, conv: &str, text: &str, how: Delivery) -> Result<()> {
    db.immediate(|c| deliver(c, conv, "user", &json!({ "text": text }), how).map(|_| ()))
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

/// The model of the turn running in `conv`, if one is.
pub fn model(c: &Connection, conv: &str) -> Result<Option<String>> {
    Ok(c.query_row("SELECT model FROM turns WHERE conversation = ?1 AND lease_until >= ?2", params![conv, now()], |r| r.get(0))
        .optional()?
        .flatten())
}

/// The user's messages waiting for the running turn, oldest first.
pub fn queued(c: &Connection, conv: &str) -> Result<Vec<(i64, String)>> {
    let mut stmt = c.prepare("SELECT id, text FROM queued WHERE conversation = ?1 AND kind = 'user' ORDER BY id")?;
    Ok(stmt.query_map(params![conv], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?)
}
