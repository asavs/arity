//! End-to-end Arity v0.0.1 exit-bar tests using real `arity` CLI subprocesses and real `SIGKILL`.

use std::collections::HashMap;
use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::time::{Duration, Instant};

use arity_core::store::Store;
use arity_core::workspace;
use serde_json::{Value, json};

const ARITY_BIN: &str = env!("CARGO_BIN_EXE_arity");

struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(label: &str) -> Self {
        static SEQ: AtomicU64 = AtomicU64::new(0);
        let n = SEQ.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "arity-exit-bar-{label}-{}-{n}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).expect("create temp dir");
        Self { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

/// Ensures a spawned child process is killed and reaped even if an assertion panics.
struct ChildGuard {
    child: Option<Child>,
}

impl ChildGuard {
    fn new(child: Child) -> Self {
        Self { child: Some(child) }
    }

    fn sigkill_and_wait(&mut self) -> std::process::ExitStatus {
        let mut child = self.child.take().expect("child already waited");
        let pid = child.id() as libc::pid_t;
        let rc = unsafe { libc::kill(pid, libc::SIGKILL) };
        assert_eq!(
            rc,
            0,
            "SIGKILL pid {pid} failed: {}",
            std::io::Error::last_os_error()
        );
        child.wait().expect("wait for SIGKILLed worker")
    }

    fn wait(&mut self) -> std::process::ExitStatus {
        let mut child = self.child.take().expect("child already waited");
        child.wait().expect("wait for worker")
    }
}

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = unsafe { libc::kill(child.id() as libc::pid_t, libc::SIGKILL) };
            let _ = child.wait();
        }
    }
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
            let body = serde_json::from_slice(&buf[head_end + 4..head_end + 4 + content_len])
                .unwrap_or(Value::Null);
            return Some(MockRequest { request_line, body });
        }
    }
}

fn spawn_mock_server(
    handler: impl Fn(usize, MockRequest, &mut TcpStream) + Send + Sync + 'static,
) -> (String, Arc<Mutex<Vec<MockRequest>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind mock server");
    let port = listener.local_addr().expect("local_addr").port();
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

fn chat_text_sse(text: &str) -> String {
    format!(
        "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
        json!({
            "choices": [{
                "delta": { "content": text },
                "finish_reason": null
            }]
        }),
        json!({
            "choices": [{ "delta": {}, "finish_reason": "stop" }],
            "usage": { "prompt_tokens": 16, "completion_tokens": 4, "total_tokens": 20 }
        }),
    )
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

fn arity_cmd(data_dir: &Path, args: &[&str]) -> Output {
    Command::new(ARITY_BIN)
        .arg("--data-dir")
        .arg(data_dir)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .unwrap_or_else(|e| panic!("failed to run arity {:?}: {e}", args))
}

fn arity_ok(data_dir: &Path, args: &[&str]) -> String {
    let out = arity_cmd(data_dir, args);
    assert!(
        out.status.success(),
        "arity {:?} failed (status={:?})\nstdout:\n{}\nstderr:\n{}",
        args,
        out.status,
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr),
    );
    String::from_utf8(out.stdout).expect("arity stdout is UTF-8")
}

fn arity_json(data_dir: &Path, args: &[&str]) -> Value {
    let stdout = arity_ok(data_dir, args);
    serde_json::from_str(&stdout)
        .unwrap_or_else(|e| panic!("arity {:?} returned invalid JSON: {e}\n{stdout}", args))
}

fn spawn_worker(data_dir: &Path, thread_id: &str) -> ChildGuard {
    let child = Command::new(ARITY_BIN)
        .arg("--data-dir")
        .arg(data_dir)
        .args(["worker", "--thread", thread_id, "--once"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap_or_else(|e| panic!("failed to spawn worker for {thread_id}: {e}"));
    ChildGuard::new(child)
}

fn git_run(dir: &Path, args: &[&str]) {
    let out = Command::new("git")
        .env_remove("GIT_DIR")
        .env_remove("GIT_WORK_TREE")
        .env_remove("GIT_INDEX_FILE")
        .env("GIT_TERMINAL_PROMPT", "0")
        .arg("-C")
        .arg(dir)
        .args(args)
        .output()
        .unwrap_or_else(|e| panic!("failed to run git {:?}: {e}", args));
    assert!(
        out.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&out.stderr)
    );
}

fn init_git_repo(dir: &Path, initial_files: &[(&str, &str)]) {
    fs::create_dir_all(dir).expect("create repo dir");
    git_run(dir, &["init"]);
    git_run(dir, &["config", "user.email", "arity-test@example.invalid"]);
    git_run(dir, &["config", "user.name", "Arity Exit Bar Test"]);
    for (rel, content) in initial_files {
        let full = dir.join(rel);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent).expect("create parent");
        }
        fs::write(&full, content).expect("write initial file");
    }
    git_run(dir, &["add", "."]);
    git_run(dir, &["commit", "-m", "initial commit"]);
}

fn wait_until(timeout: Duration, poll_interval: Duration, mut pred: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if pred() {
            return true;
        }
        std::thread::sleep(poll_interval);
    }
    pred()
}

#[test]
fn sigkill_mid_stream_and_duplicate_steer_idempotency() {
    let tmp = TempDir::new("sigkill-stream");
    let data_dir = tmp.path().join("data");

    let (mid_tx, mid_rx) = mpsc::channel::<()>();
    let mid_tx = Arc::new(Mutex::new(Some(mid_tx)));

    let (chat_url, captured) = spawn_mock_server(move |idx, _req, stream| {
        if idx == 0 {
            let header = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n";
            let _ = stream.write_all(header.as_bytes());

            // Flush partial text delta and partial tool call chunk WITHOUT `data: [DONE]`.
            let text_chunk = format!(
                "data: {}\n\n",
                json!({
                    "choices": [{
                        "delta": { "content": "Thinking about changing " },
                        "finish_reason": null
                    }]
                })
            );
            let partial_tool_chunk = format!(
                "data: {}\n\n",
                json!({
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_half_baked",
                                "function": {
                                    "name": "write_file",
                                    "arguments": "{\"path\":\"never.txt\",\"content\":"
                                }
                            }]
                        },
                        "finish_reason": null
                    }]
                })
            );
            let _ = stream.write_all(text_chunk.as_bytes());
            let _ = stream.write_all(partial_tool_chunk.as_bytes());
            let _ = stream.flush();

            if let Some(tx) = mid_tx.lock().unwrap().take() {
                let _ = tx.send(());
            }

            // Hold socket open for several seconds (exits early once client is SIGKILLed).
            let hold_until = Instant::now() + Duration::from_secs(8);
            while Instant::now() < hold_until {
                if stream.write_all(b": keepalive\n\n").is_err() {
                    break;
                }
                let _ = stream.flush();
                std::thread::sleep(Duration::from_millis(100));
            }
        } else {
            write_sse_response(stream, &chat_text_sse("DONE"));
        }
    });

    // Register provider, create thread, and enqueue initial message without spawning worker.
    arity_ok(
        &data_dir,
        &["provider", "set", "mock-chat", &chat_url, "--wire", "chat_completions"],
    );
    arity_ok(&data_dir, &["create", "t-stream", "--model", "mock-chat:m1"]);
    arity_ok(
        &data_dir,
        &[
            "chat",
            "t-stream",
            "--no-spawn",
            "--no-wait",
            "Initial prompt",
        ],
    );

    // Spawn real `arity worker --thread t-stream --once` subprocess.
    let mut worker1 = spawn_worker(&data_dir, "t-stream");

    // Wait until server confirms mid-stream bytes were flushed.
    mid_rx
        .recv_timeout(Duration::from_secs(10))
        .expect("server did not reach mid-stream");

    // Wait until Store lease reflects non-empty partial_text from the live stream.
    let saw_partial = wait_until(Duration::from_secs(5), Duration::from_millis(20), || {
        Store::open(&data_dir)
            .ok()
            .and_then(|s| s.read_lease("t-stream").ok().flatten())
            .is_some_and(|l| l.partial_text.contains("Thinking about changing "))
    });
    assert!(
        saw_partial,
        "expected runner_leases.partial_text to become non-empty mid-stream"
    );

    // Send real SIGKILL to the worker subprocess and reap it.
    let kill_status = worker1.sigkill_and_wait();
    assert_eq!(
        kill_status.signal(),
        Some(libc::SIGKILL),
        "expected worker1 to terminate via SIGKILL, got {kill_status:?}"
    );

    // Verify journal has NO model.completed and NO tool.call entries after mid-stream SIGKILL.
    let log_after_kill = arity_json(&data_dir, &["log", "t-stream", "--json"]);
    let entries_after_kill = log_after_kill
        .as_array()
        .expect("log --json returns an array");
    assert!(
        entries_after_kill
            .iter()
            .all(|e| e["kind"] != "model.completed"),
        "unexpected model.completed after mid-stream SIGKILL: {entries_after_kill:?}"
    );
    assert!(
        entries_after_kill.iter().all(|e| e["kind"] != "tool.call"),
        "unexpected tool.call after mid-stream SIGKILL: {entries_after_kill:?}"
    );

    // Invoke `arity steer` TWICE with identical --req-id steer-idem-42.
    let steer1 = arity_json(
        &data_dir,
        &[
            "steer",
            "t-stream",
            "--req-id",
            "steer-idem-42",
            "--no-spawn",
            "--json",
            "Switch approach and reply DONE",
        ],
    );
    let steer2 = arity_json(
        &data_dir,
        &[
            "steer",
            "t-stream",
            "--req-id",
            "steer-idem-42",
            "--no-spawn",
            "--json",
            "Switch approach and reply DONE",
        ],
    );
    let steer_entry_id = steer1["id"].as_i64().expect("steer1.id");
    assert_eq!(
        steer_entry_id,
        steer2["id"].as_i64().expect("steer2.id"),
        "duplicate steer with same --req-id must return identical entry.id"
    );

    let store = Store::open(&data_dir).expect("open store");
    let matching_deliveries: Vec<_> = store
        .list_deliveries()
        .expect("list deliveries")
        .into_iter()
        .filter(|d| d.request_id == "steer-idem-42")
        .collect();
    assert_eq!(
        matching_deliveries.len(),
        1,
        "deliveries table must have exactly one row for steer-idem-42: {matching_deliveries:?}"
    );
    assert_eq!(matching_deliveries[0].entry_id, steer_entry_id);
    drop(store);

    // Spawn worker #2 and wait for clean completion.
    let mut worker2 = spawn_worker(&data_dir, "t-stream");
    let worker2_status = worker2.wait();
    assert!(
        worker2_status.success(),
        "worker2 should exit 0, got {worker2_status:?}"
    );

    // Verify only ONE delivery.accepted folded steer-idem-42.
    let log_final = arity_json(&data_dir, &["log", "t-stream", "--json"]);
    let entries_final = log_final.as_array().expect("final log array");
    let folded_steer_entries: Vec<&Value> = entries_final
        .iter()
        .filter(|e| {
            e["kind"] == "delivery.accepted"
                && e["body"]["deliveries"]
                    .as_array()
                    .is_some_and(|arr| arr.iter().any(|d| d["request_id"] == "steer-idem-42"))
        })
        .collect();
    assert_eq!(
        folded_steer_entries.len(),
        1,
        "steer-idem-42 must be folded into exactly one delivery.accepted: {folded_steer_entries:?}"
    );
    let steer_items_in_folded = folded_steer_entries[0]["body"]["deliveries"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|d| d["request_id"] == "steer-idem-42")
        .count();
    assert_eq!(
        steer_items_in_folded, 1,
        "steer-idem-42 must appear exactly once inside delivery.accepted"
    );

    // Also confirm request #2 to the model saw the steer text exactly once.
    let reqs = captured.lock().unwrap();
    assert_eq!(reqs.len(), 2, "expected exactly 2 model requests");
    let req2_str = reqs[1].body.to_string();
    assert_eq!(
        req2_str.matches("Switch approach and reply DONE").count(),
        1,
        "steer text should be sent to model exactly once: {req2_str}"
    );

    // Confirm final ModelCompleted says DONE and epoch incremented cleanly from 1 -> 2.
    let completed = entries_final
        .iter()
        .find(|e| e["kind"] == "model.completed")
        .expect("expected model.completed");
    assert_eq!(completed["body"]["text"], "DONE");

    let status_json = arity_json(&data_dir, &["status", "t-stream", "--json"]);
    assert_eq!(status_json["thread"]["epoch"].as_i64(), Some(2));
    assert_eq!(status_json["thread"]["status"].as_str(), Some("idle"));

    // Replay-check must pass 100%.
    let replay = arity_json(&data_dir, &["replay-check", "--json"]);
    assert_eq!(replay["ok"].as_bool(), Some(true));
}

#[test]
fn sigkill_mid_bash_restores_tree_sha_marks_unknown_and_never_reruns() {
    let tmp = TempDir::new("sigkill-bash");
    let repo_dir = tmp.path().join("repo");
    let data_dir = tmp.path().join("data");
    let outside_dir = tmp.path().join("outside");
    fs::create_dir_all(&outside_dir).expect("create outside dir");

    init_git_repo(&repo_dir, &[("tracked.txt", "base\n")]);

    let bash_runs_log = outside_dir.join("bash_runs.log");
    let bg_sleep_pid_file = outside_dir.join("bg_sleep.pid");
    let mid_bash_ready = outside_dir.join("mid_bash_ready");

    let bash_cmd = format!(
        "echo run >> '{}' && \
         printf 'corrupted mid-crash\\n' > tracked.txt && \
         printf 'orphan\\n' > orphan.tmp && \
         sleep 30 & echo $! > '{}' && \
         touch '{}' && \
         sleep 30",
        bash_runs_log.display(),
        bg_sleep_pid_file.display(),
        mid_bash_ready.display(),
    );

    let (chat_url, captured) = spawn_mock_server(move |idx, _req, stream| match idx {
        0 => write_sse_response(
            stream,
            &chat_tool_call_sse(
                "call_step1_write",
                "write_file",
                &json!({
                    "path": "step1.txt",
                    "content": "committed step 1\n"
                }),
            ),
        ),
        1 => write_sse_response(
            stream,
            &chat_tool_call_sse(
                "call_step2_bash",
                "bash",
                &json!({ "command": bash_cmd }),
            ),
        ),
        _ => write_sse_response(
            stream,
            &chat_text_sse("UNEXPECTED: model should not be called during recovery halt"),
        ),
    });

    arity_ok(
        &data_dir,
        &["provider", "set", "mock-chat", &chat_url, "--wire", "chat_completions"],
    );
    arity_ok(
        &data_dir,
        &[
            "create",
            "t-bash",
            "--model",
            "mock-chat:m1",
            "--dir",
            repo_dir.to_str().unwrap(),
        ],
    );
    arity_ok(
        &data_dir,
        &[
            "chat",
            "t-bash",
            "--no-spawn",
            "--no-wait",
            "Write step1.txt then run bash",
        ],
    );

    let (_, worktree_root, _) =
        workspace::worktree_paths(&data_dir, "t-bash", &repo_dir).expect("worktree_paths");

    // Spawn worker #1 and wait until bash has corrupted the worktree, spawned bg sleep,
    // touched mid_bash_ready, and recorded its active_pgid in the lease.
    let mut worker1 = spawn_worker(&data_dir, "t-bash");

    let ready = wait_until(Duration::from_secs(10), Duration::from_millis(20), || {
        mid_bash_ready.exists()
            && Store::open(&data_dir)
                .ok()
                .and_then(|s| s.read_lease("t-bash").ok().flatten())
                .and_then(|l| l.active_pgid)
                .is_some()
    });
    assert!(
        ready,
        "timed out waiting for mid_bash_ready and active_pgid lease"
    );

    // Give the pipe-EOF watchdog child a brief moment to settle after active_pgid was written.
    std::thread::sleep(Duration::from_millis(50));

    let bg_pid: i64 = fs::read_to_string(&bg_sleep_pid_file)
        .expect("read bg_sleep.pid")
        .trim()
        .parse()
        .expect("parse bg_sleep.pid");
    assert!(bg_pid > 0, "expected positive bg_pid, got {bg_pid}");
    assert!(
        Store::pid_is_alive(bg_pid),
        "expected background sleeper pid {bg_pid} to be alive before SIGKILL"
    );
    assert!(
        worktree_root.join("orphan.tmp").exists(),
        "expected orphan.tmp to exist in worktree before SIGKILL"
    );
    assert_eq!(
        fs::read_to_string(worktree_root.join("tracked.txt")).expect("read corrupted tracked.txt"),
        "corrupted mid-crash\n"
    );

    // Record step 1's committed tree_sha before killing worker #1.
    let log_mid = arity_json(&data_dir, &["log", "t-bash", "--json"]);
    let step1_tree_sha = log_mid
        .as_array()
        .expect("log array")
        .iter()
        .find(|e| e["kind"] == "tool.result" && e["body"]["name"] == "write_file")
        .and_then(|e| e["tree_sha"].as_str())
        .expect("step1 tool.result tree_sha")
        .to_string();

    // Send real SIGKILL to the `arity worker` subprocess and wait for it.
    let kill_status = worker1.sigkill_and_wait();
    assert_eq!(
        kill_status.signal(),
        Some(libc::SIGKILL),
        "expected worker1 to terminate via SIGKILL, got {kill_status:?}"
    );

    // Assert that within 500ms, the background child bg_pid is dead thanks to the pipe-EOF reaper.
    let bg_reaped = wait_until(Duration::from_millis(500), Duration::from_millis(10), || {
        !Store::pid_is_alive(bg_pid)
    });
    if !bg_reaped {
        unsafe {
            libc::kill(bg_pid as libc::pid_t, libc::SIGKILL);
        }
    }
    assert!(
        bg_reaped,
        "background sleeper pid {bg_pid} must be reaped within 500ms of worker SIGKILL"
    );

    // Restart `arity worker --thread t-bash --once` and wait for exit 0.
    let mut worker2 = spawn_worker(&data_dir, "t-bash");
    let worker2_status = worker2.wait();
    assert!(
        worker2_status.success(),
        "recovery worker2 should exit 0, got {worker2_status:?}"
    );

    // 1. bash_runs.log has EXACTLY 1 line (bash was NEVER rerun).
    let runs_content = fs::read_to_string(&bash_runs_log).expect("read bash_runs.log");
    let run_lines: Vec<&str> = runs_content.lines().collect();
    assert_eq!(
        run_lines.len(),
        1,
        "bash must have run exactly once and NEVER rerun; got: {run_lines:?}"
    );
    assert_eq!(
        captured.lock().unwrap().len(),
        2,
        "model must not be called again during crash-recovery halt"
    );

    // 2. tool.result entry for bash has body["status"] == "unknown".
    let log_after = arity_json(&data_dir, &["log", "t-bash", "--json"]);
    let bash_results: Vec<&Value> = log_after
        .as_array()
        .expect("log array")
        .iter()
        .filter(|e| e["kind"] == "tool.result" && e["body"]["name"] == "bash")
        .collect();
    assert_eq!(
        bash_results.len(),
        1,
        "expected exactly one tool.result for bash: {bash_results:?}"
    );
    assert_eq!(bash_results[0]["body"]["status"].as_str(), Some("unknown"));
    assert_eq!(
        bash_results[0]["tree_sha"].as_str(),
        Some(step1_tree_sha.as_str())
    );

    // 3. `arity status t-bash --json` shows status == "halted" and last_tree_sha == step1_tree_sha.
    let status_json = arity_json(&data_dir, &["status", "t-bash", "--json"]);
    assert_eq!(status_json["thread"]["status"].as_str(), Some("halted"));
    assert_eq!(
        status_json["thread"]["last_tree_sha"].as_str(),
        Some(step1_tree_sha.as_str())
    );
    assert_eq!(status_json["thread"]["epoch"].as_i64(), Some(2));

    // 4. On disk inside the worktree, snapshot_tree equals last_tree_sha and dirty state is gone.
    let current_tree_sha =
        workspace::snapshot_tree(&worktree_root).expect("snapshot restored worktree");
    assert_eq!(current_tree_sha, step1_tree_sha);
    assert_eq!(
        fs::read_to_string(worktree_root.join("step1.txt")).expect("read step1.txt"),
        "committed step 1\n"
    );
    assert_eq!(
        fs::read_to_string(worktree_root.join("tracked.txt")).expect("read restored tracked.txt"),
        "base\n"
    );
    assert!(
        !worktree_root.join("orphan.tmp").exists(),
        "orphan.tmp must be completely removed after restore_tree"
    );

    // 5. `arity replay-check --json` exits 0.
    let replay = arity_json(&data_dir, &["replay-check", "--json"]);
    assert_eq!(replay["ok"].as_bool(), Some(true));
}

#[test]
fn cli_step3_cross_codec_fork_diff_and_apply() {
    let tmp = TempDir::new("cross-codec-fork");
    let repo_dir = tmp.path().join("repo");
    let data_dir = tmp.path().join("data");

    init_git_repo(&repo_dir, &[("README.md", "# v0\n")]);

    // Mock Chat Completions server for main-chat: 4 mutating tool steps + final answer.
    let (chat_url, chat_captured) = spawn_mock_server(|idx, _req, stream| match idx {
        0 => write_sse_response(
            stream,
            &chat_tool_call_sse(
                "call_step1",
                "write_file",
                &json!({ "path": "a.txt", "content": "alpha\n" }),
            ),
        ),
        1 => write_sse_response(
            stream,
            &chat_tool_call_sse(
                "call_step2",
                "write_file",
                &json!({ "path": "b.txt", "content": "beta\n" }),
            ),
        ),
        2 => write_sse_response(
            stream,
            &chat_tool_call_sse(
                "call_step3",
                "edit_file",
                &json!({
                    "path": "README.md",
                    "old_text": "# v0",
                    "new_text": "# v1"
                }),
            ),
        ),
        3 => write_sse_response(
            stream,
            &chat_tool_call_sse(
                "call_step4",
                "write_file",
                &json!({
                    "path": "step4_only_on_main.txt",
                    "content": "only on main\n"
                }),
            ),
        ),
        _ => write_sse_response(stream, &chat_text_sse("Main thread finished all 4 steps.")),
    });

    // Mock Responses SSE server for branch-resp.
    let (resp_url, resp_captured) = spawn_mock_server(|idx, _req, stream| {
        if idx == 0 {
            let sse = format!(
                "data: {}\n\ndata: {}\n\n",
                json!({
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "fc_resp_write_1",
                        "name": "write_file",
                        "arguments": json!({
                            "path": "from_responses.txt",
                            "content": "hello from responses\n"
                        }).to_string(),
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
                json!({
                    "type": "response.output_text.delta",
                    "delta": "Created from_responses.txt via Responses API."
                }),
                json!({
                    "type": "response.output_item.done",
                    "item": {
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": "Created from_responses.txt via Responses API."
                        }]
                    }
                }),
                json!({
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "usage": {
                            "input_tokens": 80,
                            "output_tokens": 12,
                            "total_tokens": 92
                        }
                    }
                }),
            );
            write_sse_response(stream, &sse);
        }
    });

    // Register both mock-chat and mock-resp via `arity provider set`.
    arity_ok(
        &data_dir,
        &["provider", "set", "mock-chat", &chat_url, "--wire", "chat_completions"],
    );
    arity_ok(
        &data_dir,
        &["provider", "set", "mock-resp", &resp_url, "--wire", "responses"],
    );

    // Run thread `main-chat` through 4 mutating tool steps.
    arity_ok(
        &data_dir,
        &[
            "create",
            "main-chat",
            "--model",
            "mock-chat:m1",
            "--dir",
            repo_dir.to_str().unwrap(),
        ],
    );
    arity_ok(&data_dir, &["chat", "main-chat", "Perform four tool steps"]);
    assert_eq!(
        chat_captured.lock().unwrap().len(),
        5,
        "expected 4 tool steps + final reply on main-chat"
    );

    // Find step 3's tool.result entry ID from `arity log main-chat --json`.
    let main_log = arity_json(&data_dir, &["log", "main-chat", "--json"]);
    let tool_results: Vec<&Value> = main_log
        .as_array()
        .expect("main-chat log array")
        .iter()
        .filter(|e| e["kind"] == "tool.result")
        .collect();
    assert_eq!(
        tool_results.len(),
        4,
        "expected 4 tool.result entries on main-chat: {tool_results:?}"
    );
    let step3 = tool_results[2];
    assert_eq!(step3["body"]["name"].as_str(), Some("edit_file"));
    assert_eq!(step3["body"]["status"].as_str(), Some("ok"));
    let step3_entry_id = step3["id"].as_i64().expect("step3 entry id");
    let step3_tree_sha = step3["tree_sha"]
        .as_str()
        .expect("step3 tree_sha")
        .to_string();

    // Fork at step 3 onto `mock-resp:r1`.
    let step3_id_str = step3_entry_id.to_string();
    let fork_json = arity_json(
        &data_dir,
        &[
            "fork",
            &step3_id_str,
            "branch-resp",
            "--model",
            "mock-resp:r1",
            "--json",
        ],
    );
    assert_eq!(fork_json["thread_id"].as_str(), Some("branch-resp"));
    assert_eq!(fork_json["parent_id"].as_i64(), Some(step3_entry_id));
    assert_eq!(
        fork_json["tree_sha"].as_str(),
        Some(step3_tree_sha.as_str())
    );
    assert_eq!(fork_json["body"]["model"].as_str(), Some("mock-resp:r1"));

    // Send a prompt to `branch-resp` via `arity chat branch-resp ...` (Responses wire protocol).
    arity_ok(
        &data_dir,
        &[
            "chat",
            "branch-resp",
            "Write from_responses.txt now",
        ],
    );

    // Inspect incoming Responses request #0 to assert valid Responses `input` items with
    // steps 1..=3 and NOT step 4.
    let resp_reqs = resp_captured.lock().unwrap();
    assert_eq!(
        resp_reqs.len(),
        2,
        "expected 2 Responses requests (tool call + final reply)"
    );
    let first_resp_req = &resp_reqs[0];
    assert!(
        first_resp_req.request_line.starts_with("POST /v1/responses "),
        "unexpected Responses request line: {}",
        first_resp_req.request_line
    );
    assert_eq!(first_resp_req.body["model"].as_str(), Some("r1"));

    let input = first_resp_req.body["input"]
        .as_array()
        .expect("Responses input array");
    let fn_calls: Vec<&Value> = input
        .iter()
        .filter(|item| item["type"] == "function_call")
        .collect();
    let fn_outputs: Vec<&Value> = input
        .iter()
        .filter(|item| item["type"] == "function_call_output")
        .collect();
    assert_eq!(
        fn_calls.len(),
        3,
        "expected exactly 3 inherited function_call items (steps 1..=3): {input:?}"
    );
    assert_eq!(
        fn_outputs.len(),
        3,
        "expected exactly 3 inherited function_call_output items (steps 1..=3): {input:?}"
    );
    let call_names: Vec<&str> = fn_calls
        .iter()
        .map(|c| c["name"].as_str().unwrap_or(""))
        .collect();
    assert_eq!(call_names, vec!["write_file", "write_file", "edit_file"]);

    // Each function_call call_id must pair 1-to-1 with its function_call_output call_id.
    for (call, out) in fn_calls.iter().zip(fn_outputs.iter()) {
        assert_eq!(call["call_id"], out["call_id"]);
    }

    let first_req_serialized = first_resp_req.body.to_string();
    assert!(
        first_req_serialized.contains("a.txt")
            && first_req_serialized.contains("b.txt")
            && first_req_serialized.contains("README.md"),
        "expected steps 1..=3 in Responses input: {first_req_serialized}"
    );
    assert!(
        !first_req_serialized.contains("step4_only_on_main.txt")
            && !first_req_serialized.contains("call_step4"),
        "step 4 must NOT appear in forked Responses input: {first_req_serialized}"
    );
    drop(resp_reqs);

    // Run `arity diff branch-resp` and verify files.
    let diff_text = arity_ok(&data_dir, &["diff", "branch-resp"]);
    assert!(diff_text.contains("a.txt"), "diff missing a.txt:\n{diff_text}");
    assert!(diff_text.contains("b.txt"), "diff missing b.txt:\n{diff_text}");
    assert!(
        diff_text.contains("README.md"),
        "diff missing README.md:\n{diff_text}"
    );
    assert!(
        diff_text.contains("from_responses.txt"),
        "diff missing from_responses.txt:\n{diff_text}"
    );
    assert!(
        !diff_text.contains("step4_only_on_main.txt"),
        "diff must NOT include step4_only_on_main.txt:\n{diff_text}"
    );

    // Run `arity apply branch-resp --req-id apply-idem-1 --json` twice and verify idempotency
    // without re-running `git apply`, plus causal chain linkage.
    let apply_json = arity_json(
        &data_dir,
        &["apply", "branch-resp", "--req-id", "apply-idem-1", "--json"],
    );
    assert_eq!(apply_json["result"]["files_applied"].as_u64(), Some(4));

    let apply_json_2 = arity_json(
        &data_dir,
        &["apply", "branch-resp", "--req-id", "apply-idem-1", "--json"],
    );
    assert_eq!(
        apply_json_2["entry"]["id"],
        apply_json["entry"]["id"],
        "duplicate apply with same --req-id must return identical entry"
    );
    assert_eq!(
        apply_json_2["result"]["files_applied"].as_u64(),
        Some(4)
    );

    // Verify `arity log branch-resp --causal --json` ends with `workspace.applied`.
    let causal_log = arity_json(&data_dir, &["log", "branch-resp", "--causal", "--json"]);
    let causal_entries = causal_log.as_array().expect("causal log array");
    let tip = causal_entries.last().expect("non-empty causal chain");
    assert_eq!(tip["kind"].as_str(), Some("workspace.applied"));
    assert_eq!(tip["id"], apply_json["entry"]["id"]);
    assert!(tip["parent_id"].as_i64().is_some());

    // Verify subcommand `--help` / `-h` exits 0 cleanly and prints usage.
    for help_args in [
        &["apply", "--help"][..],
        &["chat", "-h"][..],
        &["log", "--help"][..],
        &["provider", "set", "--help"][..],
    ] {
        let help_out = arity_ok(&data_dir, help_args);
        assert!(
            help_out.contains("Usage:"),
            "expected usage output for {help_args:?}, got:\n{help_out}"
        );
    }

    assert_eq!(
        fs::read_to_string(repo_dir.join("a.txt")).expect("read a.txt"),
        "alpha\n"
    );
    assert_eq!(
        fs::read_to_string(repo_dir.join("b.txt")).expect("read b.txt"),
        "beta\n"
    );
    assert_eq!(
        fs::read_to_string(repo_dir.join("README.md")).expect("read README.md"),
        "# v1\n"
    );
    assert_eq!(
        fs::read_to_string(repo_dir.join("from_responses.txt")).expect("read from_responses.txt"),
        "hello from responses\n"
    );
    assert!(
        !repo_dir.join("step4_only_on_main.txt").exists(),
        "step4_only_on_main.txt must NOT exist in source repo after applying branch-resp"
    );

    // Run `arity replay-check --json` and assert 100% match.
    let replay = arity_json(&data_dir, &["replay-check", "--json"]);
    assert_eq!(replay["ok"].as_bool(), Some(true));
}
