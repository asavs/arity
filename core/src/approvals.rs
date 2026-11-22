//! Persistent approvals for tool calls parked by `before_tool` hooks.

use anyhow::{Result, bail};
use rusqlite::{Connection, OptionalExtension, Row, params, types::Type};
use serde_json::Value;

use crate::board::now;

pub const TABLES: &str = "
    CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation TEXT NOT NULL,
        call_id TEXT NOT NULL,
        tool TEXT NOT NULL,
        args TEXT NOT NULL,
        hook TEXT NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        decision_note TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        decided_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS approvals_conv_status ON approvals(conversation, status);
    CREATE UNIQUE INDEX IF NOT EXISTS approvals_conv_call ON approvals(conversation, call_id);
";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApprovalStatus {
    Pending,
    Approved,
    Denied,
    Consumed,
}

impl ApprovalStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Approved => "approved",
            Self::Denied => "denied",
            Self::Consumed => "consumed",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "pending" => Ok(Self::Pending),
            "approved" => Ok(Self::Approved),
            "denied" => Ok(Self::Denied),
            "consumed" => Ok(Self::Consumed),
            _ => bail!("unknown approval status: {value}"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Approval {
    pub id: i64,
    pub conversation: String,
    pub call_id: String,
    pub tool: String,
    pub args: String,
    pub hook: String,
    pub reason: String,
    pub status: ApprovalStatus,
    pub decision_note: String,
    pub created_at: i64,
    pub decided_at: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PriorDecision {
    None,
    Pending(i64),
    Approved,
    Denied(String),
}

const COLUMNS: &str = "id, conversation, call_id, tool, args, hook, reason, status, decision_note, created_at, decided_at";

fn approval_from_row(row: &Row<'_>) -> rusqlite::Result<Approval> {
    let status: String = row.get(7)?;
    let status = ApprovalStatus::parse(&status)
        .map_err(|error| rusqlite::Error::FromSqlConversionFailure(7, Type::Text, error.into()))?;
    Ok(Approval {
        id: row.get(0)?,
        conversation: row.get(1)?,
        call_id: row.get(2)?,
        tool: row.get(3)?,
        args: row.get(4)?,
        hook: row.get(5)?,
        reason: row.get(6)?,
        status,
        decision_note: row.get(8)?,
        created_at: row.get(9)?,
        decided_at: row.get(10)?,
    })
}

pub fn request(
    conn: &Connection,
    conv: &str,
    call_id: &str,
    tool: &str,
    args: &Value,
    hook: &str,
    reason: &str,
) -> Result<Approval> {
    // A consumed row belongs to an earlier call that reused this id; it is asked afresh.
    conn.execute(
        "INSERT INTO approvals
         (conversation, call_id, tool, args, hook, reason, status, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'pending', ?7)
         ON CONFLICT(conversation, call_id) DO UPDATE SET
             tool = excluded.tool, args = excluded.args, hook = excluded.hook, reason = excluded.reason,
             status = 'pending', decision_note = '', created_at = excluded.created_at, decided_at = NULL
         WHERE approvals.status = 'consumed'",
        params![conv, call_id, tool, args.to_string(), hook, reason, now()],
    )?;
    get_for_call(conn, conv, call_id)?
        .ok_or_else(|| anyhow::anyhow!("approval was not created for {conv}/{call_id}"))
}

pub fn check(conn: &Connection, conv: &str, call_id: &str) -> Result<PriorDecision> {
    let decision = conn
        .query_row(
            "SELECT id, status, decision_note
             FROM approvals
             WHERE conversation = ?1 AND call_id = ?2",
            params![conv, call_id],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            },
        )
        .optional()?;
    let Some((id, status, note)) = decision else {
        return Ok(PriorDecision::None);
    };
    Ok(match ApprovalStatus::parse(&status)? {
        ApprovalStatus::Pending => PriorDecision::Pending(id),
        ApprovalStatus::Approved => PriorDecision::Approved,
        ApprovalStatus::Denied => PriorDecision::Denied(note),
        ApprovalStatus::Consumed => PriorDecision::None,
    })
}

pub fn consume(conn: &Connection, conv: &str, call_id: &str) -> Result<()> {
    conn.execute(
        "UPDATE approvals
         SET status = 'consumed'
         WHERE conversation = ?1
           AND call_id = ?2
           AND status IN ('approved', 'denied')",
        params![conv, call_id],
    )?;
    Ok(())
}

pub fn get(conn: &Connection, id: i64) -> Result<Option<Approval>> {
    let sql = format!("SELECT {COLUMNS} FROM approvals WHERE id = ?1");
    Ok(conn
        .query_row(&sql, params![id], approval_from_row)
        .optional()?)
}

pub fn pending_for(conn: &Connection, conv: &str) -> Result<Vec<Approval>> {
    let sql = format!(
        "SELECT {COLUMNS}
         FROM approvals
         WHERE conversation = ?1 AND status = 'pending'
         ORDER BY created_at, id"
    );
    let mut statement = conn.prepare(&sql)?;
    Ok(statement
        .query_map(params![conv], approval_from_row)?
        .collect::<rusqlite::Result<_>>()?)
}

pub fn list_pending(conn: &Connection) -> Result<Vec<Approval>> {
    let sql = format!(
        "SELECT {COLUMNS}
         FROM approvals
         WHERE status = 'pending'
         ORDER BY created_at, id"
    );
    let mut statement = conn.prepare(&sql)?;
    Ok(statement
        .query_map([], approval_from_row)?
        .collect::<rusqlite::Result<_>>()?)
}

pub fn has_pending(conn: &Connection, conv: &str) -> Result<bool> {
    Ok(conn.query_row(
        "SELECT EXISTS(
             SELECT 1 FROM approvals
             WHERE conversation = ?1 AND status = 'pending'
         )",
        params![conv],
        |row| row.get(0),
    )?)
}

/// Whether `conv` has a decision not yet acted on: approved or denied, but not consumed.
pub fn has_decided(conn: &Connection, conv: &str) -> Result<bool> {
    Ok(conn.query_row(
        "SELECT EXISTS(
             SELECT 1 FROM approvals
             WHERE conversation = ?1 AND status IN ('approved', 'denied')
         )",
        params![conv],
        |row| row.get(0),
    )?)
}

pub fn decide(conn: &Connection, id: i64, approve: bool, note: &str) -> Result<Approval> {
    let status = if approve { "approved" } else { "denied" };
    let changed = conn.execute(
        "UPDATE approvals
         SET status = ?2, decision_note = ?3, decided_at = ?4
         WHERE id = ?1 AND status = 'pending'",
        params![id, status, note, now()],
    )?;
    if changed == 0 {
        match get(conn, id)? {
            Some(approval) => bail!(
                "approval {id} is no longer pending (status: {})",
                approval.status.as_str()
            ),
            None => bail!("approval {id} does not exist"),
        }
    }
    get(conn, id)?.ok_or_else(|| anyhow::anyhow!("approval {id} disappeared after decision"))
}

pub fn cancel_pending_for(conn: &Connection, conv: &str, note: &str) -> Result<usize> {
    Ok(conn.execute(
        "UPDATE approvals
         SET status = 'denied', decision_note = ?2, decided_at = ?3
         WHERE conversation = ?1 AND status = 'pending'",
        params![conv, note, now()],
    )?)
}

fn get_for_call(conn: &Connection, conv: &str, call_id: &str) -> Result<Option<Approval>> {
    let sql = format!(
        "SELECT {COLUMNS}
         FROM approvals
         WHERE conversation = ?1 AND call_id = ?2"
    );
    Ok(conn
        .query_row(&sql, params![conv, call_id], approval_from_row)
        .optional()?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn database() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(TABLES).unwrap();
        conn
    }

    #[test]
    fn request_is_idempotent_for_conversation_and_call() {
        let conn = database();
        let first = request(
            &conn,
            "conv",
            "call-1",
            "shell",
            &json!({"cmd": "one"}),
            "guard",
            "review",
        )
        .unwrap();
        let second = request(
            &conn,
            "conv",
            "call-1",
            "shell",
            &json!({"cmd": "two"}),
            "other",
            "changed",
        )
        .unwrap();

        assert_eq!(first.id, second.id);
        assert_eq!(second.args, r#"{"cmd":"one"}"#);
        assert_eq!(second.hook, "guard");
        assert_eq!(list_pending(&conn).unwrap().len(), 1);
    }

    #[test]
    fn a_consumed_row_is_asked_afresh_when_its_call_id_comes_back() {
        let conn = database();
        let first = request(&conn, "conv", "call_0", "bash", &json!({"command": "a"}), "guard", "one").unwrap();
        decide(&conn, first.id, true, "ok").unwrap();
        assert!(has_decided(&conn, "conv").unwrap());
        consume(&conn, "conv", "call_0").unwrap();
        assert!(!has_decided(&conn, "conv").unwrap());

        let again = request(&conn, "conv", "call_0", "bash", &json!({"command": "b"}), "guard", "two").unwrap();
        assert_eq!(again.id, first.id);
        assert_eq!((again.status, again.args.as_str(), again.reason.as_str()), (ApprovalStatus::Pending, r#"{"command":"b"}"#, "two"));
        assert_eq!((again.decision_note.as_str(), again.decided_at), ("", None));
        assert_eq!(check(&conn, "conv", "call_0").unwrap(), PriorDecision::Pending(first.id));
    }

    #[test]
    fn pending_queries_are_scoped_and_ordered() {
        let conn = database();
        let first = request(&conn, "one", "call-1", "a", &json!({}), "hook", "reason").unwrap();
        let second = request(&conn, "one", "call-2", "b", &json!({}), "hook", "reason").unwrap();
        let third = request(&conn, "two", "call-3", "c", &json!({}), "hook", "reason").unwrap();

        assert!(has_pending(&conn, "one").unwrap());
        assert!(!has_pending(&conn, "missing").unwrap());
        assert_eq!(
            pending_for(&conn, "one")
                .unwrap()
                .iter()
                .map(|a| a.id)
                .collect::<Vec<_>>(),
            vec![first.id, second.id]
        );
        assert_eq!(
            list_pending(&conn)
                .unwrap()
                .iter()
                .map(|a| a.id)
                .collect::<Vec<_>>(),
            vec![first.id, second.id, third.id]
        );
    }

    #[test]
    fn approved_decision_can_be_checked_and_consumed() {
        let conn = database();
        let approval =
            request(&conn, "conv", "call", "tool", &json!({}), "hook", "reason").unwrap();

        let decided = decide(&conn, approval.id, true, "looks good").unwrap();
        assert_eq!(decided.status, ApprovalStatus::Approved);
        assert_eq!(decided.decision_note, "looks good");
        assert!(decided.decided_at.is_some());
        assert_eq!(
            check(&conn, "conv", "call").unwrap(),
            PriorDecision::Approved
        );

        consume(&conn, "conv", "call").unwrap();
        assert_eq!(
            get(&conn, approval.id).unwrap().unwrap().status,
            ApprovalStatus::Consumed
        );
        assert_eq!(check(&conn, "conv", "call").unwrap(), PriorDecision::None);
    }

    #[test]
    fn denied_decision_returns_its_note() {
        let conn = database();
        let approval =
            request(&conn, "conv", "call", "tool", &json!({}), "hook", "reason").unwrap();

        decide(&conn, approval.id, false, "too risky").unwrap();
        assert_eq!(
            check(&conn, "conv", "call").unwrap(),
            PriorDecision::Denied("too risky".to_owned())
        );
    }

    #[test]
    fn decide_rejects_decided_and_missing_approvals() {
        let conn = database();
        let approval =
            request(&conn, "conv", "call", "tool", &json!({}), "hook", "reason").unwrap();

        decide(&conn, approval.id, true, "").unwrap();
        assert!(decide(&conn, approval.id, false, "").is_err());
        assert!(decide(&conn, approval.id + 100, true, "").is_err());
    }

    #[test]
    fn cancellation_only_denies_pending_for_one_conversation() {
        let conn = database();
        let one = request(&conn, "one", "call-1", "a", &json!({}), "hook", "reason").unwrap();
        let two = request(&conn, "one", "call-2", "b", &json!({}), "hook", "reason").unwrap();
        let other = request(&conn, "two", "call-3", "c", &json!({}), "hook", "reason").unwrap();

        assert_eq!(cancel_pending_for(&conn, "one", "stopped").unwrap(), 2);
        assert_eq!(
            check(&conn, "one", "call-1").unwrap(),
            PriorDecision::Denied("stopped".to_owned())
        );
        assert_eq!(
            check(&conn, "one", "call-2").unwrap(),
            PriorDecision::Denied("stopped".to_owned())
        );
        assert_eq!(
            check(&conn, "two", "call-3").unwrap(),
            PriorDecision::Pending(other.id)
        );
        assert_eq!(
            get(&conn, one.id).unwrap().unwrap().decision_note,
            "stopped"
        );
        assert_eq!(
            get(&conn, two.id).unwrap().unwrap().decision_note,
            "stopped"
        );
    }
}
