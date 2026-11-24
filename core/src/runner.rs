//! Single-writer turn loop: epoch-fenced execution, crash recovery, and two-phase tool commits.

use std::cell::{Cell, RefCell};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{Result, anyhow};
use serde_json::Value;

use crate::codec::{self, ModelClient, StreamControl};
use crate::store::Store;
use crate::tools;
use crate::types::{
    DeliveryMode, Entry, EntryKind, InterruptReason, ModelStreamOutcome, StoreError, ThreadRow,
    ThreadStatus, ToolCallItem, ToolReceiptStatus, now_ms,
};
use crate::workspace;

pub const DEFAULT_SYSTEM_PROMPT: &str = "\
You are Arity, a concise and careful software engineering assistant.
When a workspace folder is attached, you have access to workspace tools: \
list_files, read_file, search_files, write_file, edit_file, and bash.
Use relative paths inside the attached folder, inspect files before editing, \
keep changes focused, and verify your work before finishing.";

pub const LEASE_MS: i64 = 3_000;
pub const MAX_STEPS_PER_TURN: usize = 32;

const TOOL_ABORT_POLL_INTERVAL: Duration = Duration::from_millis(80);
const DELTA_HEARTBEAT_INTERVAL: Duration = Duration::from_millis(60);
const IDLE_HEARTBEAT_INTERVAL: Duration = Duration::from_millis(100);

pub struct Runner {
    data_dir: PathBuf,
    client: ModelClient,
}

impl Runner {
    pub fn new(data_dir: PathBuf) -> Self {
        Self {
            data_dir,
            client: ModelClient::new(),
        }
    }

    pub fn with_client(data_dir: PathBuf, client: ModelClient) -> Self {
        Self { data_dir, client }
    }

    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    pub fn run_turn_once(&self, thread_id: &str) -> Result<bool> {
        run_turn_once(&self.data_dir, thread_id, &self.client)
    }

    pub fn run_until_idle(&self, thread_id: &str) -> Result<usize> {
        run_worker_until_idle(&self.data_dir, thread_id, &self.client)
    }
}

/// Run turns for `thread_id` until no further work remains, returning the count of turns run.
pub fn run_worker_until_idle(
    data_dir: &Path,
    thread_id: &str,
    client: &ModelClient,
) -> Result<usize> {
    let mut turns = 0usize;
    loop {
        let store = Store::open(data_dir)?;
        if !store.thread_needs_work(thread_id)? {
            break;
        }
        let lease = store.read_lease(thread_id)?;
        drop(store);

        if run_turn_once(data_dir, thread_id, client)? {
            turns += 1;
            continue;
        }

        let live_other = lease
            .is_some_and(|l| l.lease_until_ms > now_ms() && Store::pid_is_alive(l.owner_pid));
        if live_other {
            std::thread::sleep(Duration::from_millis(50));
        } else {
            break;
        }
    }
    Ok(turns)
}

/// Claim and execute a single epoch-fenced turn on `thread_id`.
///
/// Returns `Ok(true)` if a turn was claimed and executed (or recovered), or `Ok(false)` if
/// no work was needed or another live runner already holds the lease.
pub fn run_turn_once(data_dir: &Path, thread_id: &str, client: &ModelClient) -> Result<bool> {
    let mut store = Store::open(data_dir)?;
    if !store.thread_needs_work(thread_id)? {
        return Ok(false);
    }

    let Some(claim) = store.try_claim_turn(thread_id, std::process::id() as i64, LEASE_MS)? else {
        return Ok(false);
    };
    let epoch = claim.epoch;

    // 1. Reap any stale process group left by a crashed previous runner.
    if let Some(pgid) = claim.stale_pgid {
        tools::reap_process_group(pgid);
        std::thread::sleep(Duration::from_millis(20));
    }

    // 2. Restore isolated worktree to the last committed tree_sha.
    let (worktree_root, work_dir) = match prepare_workspace(data_dir, thread_id, &claim.thread) {
        Ok(paths) => paths,
        Err(err) => {
            let _ = store.finish_turn(
                thread_id,
                epoch,
                ThreadStatus::Halted,
                "workspace_error",
                Some(&err.to_string()),
            );
            return Err(err);
        }
    };

    // 3. Dangling tool.call crash recovery: never rerun an unreceipted tool call.
    if let Some(head_id) = claim.thread.head_entry_id {
        let chain = store.causal_chain(head_id)?;
        if let Some(head) = chain.last()
            && head.kind == EntryKind::ToolCall
        {
            let call_id = head
                .body
                .get("call_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let name = head
                .body
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();

            store.append_tool_result(
                thread_id,
                epoch,
                &call_id,
                &name,
                ToolReceiptStatus::Unknown,
                "tool execution outcome unknown: process terminated before receipt; workspace restored to last committed tree_sha; tool was not rerun",
                None,
                claim.thread.last_tree_sha.as_deref(),
            )?;

            // Abort any remaining unstarted tool calls in the same ModelCompleted batch
            // so every declared tool call has a paired ToolResult for wire validity.
            let remaining = remaining_tool_calls(&chain);
            for call_k in remaining {
                store.append_tool_call(
                    thread_id,
                    epoch,
                    &call_k,
                    tools::is_mutating_tool(&call_k.name),
                )?;
                store.append_tool_result(
                    thread_id,
                    epoch,
                    &call_k.id,
                    &call_k.name,
                    ToolReceiptStatus::Aborted,
                    "aborted because earlier tool call in batch ended with unknown receipt after crash",
                    None,
                    claim.thread.last_tree_sha.as_deref(),
                )?;
            }

            let has_non_inbox = store
                .pending_deliveries(thread_id)?
                .iter()
                .any(|d| d.mode != DeliveryMode::Inbox);
            if !has_non_inbox {
                store.finish_turn(
                    thread_id,
                    epoch,
                    ThreadStatus::Halted,
                    "halted_unknown_tool",
                    None,
                )?;
                return Ok(true);
            }

            // A Steer or AfterTurn delivery was already queued; accept it right after the
            // unknown receipt so the model answers the user's message with the receipt in history.
            store.accept_deliveries(
                thread_id,
                epoch,
                &[
                    DeliveryMode::Inbox,
                    DeliveryMode::Steer,
                    DeliveryMode::AfterTurn,
                ],
            )?;
        }
    }

    // 4. Main bounded step loop.
    for _step in 0..MAX_STEPS_PER_TURN {
        let stop_req = store.heartbeat_lease(thread_id, epoch, LEASE_MS, None, None)?;
        let thread = require_thread(&store, thread_id)?;
        let chain = match thread.head_entry_id {
            Some(hid) => store.causal_chain(hid)?,
            None => Vec::new(),
        };
        let remaining = remaining_tool_calls(&chain);

        // Explicit stop: abort any unstarted tool calls cleanly and return to Idle.
        if stop_req == 1 {
            abort_remaining_calls(
                &mut store,
                thread_id,
                epoch,
                &remaining,
                "aborted due to stop",
                thread.last_tree_sha.as_deref(),
            )?;
            store.finish_turn(thread_id, epoch, ThreadStatus::Idle, "stopped", None)?;
            return Ok(true);
        }

        // Execute pending tool calls from the latest ModelCompleted batch.
        if !remaining.is_empty() {
            if stop_req == 2 {
                // Steer arrived while executing a multi-tool batch: abort remaining calls
                // so the wire transcript stays valid, then fall through to accept the Steer.
                abort_remaining_calls(
                    &mut store,
                    thread_id,
                    epoch,
                    &remaining,
                    "aborted due to steer",
                    thread.last_tree_sha.as_deref(),
                )?;
            } else {
                let call = &remaining[0];
                let mutating = tools::is_mutating_tool(&call.name);

                // Phase 1: commit tool.call BEFORE executing the tool.
                store.append_tool_call(thread_id, epoch, call, mutating)?;

                // Execute tool with pgid reporting and throttled abort/epoch heartbeat.
                let exec_out = {
                    let store_cell = RefCell::new(&mut store);
                    let last_check = Cell::new(Instant::now());
                    let stale = Cell::new(false);

                    let mut on_spawn_pgid = |pgid: i64| {
                        let mut s = store_cell.borrow_mut();
                        if matches!(
                            s.heartbeat_lease(thread_id, epoch, LEASE_MS, None, Some(Some(pgid))),
                            Err(StoreError::StaleEpoch { .. })
                        ) {
                            stale.set(true);
                        }
                    };

                    let mut should_abort = || {
                        if stale.get() {
                            return true;
                        }
                        if last_check.get().elapsed() < TOOL_ABORT_POLL_INTERVAL {
                            return false;
                        }
                        last_check.set(Instant::now());
                        let mut s = store_cell.borrow_mut();
                        match s.heartbeat_lease(thread_id, epoch, LEASE_MS, None, None) {
                            Ok(sr) => sr != 0,
                            Err(StoreError::StaleEpoch { .. }) => {
                                stale.set(true);
                                true
                            }
                            Err(_) => false,
                        }
                    };

                    tools::execute_tool(
                        work_dir.as_deref(),
                        &call.name,
                        &call.arguments,
                        &mut on_spawn_pgid,
                        &mut should_abort,
                    )
                };

                // Re-verify epoch is still live and clear active_pgid BEFORE touching git.
                store.heartbeat_lease(thread_id, epoch, LEASE_MS, None, Some(None))?;

                // Phase 2: snapshot worktree AFTER tool finishes & reaps children, then commit receipt.
                let new_sha = if mutating {
                    if let Some(ref wt_root) = worktree_root {
                        Some(workspace::snapshot_tree(wt_root)?)
                    } else {
                        None
                    }
                } else {
                    None
                };

                store.append_tool_result(
                    thread_id,
                    epoch,
                    &call.id,
                    &call.name,
                    exec_out.status,
                    &exec_out.output,
                    exec_out.exit_code,
                    new_sha.as_deref(),
                )?;
                continue;
            }
        }

        // No unexecuted tool calls remain. Accept pending deliveries as appropriate.
        let mut thread = require_thread(&store, thread_id)?;
        let mut head = load_head_entry(&store, &thread)?;
        let pending = store.pending_deliveries(thread_id)?;
        let has_steer = pending.iter().any(|d| d.mode == DeliveryMode::Steer);
        let has_after_turn = pending.iter().any(|d| d.mode == DeliveryMode::AfterTurn);

        if entry_needs_model(head.as_ref()) || has_steer {
            store.accept_deliveries(thread_id, epoch, &[DeliveryMode::Inbox, DeliveryMode::Steer])?;
            thread = require_thread(&store, thread_id)?;
            head = load_head_entry(&store, &thread)?;
        }

        if !entry_needs_model(head.as_ref()) {
            if has_after_turn {
                store.accept_deliveries(
                    thread_id,
                    epoch,
                    &[
                        DeliveryMode::Inbox,
                        DeliveryMode::Steer,
                        DeliveryMode::AfterTurn,
                    ],
                )?;
                thread = require_thread(&store, thread_id)?;
                head = load_head_entry(&store, &thread)?;
            }
            if !entry_needs_model(head.as_ref()) {
                store.finish_turn(thread_id, epoch, ThreadStatus::Idle, "completed", None)?;
                return Ok(true);
            }
        }

        // Model streaming step.
        let head_id = thread
            .head_entry_id
            .ok_or_else(|| anyhow!("thread '{thread_id}' has no head entry"))?;
        let providers = store.list_providers()?;
        let chain = store.causal_chain(head_id)?;
        let resolved = match codec::resolve_model(&thread.model, data_dir, &providers) {
            Ok(r) => r,
            Err(err) => {
                let _ = store.finish_turn(
                    thread_id,
                    epoch,
                    ThreadStatus::Halted,
                    "model_error",
                    Some(&err.to_string()),
                );
                return Err(err);
            }
        };

        let tool_specs = tools::tool_specs(work_dir.is_some());
        let mut observed_limits = Vec::new();
        let (stream_res, stale_err) = {
            let mut ctrl = RunnerStreamControl::new(&mut store, thread_id, epoch);
            let res = client.stream(
                &resolved,
                thread_id,
                &chain,
                &tool_specs,
                &mut ctrl,
                &mut observed_limits,
            );
            (res, ctrl.stale_err)
        };

        if let Some(err) = stale_err {
            return Err(err.into());
        }
        if !observed_limits.is_empty() {
            let _ = store.record_rate_limits(&observed_limits);
        }

        match stream_res {
            Ok(ModelStreamOutcome::Completed {
                text,
                tool_calls,
                usage,
                ttft_ms,
                duration_ms,
            }) => {
                store.append_model_completed(
                    thread_id,
                    epoch,
                    &resolved.spec,
                    &text,
                    &tool_calls,
                    usage,
                    ttft_ms,
                    duration_ms,
                )?;
            }
            Ok(ModelStreamOutcome::Interrupted { text, reason }) => {
                store.append_model_interrupted(
                    thread_id,
                    epoch,
                    &resolved.spec,
                    &text,
                    reason,
                )?;
                if reason == InterruptReason::Stop {
                    store.finish_turn(thread_id, epoch, ThreadStatus::Idle, "stopped", None)?;
                    return Ok(true);
                }
                // InterruptReason::Steer -> immediately loop to accept the Steer delivery.
            }
            Err(err) => {
                let _ = store.finish_turn(
                    thread_id,
                    epoch,
                    ThreadStatus::Halted,
                    "model_error",
                    Some(&err.to_string()),
                );
                return Err(err);
            }
        }
    }

    store.finish_turn(
        thread_id,
        epoch,
        ThreadStatus::Halted,
        "max_steps_exceeded",
        Some("maximum steps per turn reached"),
    )?;
    Ok(true)
}

fn prepare_workspace(
    data_dir: &Path,
    thread_id: &str,
    thread: &ThreadRow,
) -> Result<(Option<PathBuf>, Option<PathBuf>)> {
    match (
        thread.source_dir.as_deref().filter(|s| !s.is_empty()),
        thread.last_tree_sha.as_deref().filter(|s| !s.is_empty()),
    ) {
        (Some(src), Some(last_sha)) => {
            let src_path = Path::new(src);
            workspace::clean_stale_temp_indexes(src_path);
            let (_, wt_root, _) = workspace::worktree_paths(data_dir, thread_id, src_path)?;
            let wd = workspace::materialize_workspace(data_dir, thread_id, src_path, last_sha)?;
            Ok((Some(wt_root), Some(wd)))
        }
        _ => Ok((None, None)),
    }
}

fn require_thread(store: &Store, thread_id: &str) -> Result<ThreadRow> {
    store
        .get_thread(thread_id)?
        .ok_or_else(|| anyhow!("thread '{thread_id}' not found"))
}

fn load_head_entry(store: &Store, thread: &ThreadRow) -> Result<Option<Entry>> {
    match thread.head_entry_id {
        Some(id) => Ok(store.get_entry(id)?),
        None => Ok(None),
    }
}

fn entry_needs_model(head: Option<&Entry>) -> bool {
    matches!(
        head.map(|e| e.kind),
        Some(EntryKind::DeliveryAccepted | EntryKind::ToolResult)
    )
}

fn remaining_tool_calls(chain: &[Entry]) -> Vec<ToolCallItem> {
    let mut seen_call_ids = HashSet::new();
    let mut model_completed: Option<&Entry> = None;

    for entry in chain.iter().rev() {
        match entry.kind {
            EntryKind::ToolCall | EntryKind::ToolResult => {
                if let Some(cid) = entry.body.get("call_id").and_then(Value::as_str) {
                    seen_call_ids.insert(cid.to_string());
                }
            }
            EntryKind::ModelCompleted => {
                model_completed = Some(entry);
                break;
            }
            _ => break,
        }
    }

    let Some(mc) = model_completed else {
        return Vec::new();
    };
    let Some(arr) = mc.body.get("tool_calls").and_then(Value::as_array) else {
        return Vec::new();
    };

    let mut remaining = Vec::new();
    for item in arr {
        if let Ok(tc) = serde_json::from_value::<ToolCallItem>(item.clone())
            && !seen_call_ids.contains(&tc.id)
        {
            remaining.push(tc);
        }
    }
    remaining
}

fn abort_remaining_calls(
    store: &mut Store,
    thread_id: &str,
    epoch: i64,
    remaining: &[ToolCallItem],
    message: &str,
    tree_sha: Option<&str>,
) -> Result<()> {
    for call in remaining {
        store.append_tool_call(
            thread_id,
            epoch,
            call,
            tools::is_mutating_tool(&call.name),
        )?;
        store.append_tool_result(
            thread_id,
            epoch,
            &call.id,
            &call.name,
            ToolReceiptStatus::Aborted,
            message,
            None,
            tree_sha,
        )?;
    }
    Ok(())
}

struct RunnerStreamControl<'a> {
    store: &'a mut Store,
    thread_id: &'a str,
    epoch: i64,
    accumulated_text: String,
    last_beat: Instant,
    stale_err: Option<StoreError>,
}

impl<'a> RunnerStreamControl<'a> {
    fn new(store: &'a mut Store, thread_id: &'a str, epoch: i64) -> Self {
        Self {
            store,
            thread_id,
            epoch,
            accumulated_text: String::new(),
            last_beat: Instant::now() - DELTA_HEARTBEAT_INTERVAL,
            stale_err: None,
        }
    }

    fn heartbeat(&mut self, min_interval: Duration, include_text: bool) -> Option<InterruptReason> {
        if self.stale_err.is_some() {
            return Some(InterruptReason::Stop);
        }
        if self.last_beat.elapsed() < min_interval {
            return None;
        }
        self.last_beat = Instant::now();
        let partial = if include_text && !self.accumulated_text.is_empty() {
            Some(self.accumulated_text.as_str())
        } else {
            None
        };
        match self
            .store
            .heartbeat_lease(self.thread_id, self.epoch, LEASE_MS, partial, None)
        {
            Ok(1) => Some(InterruptReason::Stop),
            Ok(2) => Some(InterruptReason::Steer),
            Ok(_) => None,
            Err(e @ StoreError::StaleEpoch { .. }) => {
                self.stale_err = Some(e);
                Some(InterruptReason::Stop)
            }
            Err(_) => None,
        }
    }
}

impl StreamControl for RunnerStreamControl<'_> {
    fn on_text_delta(&mut self, delta: &str) -> Option<InterruptReason> {
        self.accumulated_text.push_str(delta);
        self.heartbeat(DELTA_HEARTBEAT_INTERVAL, true)
    }

    fn poll_interrupt(&mut self) -> Option<InterruptReason> {
        self.heartbeat(IDLE_HEARTBEAT_INTERVAL, true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::fs;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::process::Command;
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex};

    use serde_json::json;

    use crate::types::{ProviderConfig, WireCodec};

    struct TestDir(PathBuf);

    impl TestDir {
        fn new(label: &str) -> Self {
            static SEQ: AtomicU64 = AtomicU64::new(0);
            let n = SEQ.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "arity-runner-{label}-{}-{n}",
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

    fn git_run(dir: &Path, args: &[&str]) {
        let out = Command::new("git")
            .env_remove("GIT_DIR")
            .env_remove("GIT_WORK_TREE")
            .env_remove("GIT_INDEX_FILE")
            .arg("-C")
            .arg(dir)
            .args(args)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "git {:?} failed: {}",
            args,
            String::from_utf8_lossy(&out.stderr)
        );
    }

    fn make_git_repo(label: &str) -> TestDir {
        let dir = TestDir::new(label);
        git_run(&dir.0, &["init"]);
        git_run(&dir.0, &["config", "user.email", "arity@example.invalid"]);
        git_run(&dir.0, &["config", "user.name", "Arity Runner Test"]);
        fs::write(dir.0.join("README.md"), "base readme\n").unwrap();
        git_run(&dir.0, &["add", "."]);
        git_run(&dir.0, &["commit", "-m", "init"]);
        dir
    }

    #[derive(Debug, Clone)]
    struct MockRequest {
        request_line: String,
        body: Value,
    }

    fn read_http_request(stream: &mut TcpStream) -> Option<MockRequest> {
        let mut buf = Vec::new();
        let mut chunk = [0u8; 8192];
        loop {
            let n = stream.read(&mut chunk).ok()?;
            if n == 0 {
                return None;
            }
            buf.extend_from_slice(&chunk[..n]);
            let raw = String::from_utf8_lossy(&buf);
            let Some(head_end) = raw.find("\r\n\r\n") else {
                continue;
            };
            let head = &raw[..head_end];
            let mut lines = head.lines();
            let request_line = lines.next().unwrap_or_default().to_string();
            let mut headers = HashMap::new();
            for line in lines {
                if let Some((k, v)) = line.split_once(':') {
                    headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
                }
            }
            let content_len = headers
                .get("content-length")
                .and_then(|v| v.parse::<usize>().ok())
                .unwrap_or(0);
            if buf.len() >= head_end + 4 + content_len {
                let body =
                    serde_json::from_slice(&buf[head_end + 4..head_end + 4 + content_len])
                        .unwrap_or(Value::Null);
                return Some(MockRequest { request_line, body });
            }
        }
    }

    fn spawn_stream_server(
        handler: impl Fn(usize, MockRequest, &mut TcpStream) + Send + Sync + 'static,
    ) -> (String, Arc<Mutex<Vec<MockRequest>>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let captured = Arc::new(Mutex::new(Vec::new()));
        let captured_bg = Arc::clone(&captured);
        let req_idx = Arc::new(AtomicUsize::new(0));
        let handler = Arc::new(handler);

        std::thread::spawn(move || {
            for mut stream in listener.incoming().flatten() {
                let captured_c = Arc::clone(&captured_bg);
                let req_idx_c = Arc::clone(&req_idx);
                let handler_c = Arc::clone(&handler);
                std::thread::spawn(move || {
                    let Some(req) = read_http_request(&mut stream) else {
                        return;
                    };
                    let idx = req_idx_c.fetch_add(1, Ordering::SeqCst);
                    captured_c.lock().unwrap().push(req.clone());
                    handler_c(idx, req, &mut stream);
                });
            }
        });

        (format!("http://127.0.0.1:{port}/v1"), captured)
    }

    fn write_sse_response(stream: &mut TcpStream, body: &str) {
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let _ = stream.write_all(resp.as_bytes());
        let _ = stream.flush();
    }

    fn chat_tool_call_sse(call_id: &str, name: &str, args: &Value) -> String {
        format!(
            "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
            json!({
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": call_id,
                            "function": {
                                "name": name,
                                "arguments": args.to_string(),
                            }
                        }]
                    },
                    "finish_reason": null
                }]
            }),
            json!({
                "choices": [{ "delta": {}, "finish_reason": "tool_calls" }]
            }),
        )
    }

    fn chat_text_sse(text: &str) -> String {
        format!(
            "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
            json!({
                "choices": [{ "delta": { "content": text }, "finish_reason": null }]
            }),
            json!({
                "choices": [{ "delta": {}, "finish_reason": "stop" }],
                "usage": { "prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25 }
            }),
        )
    }

    fn register_provider(store: &mut Store, name: &str, url: String, wire: WireCodec) {
        store
            .upsert_provider(&ProviderConfig {
                name: name.into(),
                url,
                wire,
                needs_key: false,
                key_name: String::new(),
                session_header: None,
                models: vec![],
            })
            .unwrap();
    }

    #[test]
    fn full_tool_calling_loop_commits_two_phase_tree_shas_and_replays_identically() {
        let repo = make_git_repo("tool-loop-repo");
        let data = TestDir::new("tool-loop-data");

        let (url, captured) = spawn_stream_server(|idx, _req, stream| match idx {
            0 => write_sse_response(
                stream,
                &chat_tool_call_sse(
                    "call_w1",
                    "write_file",
                    &json!({ "path": "hello.txt", "content": "hello world\n" }),
                ),
            ),
            1 => write_sse_response(
                stream,
                &chat_tool_call_sse(
                    "call_e1",
                    "edit_file",
                    &json!({ "path": "hello.txt", "old_text": "world", "new_text": "arity" }),
                ),
            ),
            2 => write_sse_response(
                stream,
                &chat_tool_call_sse(
                    "call_b1",
                    "bash",
                    &json!({ "command": "echo 'from bash' >> hello.txt && cat hello.txt" }),
                ),
            ),
            _ => write_sse_response(stream, &chat_text_sse("All edits applied and verified.")),
        });

        let mut store = Store::open(&data.0).unwrap();
        register_provider(&mut store, "mock-chat", url, WireCodec::ChatCompletions);

        let init = workspace::init_workspace(&data.0, "t-loop", &repo.0).unwrap();
        store
            .create_thread(
                "t-loop",
                "mock-chat:m1",
                Some(&init.source_dir),
                Some(&init.base_commit),
                Some(&init.base_tree_sha),
                DEFAULT_SYSTEM_PROMPT,
                "req-create",
            )
            .unwrap();
        store
            .enqueue_delivery("t-loop", DeliveryMode::Steer, "make hello.txt", None, "req-msg")
            .unwrap();
        drop(store);

        let client = ModelClient::new();
        assert!(run_turn_once(&data.0, "t-loop", &client).unwrap());
        assert_eq!(captured.lock().unwrap().len(), 4);

        let mut store = Store::open(&data.0).unwrap();
        let thread = store.get_thread("t-loop").unwrap().unwrap();
        assert_eq!(thread.status, ThreadStatus::Idle);
        assert!(store.read_lease("t-loop").unwrap().is_none());
        assert_ne!(thread.last_tree_sha.as_deref(), Some(init.base_tree_sha.as_str()));

        // Verify tool.call precedes tool.result and every mutating tool updates tree_sha.
        let entries = store.thread_entries("t-loop", 0).unwrap();
        let tool_pairs: Vec<(i64, String, Option<String>)> = entries
            .iter()
            .filter(|e| matches!(e.kind, EntryKind::ToolCall | EntryKind::ToolResult))
            .map(|e| (e.id, e.kind.as_str().to_string(), e.tree_sha.clone()))
            .collect();
        assert_eq!(tool_pairs.len(), 6);
        assert_eq!(tool_pairs[0].1, "tool.call");
        assert_eq!(tool_pairs[1].1, "tool.result");
        assert_eq!(tool_pairs[2].1, "tool.call");
        assert_eq!(tool_pairs[3].1, "tool.result");
        assert_eq!(tool_pairs[4].1, "tool.call");
        assert_eq!(tool_pairs[5].1, "tool.result");

        let sha1 = tool_pairs[1].2.as_deref().unwrap();
        let sha2 = tool_pairs[3].2.as_deref().unwrap();
        let sha3 = tool_pairs[5].2.as_deref().unwrap();
        assert_ne!(sha1, init.base_tree_sha);
        assert_ne!(sha2, sha1);
        assert_ne!(sha3, sha2);
        assert_eq!(thread.last_tree_sha.as_deref(), Some(sha3));

        // Verify final file contents in worktree.
        assert_eq!(
            fs::read_to_string(init.worktree_root.join("hello.txt")).unwrap(),
            "hello arity\nfrom bash\n"
        );

        // Verify rebuild_projections matches 100%.
        let threads_before = store.list_threads().unwrap();
        let deliveries_before = store.list_deliveries().unwrap();
        store.rebuild_projections().unwrap();
        assert_eq!(threads_before, store.list_threads().unwrap());
        assert_eq!(deliveries_before, store.list_deliveries().unwrap());
    }

    #[test]
    fn truncated_stream_halts_without_model_completed_or_tool_execution() {
        let repo = make_git_repo("trunc-repo");
        let data = TestDir::new("trunc-data");

        // Server streams a write_file tool call delta then disconnects WITHOUT data: [DONE].
        let (url, _) = spawn_stream_server(|_, _, stream| {
            let truncated_sse = format!(
                "data: {}\n\n",
                json!({
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_trunc",
                                "function": {
                                    "name": "write_file",
                                    "arguments": "{\"path\":\"should_not_exist.txt\",\"content\":\"nope\"}"
                                }
                            }]
                        },
                        "finish_reason": "tool_calls"
                    }]
                })
            );
            write_sse_response(stream, &truncated_sse);
        });

        let mut store = Store::open(&data.0).unwrap();
        register_provider(&mut store, "mock-chat", url, WireCodec::ChatCompletions);
        let init = workspace::init_workspace(&data.0, "t-trunc", &repo.0).unwrap();
        store
            .create_thread(
                "t-trunc",
                "mock-chat:m1",
                Some(&init.source_dir),
                Some(&init.base_commit),
                Some(&init.base_tree_sha),
                DEFAULT_SYSTEM_PROMPT,
                "req-create",
            )
            .unwrap();
        store
            .enqueue_delivery("t-trunc", DeliveryMode::Steer, "write file", None, "req-msg")
            .unwrap();
        drop(store);

        let client = ModelClient::new();
        let err = run_turn_once(&data.0, "t-trunc", &client).unwrap_err();
        assert!(err.to_string().contains("[DONE]"), "{err}");

        let store = Store::open(&data.0).unwrap();
        let thread = store.get_thread("t-trunc").unwrap().unwrap();
        assert_eq!(thread.status, ThreadStatus::Halted);
        assert!(store.read_lease("t-trunc").unwrap().is_none());

        let entries = store.thread_entries("t-trunc", 0).unwrap();
        assert!(entries.iter().all(|e| e.kind != EntryKind::ModelCompleted));
        assert!(entries.iter().all(|e| e.kind != EntryKind::ToolCall));
        assert!(entries.iter().all(|e| e.kind != EntryKind::ToolResult));
        assert!(!init.worktree_root.join("should_not_exist.txt").exists());
    }

    #[test]
    fn crash_recovery_restores_worktree_writes_unknown_receipt_does_not_rerun_and_halts() {
        let repo = make_git_repo("crash-repo");
        let data = TestDir::new("crash-data");

        // Model server should NEVER be called during crash recovery halt.
        let (url, captured) = spawn_stream_server(|_, _, stream| {
            write_sse_response(stream, &chat_text_sse("unexpected model call"));
        });

        let mut store = Store::open(&data.0).unwrap();
        register_provider(&mut store, "mock-chat", url, WireCodec::ChatCompletions);
        let init = workspace::init_workspace(&data.0, "t-crash", &repo.0).unwrap();
        store
            .create_thread(
                "t-crash",
                "mock-chat:m1",
                Some(&init.source_dir),
                Some(&init.base_commit),
                Some(&init.base_tree_sha),
                DEFAULT_SYSTEM_PROMPT,
                "req-create",
            )
            .unwrap();
        store
            .enqueue_delivery("t-crash", DeliveryMode::Steer, "run bash", None, "req-msg")
            .unwrap();

        // Simulate epoch-1 runner claiming with lease_ms = 0, committing Phase 1 tool.call for bash,
        // leaving dirty/untracked files on disk, and crashing before Phase 2 tool.result.
        let claim1 = store
            .try_claim_turn("t-crash", std::process::id() as i64, 0)
            .unwrap()
            .unwrap();
        store
            .accept_deliveries("t-crash", claim1.epoch, &[DeliveryMode::Steer])
            .unwrap();
        let tc_bash = ToolCallItem {
            id: "call_bash_crash".into(),
            name: "bash".into(),
            arguments: json!({ "command": "echo RERUN > rerun_marker.txt" }).to_string(),
        };
        let tc_read = ToolCallItem {
            id: "call_read_after".into(),
            name: "read_file".into(),
            arguments: json!({ "path": "README.md" }).to_string(),
        };
        store
            .append_model_completed(
                "t-crash",
                claim1.epoch,
                "mock-chat:m1",
                "",
                &[tc_bash.clone(), tc_read],
                None,
                None,
                10,
            )
            .unwrap();
        store
            .append_tool_call("t-crash", claim1.epoch, &tc_bash, true)
            .unwrap();

        // Dirty the worktree as if killed mid-bash.
        fs::write(init.worktree_root.join("README.md"), "CORRUPTED BY CRASH\n").unwrap();
        fs::write(init.worktree_root.join("stray_untracked.tmp"), "junk").unwrap();
        fs::write(init.worktree_root.join("rerun_marker.txt"), "half written").unwrap();
        drop(store);

        // Epoch-2 runner recovers cleanly.
        let client = ModelClient::new();
        assert!(run_turn_once(&data.0, "t-crash", &client).unwrap());

        // Model was never called; bash was never rerun; worktree restored to base_tree_sha.
        assert_eq!(captured.lock().unwrap().len(), 0);
        assert_eq!(
            fs::read_to_string(init.worktree_root.join("README.md")).unwrap(),
            "base readme\n"
        );
        assert!(!init.worktree_root.join("stray_untracked.tmp").exists());
        assert!(!init.worktree_root.join("rerun_marker.txt").exists());
        assert_eq!(
            workspace::snapshot_tree(&init.worktree_root).unwrap(),
            init.base_tree_sha
        );

        let store = Store::open(&data.0).unwrap();
        let thread = store.get_thread("t-crash").unwrap().unwrap();
        assert_eq!(thread.epoch, 2);
        assert_eq!(thread.status, ThreadStatus::Halted);
        assert!(store.read_lease("t-crash").unwrap().is_none());

        let entries = store.thread_entries("t-crash", 0).unwrap();
        let tool_results: Vec<&Entry> = entries
            .iter()
            .filter(|e| e.kind == EntryKind::ToolResult)
            .collect();
        assert_eq!(tool_results.len(), 2);
        assert_eq!(tool_results[0].body["call_id"], "call_bash_crash");
        assert_eq!(tool_results[0].body["status"], "unknown");
        assert_eq!(
            tool_results[0].tree_sha.as_deref(),
            Some(init.base_tree_sha.as_str())
        );
        assert_eq!(tool_results[1].body["call_id"], "call_read_after");
        assert_eq!(tool_results[1].body["status"], "aborted");

        let finished = entries
            .iter()
            .rev()
            .find(|e| e.kind == EntryKind::TurnFinished)
            .unwrap();
        assert_eq!(finished.body["reason"], "halted_unknown_tool");
    }

    #[test]
    fn mid_stream_steer_interrupts_and_deduplicates_duplicate_request_id() {
        let data = TestDir::new("steer-data");
        let data_for_server = data.0.clone();

        let (url, captured) = spawn_stream_server(move |idx, req, stream| {
            if idx == 0 {
                // Stream first chunk, then enqueue the SAME Steer twice with identical request_id.
                let header = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n";
                let _ = stream.write_all(header.as_bytes());
                let chunk1 = format!(
                    "data: {}\n\n",
                    json!({ "choices": [{ "delta": { "content": "Starting initial answer... " }, "finish_reason": null }] })
                );
                let _ = stream.write_all(chunk1.as_bytes());
                let _ = stream.flush();

                // Enqueue Steer twice with the same request_id while streaming is in progress.
                std::thread::sleep(Duration::from_millis(80));
                let mut s = Store::open(&data_for_server).unwrap();
                let d1 = s
                    .enqueue_delivery(
                        "t-steer",
                        DeliveryMode::Steer,
                        "Actually pivot to Rust.",
                        None,
                        "req-steer-dup",
                    )
                    .unwrap();
                let d2 = s
                    .enqueue_delivery(
                        "t-steer",
                        DeliveryMode::Steer,
                        "Actually pivot to Rust.",
                        None,
                        "req-steer-dup",
                    )
                    .unwrap();
                assert_eq!(d1.id, d2.id);

                // Keep stream open slowly so RunnerStreamControl detects stop_requested == 2.
                for i in 0..10 {
                    std::thread::sleep(Duration::from_millis(60));
                    let chunk = format!(
                        "data: {}\n\n",
                        json!({ "choices": [{ "delta": { "content": format!("more {i} ") }, "finish_reason": null }] })
                    );
                    if stream.write_all(chunk.as_bytes()).is_err() {
                        break;
                    }
                    let _ = stream.flush();
                }
            } else {
                // Second request must carry the interrupted partial text and steered user message once.
                let msgs = req.body["messages"].as_array().unwrap();
                let steer_count = msgs
                    .iter()
                    .filter(|m| {
                        m["role"] == "user"
                            && m["content"]
                                .as_str()
                                .is_some_and(|c| c.contains("Actually pivot to Rust."))
                    })
                    .count();
                assert_eq!(steer_count, 1, "steer message must appear exactly once: {msgs:?}");
                write_sse_response(stream, &chat_text_sse("Pivoted to Rust cleanly."));
            }
        });

        let mut store = Store::open(&data.0).unwrap();
        register_provider(&mut store, "mock-chat", url, WireCodec::ChatCompletions);
        store
            .create_thread(
                "t-steer",
                "mock-chat:m1",
                None,
                None,
                None,
                DEFAULT_SYSTEM_PROMPT,
                "req-create",
            )
            .unwrap();
        store
            .enqueue_delivery("t-steer", DeliveryMode::Steer, "Tell me about Go.", None, "req-1")
            .unwrap();
        drop(store);

        let client = ModelClient::new();
        assert!(run_turn_once(&data.0, "t-steer", &client).unwrap());
        assert_eq!(captured.lock().unwrap().len(), 2);

        let store = Store::open(&data.0).unwrap();
        let thread = store.get_thread("t-steer").unwrap().unwrap();
        assert_eq!(thread.status, ThreadStatus::Idle);
        assert_eq!(thread.stop_requested, 0);

        let deliveries = store.list_deliveries().unwrap();
        assert_eq!(deliveries.len(), 2);

        let entries = store.thread_entries("t-steer", 0).unwrap();
        let interrupted = entries
            .iter()
            .find(|e| e.kind == EntryKind::ModelInterrupted)
            .expect("expected ModelInterrupted");
        assert_eq!(interrupted.body["reason"], "steer");
        assert!(
            interrupted.body["text"]
                .as_str()
                .unwrap()
                .contains("Starting initial answer...")
        );

        let completed = entries
            .iter()
            .find(|e| e.kind == EntryKind::ModelCompleted)
            .expect("expected ModelCompleted");
        assert_eq!(completed.body["text"], "Pivoted to Rust cleanly.");
    }

    #[test]
    fn cross_codec_step3_fork_diff_and_apply() {
        let repo = make_git_repo("fork-repo");
        let data = TestDir::new("fork-data");

        // Mock Chat Completions server for Thread A (3 tool steps + final answer).
        let (chat_url, _) = spawn_stream_server(|idx, _req, stream| match idx {
            0 => write_sse_response(
                stream,
                &chat_tool_call_sse(
                    "call_step1",
                    "write_file",
                    &json!({ "path": "notes.txt", "content": "step1\n" }),
                ),
            ),
            1 => write_sse_response(
                stream,
                &chat_tool_call_sse(
                    "call_step2",
                    "edit_file",
                    &json!({ "path": "notes.txt", "old_text": "step1", "new_text": "step1\nstep2" }),
                ),
            ),
            2 => write_sse_response(
                stream,
                &chat_tool_call_sse(
                    "call_step3",
                    "bash",
                    &json!({ "command": "echo step3 >> notes.txt" }),
                ),
            ),
            _ => write_sse_response(stream, &chat_text_sse("Thread A finished 3 steps.")),
        });

        // Mock Responses server for forked Thread B.
        let (resp_url, resp_captured) = spawn_stream_server(|idx, req, stream| {
            assert!(req.request_line.starts_with("POST /v1/responses "));
            if idx == 0 {
                // Verify Thread A's Chat Completions tool history was normalized into Responses input.
                let input = req.body["input"].as_array().unwrap();
                let fn_calls: Vec<&Value> = input
                    .iter()
                    .filter(|i| i["type"] == "function_call")
                    .collect();
                let fn_outputs: Vec<&Value> = input
                    .iter()
                    .filter(|i| i["type"] == "function_call_output")
                    .collect();
                assert_eq!(fn_calls.len(), 3, "expected 3 inherited function_calls: {input:?}");
                assert_eq!(fn_outputs.len(), 3, "expected 3 inherited function_call_outputs: {input:?}");

                let sse = format!(
                    "data: {}\n\ndata: {}\n\n",
                    json!({
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "call_id": "fc_resp_write",
                            "name": "write_file",
                            "arguments": json!({ "path": "from_responses.txt", "content": "forked edit\n" }).to_string(),
                        }
                    }),
                    json!({
                        "type": "response.completed",
                        "response": { "status": "completed" }
                    }),
                );
                write_sse_response(stream, &sse);
            } else {
                let sse = format!(
                    "data: {}\n\ndata: {}\n\ndata: {}\n\n",
                    json!({ "type": "response.output_text.delta", "delta": "Fork completed on Responses!" }),
                    json!({
                        "type": "response.output_item.done",
                        "item": {
                            "type": "message",
                            "content": [{ "type": "output_text", "text": "Fork completed on Responses!" }]
                        }
                    }),
                    json!({
                        "type": "response.completed",
                        "response": {
                            "status": "completed",
                            "usage": { "input_tokens": 50, "output_tokens": 8, "total_tokens": 58 }
                        }
                    }),
                );
                write_sse_response(stream, &sse);
            }
        });

        let mut store = Store::open(&data.0).unwrap();
        register_provider(&mut store, "mock-chat", chat_url, WireCodec::ChatCompletions);
        register_provider(&mut store, "mock-resp", resp_url, WireCodec::Responses);

        let init = workspace::init_workspace(&data.0, "thread-a", &repo.0).unwrap();
        store
            .create_thread(
                "thread-a",
                "mock-chat:m1",
                Some(&init.source_dir),
                Some(&init.base_commit),
                Some(&init.base_tree_sha),
                DEFAULT_SYSTEM_PROMPT,
                "req-create-a",
            )
            .unwrap();
        store
            .enqueue_delivery("thread-a", DeliveryMode::Steer, "do 3 steps", None, "req-msg-a")
            .unwrap();
        drop(store);

        let client = ModelClient::new();
        assert!(run_turn_once(&data.0, "thread-a", &client).unwrap());

        // Find step 3's ToolResult entry on Thread A.
        let mut store = Store::open(&data.0).unwrap();
        let entries_a = store.thread_entries("thread-a", 0).unwrap();
        let step3_result = entries_a
            .iter()
            .filter(|e| e.kind == EntryKind::ToolResult)
            .nth(2)
            .expect("expected 3rd tool result");
        let step3_entry_id = step3_result.id;
        let step3_sha = step3_result.tree_sha.clone().unwrap();

        // Fork Thread B at step 3 onto Responses provider (`mock-resp:m2`).
        let fork_entry = store
            .fork_thread("thread-b", step3_entry_id, Some("mock-resp:m2"), "req-fork-b")
            .unwrap();
        assert_eq!(fork_entry.tree_sha.as_deref(), Some(step3_sha.as_str()));
        store
            .enqueue_delivery(
                "thread-b",
                DeliveryMode::Steer,
                "add from_responses.txt",
                None,
                "req-msg-b",
            )
            .unwrap();
        drop(store);

        // Run Thread B; runner materializes exact step3_sha and runs Responses codec.
        assert!(run_turn_once(&data.0, "thread-b", &client).unwrap());
        assert_eq!(resp_captured.lock().unwrap().len(), 2);

        let store = Store::open(&data.0).unwrap();
        let thread_b = store.get_thread("thread-b").unwrap().unwrap();
        assert_eq!(thread_b.status, ThreadStatus::Idle);
        let b_base_sha = thread_b.base_tree_sha.as_deref().unwrap();
        let b_last_sha = thread_b.last_tree_sha.as_deref().unwrap();

        // Thread A worktree is isolated from Thread B's edits.
        assert!(!init.worktree_root.join("from_responses.txt").exists());
        assert_eq!(
            fs::read_to_string(init.worktree_root.join("notes.txt")).unwrap(),
            "step1\nstep2\nstep3\n"
        );

        // Diff Thread B against base_tree_sha.
        let diff = workspace::diff_trees(&repo.0, b_base_sha, b_last_sha).unwrap();
        let paths: Vec<&str> = diff.files.iter().map(|f| f.path.as_str()).collect();
        assert!(paths.contains(&"notes.txt"), "{paths:?}");
        assert!(paths.contains(&"from_responses.txt"), "{paths:?}");

        // Apply Thread B onto source repo.
        let applied = workspace::apply_tree(&repo.0, b_base_sha, b_last_sha).unwrap();
        assert_eq!(applied.files_applied, 2);
        assert_eq!(
            fs::read_to_string(repo.0.join("notes.txt")).unwrap(),
            "step1\nstep2\nstep3\n"
        );
        assert_eq!(
            fs::read_to_string(repo.0.join("from_responses.txt")).unwrap(),
            "forked edit\n"
        );
    }
}
