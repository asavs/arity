//! Thin UniFFI client facade over Arity's single-writer SQLite spine.
//!
//! Strict invariant: this module NEVER runs a turn in-process. Like the CLI, it only
//! appends idempotent intents to SQLite, reads/tails projections, and spawns the
//! detached `arity worker --thread <id> --once` binary when no live lease exists.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::Value;

use crate::runner::DEFAULT_SYSTEM_PROMPT;
use crate::secrets;
use crate::store::{LeaseRow, Store};
use crate::types::{
    DeliveryMode, Entry, EntryKind, ProviderConfig, StoreError, ThreadRow, ThreadStatus, WireCodec,
    now_ms,
};
use crate::workspace;

#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum FfiError {
    #[error("{message}")]
    Message { message: String },
}

impl FfiError {
    fn msg(s: impl Into<String>) -> Self {
        Self::Message { message: s.into() }
    }
}

impl From<anyhow::Error> for FfiError {
    fn from(err: anyhow::Error) -> Self {
        Self::msg(format!("{err:#}"))
    }
}

impl From<StoreError> for FfiError {
    fn from(err: StoreError) -> Self {
        Self::msg(err.to_string())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiThreadSummary {
    pub id: String,
    pub epoch: i64,
    pub model: String,
    pub source_dir: Option<String>,
    pub base_tree_sha: Option<String>,
    pub last_tree_sha: Option<String>,
    pub status: String,
    pub has_changes: bool,
    pub is_running: bool,
    pub pending_count: u32,
    pub updated_ms: i64,
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiToolItem {
    pub call_id: String,
    pub name: String,
    pub arguments: String,
    pub status: String,
    pub output: String,
    pub exit_code: Option<i32>,
    pub tree_sha: Option<String>,
    pub call_entry_id: i64,
    pub result_entry_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Enum)]
pub enum UiTranscriptItem {
    UserMessage {
        entry_id: i64,
        text: String,
        mode: String,
        queued: bool,
        model_override: Option<String>,
        created_ms: i64,
    },
    AssistantMessage {
        entry_id: i64,
        model: String,
        text: String,
        interrupted: bool,
        interrupt_reason: Option<String>,
        input_tokens: Option<u64>,
        cached_tokens: Option<u64>,
        output_tokens: Option<u64>,
        created_ms: i64,
    },
    ToolStep {
        tool: UiToolItem,
        created_ms: i64,
    },
    Notice {
        entry_id: i64,
        kind: String,
        text: String,
        created_ms: i64,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiThreadDetail {
    pub summary: UiThreadSummary,
    pub partial_text: String,
    pub items: Vec<UiTranscriptItem>,
    pub needs_work: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiModelOption {
    pub spec: String,
    pub provider: String,
    pub model_id: String,
    pub ready: bool,
    pub local: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiDiffFileStat {
    pub path: String,
    pub status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiWorkspaceDiff {
    pub base_tree_sha: String,
    pub target_tree_sha: String,
    pub files: Vec<UiDiffFileStat>,
    pub patch: String,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiApplyResult {
    pub files_applied: u32,
    pub summary: String,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiReplayCheck {
    pub ok: bool,
    pub threads: u32,
    pub deliveries: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiProviderConfig {
    pub name: String,
    pub url: String,
    pub wire: String,
    pub needs_key: bool,
    pub key_name: String,
    pub session_header: Option<String>,
    pub models: Vec<String>,
    pub ready: bool,
    pub local: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct UiRateLimit {
    pub provider: String,
    pub limit_name: String,
    pub limit_val: u64,
    pub remaining: u64,
    pub reset_secs: Option<u64>,
    pub observed_ms: i64,
}

#[derive(uniffi::Object)]
pub struct ArityClient {
    data_dir: PathBuf,
    worker_bin: PathBuf,
    local_models: Mutex<HashMap<String, Vec<String>>>,
}

#[uniffi::export]
impl ArityClient {
    #[uniffi::constructor]
    pub fn new(
        data_dir: Option<String>,
        worker_bin: Option<String>,
    ) -> Result<Arc<Self>, FfiError> {
        let data_dir = data_dir
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(crate::default_data_dir);

        let worker_bin = worker_bin
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(resolve_sibling_worker_bin);

        // Initialize database schema & builtin providers eagerly.
        let _ = Store::open(&data_dir)?;

        Ok(Arc::new(Self {
            data_dir,
            worker_bin,
            local_models: Mutex::new(HashMap::new()),
        }))
    }

    pub fn data_dir(&self) -> String {
        self.data_dir.to_string_lossy().into_owned()
    }

    pub fn list_threads(&self) -> Result<Vec<UiThreadSummary>, FfiError> {
        let store = Store::open(&self.data_dir)?;
        let threads = store.list_threads()?;
        let mut out = Vec::with_capacity(threads.len());
        for t in threads {
            out.push(build_thread_summary(&store, &t)?);
        }
        Ok(out)
    }

    pub fn thread_detail(&self, thread_id: String) -> Result<UiThreadDetail, FfiError> {
        let store = Store::open(&self.data_dir)?;
        let thread = store
            .get_thread(&thread_id)?
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' not found")))?;

        let lease = store.read_lease(&thread_id)?;
        let is_running = lease_is_live(lease.as_ref());
        let partial_text = if is_running {
            lease.map(|l| l.partial_text).unwrap_or_default()
        } else {
            String::new()
        };

        let summary = build_thread_summary(&store, &thread)?;
        let needs_work = store.thread_needs_work(&thread_id)?;

        let chain = match thread.head_entry_id {
            Some(head_id) => store.causal_chain(head_id)?,
            None => Vec::new(),
        };
        let queued = store.pending_deliveries(&thread_id)?;
        let mut items = build_transcript_items(&chain, &queued);

        // Include trailing workspace.applied receipt if present (not rendered inline by build_transcript_items).
        if thread.status == ThreadStatus::Applied {
            let entries = store.thread_entries(&thread_id, 0)?;
            if let Some(applied) = entries
                .iter()
                .rev()
                .find(|e| e.kind == EntryKind::WorkspaceApplied)
            {
                let count = applied.body["files_applied"].as_u64().unwrap_or(0);
                items.push(UiTranscriptItem::Notice {
                    entry_id: applied.id,
                    kind: "applied".into(),
                    text: format!(
                        "Applied {count} changed file{} to workspace",
                        if count == 1 { "" } else { "s" }
                    ),
                    created_ms: applied.created_ms,
                });
            }
        }

        Ok(UiThreadDetail {
            summary,
            partial_text,
            items,
            needs_work,
        })
    }

    pub fn available_models(&self) -> Result<Vec<UiModelOption>, FfiError> {
        let store = Store::open(&self.data_dir)?;
        let providers = store.list_providers()?;
        let cached = self
            .local_models
            .lock()
            .map(|g| g.clone())
            .unwrap_or_default();

        let mut out = Vec::new();
        for p in providers {
            let ready = provider_is_ready(&self.data_dir, &p);
            let local = is_local_url(&p.url);
            let mut seen = HashSet::new();

            // Prefer freshly discovered local models first if available, then configured models.
            if let Some(live_models) = cached.get(&p.name) {
                for m in live_models {
                    if !m.trim().is_empty() && seen.insert(m.clone()) {
                        out.push(UiModelOption {
                            spec: format!("{}:{m}", p.name),
                            provider: p.name.clone(),
                            model_id: m.clone(),
                            ready,
                            local,
                        });
                    }
                }
            }
            for m in p.models {
                if !m.trim().is_empty() && seen.insert(m.clone()) {
                    out.push(UiModelOption {
                        spec: format!("{}:{m}", p.name),
                        provider: p.name.clone(),
                        model_id: m,
                        ready,
                        local,
                    });
                }
            }
        }
        Ok(out)
    }

    pub fn refresh_local_models(&self) -> Result<Vec<UiModelOption>, FfiError> {
        let store = Store::open(&self.data_dir)?;
        let providers = store.list_providers()?;
        let mut discovered: HashMap<String, Vec<String>> = HashMap::new();

        let client = reqwest::blocking::Client::builder()
            .connect_timeout(Duration::from_millis(800))
            .timeout(Duration::from_millis(800))
            .build()
            .ok();

        if let Some(http) = client {
            for p in &providers {
                if !is_local_url(&p.url) {
                    continue;
                }
                let endpoint = format!("{}/models", p.url.trim_end_matches('/'));
                if let Ok(resp) = http.get(&endpoint).send()
                    && resp.status().is_success()
                    && let Ok(val) = resp.json::<Value>()
                    && let Some(arr) = val.get("data").and_then(Value::as_array)
                {
                    let models: Vec<String> = arr
                        .iter()
                        .filter_map(|item| item.get("id").and_then(Value::as_str))
                        .map(str::trim)
                        .filter(|s| !s.is_empty())
                        .map(str::to_string)
                        .collect();
                    if !models.is_empty() {
                        discovered.insert(p.name.clone(), models);
                    }
                }
            }
        }

        if let Ok(mut guard) = self.local_models.lock() {
            for (k, v) in discovered {
                guard.insert(k, v);
            }
        }

        self.available_models()
    }

    pub fn create_thread(
        &self,
        thread_id: Option<String>,
        model: String,
        source_dir: Option<String>,
        request_id: Option<String>,
    ) -> Result<String, FfiError> {
        let model = model.trim().to_string();
        if model.is_empty() || !model.contains(':') {
            return Err(FfiError::msg(format!(
                "model spec must be '<provider>:<model_id>', got '{model}'"
            )));
        }

        let mut store = Store::open(&self.data_dir)?;
        let final_id = match thread_id.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
            Some(id) => id.to_string(),
            None => next_thread_slug(&store, "chat")?,
        };
        let req_id = request_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(random_req_id);

        let already_exists = store.get_thread(&final_id)?.is_some();
        let clean_source = source_dir
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);

        let (resolved_src, base_commit, base_tree_sha) = if !already_exists {
            if let Some(ref d) = clean_source {
                let ws = workspace::init_workspace(&self.data_dir, &final_id, Path::new(d))?;
                (
                    Some(ws.source_dir),
                    Some(ws.base_commit),
                    Some(ws.base_tree_sha),
                )
            } else {
                (None, None, None)
            }
        } else {
            (None, None, None)
        };

        let entry = store.create_thread(
            &final_id,
            &model,
            resolved_src.as_deref(),
            base_commit.as_deref(),
            base_tree_sha.as_deref(),
            DEFAULT_SYSTEM_PROMPT,
            &req_id,
        )?;
        Ok(entry.thread_id)
    }

    pub fn send_message(
        &self,
        thread_id: String,
        text: String,
        mode: String,
        model_override: Option<String>,
        request_id: Option<String>,
    ) -> Result<i64, FfiError> {
        let trimmed = text.trim();
        if trimmed.is_empty() {
            return Err(FfiError::msg("message text must not be empty"));
        }
        let parsed_mode = DeliveryMode::parse(&mode)?;
        let req_id = request_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(random_req_id);
        let model_override = model_override
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);

        let mut store = Store::open(&self.data_dir)?;
        let entry = store.enqueue_delivery(
            &thread_id,
            parsed_mode,
            trimmed,
            model_override.as_deref(),
            &req_id,
        )?;

        if parsed_mode != DeliveryMode::Inbox
            && !lease_is_live(store.read_lease(&thread_id)?.as_ref())
        {
            spawn_detached_worker(&self.worker_bin, &self.data_dir, &thread_id)?;
        }

        Ok(entry.id)
    }

    pub fn steer_thread(
        &self,
        thread_id: String,
        text: String,
        model_override: Option<String>,
        request_id: Option<String>,
    ) -> Result<i64, FfiError> {
        self.send_message(thread_id, text, "steer".into(), model_override, request_id)
    }

    pub fn stop_thread(
        &self,
        thread_id: String,
        request_id: Option<String>,
    ) -> Result<(), FfiError> {
        let req_id = request_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(random_req_id);
        let mut store = Store::open(&self.data_dir)?;
        store.request_stop(&thread_id, "user", &req_id)?;
        Ok(())
    }

    pub fn spawn_worker(&self, thread_id: String) -> Result<bool, FfiError> {
        let store = Store::open(&self.data_dir)?;
        if lease_is_live(store.read_lease(&thread_id)?.as_ref()) {
            return Ok(false);
        }
        if !store.thread_needs_work(&thread_id)? {
            return Ok(false);
        }
        spawn_detached_worker(&self.worker_bin, &self.data_dir, &thread_id)?;
        Ok(true)
    }

    pub fn fork_thread(
        &self,
        from_entry_id: i64,
        new_thread_id: Option<String>,
        model_override: Option<String>,
        request_id: Option<String>,
    ) -> Result<String, FfiError> {
        let mut store = Store::open(&self.data_dir)?;
        let from_entry = store
            .get_entry(from_entry_id)?
            .ok_or_else(|| FfiError::msg(format!("entry {from_entry_id} not found")))?;

        let final_id = match new_thread_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
        {
            Some(id) => id.to_string(),
            None => next_thread_slug(&store, &format!("{}-fork", from_entry.thread_id))?,
        };
        let req_id = request_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(random_req_id);
        let model_override = model_override
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);

        let entry = store.fork_thread(
            &final_id,
            from_entry_id,
            model_override.as_deref(),
            &req_id,
        )?;

        if !lease_is_live(store.read_lease(&entry.thread_id)?.as_ref())
            && let Some(thread) = store.get_thread(&entry.thread_id)?
            && let (Some(src), Some(sha)) = (
                thread.source_dir.as_deref().filter(|s| !s.is_empty()),
                thread.last_tree_sha.as_deref().filter(|s| !s.is_empty()),
            )
        {
            workspace::materialize_workspace(&self.data_dir, &entry.thread_id, Path::new(src), sha)?;
        }

        Ok(entry.thread_id)
    }

    pub fn diff_thread(&self, thread_id: String) -> Result<UiWorkspaceDiff, FfiError> {
        let store = Store::open(&self.data_dir)?;
        let thread = store
            .get_thread(&thread_id)?
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' not found")))?;
        let src = thread
            .source_dir
            .as_deref()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' has no attached workspace")))?;
        let base_sha = thread
            .base_tree_sha
            .as_deref()
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' has no base_tree_sha")))?;
        let last_sha = thread
            .last_tree_sha
            .as_deref()
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' has no last_tree_sha")))?;

        let diff = workspace::diff_trees(Path::new(src), base_sha, last_sha)?;
        Ok(UiWorkspaceDiff {
            base_tree_sha: diff.base_tree_sha,
            target_tree_sha: diff.target_tree_sha,
            files: diff
                .files
                .into_iter()
                .map(|f| UiDiffFileStat {
                    path: f.path,
                    status: f.status,
                })
                .collect(),
            patch: diff.patch,
        })
    }

    pub fn apply_thread(
        &self,
        thread_id: String,
        request_id: Option<String>,
    ) -> Result<UiApplyResult, FfiError> {
        let req_id = request_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(random_req_id);

        let mut store = Store::open(&self.data_dir)?;
        if let Some(existing) = store.find_entry_by_request_id(&req_id)? {
            if existing.kind != EntryKind::WorkspaceApplied {
                return Err(FfiError::msg(format!(
                    "request_id '{req_id}' already belongs to {} entry #{}",
                    existing.kind.as_str(),
                    existing.id
                )));
            }
            let files_applied = existing.body["files_applied"]
                .as_u64()
                .unwrap_or(0)
                .try_into()
                .unwrap_or(u32::MAX);
            let summary = if files_applied == 0 {
                "No changes to apply".to_string()
            } else {
                format!("Applied {files_applied} changed file(s)")
            };
            return Ok(UiApplyResult {
                files_applied,
                summary,
            });
        }

        let thread = store
            .get_thread(&thread_id)?
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' not found")))?;
        if lease_is_live(store.read_lease(&thread_id)?.as_ref()) {
            return Err(FfiError::msg(format!(
                "cannot apply thread '{thread_id}' while an active runner holds the lease"
            )));
        }

        let src = thread
            .source_dir
            .as_deref()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' has no attached workspace")))?;
        let base_sha = thread
            .base_tree_sha
            .as_deref()
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' has no base_tree_sha")))?;
        let last_sha = thread
            .last_tree_sha
            .as_deref()
            .ok_or_else(|| FfiError::msg(format!("thread '{thread_id}' has no last_tree_sha")))?;

        let applied = workspace::apply_tree(Path::new(src), base_sha, last_sha)?;
        store.record_workspace_applied(
            &thread_id,
            applied.files_applied,
            base_sha,
            last_sha,
            &req_id,
        )?;
        Ok(UiApplyResult {
            files_applied: applied.files_applied,
            summary: applied.summary,
        })
    }

    pub fn replay_check(&self) -> Result<UiReplayCheck, FfiError> {
        let mut store = Store::open(&self.data_dir)?;
        let threads_before = store.list_threads()?;
        let deliveries_before = store.list_deliveries()?;

        store.rebuild_projections()?;

        let threads_after = store.list_threads()?;
        let deliveries_after = store.list_deliveries()?;

        let ok = threads_before == threads_after && deliveries_before == deliveries_after;
        Ok(UiReplayCheck {
            ok,
            threads: threads_after.len().try_into().unwrap_or(u32::MAX),
            deliveries: deliveries_after.len().try_into().unwrap_or(u32::MAX),
        })
    }

    pub fn list_providers(&self) -> Result<Vec<UiProviderConfig>, FfiError> {
        let store = Store::open(&self.data_dir)?;
        let providers = store.list_providers()?;
        Ok(providers
            .into_iter()
            .map(|p| {
                let ready = provider_is_ready(&self.data_dir, &p);
                let local = is_local_url(&p.url);
                UiProviderConfig {
                    name: p.name,
                    url: p.url,
                    wire: p.wire.as_str().to_string(),
                    needs_key: p.needs_key,
                    key_name: p.key_name,
                    session_header: p.session_header,
                    models: p.models,
                    ready,
                    local,
                }
            })
            .collect())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn upsert_provider(
        &self,
        name: String,
        url: String,
        wire: String,
        needs_key: bool,
        key_name: String,
        session_header: Option<String>,
        models: Vec<String>,
    ) -> Result<(), FfiError> {
        let name = name.trim().to_string();
        let url = url.trim().to_string();
        if name.is_empty() || url.is_empty() {
            return Err(FfiError::msg("provider name and url are required"));
        }
        let wire = WireCodec::parse(&wire)?;
        let key_name = if key_name.trim().is_empty() {
            name.clone()
        } else {
            key_name.trim().to_string()
        };
        let session_header = session_header
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        let models = models
            .into_iter()
            .map(|m| m.trim().to_string())
            .filter(|m| !m.is_empty())
            .collect();

        let mut store = Store::open(&self.data_dir)?;
        store.upsert_provider(&ProviderConfig {
            name,
            url,
            wire,
            needs_key,
            key_name,
            session_header,
            models,
        })?;
        Ok(())
    }

    pub fn list_rate_limits(&self) -> Result<Vec<UiRateLimit>, FfiError> {
        let store = Store::open(&self.data_dir)?;
        let limits = store.list_rate_limits(None)?;
        Ok(limits
            .into_iter()
            .map(|r| UiRateLimit {
                provider: r.provider,
                limit_name: r.limit_name,
                limit_val: r.limit_val,
                remaining: r.remaining,
                reset_secs: r.reset_secs,
                observed_ms: r.observed_ms,
            })
            .collect())
    }

    pub fn has_key(&self, key_name: String) -> bool {
        secrets::keys::get(&self.data_dir, key_name.trim()).is_some()
    }

    pub fn set_key(&self, key_name: String, key: String) -> Result<(), FfiError> {
        let key_name = key_name.trim();
        if key_name.is_empty() {
            return Err(FfiError::msg("key_name must not be empty"));
        }
        secrets::keys::set(&self.data_dir, key_name, &key)?;
        Ok(())
    }

    pub fn codex_account(&self) -> Option<String> {
        secrets::codex_auth::account(&self.data_dir)
    }

    pub fn codex_login(&self) -> Result<String, FfiError> {
        let who = secrets::codex_auth::login(&self.data_dir)?;
        Ok(who)
    }

    pub fn codex_cancel_login(&self) {
        secrets::codex_auth::cancel();
    }

    pub fn codex_logout(&self) -> Result<(), FfiError> {
        secrets::codex_auth::logout(&self.data_dir)?;
        Ok(())
    }
}

// -----------------------------------------------------------------------------
// Internal helpers
// -----------------------------------------------------------------------------

fn resolve_sibling_worker_bin() -> PathBuf {
    if let Ok(exe) = std::env::current_exe()
        && let Some(dir) = exe.parent()
    {
        // Distinct name in Contents/MacOS so case-insensitive APFS never collides with `Arity`.
        let worker = dir.join("arity-worker");
        if worker.is_file() {
            return worker;
        }
        if let Some(contents) = dir.parent() {
            let helper = contents.join("Helpers").join("arity");
            if helper.is_file() {
                return helper;
            }
        }
        let sibling = dir.join("arity");
        if sibling.is_file() && !is_same_file(&exe, &sibling) {
            return sibling;
        }
    }
    PathBuf::from("arity")
}

fn is_same_file(a: &Path, b: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if let (Ok(ma), Ok(mb)) = (std::fs::metadata(a), std::fs::metadata(b)) {
            return ma.dev() == mb.dev() && ma.ino() == mb.ino();
        }
    }
    a == b
}

fn spawn_detached_worker(
    worker_bin: &Path,
    data_dir: &Path,
    thread_id: &str,
) -> Result<(), FfiError> {
    let mut cmd = Command::new(worker_bin);
    cmd.arg("--data-dir")
        .arg(data_dir)
        .args(["worker", "--thread", thread_id, "--once"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    cmd.spawn().map_err(|e| {
        FfiError::msg(format!(
            "failed to spawn worker '{}' for thread '{thread_id}': {e}",
            worker_bin.display()
        ))
    })?;
    Ok(())
}

fn lease_is_live(lease: Option<&LeaseRow>) -> bool {
    lease.is_some_and(|l| l.lease_until_ms > now_ms() && Store::pid_is_alive(l.owner_pid))
}

fn is_local_url(url: &str) -> bool {
    let lower = url.trim().to_ascii_lowercase();
    let rest = lower
        .strip_prefix("http://")
        .or_else(|| lower.strip_prefix("https://"))
        .unwrap_or(&lower);
    let host_port = rest.split('/').next().unwrap_or("");
    let host = if let Some(bracketed) = host_port.strip_prefix('[') {
        bracketed.split(']').next().unwrap_or("")
    } else {
        host_port.split(':').next().unwrap_or("")
    };
    matches!(host, "127.0.0.1" | "localhost" | "::1")
}

fn provider_is_ready(data_dir: &Path, p: &ProviderConfig) -> bool {
    if p.name == "codex" {
        secrets::codex_auth::signed_in(data_dir)
    } else if p.needs_key {
        secrets::keys::get(data_dir, &p.key_name).is_some()
    } else {
        true
    }
}

fn random_req_id() -> String {
    let bytes: [u8; 16] = rand::random();
    let mut hex = String::with_capacity(32);
    for b in bytes {
        use std::fmt::Write as _;
        let _ = write!(&mut hex, "{b:02x}");
    }
    hex
}

fn next_thread_slug(store: &Store, prefix: &str) -> Result<String, StoreError> {
    let existing: HashSet<String> = store
        .list_threads()?
        .into_iter()
        .map(|t| t.id)
        .collect();
    let mut n: u64 = 1;
    loop {
        let candidate = format!("{prefix}-{n}");
        if !existing.contains(&candidate) {
            return Ok(candidate);
        }
        n += 1;
    }
}

fn build_thread_summary(store: &Store, t: &ThreadRow) -> Result<UiThreadSummary, StoreError> {
    let lease = store.read_lease(&t.id)?;
    let is_running = lease_is_live(lease.as_ref());
    let pending = store.pending_deliveries(&t.id)?;
    let has_changes = t.source_dir.as_deref().is_some_and(|s| !s.is_empty())
        && match (t.base_tree_sha.as_deref(), t.last_tree_sha.as_deref()) {
            (Some(b), Some(l)) => !b.is_empty() && !l.is_empty() && b != l,
            _ => false,
        };

    let last_error = if t.status == ThreadStatus::Halted {
        let entries = store.thread_entries(&t.id, 0)?;
        entries
            .iter()
            .rev()
            .find(|e| e.kind == EntryKind::TurnFinished)
            .and_then(|e| {
                e.body
                    .get("error")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .or_else(|| {
                        let reason = e.body.get("reason").and_then(Value::as_str)?;
                        if reason == "halted_unknown_tool" {
                            Some("Halted after crash recovery: previous tool outcome was unknown.".into())
                        } else {
                            None
                        }
                    })
            })
    } else {
        None
    };

    Ok(UiThreadSummary {
        id: t.id.clone(),
        epoch: t.epoch,
        model: t.model.clone(),
        source_dir: t.source_dir.clone(),
        base_tree_sha: t.base_tree_sha.clone(),
        last_tree_sha: t.last_tree_sha.clone(),
        status: t.status.as_str().to_string(),
        has_changes,
        is_running,
        pending_count: pending.len().try_into().unwrap_or(u32::MAX),
        updated_ms: t.updated_ms,
        last_error,
    })
}

fn build_transcript_items(
    chain: &[Entry],
    queued: &[crate::types::DeliveryRow],
) -> Vec<UiTranscriptItem> {
    let mut items: Vec<UiTranscriptItem> = Vec::new();
    // Map call_id -> index in `items` for open ToolStep awaiting its ToolResult.
    let mut open_tools: HashMap<String, Vec<usize>> = HashMap::new();

    for entry in chain {
        match entry.kind {
            EntryKind::ThreadForked => {
                let from_id = entry.body["from_entry_id"].as_i64().unwrap_or(0);
                let from_thread = entry.body["from_thread_id"].as_str().unwrap_or("thread");
                items.push(UiTranscriptItem::Notice {
                    entry_id: entry.id,
                    kind: "forked".into(),
                    text: format!("Forked from {from_thread} (#{from_id})"),
                    created_ms: entry.created_ms,
                });
            }
            EntryKind::DeliveryAccepted => {
                let text = entry.body["text"].as_str().unwrap_or_default().to_string();
                let mode = entry.body["deliveries"]
                    .as_array()
                    .and_then(|arr| arr.last())
                    .and_then(|d| d.get("mode"))
                    .and_then(Value::as_str)
                    .unwrap_or("steer")
                    .to_string();
                let model_override = entry.body["model_override"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .map(str::to_string);
                items.push(UiTranscriptItem::UserMessage {
                    entry_id: entry.id,
                    text,
                    mode,
                    queued: false,
                    model_override,
                    created_ms: entry.created_ms,
                });
            }
            EntryKind::ModelCompleted => {
                let text = entry.body["text"].as_str().unwrap_or_default().to_string();
                let model = entry.body["model"].as_str().unwrap_or_default().to_string();
                let usage = entry.body.get("usage").filter(|u| u.is_object());
                let input_tokens = usage.and_then(|u| u["input"].as_u64());
                let cached_tokens = usage.and_then(|u| u["cached"].as_u64());
                let output_tokens = usage.and_then(|u| u["output"].as_u64());
                if !text.is_empty() {
                    items.push(UiTranscriptItem::AssistantMessage {
                        entry_id: entry.id,
                        model,
                        text,
                        interrupted: false,
                        interrupt_reason: None,
                        input_tokens,
                        cached_tokens,
                        output_tokens,
                        created_ms: entry.created_ms,
                    });
                }
            }
            EntryKind::ModelInterrupted => {
                let text = entry.body["text"].as_str().unwrap_or_default().to_string();
                let model = entry.body["model"].as_str().unwrap_or_default().to_string();
                let reason = entry.body["reason"].as_str().map(str::to_string);
                if !text.is_empty() {
                    items.push(UiTranscriptItem::AssistantMessage {
                        entry_id: entry.id,
                        model,
                        text,
                        interrupted: true,
                        interrupt_reason: reason,
                        input_tokens: None,
                        cached_tokens: None,
                        output_tokens: None,
                        created_ms: entry.created_ms,
                    });
                } else {
                    let label = match reason.as_deref() {
                        Some("steer") => "Interrupted by steer",
                        Some("stop") => "Stopped",
                        _ => "Interrupted",
                    };
                    items.push(UiTranscriptItem::Notice {
                        entry_id: entry.id,
                        kind: "interrupted".into(),
                        text: label.into(),
                        created_ms: entry.created_ms,
                    });
                }
            }
            EntryKind::ToolCall => {
                let call_id = entry.body["call_id"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string();
                let name = entry.body["name"].as_str().unwrap_or_default().to_string();
                let arguments = match &entry.body["arguments"] {
                    Value::String(s) => s.clone(),
                    Value::Null => "{}".into(),
                    other => other.to_string(),
                };
                let idx = items.len();
                open_tools.entry(call_id.clone()).or_default().push(idx);
                items.push(UiTranscriptItem::ToolStep {
                    tool: UiToolItem {
                        call_id,
                        name,
                        arguments,
                        status: "running".into(),
                        output: String::new(),
                        exit_code: None,
                        tree_sha: entry.tree_sha.clone(),
                        call_entry_id: entry.id,
                        result_entry_id: None,
                    },
                    created_ms: entry.created_ms,
                });
            }
            EntryKind::ToolResult => {
                let call_id = entry.body["call_id"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string();
                let name = entry.body["name"].as_str().unwrap_or_default().to_string();
                let status = entry.body["status"]
                    .as_str()
                    .unwrap_or("ok")
                    .to_string();
                let output = match &entry.body["output"] {
                    Value::String(s) => s.clone(),
                    Value::Null => String::new(),
                    other => other.to_string(),
                };
                let exit_code = entry.body["exit_code"].as_i64().map(|v| v as i32);

                let matched_idx = open_tools
                    .get_mut(&call_id)
                    .and_then(|stack| stack.pop());

                if let Some(idx) = matched_idx
                    && let Some(UiTranscriptItem::ToolStep { tool, .. }) = items.get_mut(idx)
                {
                    tool.status = status;
                    tool.output = output;
                    tool.exit_code = exit_code;
                    if entry.tree_sha.is_some() {
                        tool.tree_sha = entry.tree_sha.clone();
                    }
                    tool.result_entry_id = Some(entry.id);
                } else {
                    items.push(UiTranscriptItem::ToolStep {
                        tool: UiToolItem {
                            call_id,
                            name,
                            arguments: "{}".into(),
                            status,
                            output,
                            exit_code,
                            tree_sha: entry.tree_sha.clone(),
                            call_entry_id: entry.id,
                            result_entry_id: Some(entry.id),
                        },
                        created_ms: entry.created_ms,
                    });
                }
            }
            EntryKind::ThreadCreated
            | EntryKind::DeliveryQueued
            | EntryKind::ThreadStop
            | EntryKind::WorkspaceApplied
            | EntryKind::TurnClaimed
            | EntryKind::TurnFinished => {}
        }
    }

    for d in queued {
        items.push(UiTranscriptItem::UserMessage {
            entry_id: d.entry_id,
            text: d.text.clone(),
            mode: d.mode.as_str().to_string(),
            queued: true,
            model_override: d.model_override.clone(),
            created_ms: d.created_ms,
        });
    }

    items
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{ToolCallItem, ToolReceiptStatus};
    use serde_json::json;
    use std::fs;
    use std::io::Write;
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicU64, Ordering};

    struct TestDir(PathBuf);

    impl TestDir {
        fn new(label: &str) -> Self {
            static SEQ: AtomicU64 = AtomicU64::new(0);
            let n = SEQ.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "arity-ffi-{label}-{}-{n}",
                std::process::id()
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn make_repo(label: &str) -> TestDir {
        let dir = TestDir::new(label);
        for args in [
            &["init"][..],
            &["config", "user.email", "test@example.invalid"],
            &["config", "user.name", "Arity FFI Test"],
        ] {
            let out = Command::new("git")
                .env_remove("GIT_DIR")
                .env_remove("GIT_WORK_TREE")
                .env_remove("GIT_INDEX_FILE")
                .arg("-C")
                .arg(&dir.0)
                .args(args)
                .output()
                .unwrap();
            assert!(out.status.success());
        }
        fs::write(dir.0.join("a.txt"), "hello\n").unwrap();
        for args in [&["add", "."][..], &["commit", "-m", "init"]] {
            let out = Command::new("git")
                .env_remove("GIT_DIR")
                .env_remove("GIT_WORK_TREE")
                .env_remove("GIT_INDEX_FILE")
                .arg("-C")
                .arg(&dir.0)
                .args(args)
                .output()
                .unwrap();
            assert!(out.status.success());
        }
        dir
    }

    #[test]
    fn client_crud_transcript_grouping_fork_diff_apply_and_replay() {
        let data = TestDir::new("data");
        let repo = make_repo("repo");
        let client = ArityClient::new(
            Some(data.0.to_string_lossy().into_owned()),
            Some("/usr/bin/true".into()),
        )
        .unwrap();

        assert_eq!(client.data_dir(), data.0.to_string_lossy());

        // Auto slugging: chat-1, chat-2
        let t1 = client
            .create_thread(
                None,
                "mlx:mlx-community/Qwen3.5-4B-MLX-4bit".into(),
                Some(repo.0.to_string_lossy().into_owned()),
                None,
            )
            .unwrap();
        assert_eq!(t1, "chat-1");

        let t2 = client
            .create_thread(None, "openai:gpt-4.1".into(), None, None)
            .unwrap();
        assert_eq!(t2, "chat-2");

        // Enqueue inbox message (never spawns worker) and verify queued item appears in thread_detail.
        let inbox_id = client
            .send_message(t1.clone(), "queued note".into(), "inbox".into(), None, None)
            .unwrap();
        assert!(inbox_id > 0);

        let detail = client.thread_detail(t1.clone()).unwrap();
        assert_eq!(detail.items.len(), 1);
        match &detail.items[0] {
            UiTranscriptItem::UserMessage {
                text,
                mode,
                queued,
                ..
            } => {
                assert_eq!(text, "queued note");
                assert_eq!(mode, "inbox");
                assert!(*queued);
            }
            other => panic!("expected queued UserMessage, got {other:?}"),
        }

        // Simulate a committed tool step + worktree mutation directly in Store to test grouping, fork, diff, apply.
        let mut store = Store::open(&data.0).unwrap();
        let claim = store
            .try_claim_turn(&t1, std::process::id() as i64, 15_000)
            .unwrap()
            .unwrap();
        store
            .accept_deliveries(&t1, claim.epoch, &[DeliveryMode::Inbox])
            .unwrap();
        let tc = ToolCallItem {
            id: "call_w1".into(),
            name: "write_file".into(),
            arguments: json!({"path": "b.txt", "content": "new\n"}).to_string(),
        };
        store
            .append_model_completed(
                &t1,
                claim.epoch,
                "mlx:mlx-community/Qwen3.5-4B-MLX-4bit",
                "Writing b.txt",
                std::slice::from_ref(&tc),
                None,
                None,
                15,
            )
            .unwrap();
        store.append_tool_call(&t1, claim.epoch, &tc, true).unwrap();

        let (_, wt_root, _) = workspace::worktree_paths(&data.0, &t1, &repo.0).unwrap();
        fs::write(wt_root.join("b.txt"), "new\n").unwrap();
        let new_sha = workspace::snapshot_tree(&wt_root).unwrap();

        let tr = store
            .append_tool_result(
                &t1,
                claim.epoch,
                "call_w1",
                "write_file",
                ToolReceiptStatus::Ok,
                "wrote b.txt",
                None,
                Some(&new_sha),
            )
            .unwrap();
        store
            .finish_turn(&t1, claim.epoch, ThreadStatus::Idle, "completed", None)
            .unwrap();
        drop(store);

        let detail = client.thread_detail(t1.clone()).unwrap();
        assert!(detail.summary.has_changes);
        assert!(!detail.summary.is_running);
        // Should contain: UserMessage, AssistantMessage, ToolStep (grouped with result_entry_id == Some(tr.id)).
        assert_eq!(detail.items.len(), 3);
        match &detail.items[2] {
            UiTranscriptItem::ToolStep { tool, .. } => {
                assert_eq!(tool.call_id, "call_w1");
                assert_eq!(tool.status, "ok");
                assert_eq!(tool.output, "wrote b.txt");
                assert_eq!(tool.result_entry_id, Some(tr.id));
                assert_eq!(tool.tree_sha.as_deref(), Some(new_sha.as_str()));
            }
            other => panic!("expected ToolStep, got {other:?}"),
        }

        // Fork from the tool result entry.
        let forked_id = client
            .fork_thread(tr.id, None, Some("openai:gpt-4.1".into()), None)
            .unwrap();
        assert_eq!(forked_id, "chat-1-fork-1");

        let diff = client.diff_thread(forked_id.clone()).unwrap();
        assert_eq!(diff.files.len(), 1);
        assert_eq!(diff.files[0].path, "b.txt");
        assert_eq!(diff.files[0].status, "added");

        let applied = client
            .apply_thread(forked_id.clone(), Some("ffi-apply-idem-1".into()))
            .unwrap();
        assert_eq!(applied.files_applied, 1);
        assert_eq!(fs::read_to_string(repo.0.join("b.txt")).unwrap(), "new\n");

        // Re-applying with the same request_id succeeds idempotently without re-running git apply.
        let applied_again = client
            .apply_thread(forked_id.clone(), Some("ffi-apply-idem-1".into()))
            .unwrap();
        assert_eq!(applied_again, applied);

        let replay = client.replay_check().unwrap();
        assert!(replay.ok);
        assert_eq!(replay.threads, 3);
    }

    #[test]
    fn settings_keys_providers_and_refresh_local_models() {
        let data = TestDir::new("settings");
        let client = ArityClient::new(
            Some(data.0.to_string_lossy().into_owned()),
            Some("/usr/bin/true".into()),
        )
        .unwrap();

        assert!(!client.has_key("openai".into()));
        client.set_key("openai".into(), "sk-test-key".into()).unwrap();
        assert!(client.has_key("openai".into()));

        assert_eq!(client.codex_account(), None);

        // Start a tiny local HTTP server returning /v1/models
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            use std::io::Read;
            if let Ok((mut stream, _)) = listener.accept() {
                let mut req_buf = [0u8; 2048];
                let _ = stream.read(&mut req_buf);
                let body = json!({
                    "data": [{ "id": "local-model-live" }]
                })
                .to_string();
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(resp.as_bytes());
                let _ = stream.flush();
                let _ = stream.shutdown(std::net::Shutdown::Write);
            }
        });

        client
            .upsert_provider(
                "local-test".into(),
                format!("http://127.0.0.1:{port}/v1"),
                "chat_completions".into(),
                false,
                "local-test".into(),
                None,
                vec!["fallback-model".into()],
            )
            .unwrap();

        let models = client.refresh_local_models().unwrap();
        assert!(models.iter().any(|m| m.spec == "local-test:local-model-live" && m.local && m.ready));
        assert!(models.iter().any(|m| m.spec == "local-test:fallback-model"));
        assert!(models.iter().any(|m| m.spec == "openai:gpt-4.1" && m.ready && !m.local));
    }
}
