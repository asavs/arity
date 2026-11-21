//! The journal: an append-only tree of entries, one SQLite file.
//!
//! Every entry points at its parent. A conversation is just a name for a head
//! entry, so resuming is "read the chain from the head", and forking is "make a
//! new name whose head is an older entry". Nothing is ever rewritten.

use anyhow::{Context, Result, bail};
use rusqlite::{Connection, OptionalExtension, Transaction, params};
use serde_json::Value;

use crate::model::ToolCall;
use crate::tools;

#[derive(Debug, Clone)]
pub struct Entry {
    pub id: i64,
    pub kind: String, // "user" | "assistant" | "tool"
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
                 name TEXT PRIMARY KEY,
                 head INTEGER NOT NULL REFERENCES entries(id)
             );
             CREATE TABLE IF NOT EXISTS tasks (
                 id    INTEGER PRIMARY KEY,
                 title TEXT NOT NULL,
                 done  INTEGER NOT NULL DEFAULT 0
             );",
        )?;
        Ok(Self { conn })
    }

    /// Append one entry under the conversation's head and move the head, atomically.
    pub fn append(&self, conv: &str, kind: &str, body: &Value, model: Option<&str>) -> Result<i64> {
        let tx = self.conn.unchecked_transaction()?;
        let id = insert(&tx, conv, kind, body, model)?;
        tx.commit()?;
        Ok(id)
    }

    /// Run a tool and record its result in one transaction. If the process dies
    /// before commit, neither the tool's effect on the board nor the entry exists,
    /// so a resume runs it again exactly once.
    pub fn run_tool(&self, conv: &str, call: &ToolCall) -> Result<String> {
        let tx = self.conn.unchecked_transaction()?;
        let output = tools::execute(&tx, &call.name, &call.arguments);
        let body = serde_json::json!({ "call_id": call.id, "name": call.name, "output": output });
        insert(&tx, conv, "tool", &body, None)?;
        tx.commit()?;
        Ok(output)
    }

    /// The conversation's entries, oldest first, following parent links from the head.
    pub fn chain(&self, conv: &str) -> Result<Vec<Entry>> {
        let mut stmt = self.conn.prepare(
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
            .execute("INSERT INTO conversations (name, head) VALUES (?1, ?2)", params![new_conv, entry])
            .with_context(|| format!("conversation {new_conv} already exists?"))?;
        Ok(())
    }

    /// Every conversation with its head entry and when that entry was written, newest first.
    pub fn conversations(&self) -> Result<Vec<(String, i64, String)>> {
        let mut stmt = self.conn.prepare(
            "SELECT c.name, c.head, e.created FROM conversations c JOIN entries e ON e.id = c.head ORDER BY c.head DESC",
        )?;
        let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }

    /// Changes whenever anything is written: an entry, a fork, or a task. Cheap enough to poll.
    pub fn revision(&self) -> Result<String> {
        Ok(self.conn.query_row(
            "SELECT (SELECT ifnull(max(id), 0) FROM entries) || '.' || (SELECT count(*) FROM conversations)
                 || '.' || (SELECT count(*) + ifnull(sum(done), 0) FROM tasks)",
            [],
            |r| r.get(0),
        )?)
    }

    pub fn tasks(&self) -> Result<Vec<(i64, String, bool)>> {
        let mut stmt = self.conn.prepare("SELECT id, title, done FROM tasks ORDER BY id")?;
        let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get::<_, i64>(2)? != 0)))?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }
}

fn insert(tx: &Transaction, conv: &str, kind: &str, body: &Value, model: Option<&str>) -> Result<i64> {
    let head: Option<i64> = tx
        .query_row("SELECT head FROM conversations WHERE name = ?1", params![conv], |r| r.get(0))
        .optional()?;
    tx.execute(
        "INSERT INTO entries (parent, kind, body, model) VALUES (?1, ?2, ?3, ?4)",
        params![head, kind, body.to_string(), model],
    )?;
    let id = tx.last_insert_rowid();
    tx.execute(
        "INSERT INTO conversations (name, head) VALUES (?1, ?2)
         ON CONFLICT(name) DO UPDATE SET head = excluded.head",
        params![conv, id],
    )?;
    Ok(id)
}
