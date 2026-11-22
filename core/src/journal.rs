//! The journal: an append-only tree of entries, one SQLite file.
//!
//! Every entry points at its parent. A conversation is just a name for a head
//! entry, so resuming is "read the chain from the head", and forking is "make a
//! new name whose head is an older entry". Nothing is ever rewritten.

use anyhow::{Context, Result, bail};
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::Value;

#[derive(Debug, Clone)]
pub struct Entry {
    pub id: i64,
    pub kind: String, // "user" | "assistant" | "tool" | "report" | "notice" | "draft" | "system" | "compact" | "reset" | ...
    pub body: Value,
    pub model: Option<String>,
}

pub struct Journal {
    conn: Connection,
}

impl Journal {
    pub fn open(path: &std::path::Path) -> Result<Self> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let conn = Connection::open(path)?;
        // The CLI and the app share this file; wait for the other writer instead of failing.
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             CREATE TABLE IF NOT EXISTS entries (
                 id      INTEGER PRIMARY KEY,
                 parent  INTEGER REFERENCES entries(id),
                 kind    TEXT NOT NULL,
                 body    TEXT NOT NULL,
                 model   TEXT,
                 created TEXT NOT NULL DEFAULT (datetime('now'))
             );
             CREATE TABLE IF NOT EXISTS conversations (
                 name       TEXT PRIMARY KEY,
                 head       INTEGER NOT NULL REFERENCES entries(id),
                 wake_at    INTEGER NOT NULL DEFAULT 0, -- 0 idle; -1 waiting on children; >0 eligible unix ts to wake
                 session_id TEXT NOT NULL DEFAULT '',   -- stable provider cache/session id; fresh on fork
                 lease_until INTEGER NOT NULL DEFAULT 0, -- a dispatcher woke it and holds it until then
                 restarts   INTEGER NOT NULL DEFAULT 0  -- woken again after its dispatcher died, with no progress since
             );
             CREATE TABLE IF NOT EXISTS hook_runs (
                 conversation TEXT NOT NULL,
                 anchor_entry INTEGER NOT NULL,
                 hook         TEXT NOT NULL,
                 created      TEXT NOT NULL DEFAULT (datetime('now')),
                 PRIMARY KEY (conversation, anchor_entry, hook)
             );",
        )?;
        let mut stmt = conn.prepare("SELECT name FROM pragma_table_info('conversations')")?;
        let conv_cols: Vec<String> = stmt.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        drop(stmt);
        if !conv_cols.iter().any(|c| c == "wake_at") {
            conn.execute_batch("ALTER TABLE conversations ADD COLUMN wake_at INTEGER NOT NULL DEFAULT 0")?;
        }
        if !conv_cols.iter().any(|c| c == "session_id") {
            conn.execute_batch("ALTER TABLE conversations ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")?;
        }
        for col in ["lease_until", "restarts"] {
            if !conv_cols.iter().any(|c| c == col) {
                conn.execute_batch(&format!("ALTER TABLE conversations ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"))?;
            }
        }
        // Backfill legacy conversations with a stable random session id.
        let missing_sid: Vec<String> = {
            let mut stmt = conn.prepare("SELECT name FROM conversations WHERE session_id = ''")?;
            stmt.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?
        };
        for name in missing_sid {
            conn.execute("UPDATE conversations SET session_id = ?2 WHERE name = ?1", params![name, new_session_id()])?;
        }
        conn.execute_batch(crate::turn::TABLES)?;
        crate::turn::migrate(&conn)?;
        conn.execute_batch(crate::leaderboard::TABLES)?;
        crate::board::migrate(&conn)?;
        conn.execute_batch(crate::worktree::TABLES)?;
        Ok(Self { conn })
    }

    /// Append one entry under the conversation's head and move the head, atomically.
    pub fn append(&self, conv: &str, kind: &str, body: &Value, model: Option<&str>) -> Result<i64> {
        self.immediate(|c| insert(c, conv, kind, body, model))
    }

    /// The conversation's entries, oldest first, following parent links from the head.
    /// Everything ever written: what the transcript shows and forks branch from.
    pub fn chain(&self, conv: &str) -> Result<Vec<Entry>> {
        chain(&self.conn, conv)
    }

    /// The part of the chain the model reads: from the newest `"compact"` or `"reset"` head marker on.
    pub fn chain_for_model(&self, conv: &str) -> Result<Vec<Entry>> {
        Ok(model_view(chain(&self.conn, conv)?))
    }

    /// A new conversation that shares history up to and including `entry`.
    pub fn fork(&self, entry: i64, new_conv: &str) -> Result<()> {
        let exists: Option<i64> = self
            .conn
            .query_row("SELECT id FROM entries WHERE id = ?1", params![entry], |r| r.get(0))
            .optional()?;
        if exists.is_none() {
            bail!("no entry {entry}");
        }
        self.conn
            .execute(
                "INSERT INTO conversations (name, head, session_id) VALUES (?1, ?2, ?3)",
                params![new_conv, entry, new_session_id()],
            )
            .with_context(|| format!("conversation {new_conv} already exists?"))?;
        // The fork reads the folder of the conversation it branched from.
        let folders: Vec<(String, String)> = {
            let mut stmt = self.conn.prepare("SELECT conversation, dir FROM folders")?;
            stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?
        };
        for (conv, dir) in folders {
            if conv != new_conv && chain(&self.conn, &conv)?.iter().any(|e| e.id == entry) {
                self.conn.execute("INSERT OR REPLACE INTO folders (conversation, dir) VALUES (?1, ?2)", params![new_conv, dir])?;
                break;
            }
        }
        Ok(())
    }

    /// Every conversation and when its last entry was written, newest first.
    pub fn conversations(&self) -> Result<Vec<(String, String)>> {
        let mut stmt = self.conn.prepare(
            "SELECT c.name, e.created FROM conversations c JOIN entries e ON e.id = c.head ORDER BY c.head DESC",
        )?;
        let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }

    /// Changes whenever anything is written: an entry, a fork, a task, or a turn's text so far. Cheap enough to poll.
    pub fn revision(&self) -> Result<String> {
        Ok(self.conn.query_row(
            "SELECT (SELECT ifnull(max(id), 0) FROM entries) || '.' || (SELECT count(*) FROM conversations)
                 || '.' || (SELECT count(*) + ifnull(sum(edits), 0) FROM tasks)
                 || '.' || (SELECT count(*) FROM task_blockers)
                 || '.' || (SELECT (count(*) + count(ended) + count(error)) || ':' || ifnull(sum(paused), 0) FROM trials)
                 || '.' || (SELECT count(*) FROM ratings)
                 || '.' || (SELECT count(*) || ':' || ifnull(sum(seq), 0) FROM turns)
                 || '.' || (SELECT ifnull(max(id), 0) || ':' || count(*) FROM queued)
                 || '.' || (SELECT count(*) || ':' || ifnull(sum(length(dir)), 0) FROM folders)
                 || '.' || (SELECT count(*) || ':' || ifnull(sum(status != 'active'), 0) FROM worktrees)",
            [],
            |r| r.get(0),
        )?)
    }

    pub fn conn(&self) -> &Connection {
        &self.conn
    }

    /// Run `f` in a write transaction taken up front, so two processes cannot
    /// both read "unclaimed" and then both claim.
    pub fn immediate<T>(&self, f: impl FnOnce(&Connection) -> Result<T>) -> Result<T> {
        self.conn.execute_batch("BEGIN IMMEDIATE")?;
        match f(&self.conn) {
            Ok(v) => {
                self.conn.execute_batch("COMMIT")?;
                Ok(v)
            }
            Err(e) => {
                let _ = self.conn.execute_batch("ROLLBACK");
                Err(e)
            }
        }
    }
}

/// The conversation's entries, oldest first, on any connection (or inside a transaction).
pub fn chain(conn: &Connection, conv: &str) -> Result<Vec<Entry>> {
    let mut stmt = conn.prepare(
        "WITH RECURSIVE c(id) AS (
             SELECT head FROM conversations WHERE name = ?1
             UNION ALL
             SELECT e.parent FROM entries e JOIN c ON e.id = c.id WHERE e.parent IS NOT NULL
         )
         SELECT e.id, e.kind, e.body, e.model FROM entries e JOIN c ON e.id = c.id ORDER BY e.id",
    )?;
    let rows = stmt.query_map(params![conv], |r| {
        let body: String = r.get(2)?;
        Ok(Entry {
            id: r.get(0)?,
            kind: r.get(1)?,
            body: serde_json::from_str(&body).unwrap_or(Value::Null),
            model: r.get(3)?,
        })
    })?;
    Ok(rows.collect::<std::result::Result<_, _>>()?)
}

/// Entry kinds that start a new context for the model without touching the entries before them.
pub const HEAD_MARKERS: [&str; 2] = ["compact", "reset"];

/// Where the model's context starts in `chain`: the index of the newest head marker, if any.
pub fn head_marker(chain: &[Entry]) -> Option<usize> {
    chain.iter().rposition(|e| HEAD_MARKERS.contains(&e.kind.as_str()))
}

/// The entries the model reads, given the whole chain:
/// - no head marker: all of it;
/// - a `"reset"`: only what came after it;
/// - a `"compact"`: the compact entry (its summary), then the entries it kept verbatim, then what came after it.
///
/// A compact's `head_entry` names the oldest entry it keeps. It is moved forward to the next entry that
/// starts a turn, so the view never opens on a tool result whose call was summarized away.
pub fn model_view(chain: Vec<Entry>) -> Vec<Entry> {
    let Some(i) = head_marker(&chain) else { return chain };
    let mut rest = chain;
    let after = rest.split_off(i + 1);
    let marker = rest.pop().expect("the marker");
    if marker.kind == "reset" {
        return after;
    }
    let mut out = Vec::with_capacity(after.len() + 1);
    let keep_from = marker.body["head_entry"].as_i64();
    out.push(marker);
    if let Some(from) = keep_from {
        let starts_turn = |e: &Entry| matches!(e.kind.as_str(), "user" | "report" | "message" | "notice" | "draft");
        if let Some(start) = rest.iter().position(|e| e.id >= from && starts_turn(e)) {
            out.extend(rest.drain(start..));
        }
    }
    out.extend(after);
    out
}

pub fn insert(tx: &Connection, conv: &str, kind: &str, body: &Value, model: Option<&str>) -> Result<i64> {
    let head: Option<i64> = tx
        .query_row("SELECT head FROM conversations WHERE name = ?1", params![conv], |r| r.get(0))
        .optional()?;
    tx.execute(
        "INSERT INTO entries (parent, kind, body, model) VALUES (?1, ?2, ?3, ?4)",
        params![head, kind, body.to_string(), model],
    )?;
    let id = tx.last_insert_rowid();
    tx.execute(
        "INSERT INTO conversations (name, head, session_id) VALUES (?1, ?2, ?3)
         ON CONFLICT(name) DO UPDATE SET head = excluded.head",
        params![conv, id, new_session_id()],
    )?;
    // Real progress clears the crash-loop count. Prompt sections and Arity's own notices are not progress.
    if !matches!(kind, "system" | "notice") {
        tx.execute("UPDATE conversations SET restarts = 0 WHERE name = ?1 AND restarts > 0", params![conv])?;
        tx.execute("UPDATE trials SET restarts = 0 WHERE conversation = ?1 AND restarts > 0", params![conv])?;
    }
    Ok(id)
}

/// Generate a fresh random 32-hex provider session id.
pub fn new_session_id() -> String {
    let a: u64 = rand::random();
    let b: u64 = rand::random();
    format!("{a:016x}{b:016x}")
}

/// The persisted provider session id for `conv`, or a deterministic fallback if
/// `conv` has no row in `conversations` yet.
pub fn session_id(conn: &Connection, conv: &str) -> Result<String> {
    let sid: Option<String> = conn
        .query_row("SELECT session_id FROM conversations WHERE name = ?1", params![conv], |r| r.get(0))
        .optional()?;
    Ok(match sid {
        Some(s) if !s.is_empty() => s,
        _ => {
            use sha2::Digest;
            sha2::Sha256::digest(conv.as_bytes())[..16].iter().map(|b| format!("{b:02x}")).collect()
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn session_id_persists_across_appends_and_is_fresh_on_fork() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-journal-sid-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;
        let e1 = db.append("chat-1", "user", &json!({ "text": "hi" }), None)?;
        let sid1 = session_id(db.conn(), "chat-1")?;
        assert_eq!(sid1.len(), 32);
        db.append("chat-1", "assistant", &json!({ "text": "hello" }), Some("m"))?;
        assert_eq!(session_id(db.conn(), "chat-1")?, sid1, "stable across turns");
        db.fork(e1, "chat-2")?;
        let sid2 = session_id(db.conn(), "chat-2")?;
        assert_eq!(sid2.len(), 32);
        assert_ne!(sid1, sid2, "fork receives a fresh session id");
        drop(db);
        let reopened = Journal::open(&path)?;
        assert_eq!(session_id(reopened.conn(), "chat-1")?, sid1, "survives reopen");
        assert_eq!(session_id(reopened.conn(), "chat-2")?, sid2);
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    fn ids(entries: &[Entry]) -> Vec<i64> {
        entries.iter().map(|e| e.id).collect()
    }

    #[test]
    fn head_markers_cut_the_model_view_but_never_the_chain_or_forks() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-journal-markers-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;
        let say = |kind: &str, text: &str| db.append("c", kind, &json!({ "text": text }), None).unwrap();
        let u1 = say("user", "one");
        let a1 = say("assistant", "1");
        assert_eq!(ids(&db.chain_for_model("c")?), [u1, a1], "no marker: the whole chain");

        let compact = db.append("c", "compact", &json!({ "summary": "said one", "compacted_count": 2 }), None)?;
        let u2 = say("user", "two");
        let a2 = say("assistant", "2");
        assert_eq!(ids(&db.chain("c")?), [u1, a1, compact, u2, a2], "the raw chain keeps everything");
        assert_eq!(ids(&db.chain_for_model("c")?), [compact, u2, a2], "starts at the summary");
        assert_eq!(db.chain("c")?[0].body["text"], "one", "nothing before the marker changed");

        let reset = db.append("c", "reset", &json!({ "note": "fresh start", "head_entry": null }), None)?;
        assert!(db.chain_for_model("c")?.is_empty(), "a reset leaves nothing before it");
        let u3 = say("user", "three");
        assert_eq!(ids(&db.chain_for_model("c")?), [u3]);
        assert_eq!(db.chain("c")?.len(), 7);

        // A fork from before the reset sees the compact; one from before the compact sees everything.
        db.fork(a2, "after-compact")?;
        assert_eq!(ids(&db.chain_for_model("after-compact")?), [compact, u2, a2]);
        db.fork(a1, "before-compact")?;
        assert_eq!(ids(&db.chain_for_model("before-compact")?), [u1, a1]);
        let _ = reset;
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn a_compact_that_keeps_recent_turns_never_opens_on_an_orphan_tool_result() {
        let e = |id: i64, kind: &str, body: Value| Entry { id, kind: kind.into(), body, model: None };
        let chain = vec![
            e(1, "user", json!({ "text": "fix it" })),
            e(2, "assistant", json!({ "text": "", "tool_calls": [{ "id": "c1", "name": "read_file", "arguments": "{}" }] })),
            e(3, "tool", json!({ "call_id": "c1", "output": "..." })),
            e(4, "assistant", json!({ "text": "Fixed." })),
            e(5, "user", json!({ "text": "now test" })),
            e(6, "assistant", json!({ "text": "Passing." })),
            // Asks to keep from the tool result: moved on to the next user message.
            e(7, "compact", json!({ "summary": "fixed a bug", "head_entry": 3, "compacted_count": 4 })),
            e(8, "user", json!({ "text": "ship it" })),
        ];
        assert_eq!(ids(&model_view(chain.clone())), [7, 5, 6, 8]);
        let rendered = crate::model::chat_messages("s", &model_view(chain.clone()));
        assert!(rendered.iter().all(|m| m["role"] != "tool"), "{rendered:?}");
        // Keeping from a turn's start keeps that turn whole.
        let mut from_start = chain.clone();
        from_start[6].body["head_entry"] = json!(1);
        assert_eq!(ids(&model_view(from_start)), [7, 1, 2, 3, 4, 5, 6, 8]);
        // Keeping from past the last turn start keeps nothing extra.
        let mut none = chain;
        none[6].body["head_entry"] = json!(6);
        assert_eq!(ids(&model_view(none)), [7, 8]);
    }
}
