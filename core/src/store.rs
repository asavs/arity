//! SQLite event spine, projections, epoch fencing, and replay.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use anyhow::Context;
use rusqlite::{Connection, OptionalExtension, Params, Row, Transaction, TransactionBehavior, params};
use serde_json::{Value, json};

use crate::types::{
    DeliveryMode, DeliveryRow, DeliveryState, Entry, EntryKind, InterruptReason, ProviderConfig,
    RateLimitRecord, StoreError, ThreadRow, ThreadStatus, TokenUsage, ToolCallItem,
    ToolReceiptStatus, WireCodec, now_ms,
};

pub const DB_FILENAME: &str = "arity.db";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseRow {
    pub thread_id: String,
    pub epoch: i64,
    pub owner_pid: i64,
    pub lease_until_ms: i64,
    pub partial_text: String,
    pub active_pgid: Option<i64>,
}

#[derive(Debug, Clone)]
pub struct ClaimedTurn {
    pub thread: ThreadRow,
    pub epoch: i64,
    pub stale_pgid: Option<i64>,
}

pub struct Store {
    data_dir: PathBuf,
    conn: Connection,
}

const ENTRY_COLS: &str =
    "id, thread_id, parent_id, epoch, kind, request_id, actor, tree_sha, body, created_ms";
const THREAD_COLS: &str =
    "id, epoch, head_entry_id, model, source_dir, base_commit, base_tree_sha, last_tree_sha, status, stop_requested, updated_ms";
const DELIVERY_COLS: &str =
    "entry_id, thread_id, request_id, mode, text, model_override, state, accepted_by, created_ms";
const PROVIDER_COLS: &str = "name, url, wire, needs_key, key_name, session_header, models";
const RATE_LIMIT_COLS: &str = "provider, limit_name, limit_val, remaining, reset_secs, observed_ms";

const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    parent_id INTEGER,
    epoch INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL,
    request_id TEXT UNIQUE,
    actor TEXT NOT NULL,
    tree_sha TEXT,
    body TEXT NOT NULL,
    created_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_thread_id ON entries(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_entries_parent_id ON entries(parent_id);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL DEFAULT 0,
    head_entry_id INTEGER,
    model TEXT NOT NULL,
    source_dir TEXT,
    base_commit TEXT,
    base_tree_sha TEXT,
    last_tree_sha TEXT,
    status TEXT NOT NULL,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    updated_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    entry_id INTEGER PRIMARY KEY,
    thread_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    text TEXT NOT NULL,
    model_override TEXT,
    state TEXT NOT NULL,
    accepted_by INTEGER,
    created_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_thread_state ON deliveries(thread_id, state, entry_id);

CREATE TABLE IF NOT EXISTS runner_leases (
    thread_id TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL,
    owner_pid INTEGER NOT NULL,
    lease_until_ms INTEGER NOT NULL,
    partial_text TEXT NOT NULL DEFAULT '',
    active_pgid INTEGER
);

CREATE TABLE IF NOT EXISTS providers (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    wire TEXT NOT NULL,
    needs_key INTEGER NOT NULL,
    key_name TEXT NOT NULL,
    session_header TEXT,
    models TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limits (
    provider TEXT NOT NULL,
    limit_name TEXT NOT NULL,
    limit_val INTEGER NOT NULL,
    remaining INTEGER NOT NULL,
    reset_secs INTEGER,
    observed_ms INTEGER NOT NULL,
    PRIMARY KEY (provider, limit_name)
);
"#;

fn builtin_providers() -> Vec<ProviderConfig> {
    let mk = |name: &str,
              url: &str,
              wire: WireCodec,
              needs_key: bool,
              key_name: &str,
              session_header: Option<&str>,
              models: &[&str]| ProviderConfig {
        name: name.into(),
        url: url.into(),
        wire,
        needs_key,
        key_name: key_name.into(),
        session_header: session_header.map(str::to_string),
        models: models.iter().map(|s| (*s).to_string()).collect(),
    };
    vec![
        mk(
            "mlx",
            "http://127.0.0.1:8080/v1",
            WireCodec::ChatCompletions,
            false,
            "mlx",
            None,
            &[
                "mlx-community/Qwen3.5-4B-MLX-4bit",
                "mlx-community/gemma-4-e4b-it-4bit",
            ],
        ),
        mk(
            "openai",
            "https://api.openai.com/v1",
            WireCodec::Responses,
            true,
            "openai",
            None,
            &["gpt-4.1", "gpt-4.1-mini", "o4-mini"],
        ),
        mk(
            "openai-chat",
            "https://api.openai.com/v1",
            WireCodec::ChatCompletions,
            true,
            "openai",
            None,
            &["gpt-4.1", "gpt-4.1-mini"],
        ),
        mk(
            "codex",
            "https://api.openai.com/v1",
            WireCodec::Responses,
            false,
            "codex",
            None,
            &["gpt-6.1-terra", "gpt-6.2-sol"],
        ),
        mk(
            "nous",
            "https://inference-api.nousresearch.com/v1",
            WireCodec::ChatCompletions,
            true,
            "nous",
            None,
            &["Hermes-4-70B", "Hermes-4-405B", "Hermes-4.3-36B"],
        ),
        mk(
            "opencode",
            "https://opencode.ai/inference/openai/v1",
            WireCodec::ChatCompletions,
            true,
            "opencode",
            None,
            &[],
        ),
        mk(
            "opencode-go",
            "https://opencode.ai/inference/go/openai/v1",
            WireCodec::ChatCompletions,
            true,
            "opencode",
            Some("x-opencode-session"),
            &[],
        ),
    ]
}

impl Store {
    pub fn open(data_dir: &Path) -> anyhow::Result<Self> {
        std::fs::create_dir_all(data_dir)
            .with_context(|| format!("failed to create data_dir {}", data_dir.display()))?;
        let db_path = data_dir.join(DB_FILENAME);
        let mut conn = Connection::open(&db_path)
            .with_context(|| format!("failed to open sqlite db {}", db_path.display()))?;
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA busy_timeout = 5000;
             PRAGMA foreign_keys = ON;
             PRAGMA synchronous = NORMAL;",
        )?;
        conn.execute_batch(SCHEMA_SQL)?;

        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let count: i64 = tx.query_row("SELECT COUNT(*) FROM providers", [], |r| r.get(0))?;
        if count == 0 {
            for p in builtin_providers() {
                upsert_provider_tx(&tx, &p)?;
            }
        }
        tx.commit()?;

        Ok(Self { data_dir: data_dir.to_path_buf(), conn })
    }

    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    pub fn rebuild_projections(&mut self) -> Result<(), StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute("DELETE FROM deliveries", [])?;
        tx.execute("DELETE FROM threads", [])?;
        let sql = format!("SELECT {ENTRY_COLS} FROM entries ORDER BY id ASC");
        let entries = collect_rows(&tx, &sql, [], row_to_entry)?;
        for entry in &entries {
            apply_entry_to_projections(&tx, entry)?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn get_thread(&self, thread_id: &str) -> Result<Option<ThreadRow>, StoreError> {
        get_thread_conn(&self.conn, thread_id)
    }

    pub fn list_threads(&self) -> Result<Vec<ThreadRow>, StoreError> {
        let sql = format!("SELECT {THREAD_COLS} FROM threads ORDER BY updated_ms DESC, id ASC");
        collect_rows(&self.conn, &sql, [], row_to_thread)
    }

    pub fn get_entry(&self, entry_id: i64) -> Result<Option<Entry>, StoreError> {
        get_entry_conn(&self.conn, entry_id)
    }

    pub fn find_entry_by_request_id(&self, request_id: &str) -> Result<Option<Entry>, StoreError> {
        get_entry_by_request_id(&self.conn, request_id)
    }

    pub fn thread_entries(&self, thread_id: &str, after_id: i64) -> Result<Vec<Entry>, StoreError> {
        let sql = format!(
            "SELECT {ENTRY_COLS} FROM entries WHERE thread_id = ?1 AND id > ?2 ORDER BY id ASC"
        );
        collect_rows(&self.conn, &sql, params![thread_id, after_id], row_to_entry)
    }

    pub fn causal_chain(&self, head_entry_id: i64) -> Result<Vec<Entry>, StoreError> {
        causal_chain_conn(&self.conn, head_entry_id)
    }

    pub fn pending_deliveries(&self, thread_id: &str) -> Result<Vec<DeliveryRow>, StoreError> {
        let sql = format!(
            "SELECT {DELIVERY_COLS} FROM deliveries WHERE thread_id = ?1 AND state = 'queued' ORDER BY entry_id ASC"
        );
        collect_rows(&self.conn, &sql, params![thread_id], row_to_delivery)
    }

    pub fn list_deliveries(&self) -> Result<Vec<DeliveryRow>, StoreError> {
        let sql = format!("SELECT {DELIVERY_COLS} FROM deliveries ORDER BY entry_id ASC");
        collect_rows(&self.conn, &sql, [], row_to_delivery)
    }

    pub fn read_lease(&self, thread_id: &str) -> Result<Option<LeaseRow>, StoreError> {
        read_lease_conn(&self.conn, thread_id)
    }

    pub fn get_provider(&self, name: &str) -> Result<Option<ProviderConfig>, StoreError> {
        let sql = format!("SELECT {PROVIDER_COLS} FROM providers WHERE name = ?1");
        self.conn
            .query_row(&sql, params![name], row_to_provider)
            .optional()
            .map_err(StoreError::from)
    }

    pub fn list_providers(&self) -> Result<Vec<ProviderConfig>, StoreError> {
        let sql = format!("SELECT {PROVIDER_COLS} FROM providers ORDER BY name ASC");
        collect_rows(&self.conn, &sql, [], row_to_provider)
    }

    pub fn upsert_provider(&mut self, p: &ProviderConfig) -> Result<(), StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        upsert_provider_tx(&tx, p)?;
        tx.commit()?;
        Ok(())
    }

    pub fn record_rate_limits(&mut self, limits: &[RateLimitRecord]) -> Result<(), StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        for r in limits {
            tx.execute(
                "INSERT INTO rate_limits (provider, limit_name, limit_val, remaining, reset_secs, observed_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)
                 ON CONFLICT(provider, limit_name) DO UPDATE SET
                     limit_val = excluded.limit_val, remaining = excluded.remaining,
                     reset_secs = excluded.reset_secs, observed_ms = excluded.observed_ms",
                params![
                    r.provider,
                    r.limit_name,
                    r.limit_val as i64,
                    r.remaining as i64,
                    r.reset_secs.map(|v| v as i64),
                    r.observed_ms
                ],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn list_rate_limits(
        &self,
        provider: Option<&str>,
    ) -> Result<Vec<RateLimitRecord>, StoreError> {
        if let Some(prov) = provider {
            let sql = format!(
                "SELECT {RATE_LIMIT_COLS} FROM rate_limits WHERE provider = ?1 ORDER BY provider ASC, limit_name ASC"
            );
            collect_rows(&self.conn, &sql, params![prov], row_to_rate_limit)
        } else {
            let sql = format!(
                "SELECT {RATE_LIMIT_COLS} FROM rate_limits ORDER BY provider ASC, limit_name ASC"
            );
            collect_rows(&self.conn, &sql, [], row_to_rate_limit)
        }
    }

    // -------------------------------------------------------------------------
    // Intent methods (idempotent via request_id)
    // -------------------------------------------------------------------------

    #[allow(clippy::too_many_arguments)]
    pub fn create_thread(
        &mut self,
        thread_id: &str,
        model: &str,
        source_dir: Option<&str>,
        base_commit: Option<&str>,
        base_tree_sha: Option<&str>,
        system_prompt: &str,
        request_id: &str,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(existing) = get_entry_by_request_id(&tx, request_id)? {
            tx.commit()?;
            return Ok(existing);
        }
        if get_thread_conn(&tx, thread_id)?.is_some() {
            return Err(StoreError::ProtocolViolation(format!("thread '{thread_id}' already exists")));
        }
        let body = json!({
            "model": model,
            "source_dir": source_dir,
            "base_commit": base_commit,
            "base_tree_sha": base_tree_sha,
            "system": system_prompt,
        });
        let entry = insert_and_apply(
            &tx, thread_id, None, 0, EntryKind::ThreadCreated, Some(request_id), "client",
            base_tree_sha, body,
        )?;
        tx.commit()?;
        Ok(entry)
    }

    pub fn fork_thread(
        &mut self,
        new_thread_id: &str,
        from_entry_id: i64,
        model_override: Option<&str>,
        request_id: &str,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(existing) = get_entry_by_request_id(&tx, request_id)? {
            tx.commit()?;
            return Ok(existing);
        }
        if get_thread_conn(&tx, new_thread_id)?.is_some() {
            return Err(StoreError::ProtocolViolation(format!(
                "thread '{new_thread_id}' already exists"
            )));
        }

        let chain = causal_chain_conn(&tx, from_entry_id)?;
        let from_entry = chain.last().ok_or(StoreError::EntryNotFound(from_entry_id))?;

        let (mut model, mut source_dir, mut base_commit, mut base_tree_sha) = (None, None, None, None);
        let mut system = String::new();
        let mut effective_tree_sha: Option<String> = None;

        for e in &chain {
            match e.kind {
                EntryKind::ThreadCreated | EntryKind::ThreadForked => {
                    if let Some(m) = json_opt_str(&e.body, "model") {
                        model = Some(m);
                    }
                    source_dir = json_opt_str(&e.body, "source_dir");
                    base_commit = json_opt_str(&e.body, "base_commit");
                    base_tree_sha = json_opt_str(&e.body, "base_tree_sha");
                    if let Some(s) = e.body.get("system").and_then(Value::as_str) {
                        system = s.to_string();
                    }
                }
                EntryKind::DeliveryAccepted => {
                    if let Some(m) = json_opt_str(&e.body, "model_override")
                        && !m.is_empty()
                    {
                        model = Some(m);
                    }
                }
                _ => {}
            }
            if let Some(sha) = &e.tree_sha {
                effective_tree_sha = Some(sha.clone());
            }
        }

        let inherited_model = model.ok_or_else(|| {
            StoreError::ProtocolViolation(format!(
                "entry {from_entry_id} does not descend from a thread creation entry"
            ))
        })?;
        let final_model = model_override
            .filter(|m| !m.is_empty())
            .map(str::to_string)
            .unwrap_or(inherited_model);
        let final_tree_sha = effective_tree_sha.or_else(|| base_tree_sha.clone());

        let body = json!({
            "from_entry_id": from_entry_id,
            "from_thread_id": from_entry.thread_id,
            "model": final_model,
            "source_dir": source_dir,
            "base_commit": base_commit,
            "base_tree_sha": base_tree_sha,
            "system": system,
        });
        let entry = insert_and_apply(
            &tx, new_thread_id, Some(from_entry_id), 0, EntryKind::ThreadForked, Some(request_id),
            "client", final_tree_sha.as_deref(), body,
        )?;
        tx.commit()?;
        Ok(entry)
    }

    pub fn enqueue_delivery(
        &mut self,
        thread_id: &str,
        mode: DeliveryMode,
        text: &str,
        model_override: Option<&str>,
        request_id: &str,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(existing) = get_entry_by_request_id(&tx, request_id)? {
            tx.commit()?;
            return Ok(existing);
        }
        let thread = require_thread(&tx, thread_id)?;
        let body = json!({
            "mode": mode.as_str(),
            "text": text,
            "model_override": model_override,
        });
        let entry = insert_and_apply(
            &tx, thread_id, None, thread.epoch, EntryKind::DeliveryQueued, Some(request_id),
            "client", None, body,
        )?;
        tx.commit()?;
        Ok(entry)
    }

    pub fn request_stop(
        &mut self,
        thread_id: &str,
        reason: &str,
        request_id: &str,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(existing) = get_entry_by_request_id(&tx, request_id)? {
            tx.commit()?;
            return Ok(existing);
        }
        let thread = require_thread(&tx, thread_id)?;
        let entry = insert_and_apply(
            &tx, thread_id, None, thread.epoch, EntryKind::ThreadStop, Some(request_id),
            "client", None, json!({ "reason": reason }),
        )?;
        tx.commit()?;
        Ok(entry)
    }

    pub fn record_workspace_applied(
        &mut self,
        thread_id: &str,
        files_applied: u32,
        base_tree_sha: &str,
        applied_tree_sha: &str,
        request_id: &str,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(existing) = get_entry_by_request_id(&tx, request_id)? {
            tx.commit()?;
            return Ok(existing);
        }
        let thread = require_thread(&tx, thread_id)?;
        let body = json!({
            "files_applied": files_applied,
            "base_tree_sha": base_tree_sha,
            "applied_tree_sha": applied_tree_sha,
        });
        let entry = insert_and_apply(
            &tx, thread_id, thread.head_entry_id, thread.epoch, EntryKind::WorkspaceApplied, Some(request_id),
            "client", Some(applied_tree_sha), body,
        )?;
        tx.commit()?;
        Ok(entry)
    }

    // -------------------------------------------------------------------------
    // Runner methods (epoch-fenced)
    // -------------------------------------------------------------------------

    pub fn pid_is_alive(pid: i64) -> bool {
        if pid <= 0 {
            return false;
        }
        let Ok(pid_t) = libc::pid_t::try_from(pid) else {
            return false;
        };
        if unsafe { libc::kill(pid_t, 0) } == 0 {
            return true;
        }
        std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
    }

    pub fn thread_needs_work(&self, thread_id: &str) -> Result<bool, StoreError> {
        let thread = require_thread(&self.conn, thread_id)?;
        if thread.status == ThreadStatus::Applied {
            return Ok(false);
        }

        if let Some(head_id) = thread.head_entry_id {
            let head =
                get_entry_conn(&self.conn, head_id)?.ok_or(StoreError::EntryNotFound(head_id))?;
            if head.kind == EntryKind::ToolCall {
                return Ok(true);
            }
            if thread.status != ThreadStatus::Halted {
                match head.kind {
                    EntryKind::DeliveryAccepted | EntryKind::ToolResult => return Ok(true),
                    EntryKind::ModelCompleted
                        if head
                            .body
                            .get("tool_calls")
                            .and_then(Value::as_array)
                            .is_some_and(|a| !a.is_empty()) =>
                    {
                        return Ok(true);
                    }
                    _ => {}
                }
            }
        }

        if thread.status == ThreadStatus::Halted {
            return Ok(false);
        }

        let has_queued: bool = self.conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM deliveries WHERE thread_id = ?1 AND state = 'queued' AND mode != 'inbox')",
            params![thread_id],
            |r| r.get(0),
        )?;
        Ok(has_queued)
    }

    pub fn try_claim_turn(
        &mut self,
        thread_id: &str,
        owner_pid: i64,
        lease_ms: i64,
    ) -> Result<Option<ClaimedTurn>, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let thread = require_thread(&tx, thread_id)?;
        if thread.status == ThreadStatus::Applied {
            tx.commit()?;
            return Ok(None);
        }

        let now = now_ms();
        let existing_lease = read_lease_conn(&tx, thread_id)?;
        if let Some(ref lease) = existing_lease
            && lease.lease_until_ms > now
            && Self::pid_is_alive(lease.owner_pid)
        {
            tx.commit()?;
            return Ok(None);
        }
        let stale_pgid = existing_lease.and_then(|l| l.active_pgid);
        let new_epoch = thread.epoch + 1;

        let body = json!({ "owner_pid": owner_pid, "lease_ms": lease_ms, "stale_pgid": stale_pgid });
        insert_and_apply(
            &tx, thread_id, None, new_epoch, EntryKind::TurnClaimed, None, "runner", None, body,
        )?;

        tx.execute(
            "INSERT INTO runner_leases (thread_id, epoch, owner_pid, lease_until_ms, partial_text, active_pgid)
             VALUES (?1, ?2, ?3, ?4, '', NULL)
             ON CONFLICT(thread_id) DO UPDATE SET
                 epoch = excluded.epoch, owner_pid = excluded.owner_pid,
                 lease_until_ms = excluded.lease_until_ms, partial_text = '', active_pgid = NULL",
            params![thread_id, new_epoch, owner_pid, now.saturating_add(lease_ms)],
        )?;

        let updated = require_thread(&tx, thread_id)?;
        tx.commit()?;
        Ok(Some(ClaimedTurn { thread: updated, epoch: new_epoch, stale_pgid }))
    }

    pub fn heartbeat_lease(
        &mut self,
        thread_id: &str,
        epoch: i64,
        lease_ms: i64,
        partial_text: Option<&str>,
        active_pgid: Option<Option<i64>>,
    ) -> Result<i64, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let thread = require_epoch(&tx, thread_id, epoch)?;
        let lease_until_ms = now_ms().saturating_add(lease_ms);

        tx.execute(
            "UPDATE runner_leases SET lease_until_ms = ?1 WHERE thread_id = ?2 AND epoch = ?3",
            params![lease_until_ms, thread_id, epoch],
        )?;
        if let Some(text) = partial_text {
            tx.execute(
                "UPDATE runner_leases SET partial_text = ?1 WHERE thread_id = ?2 AND epoch = ?3",
                params![text, thread_id, epoch],
            )?;
        }
        if let Some(pgid_opt) = active_pgid {
            tx.execute(
                "UPDATE runner_leases SET active_pgid = ?1 WHERE thread_id = ?2 AND epoch = ?3",
                params![pgid_opt, thread_id, epoch],
            )?;
        }
        tx.commit()?;
        Ok(thread.stop_requested)
    }

    pub fn accept_deliveries(
        &mut self,
        thread_id: &str,
        epoch: i64,
        modes: &[DeliveryMode],
    ) -> Result<Option<Entry>, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let thread = require_epoch(&tx, thread_id, epoch)?;
        if modes.is_empty() {
            tx.commit()?;
            return Ok(None);
        }

        let sql = format!(
            "SELECT {DELIVERY_COLS} FROM deliveries WHERE thread_id = ?1 AND state = 'queued' ORDER BY entry_id ASC"
        );
        let queued: Vec<DeliveryRow> = collect_rows(&tx, &sql, params![thread_id], row_to_delivery)?
            .into_iter()
            .filter(|d| modes.contains(&d.mode))
            .collect();
        if queued.is_empty() {
            tx.commit()?;
            return Ok(None);
        }

        let delivery_ids: Vec<i64> = queued.iter().map(|d| d.entry_id).collect();
        let deliveries_json: Vec<Value> = queued
            .iter()
            .map(|d| {
                json!({
                    "entry_id": d.entry_id, "request_id": d.request_id,
                    "mode": d.mode.as_str(), "text": d.text, "model_override": d.model_override,
                })
            })
            .collect();
        let combined_text = queued.iter().map(|d| d.text.as_str()).collect::<Vec<_>>().join("\n\n");
        let model_override = queued
            .iter()
            .rev()
            .find_map(|d| d.model_override.as_deref().filter(|m| !m.is_empty()))
            .map(str::to_string);

        let body = json!({
            "delivery_ids": delivery_ids,
            "deliveries": deliveries_json,
            "text": combined_text,
            "model_override": model_override,
        });
        let entry = insert_and_apply(
            &tx, thread_id, thread.head_entry_id, epoch, EntryKind::DeliveryAccepted, None,
            "runner", thread.last_tree_sha.as_deref(), body,
        )?;
        tx.commit()?;
        Ok(Some(entry))
    }

    #[allow(clippy::too_many_arguments)]
    pub fn append_model_completed(
        &mut self,
        thread_id: &str,
        epoch: i64,
        model: &str,
        text: &str,
        tool_calls: &[ToolCallItem],
        usage: Option<TokenUsage>,
        ttft_ms: Option<u64>,
        duration_ms: u64,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let thread = require_epoch(&tx, thread_id, epoch)?;
        let body = json!({
            "model": model, "text": text, "tool_calls": tool_calls,
            "usage": usage, "ttft_ms": ttft_ms, "duration_ms": duration_ms,
        });
        let entry = insert_and_apply(
            &tx, thread_id, thread.head_entry_id, epoch, EntryKind::ModelCompleted, None,
            "runner", thread.last_tree_sha.as_deref(), body,
        )?;
        tx.execute(
            "UPDATE runner_leases SET partial_text = '' WHERE thread_id = ?1 AND epoch = ?2",
            params![thread_id, epoch],
        )?;
        tx.commit()?;
        Ok(entry)
    }

    pub fn append_model_interrupted(
        &mut self,
        thread_id: &str,
        epoch: i64,
        model: &str,
        text: &str,
        reason: InterruptReason,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let thread = require_epoch(&tx, thread_id, epoch)?;
        let body = json!({ "model": model, "text": text, "reason": reason.as_str() });
        let entry = insert_and_apply(
            &tx, thread_id, thread.head_entry_id, epoch, EntryKind::ModelInterrupted, None,
            "runner", thread.last_tree_sha.as_deref(), body,
        )?;
        tx.execute(
            "UPDATE runner_leases SET partial_text = '' WHERE thread_id = ?1 AND epoch = ?2",
            params![thread_id, epoch],
        )?;
        tx.commit()?;
        Ok(entry)
    }

    pub fn append_tool_call(
        &mut self,
        thread_id: &str,
        epoch: i64,
        call: &ToolCallItem,
        mutating: bool,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let thread = require_epoch(&tx, thread_id, epoch)?;
        let head_id = thread.head_entry_id.ok_or_else(|| {
            StoreError::ProtocolViolation("cannot append tool call without head entry".into())
        })?;
        verify_model_completed_gate(&tx, head_id, &call.id)?;

        let body = json!({
            "call_id": call.id, "name": call.name,
            "arguments": call.arguments, "mutating": mutating,
        });
        let entry = insert_and_apply(
            &tx, thread_id, Some(head_id), epoch, EntryKind::ToolCall, None, "runner",
            thread.last_tree_sha.as_deref(), body,
        )?;
        tx.commit()?;
        Ok(entry)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn append_tool_result(
        &mut self,
        thread_id: &str,
        epoch: i64,
        call_id: &str,
        name: &str,
        status: ToolReceiptStatus,
        output: &str,
        exit_code: Option<i32>,
        tree_sha: Option<&str>,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let thread = require_epoch(&tx, thread_id, epoch)?;
        let head_id = thread.head_entry_id.ok_or_else(|| {
            StoreError::ProtocolViolation("cannot append tool result without head entry".into())
        })?;
        let head = get_entry_conn(&tx, head_id)?.ok_or(StoreError::EntryNotFound(head_id))?;
        if head.kind != EntryKind::ToolCall {
            return Err(StoreError::ProtocolViolation(format!(
                "cannot append tool result when head entry {head_id} is {}",
                head.kind.as_str()
            )));
        }
        let head_call_id = head.body.get("call_id").and_then(Value::as_str);
        if head_call_id != Some(call_id) {
            return Err(StoreError::ProtocolViolation(format!(
                "tool result call_id '{call_id}' does not match head tool.call '{}'",
                head_call_id.unwrap_or("<missing>")
            )));
        }

        let effective_tree_sha = tree_sha.map(str::to_string).or_else(|| thread.last_tree_sha.clone());
        let body = json!({
            "call_id": call_id, "name": name, "status": status.as_str(),
            "output": output, "exit_code": exit_code,
        });
        let entry = insert_and_apply(
            &tx, thread_id, Some(head_id), epoch, EntryKind::ToolResult, None, "runner",
            effective_tree_sha.as_deref(), body,
        )?;
        tx.execute("UPDATE runner_leases SET active_pgid = NULL WHERE thread_id = ?1", params![thread_id])?;
        tx.commit()?;
        Ok(entry)
    }

    pub fn finish_turn(
        &mut self,
        thread_id: &str,
        epoch: i64,
        status: ThreadStatus,
        reason: &str,
        error: Option<&str>,
    ) -> Result<Entry, StoreError> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        require_epoch(&tx, thread_id, epoch)?;
        let body = json!({ "status": status.as_str(), "reason": reason, "error": error });
        let entry = insert_and_apply(
            &tx, thread_id, None, epoch, EntryKind::TurnFinished, None, "runner", None, body,
        )?;
        tx.execute(
            "DELETE FROM runner_leases WHERE thread_id = ?1 AND epoch = ?2",
            params![thread_id, epoch],
        )?;
        tx.commit()?;
        Ok(entry)
    }
}

// -----------------------------------------------------------------------------
// Projection engine (single source of truth for live writes AND replay)
// -----------------------------------------------------------------------------

fn apply_entry_to_projections(tx: &Transaction<'_>, entry: &Entry) -> Result<(), StoreError> {
    match entry.kind {
        EntryKind::ThreadCreated | EntryKind::ThreadForked => {
            let model = json_opt_str(&entry.body, "model").unwrap_or_default();
            let base_tree_sha = json_opt_str(&entry.body, "base_tree_sha");
            let last_tree_sha = entry.tree_sha.clone().or_else(|| base_tree_sha.clone());
            tx.execute(
                "INSERT INTO threads (
                    id, epoch, head_entry_id, model, source_dir, base_commit,
                    base_tree_sha, last_tree_sha, status, stop_requested, updated_ms
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 0, ?10)",
                params![
                    entry.thread_id,
                    entry.epoch,
                    entry.id,
                    model,
                    json_opt_str(&entry.body, "source_dir"),
                    json_opt_str(&entry.body, "base_commit"),
                    base_tree_sha,
                    last_tree_sha,
                    ThreadStatus::Idle.as_str(),
                    entry.created_ms,
                ],
            )?;
        }
        EntryKind::DeliveryQueued => {
            let mode = DeliveryMode::parse(
                entry.body.get("mode").and_then(Value::as_str).unwrap_or("steer"),
            )
            .map_err(StoreError::Other)?;
            let text = entry.body.get("text").and_then(Value::as_str).unwrap_or("");
            tx.execute(
                "INSERT INTO deliveries (
                    entry_id, thread_id, request_id, mode, text, model_override,
                    state, accepted_by, created_ms
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, NULL, ?8)",
                params![
                    entry.id,
                    entry.thread_id,
                    entry.request_id.as_deref().unwrap_or(""),
                    mode.as_str(),
                    text,
                    json_opt_str(&entry.body, "model_override"),
                    DeliveryState::Queued.as_str(),
                    entry.created_ms,
                ],
            )?;

            match mode {
                DeliveryMode::Steer => {
                    tx.execute(
                        "UPDATE threads
                         SET stop_requested = CASE WHEN stop_requested < 1 THEN 2 ELSE stop_requested END,
                             status = CASE WHEN status = 'halted' THEN 'idle' ELSE status END,
                             updated_ms = ?1
                         WHERE id = ?2",
                        params![entry.created_ms, entry.thread_id],
                    )?;
                }
                DeliveryMode::AfterTurn => {
                    tx.execute(
                        "UPDATE threads
                         SET status = CASE WHEN status = 'halted' THEN 'idle' ELSE status END,
                             updated_ms = ?1
                         WHERE id = ?2",
                        params![entry.created_ms, entry.thread_id],
                    )?;
                }
                DeliveryMode::Inbox => {
                    tx.execute(
                        "UPDATE threads SET updated_ms = ?1 WHERE id = ?2",
                        params![entry.created_ms, entry.thread_id],
                    )?;
                }
            }
        }
        EntryKind::ThreadStop => {
            tx.execute(
                "UPDATE threads SET stop_requested = 1, updated_ms = ?1 WHERE id = ?2",
                params![entry.created_ms, entry.thread_id],
            )?;
        }
        EntryKind::WorkspaceApplied => {
            tx.execute(
                "UPDATE threads SET head_entry_id = ?1, status = ?2, last_tree_sha = COALESCE(?3, last_tree_sha), updated_ms = ?4 WHERE id = ?5",
                params![entry.id, ThreadStatus::Applied.as_str(), entry.tree_sha, entry.created_ms, entry.thread_id],
            )?;
        }
        EntryKind::TurnClaimed => {
            tx.execute(
                "UPDATE threads SET epoch = ?1, status = ?2, updated_ms = ?3 WHERE id = ?4",
                params![entry.epoch, ThreadStatus::Running.as_str(), entry.created_ms, entry.thread_id],
            )?;
        }
        EntryKind::DeliveryAccepted => {
            if let Some(ids) = entry.body.get("delivery_ids").and_then(Value::as_array) {
                for delivery_id in ids.iter().filter_map(Value::as_i64) {
                    tx.execute(
                        "UPDATE deliveries SET state = ?1, accepted_by = ?2 WHERE entry_id = ?3",
                        params![DeliveryState::Accepted.as_str(), entry.id, delivery_id],
                    )?;
                }
            }
            let remaining_steers: i64 = tx.query_row(
                "SELECT COUNT(*) FROM deliveries WHERE thread_id = ?1 AND state = 'queued' AND mode = 'steer'",
                params![entry.thread_id],
                |r| r.get(0),
            )?;
            let model_override = json_opt_str(&entry.body, "model_override").filter(|m| !m.is_empty());
            tx.execute(
                "UPDATE threads
                 SET head_entry_id = ?1,
                     model = COALESCE(?2, model),
                     last_tree_sha = COALESCE(?3, last_tree_sha),
                     stop_requested = CASE WHEN ?4 = 0 AND stop_requested = 2 THEN 0 ELSE stop_requested END,
                     updated_ms = ?5
                 WHERE id = ?6",
                params![entry.id, model_override, entry.tree_sha, remaining_steers, entry.created_ms, entry.thread_id],
            )?;
        }
        EntryKind::ModelCompleted
        | EntryKind::ModelInterrupted
        | EntryKind::ToolCall
        | EntryKind::ToolResult => {
            tx.execute(
                "UPDATE threads SET head_entry_id = ?1, last_tree_sha = COALESCE(?2, last_tree_sha), updated_ms = ?3 WHERE id = ?4",
                params![entry.id, entry.tree_sha, entry.created_ms, entry.thread_id],
            )?;
        }
        EntryKind::TurnFinished => {
            let status = ThreadStatus::parse(
                entry.body.get("status").and_then(Value::as_str).unwrap_or("idle"),
            )
            .map_err(StoreError::Other)?;
            tx.execute(
                "UPDATE threads SET status = ?1, stop_requested = 0, updated_ms = ?2 WHERE id = ?3",
                params![status.as_str(), entry.created_ms, entry.thread_id],
            )?;
        }
    }
    Ok(())
}

// -----------------------------------------------------------------------------
// Internal helpers
// -----------------------------------------------------------------------------

#[allow(clippy::too_many_arguments)]
fn insert_and_apply(
    tx: &Transaction<'_>,
    thread_id: &str,
    parent_id: Option<i64>,
    epoch: i64,
    kind: EntryKind,
    request_id: Option<&str>,
    actor: &str,
    tree_sha: Option<&str>,
    body: Value,
) -> Result<Entry, StoreError> {
    let created_ms = now_ms();
    let body_str = serde_json::to_string(&body).map_err(|e| StoreError::Other(e.into()))?;
    tx.execute(
        "INSERT INTO entries (thread_id, parent_id, epoch, kind, request_id, actor, tree_sha, body, created_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![thread_id, parent_id, epoch, kind.as_str(), request_id, actor, tree_sha, body_str, created_ms],
    )?;
    let entry = Entry {
        id: tx.last_insert_rowid(),
        thread_id: thread_id.to_string(),
        parent_id,
        epoch,
        kind,
        request_id: request_id.map(str::to_string),
        actor: actor.to_string(),
        tree_sha: tree_sha.map(str::to_string),
        body,
        created_ms,
    };
    apply_entry_to_projections(tx, &entry)?;
    Ok(entry)
}

fn verify_model_completed_gate(conn: &Connection, head_id: i64, call_id: &str) -> Result<(), StoreError> {
    let mut seen_call_ids = HashSet::new();
    let mut curr_id = Some(head_id);
    let mut is_head = true;

    while let Some(id) = curr_id {
        let entry = get_entry_conn(conn, id)?.ok_or(StoreError::EntryNotFound(id))?;
        match entry.kind {
            EntryKind::ToolCall => {
                if is_head {
                    return Err(StoreError::ProtocolViolation(format!(
                        "cannot append tool call '{call_id}' while prior tool.call entry {id} has no tool.result"
                    )));
                }
                if let Some(cid) = entry.body.get("call_id").and_then(Value::as_str) {
                    seen_call_ids.insert(cid.to_string());
                }
                curr_id = entry.parent_id;
            }
            EntryKind::ToolResult => curr_id = entry.parent_id,
            EntryKind::ModelCompleted => {
                let calls = entry
                    .body
                    .get("tool_calls")
                    .and_then(Value::as_array)
                    .ok_or_else(|| {
                        StoreError::ProtocolViolation(format!(
                            "model.completed entry {id} has no tool_calls array"
                        ))
                    })?;
                if !calls.iter().any(|tc| tc.get("id").and_then(Value::as_str) == Some(call_id)) {
                    return Err(StoreError::ProtocolViolation(format!(
                        "tool call '{call_id}' not found in model.completed entry {id}"
                    )));
                }
                if seen_call_ids.contains(call_id) {
                    return Err(StoreError::ProtocolViolation(format!(
                        "tool call '{call_id}' already executed in batch for model.completed entry {id}"
                    )));
                }
                return Ok(());
            }
            EntryKind::ModelInterrupted => {
                return Err(StoreError::ProtocolViolation(format!(
                    "cannot execute tool call '{call_id}' after model.interrupted entry {id}"
                )));
            }
            other => {
                return Err(StoreError::ProtocolViolation(format!(
                    "cannot execute tool call '{call_id}' without active model.completed (found {} at entry {id})",
                    other.as_str()
                )));
            }
        }
        is_head = false;
    }

    Err(StoreError::ProtocolViolation(format!(
        "cannot execute tool call '{call_id}' without active model.completed"
    )))
}

fn require_thread(conn: &Connection, thread_id: &str) -> Result<ThreadRow, StoreError> {
    get_thread_conn(conn, thread_id)?.ok_or_else(|| StoreError::ThreadNotFound(thread_id.into()))
}

fn require_epoch(conn: &Connection, thread_id: &str, writer_epoch: i64) -> Result<ThreadRow, StoreError> {
    let thread = require_thread(conn, thread_id)?;
    if thread.epoch != writer_epoch {
        return Err(StoreError::StaleEpoch {
            thread_id: thread_id.to_string(),
            writer_epoch,
            current_epoch: thread.epoch,
        });
    }
    Ok(thread)
}

fn collect_rows<T, P, F>(conn: &Connection, sql: &str, p: P, f: F) -> Result<Vec<T>, StoreError>
where
    P: Params,
    F: FnMut(&Row<'_>) -> rusqlite::Result<T>,
{
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map(p, f)?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

fn get_thread_conn(conn: &Connection, thread_id: &str) -> Result<Option<ThreadRow>, StoreError> {
    let sql = format!("SELECT {THREAD_COLS} FROM threads WHERE id = ?1");
    conn.query_row(&sql, params![thread_id], row_to_thread).optional().map_err(StoreError::from)
}

fn get_entry_conn(conn: &Connection, entry_id: i64) -> Result<Option<Entry>, StoreError> {
    let sql = format!("SELECT {ENTRY_COLS} FROM entries WHERE id = ?1");
    conn.query_row(&sql, params![entry_id], row_to_entry).optional().map_err(StoreError::from)
}

fn get_entry_by_request_id(conn: &Connection, request_id: &str) -> Result<Option<Entry>, StoreError> {
    let sql = format!("SELECT {ENTRY_COLS} FROM entries WHERE request_id = ?1");
    conn.query_row(&sql, params![request_id], row_to_entry).optional().map_err(StoreError::from)
}

fn read_lease_conn(conn: &Connection, thread_id: &str) -> Result<Option<LeaseRow>, StoreError> {
    conn.query_row(
        "SELECT thread_id, epoch, owner_pid, lease_until_ms, partial_text, active_pgid
         FROM runner_leases WHERE thread_id = ?1",
        params![thread_id],
        |r| {
            Ok(LeaseRow {
                thread_id: r.get(0)?,
                epoch: r.get(1)?,
                owner_pid: r.get(2)?,
                lease_until_ms: r.get(3)?,
                partial_text: r.get(4)?,
                active_pgid: r.get(5)?,
            })
        },
    )
    .optional()
    .map_err(StoreError::from)
}

fn causal_chain_conn(conn: &Connection, head_entry_id: i64) -> Result<Vec<Entry>, StoreError> {
    let mut chain = Vec::new();
    let mut visited = HashSet::new();
    let mut curr = Some(head_entry_id);
    while let Some(id) = curr {
        if !visited.insert(id) {
            return Err(StoreError::ProtocolViolation(format!("cycle detected in causal chain at entry {id}")));
        }
        let entry = get_entry_conn(conn, id)?.ok_or(StoreError::EntryNotFound(id))?;
        curr = entry.parent_id;
        chain.push(entry);
    }
    chain.reverse();
    Ok(chain)
}

fn upsert_provider_tx(tx: &Transaction<'_>, p: &ProviderConfig) -> Result<(), StoreError> {
    let models_json = serde_json::to_string(&p.models).map_err(|e| StoreError::Other(e.into()))?;
    tx.execute(
        "INSERT INTO providers (name, url, wire, needs_key, key_name, session_header, models)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
         ON CONFLICT(name) DO UPDATE SET
             url = excluded.url, wire = excluded.wire, needs_key = excluded.needs_key,
             key_name = excluded.key_name, session_header = excluded.session_header,
             models = excluded.models",
        params![p.name, p.url, p.wire.as_str(), i64::from(p.needs_key), p.key_name, p.session_header, models_json],
    )?;
    Ok(())
}

fn json_opt_str(body: &Value, key: &str) -> Option<String> {
    body.get(key).and_then(Value::as_str).map(str::to_string)
}

fn sql_conv_err(col: usize, err: Box<dyn std::error::Error + Send + Sync>) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(col, rusqlite::types::Type::Text, err)
}

fn row_to_entry(r: &Row<'_>) -> rusqlite::Result<Entry> {
    let kind = EntryKind::parse(&r.get::<_, String>(4)?).map_err(|e| sql_conv_err(4, e.into()))?;
    let body = serde_json::from_str(&r.get::<_, String>(8)?).map_err(|e| sql_conv_err(8, Box::new(e)))?;
    Ok(Entry {
        id: r.get(0)?,
        thread_id: r.get(1)?,
        parent_id: r.get(2)?,
        epoch: r.get(3)?,
        kind,
        request_id: r.get(5)?,
        actor: r.get(6)?,
        tree_sha: r.get(7)?,
        body,
        created_ms: r.get(9)?,
    })
}

fn row_to_thread(r: &Row<'_>) -> rusqlite::Result<ThreadRow> {
    let status = ThreadStatus::parse(&r.get::<_, String>(8)?).map_err(|e| sql_conv_err(8, e.into()))?;
    Ok(ThreadRow {
        id: r.get(0)?,
        epoch: r.get(1)?,
        head_entry_id: r.get(2)?,
        model: r.get(3)?,
        source_dir: r.get(4)?,
        base_commit: r.get(5)?,
        base_tree_sha: r.get(6)?,
        last_tree_sha: r.get(7)?,
        status,
        stop_requested: r.get(9)?,
        updated_ms: r.get(10)?,
    })
}

fn row_to_delivery(r: &Row<'_>) -> rusqlite::Result<DeliveryRow> {
    let mode = DeliveryMode::parse(&r.get::<_, String>(3)?).map_err(|e| sql_conv_err(3, e.into()))?;
    let state = DeliveryState::parse(&r.get::<_, String>(6)?).map_err(|e| sql_conv_err(6, e.into()))?;
    Ok(DeliveryRow {
        entry_id: r.get(0)?,
        thread_id: r.get(1)?,
        request_id: r.get(2)?,
        mode,
        text: r.get(4)?,
        model_override: r.get(5)?,
        state,
        accepted_by: r.get(7)?,
        created_ms: r.get(8)?,
    })
}

fn row_to_provider(r: &Row<'_>) -> rusqlite::Result<ProviderConfig> {
    let wire = WireCodec::parse(&r.get::<_, String>(2)?).map_err(|e| sql_conv_err(2, e.into()))?;
    let models = serde_json::from_str(&r.get::<_, String>(6)?).map_err(|e| sql_conv_err(6, Box::new(e)))?;
    Ok(ProviderConfig {
        name: r.get(0)?,
        url: r.get(1)?,
        wire,
        needs_key: r.get::<_, i64>(3)? != 0,
        key_name: r.get(4)?,
        session_header: r.get(5)?,
        models,
    })
}

fn row_to_rate_limit(r: &Row<'_>) -> rusqlite::Result<RateLimitRecord> {
    Ok(RateLimitRecord {
        provider: r.get(0)?,
        limit_name: r.get(1)?,
        limit_val: r.get::<_, i64>(2)?.max(0) as u64,
        remaining: r.get::<_, i64>(3)?.max(0) as u64,
        reset_secs: r.get::<_, Option<i64>>(4)?.map(|v| v.max(0) as u64),
        observed_ms: r.get(5)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

    fn temp_store() -> (PathBuf, Store) {
        let n = TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!("arity-store-test-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let store = Store::open(&dir).expect("open store");
        (dir, store)
    }

    fn all_deliveries(store: &Store) -> Vec<DeliveryRow> {
        let sql = format!("SELECT {DELIVERY_COLS} FROM deliveries ORDER BY entry_id ASC");
        collect_rows(&store.conn, &sql, [], row_to_delivery).unwrap()
    }

    #[test]
    fn seeds_builtin_providers_and_upserts_rate_limits() {
        let (dir, mut store) = temp_store();
        assert_eq!(store.list_providers().unwrap().len(), 7);
        assert!(Store::pid_is_alive(std::process::id() as i64));
        assert!(!Store::pid_is_alive(0));
        assert!(!Store::pid_is_alive(-1));

        let go = store.get_provider("opencode-go").unwrap().unwrap();
        assert_eq!(go.key_name, "opencode");
        assert_eq!(go.session_header.as_deref(), Some("x-opencode-session"));
        assert_eq!(go.wire, WireCodec::ChatCompletions);

        let codex = store.get_provider("codex").unwrap().unwrap();
        assert!(!codex.needs_key);
        assert_eq!(codex.wire, WireCodec::Responses);

        store
            .record_rate_limits(&[RateLimitRecord {
                provider: "openai".into(),
                limit_name: "requests".into(),
                limit_val: 500,
                remaining: 420,
                reset_secs: Some(30),
                observed_ms: 1000,
            }])
            .unwrap();
        let limits = store.list_rate_limits(Some("openai")).unwrap();
        assert_eq!(limits.len(), 1);
        assert_eq!(limits[0].remaining, 420);

        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn request_id_idempotency_across_intents() {
        let (dir, mut store) = temp_store();

        let e1 = store
            .create_thread("t1", "openai:gpt-4.1", Some("/repo"), Some("abc"), Some("tree0"), "sys", "req-c1")
            .unwrap();
        assert_eq!(
            e1,
            store.create_thread("t1", "other", None, None, None, "diff", "req-c1").unwrap()
        );

        for (mode, req_id) in [
            (DeliveryMode::Steer, "req-steer-1"),
            (DeliveryMode::AfterTurn, "req-after-1"),
            (DeliveryMode::Inbox, "req-inbox-1"),
        ] {
            let d1 = store.enqueue_delivery("t1", mode, "hello", None, req_id).unwrap();
            let d2 = store.enqueue_delivery("t1", mode, "other", Some("x"), req_id).unwrap();
            assert_eq!(d1, d2);
        }
        assert_eq!(store.pending_deliveries("t1").unwrap().len(), 3);

        let f1 = store.fork_thread("t1-fork", e1.id, Some("mlx:qwen"), "req-fork-1").unwrap();
        let f2 = store.fork_thread("t1-fork", e1.id, Some("other"), "req-fork-1").unwrap();
        assert_eq!(f1, f2);
        assert_eq!(f1.parent_id, Some(e1.id));
        assert_eq!(f1.tree_sha.as_deref(), Some("tree0"));

        let s1 = store.request_stop("t1", "user", "req-stop-1").unwrap();
        let s2 = store.request_stop("t1", "other", "req-stop-1").unwrap();
        assert_eq!(s1, s2);
        assert_eq!(store.get_thread("t1").unwrap().unwrap().stop_requested, 1);

        let a1 = store.record_workspace_applied("t1-fork", 2, "tree0", "tree9", "req-apply-1").unwrap();
        let a2 = store.record_workspace_applied("t1-fork", 99, "x", "y", "req-apply-1").unwrap();
        assert_eq!(a1, a2);
        assert_eq!(a1.parent_id, Some(f1.id));
        assert_eq!(store.find_entry_by_request_id("req-apply-1").unwrap(), Some(a1.clone()));
        assert_eq!(store.find_entry_by_request_id("req-missing").unwrap(), None);
        let t1_fork = store.get_thread("t1-fork").unwrap().unwrap();
        assert_eq!(t1_fork.head_entry_id, Some(a1.id));
        let chain = store.causal_chain(a1.id).unwrap();
        assert_eq!(chain.last().map(|e| e.kind), Some(EntryKind::WorkspaceApplied));

        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn stale_epoch_rejected_after_takeover() {
        let (dir, mut store) = temp_store();
        let pid = std::process::id() as i64;
        store.create_thread("t1", "openai:gpt-4.1", None, None, Some("tree0"), "sys", "r0").unwrap();
        store.enqueue_delivery("t1", DeliveryMode::Steer, "run tool", None, "r1").unwrap();

        let c1 = store.try_claim_turn("t1", pid, 0).unwrap().unwrap();
        assert_eq!(c1.epoch, 1);
        store.accept_deliveries("t1", c1.epoch, &[DeliveryMode::Steer]).unwrap().unwrap();

        let tc = ToolCallItem {
            id: "call_1".into(),
            name: "read_file".into(),
            arguments: r#"{"path":"a.txt"}"#.into(),
        };
        store
            .append_model_completed("t1", c1.epoch, "openai:gpt-4.1", "", std::slice::from_ref(&tc), None, None, 10)
            .unwrap();
        store.append_tool_call("t1", c1.epoch, &tc, false).unwrap();
        store.heartbeat_lease("t1", c1.epoch, 0, None, Some(Some(4242))).unwrap();

        // Runner 2 steals expired lease at epoch 2 and receives stale_pgid.
        let c2 = store.try_claim_turn("t1", pid, 15_000).unwrap().unwrap();
        assert_eq!(c2.epoch, 2);
        assert_eq!(c2.stale_pgid, Some(4242));
        assert!(store.try_claim_turn("t1", pid, 15_000).unwrap().is_none());

        // Runner 1 (epoch 1) is fenced out across runner methods.
        assert!(matches!(
            store.append_model_completed("t1", c1.epoch, "openai:gpt-4.1", "late", &[], None, None, 1),
            Err(StoreError::StaleEpoch { writer_epoch: 1, current_epoch: 2, .. })
        ));
        assert!(matches!(
            store.append_tool_result("t1", c1.epoch, "call_1", "read_file", ToolReceiptStatus::Ok, "data", None, None),
            Err(StoreError::StaleEpoch { writer_epoch: 1, current_epoch: 2, .. })
        ));
        assert!(matches!(
            store.heartbeat_lease("t1", c1.epoch, 15_000, None, None),
            Err(StoreError::StaleEpoch { writer_epoch: 1, current_epoch: 2, .. })
        ));
        assert!(matches!(
            store.finish_turn("t1", c1.epoch, ThreadStatus::Idle, "done", None),
            Err(StoreError::StaleEpoch { writer_epoch: 1, current_epoch: 2, .. })
        ));

        // Runner 2 resolves the crashed tool call and clears active_pgid.
        let res = store
            .append_tool_result("t1", c2.epoch, "call_1", "read_file", ToolReceiptStatus::Unknown, "recovered", None, Some("tree1"))
            .unwrap();
        assert_eq!(res.tree_sha.as_deref(), Some("tree1"));
        assert_eq!(store.read_lease("t1").unwrap().unwrap().active_pgid, None);

        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn model_completed_gate_rejects_invalid_tool_calls() {
        let (dir, mut store) = temp_store();
        store.create_thread("t1", "openai:gpt-4.1", None, None, None, "sys", "r0").unwrap();
        store.enqueue_delivery("t1", DeliveryMode::Steer, "hi", None, "r1").unwrap();
        let c = store.try_claim_turn("t1", std::process::id() as i64, 15_000).unwrap().unwrap();
        store.accept_deliveries("t1", c.epoch, &[DeliveryMode::Steer]).unwrap();

        let tc1 = ToolCallItem { id: "c1".into(), name: "bash".into(), arguments: r#"{"cmd":"ls"}"#.into() };
        let tc2 = ToolCallItem { id: "c2".into(), name: "read_file".into(), arguments: r#"{"path":"a"}"#.into() };

        // Rejected without ModelCompleted and after ModelInterrupted.
        assert!(matches!(store.append_tool_call("t1", c.epoch, &tc1, true), Err(StoreError::ProtocolViolation(_))));
        store.append_model_interrupted("t1", c.epoch, "openai:gpt-4.1", "partial", InterruptReason::Steer).unwrap();
        assert!(matches!(store.append_tool_call("t1", c.epoch, &tc1, true), Err(StoreError::ProtocolViolation(_))));

        // Allowed for declared calls; rejected for undeclared, out-of-order, or duplicate calls.
        store
            .append_model_completed("t1", c.epoch, "openai:gpt-4.1", "", &[tc1.clone(), tc2.clone()], None, None, 5)
            .unwrap();
        let undeclared = ToolCallItem { id: "c999".into(), name: "bash".into(), arguments: "{}".into() };
        assert!(matches!(store.append_tool_call("t1", c.epoch, &undeclared, false), Err(StoreError::ProtocolViolation(_))));

        store.append_tool_call("t1", c.epoch, &tc1, true).unwrap();
        assert!(matches!(store.append_tool_call("t1", c.epoch, &tc2, false), Err(StoreError::ProtocolViolation(_))));
        store.append_tool_result("t1", c.epoch, "c1", "bash", ToolReceiptStatus::Ok, "ok", Some(0), None).unwrap();
        assert!(matches!(store.append_tool_call("t1", c.epoch, &tc1, true), Err(StoreError::ProtocolViolation(_))));

        store.append_tool_call("t1", c.epoch, &tc2, false).unwrap();
        store.append_tool_result("t1", c.epoch, "c2", "read_file", ToolReceiptStatus::Ok, "hi", None, None).unwrap();

        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn rebuild_projections_is_byte_for_byte_identical() {
        let (dir, mut store) = temp_store();
        let pid = std::process::id() as i64;

        store.create_thread("t1", "openai:gpt-4.1", Some("/src"), Some("c0"), Some("tree0"), "sys", "r-create").unwrap();
        assert!(!store.thread_needs_work("t1").unwrap());

        store.enqueue_delivery("t1", DeliveryMode::Inbox, "fyi", None, "r-inbox").unwrap();
        assert!(!store.thread_needs_work("t1").unwrap());

        store.enqueue_delivery("t1", DeliveryMode::Steer, "do work", Some("openai:gpt-4.1-mini"), "r-steer").unwrap();
        assert!(store.thread_needs_work("t1").unwrap());
        assert_eq!(store.get_thread("t1").unwrap().unwrap().stop_requested, 2);

        let claim = store.try_claim_turn("t1", pid, 15_000).unwrap().unwrap();
        store.accept_deliveries("t1", claim.epoch, &[DeliveryMode::Steer, DeliveryMode::Inbox]).unwrap().unwrap();
        let t1 = store.get_thread("t1").unwrap().unwrap();
        assert_eq!(t1.model, "openai:gpt-4.1-mini");
        assert_eq!(t1.stop_requested, 0);

        let tc = ToolCallItem { id: "call_w".into(), name: "write_file".into(), arguments: r#"{"path":"x.txt"}"#.into() };
        store
            .append_model_completed(
                "t1", claim.epoch, "openai:gpt-4.1-mini", "writing", std::slice::from_ref(&tc),
                Some(TokenUsage { input: 10, cached: 2, output: 5, total: 15 }), Some(12), 40,
            )
            .unwrap();
        store.append_tool_call("t1", claim.epoch, &tc, true).unwrap();
        let tr = store
            .append_tool_result("t1", claim.epoch, "call_w", "write_file", ToolReceiptStatus::Ok, "wrote", None, Some("tree1"))
            .unwrap();

        let fork = store.fork_thread("t2", tr.id, None, "r-fork").unwrap();
        assert_eq!(fork.tree_sha.as_deref(), Some("tree1"));
        let t2 = store.get_thread("t2").unwrap().unwrap();
        assert_eq!(t2.model, "openai:gpt-4.1-mini");
        assert_eq!(t2.last_tree_sha.as_deref(), Some("tree1"));

        store.request_stop("t1", "ctrl-c", "r-stop").unwrap();
        assert_eq!(store.heartbeat_lease("t1", claim.epoch, 15_000, Some("half"), None).unwrap(), 1);
        store.append_model_interrupted("t1", claim.epoch, "openai:gpt-4.1-mini", "half", InterruptReason::Stop).unwrap();
        store.finish_turn("t1", claim.epoch, ThreadStatus::Halted, "stopped", None).unwrap();
        assert!(!store.thread_needs_work("t1").unwrap());
        assert!(store.read_lease("t1").unwrap().is_none());

        store.enqueue_delivery("t1", DeliveryMode::AfterTurn, "continue", None, "r-after").unwrap();
        assert_eq!(store.get_thread("t1").unwrap().unwrap().status, ThreadStatus::Idle);
        assert!(store.thread_needs_work("t1").unwrap());

        store.record_workspace_applied("t2", 1, "tree0", "tree1", "r-apply").unwrap();
        assert_eq!(store.get_thread("t2").unwrap().unwrap().status, ThreadStatus::Applied);
        assert!(!store.thread_needs_work("t2").unwrap());

        let threads_before = store.list_threads().unwrap();
        let deliveries_before = all_deliveries(&store);
        store.rebuild_projections().unwrap();
        assert_eq!(threads_before, store.list_threads().unwrap());
        assert_eq!(deliveries_before, all_deliveries(&store));

        let _ = std::fs::remove_dir_all(dir);
    }
}
