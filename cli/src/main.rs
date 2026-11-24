//! Intent-only CLI and detached single-writer worker entrypoint for Arity v0.0.1.
//!
//! Invariant: ONLY `arity worker --thread <id> [--once]` invokes `runner::run_turn_once`.
//! Every other subcommand exclusively appends idempotent intents to SQLite and reads/tails state.

use std::fs::File;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, anyhow, bail, ensure};
use arity_core::codec::ModelClient;
use arity_core::runner::{self, DEFAULT_SYSTEM_PROMPT};
use arity_core::secrets;
use arity_core::store::{LeaseRow, Store};
use arity_core::types::{
    DeliveryMode, Entry, EntryKind, ProviderConfig, ThreadRow, ThreadStatus, WireCodec, now_ms,
};
use arity_core::workspace;
use serde_json::{Value, json};

const USAGE: &str = "\
arity v0.0.1

Usage:
  arity [--data-dir <path>] <command> [options]

Commands:
  create <thread> --model <spec> [--dir <path>] [--system <text>] [--req-id <id>] [--json]
  chat <thread> [--model <spec>] [--dir <path>] [--system <text>] [--mode steer|after_turn|inbox] [--req-id <id>] [--no-spawn] [--no-wait] <text...>
  steer <thread> [--model <spec>] [--req-id <id>] [--no-spawn] [--json] <text...>
  stop <thread> [--req-id <id>] [--json]
  fork <from_entry_id> <new_thread> [--model <spec>] [--req-id <id>] [--json]
  worker --thread <thread> [--once]
  log <thread> [--causal] [--json]
  status [<thread>] [--json]
  diff <thread> [--json]
  apply <thread> [--req-id <id>] [--json]
  replay-check [--json]
  providers [--json]
  provider set <name> <url> --wire chat_completions|responses [--needs-key] [--key-name <k>] [--session-header <h>] [--models <m1,m2>] [--json]
  limits [<provider>] [--json]
  key set <name> [<key>]
  key rm <name>
  login
  logout
  whoami";

fn main() {
    if let Err(err) = run() {
        eprintln!("error: {err:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        println!("{USAGE}");
        return Ok(());
    }

    let mut data_dir: Option<PathBuf> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--data-dir" => {
                let val = args
                    .get(i + 1)
                    .ok_or_else(|| anyhow!("--data-dir requires a path"))?;
                data_dir = Some(PathBuf::from(val));
                args.drain(i..=i + 1);
            }
            s if s.starts_with("--data-dir=") => {
                let val = s.trim_start_matches("--data-dir=");
                data_dir = Some(PathBuf::from(val));
                args.remove(i);
            }
            "--" => break,
            _ => i += 1,
        }
    }

    let data_dir = data_dir.unwrap_or_else(arity_core::default_data_dir);
    let Some((cmd, rest)) = args.split_first() else {
        println!("{USAGE}");
        return Ok(());
    };

    match cmd.as_str() {
        "-h" | "--help" | "help" => {
            println!("{USAGE}");
            Ok(())
        }
        "-V" | "--version" | "version" => {
            println!("arity {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        "create" => cmd_create(&data_dir, rest),
        "chat" => cmd_chat(&data_dir, rest),
        "steer" => cmd_steer(&data_dir, rest),
        "stop" => cmd_stop(&data_dir, rest),
        "fork" => cmd_fork(&data_dir, rest),
        "worker" => cmd_worker(&data_dir, rest),
        "log" => cmd_log(&data_dir, rest),
        "status" => cmd_status(&data_dir, rest),
        "diff" => cmd_diff(&data_dir, rest),
        "apply" => cmd_apply(&data_dir, rest),
        "replay-check" => cmd_replay_check(&data_dir, rest),
        "providers" => cmd_providers(&data_dir, rest),
        "provider" => cmd_provider(&data_dir, rest),
        "limits" => cmd_limits(&data_dir, rest),
        "key" => cmd_key(&data_dir, rest),
        "login" => {
            let who = secrets::codex_auth::login(&data_dir)?;
            println!("signed in as {who}");
            Ok(())
        }
        "logout" => {
            secrets::codex_auth::logout(&data_dir)?;
            println!("signed out");
            Ok(())
        }
        "whoami" => {
            match secrets::codex_auth::account(&data_dir) {
                Some(who) => println!("{who}"),
                None => println!("not signed in"),
            }
            Ok(())
        }
        other => bail!("unknown command '{other}'\n\n{USAGE}"),
    }
}

// -----------------------------------------------------------------------------
// Utilities
// -----------------------------------------------------------------------------

fn take_flag_value(args: &[String], idx: &mut usize, flag: &str) -> Result<String> {
    let cur = &args[*idx];
    if let Some((_, v)) = cur.split_once('=') {
        return Ok(v.to_string());
    }
    *idx += 1;
    args.get(*idx)
        .cloned()
        .ok_or_else(|| anyhow!("{flag} requires a value"))
}

fn flag_name(arg: &str) -> &str {
    arg.split_once('=').map_or(arg, |(k, _)| k)
}

fn random_req_id() -> String {
    let mut bytes = [0u8; 16];
    let filled = File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut bytes))
        .is_ok();
    if !filled {
        static SEQ: AtomicU64 = AtomicU64::new(1);
        let t = now_ms() as u64;
        let pid = std::process::id() as u64;
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let a = t ^ (pid.rotate_left(17));
        let b = seq.wrapping_mul(0x9e3779b97f4a7c15) ^ t.rotate_right(13);
        bytes[..8].copy_from_slice(&a.to_le_bytes());
        bytes[8..].copy_from_slice(&b.to_le_bytes());
    }
    let mut hex = String::with_capacity(32);
    for b in bytes {
        use std::fmt::Write as _;
        let _ = write!(&mut hex, "{b:02x}");
    }
    hex
}

fn lease_is_live(lease: Option<&LeaseRow>) -> bool {
    lease.is_some_and(|l| l.lease_until_ms > now_ms() && Store::pid_is_alive(l.owner_pid))
}

fn spawn_detached_worker(data_dir: &Path, thread_id: &str) -> Result<()> {
    let exe = std::env::current_exe().context("failed to resolve current arity binary")?;
    let mut cmd = Command::new(exe);
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
    cmd.spawn().context("failed to spawn background worker")?;
    Ok(())
}

fn short_sha(sha: Option<&str>) -> &str {
    match sha {
        Some(s) if s.len() >= 12 => &s[..12],
        Some(s) if !s.is_empty() => s,
        _ => "-",
    }
}

fn read_text_or_stdin(parts: &[String]) -> Result<String> {
    if !parts.is_empty() {
        return Ok(parts.join(" "));
    }
    let is_tty = unsafe { libc::isatty(libc::STDIN_FILENO) } != 0;
    if is_tty {
        bail!("message text is required");
    }
    let mut buf = String::new();
    io::stdin().read_to_string(&mut buf)?;
    let trimmed = buf.trim_end_matches(['\r', '\n']).to_string();
    if trimmed.is_empty() {
        bail!("message text is required");
    }
    Ok(trimmed)
}

// -----------------------------------------------------------------------------
// Intent commands (never call runner directly)
// -----------------------------------------------------------------------------

fn cmd_create(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut model: Option<String> = None;
    let mut dir: Option<String> = None;
    let mut system: Option<String> = None;
    let mut req_id: Option<String> = None;
    let mut as_json = false;

    let mut i = 0;
    while i < args.len() {
        match flag_name(&args[i]) {
            "--model" => model = Some(take_flag_value(args, &mut i, "--model")?),
            "--dir" => dir = Some(take_flag_value(args, &mut i, "--dir")?),
            "--system" => system = Some(take_flag_value(args, &mut i, "--system")?),
            "--req-id" => req_id = Some(take_flag_value(args, &mut i, "--req-id")?),
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for create"),
            _ => {
                if thread_id.is_none() {
                    thread_id = Some(args[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", args[i]);
                }
            }
        }
        i += 1;
    }

    let thread_id = thread_id.ok_or_else(|| anyhow!("create requires <thread>"))?;
    let model = model.ok_or_else(|| anyhow!("create requires --model <provider>:<model>"))?;
    let system = system.unwrap_or_else(|| DEFAULT_SYSTEM_PROMPT.to_string());
    let req_id = req_id.unwrap_or_else(random_req_id);

    let mut store = Store::open(data_dir)?;
    let already_exists = store.get_thread(&thread_id)?.is_some();

    let (source_dir, base_commit, base_tree_sha) = if !already_exists {
        if let Some(ref d) = dir {
            let ws = workspace::init_workspace(data_dir, &thread_id, Path::new(d))?;
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
        &thread_id,
        &model,
        source_dir.as_deref(),
        base_commit.as_deref(),
        base_tree_sha.as_deref(),
        &system,
        &req_id,
    )?;

    if as_json {
        println!("{}", serde_json::to_string_pretty(&entry)?);
    } else {
        println!(
            "created thread={} entry_id={} model={} base_tree_sha={} req_id={}",
            entry.thread_id,
            entry.id,
            entry.body["model"].as_str().unwrap_or(&model),
            short_sha(entry.tree_sha.as_deref()),
            entry.request_id.as_deref().unwrap_or(&req_id),
        );
    }
    Ok(())
}

fn cmd_chat(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut model: Option<String> = None;
    let mut dir: Option<String> = None;
    let mut system: Option<String> = None;
    let mut mode = DeliveryMode::Steer;
    let mut req_id: Option<String> = None;
    let mut no_spawn = false;
    let mut no_wait = false;
    let mut text_parts: Vec<String> = Vec::new();
    let mut after_sep = false;

    let mut i = 0;
    while i < args.len() {
        if !after_sep {
            match flag_name(&args[i]) {
                "--" => {
                    after_sep = true;
                    i += 1;
                    continue;
                }
                "--model" => {
                    model = Some(take_flag_value(args, &mut i, "--model")?);
                    i += 1;
                    continue;
                }
                "--dir" => {
                    dir = Some(take_flag_value(args, &mut i, "--dir")?);
                    i += 1;
                    continue;
                }
                "--system" => {
                    system = Some(take_flag_value(args, &mut i, "--system")?);
                    i += 1;
                    continue;
                }
                "--mode" => {
                    let raw = take_flag_value(args, &mut i, "--mode")?;
                    mode = DeliveryMode::parse(&raw)?;
                    i += 1;
                    continue;
                }
                "--req-id" => {
                    req_id = Some(take_flag_value(args, &mut i, "--req-id")?);
                    i += 1;
                    continue;
                }
                "--no-spawn" => {
                    no_spawn = true;
                    i += 1;
                    continue;
                }
                "--no-wait" => {
                    no_wait = true;
                    i += 1;
                    continue;
                }
                other if other.starts_with('-') => bail!("unknown flag '{other}' for chat"),
                _ => {}
            }
        }
        if thread_id.is_none() {
            thread_id = Some(args[i].clone());
        } else {
            text_parts.push(args[i].clone());
        }
        i += 1;
    }

    let thread_id = thread_id.ok_or_else(|| anyhow!("chat requires <thread>"))?;
    let text = read_text_or_stdin(&text_parts)?;
    let req_id = req_id.unwrap_or_else(random_req_id);

    let mut store = Store::open(data_dir)?;
    let created_now = if store.get_thread(&thread_id)?.is_none() {
        let model_spec = model.as_deref().ok_or_else(|| {
            anyhow!("thread '{thread_id}' does not exist; pass --model <provider>:<model> to create it")
        })?;
        let create_req_id = format!("{req_id}:create");
        let (source_dir, base_commit, base_tree_sha) = if let Some(ref d) = dir {
            let ws = workspace::init_workspace(data_dir, &thread_id, Path::new(d))?;
            (
                Some(ws.source_dir),
                Some(ws.base_commit),
                Some(ws.base_tree_sha),
            )
        } else {
            (None, None, None)
        };
        let sys = system.as_deref().unwrap_or(DEFAULT_SYSTEM_PROMPT);
        store.create_thread(
            &thread_id,
            model_spec,
            source_dir.as_deref(),
            base_commit.as_deref(),
            base_tree_sha.as_deref(),
            sys,
            &create_req_id,
        )?;
        true
    } else {
        false
    };

    let model_override = if created_now { None } else { model.as_deref() };
    let delivery = store.enqueue_delivery(&thread_id, mode, &text, model_override, &req_id)?;

    let should_spawn = !no_spawn && mode != DeliveryMode::Inbox;
    if should_spawn && !lease_is_live(store.read_lease(&thread_id)?.as_ref()) {
        spawn_detached_worker(data_dir, &thread_id)?;
    }

    if no_wait || mode == DeliveryMode::Inbox {
        println!(
            "queued thread={} entry_id={} mode={} req_id={}",
            delivery.thread_id,
            delivery.id,
            mode.as_str(),
            delivery.request_id.as_deref().unwrap_or(&req_id),
        );
        return Ok(());
    }

    tail_delivery(&store, data_dir, &thread_id, delivery.id, should_spawn)
}

fn tail_delivery(
    store: &Store,
    data_dir: &Path,
    thread_id: &str,
    delivery_id: i64,
    allow_respawn: bool,
) -> Result<()> {
    let mut last_entry_id = delivery_id;
    let mut printed_partial_len: usize = 0;
    let mut streamed_any = false;
    let mut accepted = false;
    let mut last_error: Option<String> = None;
    let mut respawned = false;
    let started = Instant::now();

    loop {
        let new_entries = store.thread_entries(thread_id, last_entry_id)?;
        for entry in new_entries {
            last_entry_id = entry.id;
            match entry.kind {
                EntryKind::DeliveryAccepted => {
                    if entry
                        .body
                        .get("delivery_ids")
                        .and_then(Value::as_array)
                        .is_some_and(|arr| arr.iter().any(|v| v.as_i64() == Some(delivery_id)))
                    {
                        accepted = true;
                    }
                }
                EntryKind::ModelCompleted => {
                    let text = entry.body["text"].as_str().unwrap_or("");
                    flush_remaining_text(text, &mut printed_partial_len, &mut streamed_any)?;
                }
                EntryKind::ModelInterrupted => {
                    let text = entry.body["text"].as_str().unwrap_or("");
                    flush_remaining_text(text, &mut printed_partial_len, &mut streamed_any)?;
                }
                EntryKind::ToolCall => {
                    let name = entry.body["name"].as_str().unwrap_or("");
                    eprintln!("[tool] {name}");
                }
                EntryKind::ToolResult => {
                    let name = entry.body["name"].as_str().unwrap_or("");
                    let status = entry.body["status"].as_str().unwrap_or("");
                    if status != "ok" {
                        eprintln!("[tool:{status}] {name}");
                    }
                }
                EntryKind::TurnFinished => {
                    if let Some(err) = entry.body["error"].as_str() {
                        last_error = Some(err.to_string());
                    }
                }
                _ => {}
            }
        }

        if !accepted {
            let still_queued = store
                .pending_deliveries(thread_id)?
                .iter()
                .any(|d| d.entry_id == delivery_id);
            if !still_queued {
                accepted = true;
            }
        }

        let lease = store.read_lease(thread_id)?;
        let live_lease = lease_is_live(lease.as_ref());
        if let Some(ref l) = lease {
            if l.partial_text.len() > printed_partial_len
                && l.partial_text.is_char_boundary(printed_partial_len)
            {
                let delta = &l.partial_text[printed_partial_len..];
                print!("{delta}");
                io::stdout().flush()?;
                printed_partial_len = l.partial_text.len();
                streamed_any = true;
            } else if l.partial_text.len() < printed_partial_len {
                printed_partial_len = 0;
            }
        }

        let thread = store
            .get_thread(thread_id)?
            .ok_or_else(|| anyhow!("thread '{thread_id}' not found"))?;

        if accepted
            && matches!(
                thread.status,
                ThreadStatus::Idle | ThreadStatus::Halted | ThreadStatus::Applied
            )
            && !live_lease
        {
            // One final drain in case TurnFinished / ModelCompleted committed right as lease cleared.
            for entry in store.thread_entries(thread_id, last_entry_id)? {
                match entry.kind {
                    EntryKind::ModelCompleted | EntryKind::ModelInterrupted => {
                        let text = entry.body["text"].as_str().unwrap_or("");
                        flush_remaining_text(text, &mut printed_partial_len, &mut streamed_any)?;
                    }
                    EntryKind::TurnFinished => {
                        if let Some(err) = entry.body["error"].as_str() {
                            last_error = Some(err.to_string());
                        }
                    }
                    _ => {}
                }
            }
            if thread.status == ThreadStatus::Halted
                && let Some(err) = last_error
            {
                bail!("{err}");
            }
            return Ok(());
        }

        // If a previous worker finished just before seeing our delivery, spawn once more.
        if !accepted
            && !live_lease
            && allow_respawn
            && !respawned
            && thread.status == ThreadStatus::Idle
            && started.elapsed() >= Duration::from_millis(300)
        {
            respawned = true;
            spawn_detached_worker(data_dir, thread_id)?;
        }

        // Guard against hanging if worker halted with an error before accepting the delivery.
        if !accepted
            && !live_lease
            && thread.status == ThreadStatus::Halted
            && started.elapsed() >= Duration::from_millis(500)
        {
            if let Some(err) = last_error {
                bail!("{err}");
            }
            bail!("thread '{thread_id}' halted before accepting delivery {delivery_id}");
        }

        std::thread::sleep(Duration::from_millis(30));
    }
}

fn flush_remaining_text(
    text: &str,
    printed_partial_len: &mut usize,
    streamed_any: &mut bool,
) -> Result<()> {
    if text.len() > *printed_partial_len && text.is_char_boundary(*printed_partial_len) {
        print!("{}", &text[*printed_partial_len..]);
        *streamed_any = true;
    }
    if *streamed_any && !text.ends_with('\n') {
        println!();
    }
    io::stdout().flush()?;
    *printed_partial_len = 0;
    *streamed_any = false;
    Ok(())
}

fn cmd_steer(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut model: Option<String> = None;
    let mut req_id: Option<String> = None;
    let mut no_spawn = false;
    let mut as_json = false;
    let mut text_parts: Vec<String> = Vec::new();
    let mut after_sep = false;

    let mut i = 0;
    while i < args.len() {
        if !after_sep {
            match flag_name(&args[i]) {
                "--" => {
                    after_sep = true;
                    i += 1;
                    continue;
                }
                "--model" => {
                    model = Some(take_flag_value(args, &mut i, "--model")?);
                    i += 1;
                    continue;
                }
                "--req-id" => {
                    req_id = Some(take_flag_value(args, &mut i, "--req-id")?);
                    i += 1;
                    continue;
                }
                "--no-spawn" => {
                    no_spawn = true;
                    i += 1;
                    continue;
                }
                "--json" => {
                    as_json = true;
                    i += 1;
                    continue;
                }
                other if other.starts_with('-') => bail!("unknown flag '{other}' for steer"),
                _ => {}
            }
        }
        if thread_id.is_none() {
            thread_id = Some(args[i].clone());
        } else {
            text_parts.push(args[i].clone());
        }
        i += 1;
    }

    let thread_id = thread_id.ok_or_else(|| anyhow!("steer requires <thread>"))?;
    let text = read_text_or_stdin(&text_parts)?;
    let req_id = req_id.unwrap_or_else(random_req_id);

    let mut store = Store::open(data_dir)?;
    let entry = store.enqueue_delivery(
        &thread_id,
        DeliveryMode::Steer,
        &text,
        model.as_deref(),
        &req_id,
    )?;

    if !no_spawn && !lease_is_live(store.read_lease(&thread_id)?.as_ref()) {
        spawn_detached_worker(data_dir, &thread_id)?;
    }

    if as_json {
        println!("{}", serde_json::to_string_pretty(&entry)?);
    } else {
        println!(
            "steered thread={} entry_id={} req_id={}",
            entry.thread_id,
            entry.id,
            entry.request_id.as_deref().unwrap_or(&req_id),
        );
    }
    Ok(())
}

fn cmd_stop(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut req_id: Option<String> = None;
    let mut as_json = false;

    let mut i = 0;
    while i < args.len() {
        match flag_name(&args[i]) {
            "--req-id" => req_id = Some(take_flag_value(args, &mut i, "--req-id")?),
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for stop"),
            _ => {
                if thread_id.is_none() {
                    thread_id = Some(args[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", args[i]);
                }
            }
        }
        i += 1;
    }

    let thread_id = thread_id.ok_or_else(|| anyhow!("stop requires <thread>"))?;
    let req_id = req_id.unwrap_or_else(random_req_id);

    let mut store = Store::open(data_dir)?;
    let entry = store.request_stop(&thread_id, "user", &req_id)?;

    if as_json {
        println!("{}", serde_json::to_string_pretty(&entry)?);
    } else {
        println!(
            "stop_requested thread={} entry_id={} req_id={}",
            entry.thread_id,
            entry.id,
            entry.request_id.as_deref().unwrap_or(&req_id),
        );
    }
    Ok(())
}

fn cmd_fork(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut from_entry_id: Option<i64> = None;
    let mut new_thread: Option<String> = None;
    let mut model: Option<String> = None;
    let mut req_id: Option<String> = None;
    let mut as_json = false;

    let mut i = 0;
    while i < args.len() {
        match flag_name(&args[i]) {
            "--model" => model = Some(take_flag_value(args, &mut i, "--model")?),
            "--req-id" => req_id = Some(take_flag_value(args, &mut i, "--req-id")?),
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for fork"),
            _ => {
                if from_entry_id.is_none() {
                    let parsed = args[i]
                        .parse::<i64>()
                        .with_context(|| format!("invalid entry id '{}'", args[i]))?;
                    from_entry_id = Some(parsed);
                } else if new_thread.is_none() {
                    new_thread = Some(args[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", args[i]);
                }
            }
        }
        i += 1;
    }

    let from_entry_id =
        from_entry_id.ok_or_else(|| anyhow!("fork requires <from_entry_id> <new_thread>"))?;
    let new_thread =
        new_thread.ok_or_else(|| anyhow!("fork requires <from_entry_id> <new_thread>"))?;
    let req_id = req_id.unwrap_or_else(random_req_id);

    let mut store = Store::open(data_dir)?;
    let entry = store.fork_thread(&new_thread, from_entry_id, model.as_deref(), &req_id)?;

    if !lease_is_live(store.read_lease(&new_thread)?.as_ref())
        && let Some(thread) = store.get_thread(&new_thread)?
        && let (Some(src), Some(sha)) = (
            thread.source_dir.as_deref().filter(|s| !s.is_empty()),
            thread.last_tree_sha.as_deref().filter(|s| !s.is_empty()),
        )
    {
        workspace::materialize_workspace(data_dir, &new_thread, Path::new(src), sha)?;
    }

    if as_json {
        println!("{}", serde_json::to_string_pretty(&entry)?);
    } else {
        println!(
            "forked thread={} from_entry_id={} entry_id={} model={} tree_sha={} req_id={}",
            entry.thread_id,
            from_entry_id,
            entry.id,
            entry.body["model"].as_str().unwrap_or("-"),
            short_sha(entry.tree_sha.as_deref()),
            entry.request_id.as_deref().unwrap_or(&req_id),
        );
    }
    Ok(())
}

// -----------------------------------------------------------------------------
// Worker subcommand (the ONLY place that calls runner::run_turn_once)
// -----------------------------------------------------------------------------

fn cmd_worker(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut once = false;

    let mut i = 0;
    while i < args.len() {
        match flag_name(&args[i]) {
            "--thread" => thread_id = Some(take_flag_value(args, &mut i, "--thread")?),
            "--once" => once = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for worker"),
            _ => {
                if thread_id.is_none() {
                    thread_id = Some(args[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", args[i]);
                }
            }
        }
        i += 1;
    }

    let thread_id = thread_id.ok_or_else(|| anyhow!("worker requires --thread <thread>"))?;
    let client = ModelClient::new();
    if once {
        runner::run_turn_once(data_dir, &thread_id, &client)?;
    } else {
        runner::run_worker_until_idle(data_dir, &thread_id, &client)?;
    }
    Ok(())
}

// -----------------------------------------------------------------------------
// Read / inspection / workspace / admin commands
// -----------------------------------------------------------------------------

fn cmd_log(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut causal = false;
    let mut as_json = false;

    let mut i = 0;
    while i < args.len() {
        match flag_name(&args[i]) {
            "--causal" => causal = true,
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for log"),
            _ => {
                if thread_id.is_none() {
                    thread_id = Some(args[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", args[i]);
                }
            }
        }
        i += 1;
    }

    let thread_id = thread_id.ok_or_else(|| anyhow!("log requires <thread>"))?;
    let store = Store::open(data_dir)?;
    let thread = store
        .get_thread(&thread_id)?
        .ok_or_else(|| anyhow!("thread '{thread_id}' not found"))?;

    let entries = if causal {
        match thread.head_entry_id {
            Some(hid) => store.causal_chain(hid)?,
            None => Vec::new(),
        }
    } else {
        store.thread_entries(&thread_id, 0)?
    };

    if as_json {
        println!("{}", serde_json::to_string_pretty(&entries)?);
        return Ok(());
    }

    for entry in &entries {
        let req = entry.request_id.as_deref().unwrap_or("-");
        println!(
            "#{} e={} {:<18} tree={} req={} {}",
            entry.id,
            entry.epoch,
            entry.kind.as_str(),
            short_sha(entry.tree_sha.as_deref()),
            req,
            summarize_entry(entry),
        );
    }
    Ok(())
}

fn summarize_entry(entry: &Entry) -> String {
    match entry.kind {
        EntryKind::ThreadCreated => format!(
            "model={} dir={}",
            entry.body["model"].as_str().unwrap_or("-"),
            entry.body["source_dir"].as_str().unwrap_or("-"),
        ),
        EntryKind::ThreadForked => format!(
            "from=#{} model={}",
            entry.body["from_entry_id"].as_i64().unwrap_or(0),
            entry.body["model"].as_str().unwrap_or("-"),
        ),
        EntryKind::DeliveryQueued => format!(
            "mode={} text={:?}",
            entry.body["mode"].as_str().unwrap_or("-"),
            truncate_preview(entry.body["text"].as_str().unwrap_or(""), 80),
        ),
        EntryKind::DeliveryAccepted => format!(
            "ids={} text={:?}",
            entry.body["delivery_ids"],
            truncate_preview(entry.body["text"].as_str().unwrap_or(""), 80),
        ),
        EntryKind::ModelCompleted => {
            let calls = entry.body["tool_calls"]
                .as_array()
                .map_or(0, Vec::len);
            format!(
                "model={} calls={} text={:?}",
                entry.body["model"].as_str().unwrap_or("-"),
                calls,
                truncate_preview(entry.body["text"].as_str().unwrap_or(""), 80),
            )
        }
        EntryKind::ModelInterrupted => format!(
            "reason={} text={:?}",
            entry.body["reason"].as_str().unwrap_or("-"),
            truncate_preview(entry.body["text"].as_str().unwrap_or(""), 80),
        ),
        EntryKind::ToolCall => format!(
            "call_id={} name={} mutating={}",
            entry.body["call_id"].as_str().unwrap_or("-"),
            entry.body["name"].as_str().unwrap_or("-"),
            entry.body["mutating"].as_bool().unwrap_or(false),
        ),
        EntryKind::ToolResult => format!(
            "call_id={} name={} status={} output={:?}",
            entry.body["call_id"].as_str().unwrap_or("-"),
            entry.body["name"].as_str().unwrap_or("-"),
            entry.body["status"].as_str().unwrap_or("-"),
            truncate_preview(entry.body["output"].as_str().unwrap_or(""), 80),
        ),
        EntryKind::TurnClaimed => format!(
            "pid={} stale_pgid={}",
            entry.body["owner_pid"].as_i64().unwrap_or(0),
            entry.body["stale_pgid"],
        ),
        EntryKind::TurnFinished => format!(
            "status={} reason={} error={}",
            entry.body["status"].as_str().unwrap_or("-"),
            entry.body["reason"].as_str().unwrap_or("-"),
            entry.body["error"].as_str().unwrap_or("-"),
        ),
        EntryKind::ThreadStop => format!("reason={}", entry.body["reason"].as_str().unwrap_or("-")),
        EntryKind::WorkspaceApplied => format!(
            "files_applied={}",
            entry.body["files_applied"].as_u64().unwrap_or(0),
        ),
    }
}

fn truncate_preview(s: &str, max_chars: usize) -> String {
    let single: String = s
        .chars()
        .map(|c| if c == '\n' || c == '\r' { ' ' } else { c })
        .collect();
    if single.chars().count() <= max_chars {
        single
    } else {
        let cut: String = single.chars().take(max_chars).collect();
        format!("{cut}...")
    }
}

fn lease_to_json(lease: &LeaseRow) -> Value {
    json!({
        "thread_id": lease.thread_id,
        "epoch": lease.epoch,
        "owner_pid": lease.owner_pid,
        "lease_until_ms": lease.lease_until_ms,
        "live": lease_is_live(Some(lease)),
        "partial_text_len": lease.partial_text.len(),
        "active_pgid": lease.active_pgid,
    })
}

fn print_thread_status(thread: &ThreadRow, lease: Option<&LeaseRow>, pending: usize) {
    let lease_str = match lease {
        Some(l) if lease_is_live(Some(l)) => {
            format!("pid={} pgid={:?}", l.owner_pid, l.active_pgid)
        }
        Some(l) => format!("expired(pid={})", l.owner_pid),
        None => "-".into(),
    };
    println!(
        "{:<20} status={:<8} epoch={} head={:<5} model={} tree={} pending={} lease={}",
        thread.id,
        thread.status.as_str(),
        thread.epoch,
        thread
            .head_entry_id
            .map_or_else(|| "-".into(), |id| format!("#{id}")),
        thread.model,
        short_sha(thread.last_tree_sha.as_deref()),
        pending,
        lease_str,
    );
}

fn cmd_status(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut as_json = false;

    let mut i = 0;
    while i < args.len() {
        match flag_name(&args[i]) {
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for status"),
            _ => {
                if thread_id.is_none() {
                    thread_id = Some(args[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", args[i]);
                }
            }
        }
        i += 1;
    }

    let store = Store::open(data_dir)?;
    if let Some(tid) = thread_id {
        let thread = store
            .get_thread(&tid)?
            .ok_or_else(|| anyhow!("thread '{tid}' not found"))?;
        let lease = store.read_lease(&tid)?;
        let pending = store.pending_deliveries(&tid)?;
        if as_json {
            println!(
                "{}",
                serde_json::to_string_pretty(&json!({
                    "thread": thread,
                    "lease": lease.as_ref().map(lease_to_json),
                    "pending_deliveries": pending.len(),
                }))?
            );
        } else {
            print_thread_status(&thread, lease.as_ref(), pending.len());
        }
        return Ok(());
    }

    let threads = store.list_threads()?;
    if as_json {
        let mut items = Vec::with_capacity(threads.len());
        for t in threads {
            let lease = store.read_lease(&t.id)?;
            let pending = store.pending_deliveries(&t.id)?;
            items.push(json!({
                "thread": t,
                "lease": lease.as_ref().map(lease_to_json),
                "pending_deliveries": pending.len(),
            }));
        }
        println!("{}", serde_json::to_string_pretty(&items)?);
    } else if threads.is_empty() {
        println!("no threads");
    } else {
        for t in &threads {
            let lease = store.read_lease(&t.id)?;
            let pending = store.pending_deliveries(&t.id)?;
            print_thread_status(t, lease.as_ref(), pending.len());
        }
    }
    Ok(())
}

fn cmd_diff(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut as_json = false;

    let mut i = 0;
    while i < args.len() {
        match flag_name(&args[i]) {
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for diff"),
            _ => {
                if thread_id.is_none() {
                    thread_id = Some(args[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", args[i]);
                }
            }
        }
        i += 1;
    }

    let thread_id = thread_id.ok_or_else(|| anyhow!("diff requires <thread>"))?;
    let store = Store::open(data_dir)?;
    let thread = store
        .get_thread(&thread_id)?
        .ok_or_else(|| anyhow!("thread '{thread_id}' not found"))?;
    let src = thread
        .source_dir
        .as_deref()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| anyhow!("thread '{thread_id}' has no attached workspace"))?;
    let base_sha = thread
        .base_tree_sha
        .as_deref()
        .ok_or_else(|| anyhow!("thread '{thread_id}' has no base_tree_sha"))?;
    let last_sha = thread
        .last_tree_sha
        .as_deref()
        .ok_or_else(|| anyhow!("thread '{thread_id}' has no last_tree_sha"))?;

    let diff = workspace::diff_trees(Path::new(src), base_sha, last_sha)?;
    if as_json {
        println!("{}", serde_json::to_string_pretty(&diff)?);
        return Ok(());
    }

    if diff.files.is_empty() {
        println!("no changes");
        return Ok(());
    }
    for f in &diff.files {
        println!("{:<10} {}", f.status, f.path);
    }
    if !diff.patch.is_empty() {
        println!();
        print!("{}", diff.patch);
    }
    Ok(())
}

fn cmd_apply(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut thread_id: Option<String> = None;
    let mut req_id: Option<String> = None;
    let mut as_json = false;

    let mut i = 0;
    while i < args.len() {
        match flag_name(&args[i]) {
            "--req-id" => req_id = Some(take_flag_value(args, &mut i, "--req-id")?),
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for apply"),
            _ => {
                if thread_id.is_none() {
                    thread_id = Some(args[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", args[i]);
                }
            }
        }
        i += 1;
    }

    let thread_id = thread_id.ok_or_else(|| anyhow!("apply requires <thread>"))?;
    let req_id = req_id.unwrap_or_else(random_req_id);

    let mut store = Store::open(data_dir)?;
    let thread = store
        .get_thread(&thread_id)?
        .ok_or_else(|| anyhow!("thread '{thread_id}' not found"))?;
    if lease_is_live(store.read_lease(&thread_id)?.as_ref()) {
        bail!("cannot apply thread '{thread_id}' while an active runner holds the lease");
    }

    let src = thread
        .source_dir
        .as_deref()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| anyhow!("thread '{thread_id}' has no attached workspace"))?;
    let base_sha = thread
        .base_tree_sha
        .as_deref()
        .ok_or_else(|| anyhow!("thread '{thread_id}' has no base_tree_sha"))?;
    let last_sha = thread
        .last_tree_sha
        .as_deref()
        .ok_or_else(|| anyhow!("thread '{thread_id}' has no last_tree_sha"))?;

    let applied = workspace::apply_tree(Path::new(src), base_sha, last_sha)?;
    let entry = store.record_workspace_applied(
        &thread_id,
        applied.files_applied,
        base_sha,
        last_sha,
        &req_id,
    )?;

    if as_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&json!({
                "entry": entry,
                "result": applied,
            }))?
        );
    } else {
        println!(
            "{} (thread={} entry_id={} req_id={})",
            applied.summary,
            entry.thread_id,
            entry.id,
            entry.request_id.as_deref().unwrap_or(&req_id),
        );
    }
    Ok(())
}

fn cmd_replay_check(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut as_json = false;
    for arg in args {
        match arg.as_str() {
            "--json" => as_json = true,
            other => bail!("unknown argument '{other}' for replay-check"),
        }
    }

    let mut store = Store::open(data_dir)?;
    let threads_before = store.list_threads()?;
    let deliveries_before = store.list_deliveries()?;

    store.rebuild_projections()?;

    let threads_after = store.list_threads()?;
    let deliveries_after = store.list_deliveries()?;

    ensure!(
        threads_before == threads_after,
        "replay mismatch in threads table"
    );
    ensure!(
        deliveries_before == deliveries_after,
        "replay mismatch in deliveries table"
    );

    if as_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&json!({
                "ok": true,
                "threads": threads_after.len(),
                "deliveries": deliveries_after.len(),
            }))?
        );
    } else {
        println!(
            "replay-check ok: verified {} thread(s) and {} delivery(ies)",
            threads_after.len(),
            deliveries_after.len(),
        );
    }
    Ok(())
}

fn cmd_providers(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut as_json = false;
    for arg in args {
        match arg.as_str() {
            "--json" => as_json = true,
            other => bail!("unknown argument '{other}' for providers"),
        }
    }

    let store = Store::open(data_dir)?;
    let providers = store.list_providers()?;
    if as_json {
        println!("{}", serde_json::to_string_pretty(&providers)?);
    } else {
        for p in &providers {
            println!(
                "{:<14} wire={:<16} needs_key={:<5} key={} url={}",
                p.name,
                p.wire.as_str(),
                p.needs_key,
                p.key_name,
                p.url,
            );
        }
    }
    Ok(())
}

fn cmd_provider(data_dir: &Path, args: &[String]) -> Result<()> {
    let Some((sub, rest)) = args.split_first() else {
        bail!("usage: arity provider set <name> <url> --wire chat_completions|responses [--needs-key] [--key-name <k>]");
    };
    if sub != "set" {
        bail!("unknown provider subcommand '{sub}'; expected 'set'");
    }

    let mut name: Option<String> = None;
    let mut url: Option<String> = None;
    let mut wire: Option<WireCodec> = None;
    let mut needs_key = false;
    let mut key_name: Option<String> = None;
    let mut session_header: Option<String> = None;
    let mut models: Vec<String> = Vec::new();
    let mut as_json = false;

    let mut i = 0;
    while i < rest.len() {
        match flag_name(&rest[i]) {
            "--wire" => {
                let raw = take_flag_value(rest, &mut i, "--wire")?;
                wire = Some(WireCodec::parse(&raw)?);
            }
            "--needs-key" => needs_key = true,
            "--key-name" => key_name = Some(take_flag_value(rest, &mut i, "--key-name")?),
            "--session-header" => {
                session_header = Some(take_flag_value(rest, &mut i, "--session-header")?)
            }
            "--models" => {
                let raw = take_flag_value(rest, &mut i, "--models")?;
                models = raw
                    .split(',')
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .collect();
            }
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for provider set"),
            _ => {
                if name.is_none() {
                    name = Some(rest[i].clone());
                } else if url.is_none() {
                    url = Some(rest[i].clone());
                } else {
                    bail!("unexpected positional argument '{}'", rest[i]);
                }
            }
        }
        i += 1;
    }

    let name = name.ok_or_else(|| anyhow!("provider set requires <name> <url> --wire <codec>"))?;
    let url = url.ok_or_else(|| anyhow!("provider set requires <name> <url> --wire <codec>"))?;
    let wire = wire.ok_or_else(|| anyhow!("provider set requires --wire chat_completions|responses"))?;

    let config = ProviderConfig {
        key_name: key_name.unwrap_or_else(|| name.clone()),
        name,
        url,
        wire,
        needs_key,
        session_header,
        models,
    };

    let mut store = Store::open(data_dir)?;
    store.upsert_provider(&config)?;

    if as_json {
        println!("{}", serde_json::to_string_pretty(&config)?);
    } else {
        println!(
            "saved provider={} wire={} url={} needs_key={} key_name={}",
            config.name,
            config.wire.as_str(),
            config.url,
            config.needs_key,
            config.key_name,
        );
    }
    Ok(())
}

fn cmd_limits(data_dir: &Path, args: &[String]) -> Result<()> {
    let mut provider: Option<String> = None;
    let mut as_json = false;

    for arg in args {
        match arg.as_str() {
            "--json" => as_json = true,
            other if other.starts_with('-') => bail!("unknown flag '{other}' for limits"),
            _ => {
                if provider.is_none() {
                    provider = Some(arg.clone());
                } else {
                    bail!("unexpected positional argument '{arg}'");
                }
            }
        }
    }

    let store = Store::open(data_dir)?;
    let limits = store.list_rate_limits(provider.as_deref())?;
    if as_json {
        println!("{}", serde_json::to_string_pretty(&limits)?);
    } else if limits.is_empty() {
        println!("no rate limits recorded");
    } else {
        for r in &limits {
            let reset = r
                .reset_secs
                .map_or_else(|| "-".into(), |s| format!("{s}s"));
            println!(
                "{:<14} {:<18} remaining={}/{} reset={} observed_ms={}",
                r.provider, r.limit_name, r.remaining, r.limit_val, reset, r.observed_ms,
            );
        }
    }
    Ok(())
}

fn cmd_key(data_dir: &Path, args: &[String]) -> Result<()> {
    let Some((sub, rest)) = args.split_first() else {
        bail!("usage: arity key set <name> [<key>] | arity key rm <name>");
    };
    match sub.as_str() {
        "set" => {
            let name = rest
                .first()
                .ok_or_else(|| anyhow!("key set requires <name> [<key>]"))?;
            let key = if let Some(k) = rest.get(1) {
                k.clone()
            } else {
                let mut line = String::new();
                io::stdin().read_line(&mut line)?;
                line.trim().to_string()
            };
            ensure!(!key.is_empty(), "key must not be empty; use `arity key rm {name}` to remove");
            secrets::keys::set(data_dir, name, &key)?;
            println!("saved key '{name}'");
            Ok(())
        }
        "rm" => {
            let name = rest
                .first()
                .ok_or_else(|| anyhow!("key rm requires <name>"))?;
            secrets::keys::set(data_dir, name, "")?;
            println!("removed key '{name}'");
            Ok(())
        }
        other => bail!("unknown key subcommand '{other}'; expected 'set' or 'rm'"),
    }
}
