//! Path-jailed read/write/edit tools and process-group-reaped `bash` execution.

use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::time::{Duration, Instant};

use anyhow::{Result, anyhow, bail};
use serde_json::{Value, json};

use crate::types::{ToolReceiptStatus, ToolSpec};

/// Maximum size of a single tool output returned to the model/journal.
const MAX_TOOL_OUTPUT_BYTES: usize = 64 * 1024;
/// Maximum bytes captured per stream from a single `bash` command.
const MAX_CAPTURE_BYTES: usize = 4 * 1024 * 1024;

/// Folders skipped during directory listing and recursive search.
const IGNORED_DIRS: [&str; 4] = [".git", "target", ".build", "node_modules"];

const MAX_ENTRIES: usize = 200;
const DEFAULT_DEPTH: u64 = 2;
const MAX_DEPTH: u64 = 8;
const DEFAULT_LINES: usize = 200;
const MAX_LINES: usize = 400;
const MAX_MATCHES: usize = 80;
const MAX_LINE_CHARS: usize = 400;
const MAX_SEARCH_BYTES: u64 = 1 << 20;
const MAX_SEARCH_FILES: usize = 20_000;

const DEFAULT_TIMEOUT_SECS: f64 = 60.0;
const MAX_TIMEOUT_SECS: f64 = 300.0;
const READER_JOIN_TIMEOUT: Duration = Duration::from_millis(250);
const WATCHDOG_WAIT_TIMEOUT: Duration = Duration::from_millis(500);

static ATOMIC_TMP_SEQ: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone)]
pub struct ToolExecOutput {
    pub status: ToolReceiptStatus,
    pub output: String,
    pub exit_code: Option<i32>,
}

impl ToolExecOutput {
    fn ok(output: impl Into<String>) -> Self {
        Self {
            status: ToolReceiptStatus::Ok,
            output: output.into(),
            exit_code: None,
        }
    }

    fn err(output: impl Into<String>) -> Self {
        Self {
            status: ToolReceiptStatus::Error,
            output: output.into(),
            exit_code: None,
        }
    }
}

pub fn is_mutating_tool(name: &str) -> bool {
    matches!(name, "write_file" | "edit_file" | "bash")
}

pub fn tool_specs(has_workspace: bool) -> Vec<ToolSpec> {
    if !has_workspace {
        return Vec::new();
    }
    vec![
        ToolSpec {
            name: "list_files".into(),
            description: "List the files and folders in the attached folder, or a folder inside it. Folders end with /. Version control, build output and dependencies are left out.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "A folder, relative to the attached folder. Left out, the folder itself." },
                    "max_depth": { "type": "integer", "description": "How many levels down to list; 2 by default." }
                }
            }),
            mutating: false,
        },
        ToolSpec {
            name: "read_file".into(),
            description: "Read a text file in the attached folder, with numbered lines: 200 lines unless you ask for a range, at most 400 at a time. Says how many lines the file has.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Relative to the attached folder." },
                    "start_line": { "type": "integer", "description": "The first line, counting from 1." },
                    "end_line": { "type": "integer", "description": "The last line." }
                },
                "required": ["path"]
            }),
            mutating: false,
        },
        ToolSpec {
            name: "search_files".into(),
            description: "Find text in the files of the attached folder, ignoring case. Each match is path:line: text; at most 80.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "The text to find." },
                    "path": { "type": "string", "description": "A folder or file to search, relative to the attached folder. Left out, all of it." }
                },
                "required": ["query"]
            }),
            mutating: false,
        },
        ToolSpec {
            name: "write_file".into(),
            description: "Create or overwrite a UTF-8 text file in the attached folder, creating parent directories as needed. Paths are relative to the attached folder; .git cannot be modified.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "File path, relative to the attached folder." },
                    "content": { "type": "string", "description": "Full UTF-8 text content to write to the file." }
                },
                "required": ["path", "content"]
            }),
            mutating: true,
        },
        ToolSpec {
            name: "edit_file".into(),
            description: "Edit an existing UTF-8 text file in the attached folder by exact string replacement. Pass old_text and new_text (with optional replace_all), or pass edits as an array of {old_text, new_text} replacements matched against the original file.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "File path, relative to the attached folder." },
                    "old_text": { "type": "string", "description": "Exact text to replace; must appear uniquely unless replace_all is true." },
                    "new_text": { "type": "string", "description": "Replacement text." },
                    "replace_all": { "type": "boolean", "description": "Replace every occurrence of old_text instead of requiring a unique match. False by default." },
                    "edits": {
                        "type": "array",
                        "description": "Multiple non-overlapping exact replacements matched against the original file.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": { "type": "string", "description": "Exact text to replace; must match uniquely." },
                                "new_text": { "type": "string", "description": "Replacement text." }
                            },
                            "required": ["old_text", "new_text"]
                        }
                    }
                },
                "required": ["path"]
            }),
            mutating: true,
        },
        ToolSpec {
            name: "bash".into(),
            description: "Run a shell command in the attached folder and return its combined stdout and stderr.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "command": { "type": "string", "description": "Shell command to run in the attached folder." },
                    "timeout": { "type": "number", "description": "Timeout in seconds; 60 by default, at most 300." }
                },
                "required": ["command"]
            }),
            mutating: true,
        },
    ]
}

/// Send `SIGKILL` to an entire Unix process group (`-pgid`) when `pgid > 1`.
pub fn reap_process_group(pgid: i64) {
    #[cfg(unix)]
    if pgid > 1 {
        let own_pgrp = unsafe { libc::getpgrp() } as i64;
        if pgid != own_pgrp {
            unsafe {
                libc::kill(-(pgid as libc::pid_t), libc::SIGKILL);
            }
        }
    }
    #[cfg(not(unix))]
    let _ = pgid;
}

/// Execute a single tool call jailed to `work_dir`.
pub fn execute_tool(
    work_dir: Option<&Path>,
    name: &str,
    arguments_json: &str,
    on_spawn_pgid: &mut dyn FnMut(i64),
    should_abort: &mut dyn FnMut() -> bool,
) -> ToolExecOutput {
    if should_abort() {
        return ToolExecOutput {
            status: ToolReceiptStatus::Aborted,
            output: "tool execution aborted".into(),
            exit_code: None,
        };
    }

    let Some(root) = work_dir else {
        return ToolExecOutput::err(
            "error: no folder is attached to this thread, so workspace tools are unavailable",
        );
    };

    let args: Value = if arguments_json.trim().is_empty() {
        json!({})
    } else {
        match serde_json::from_str(arguments_json) {
            Ok(v @ Value::Object(_)) => v,
            Ok(_) => {
                return ToolExecOutput::err("error: tool arguments must be a JSON object");
            }
            Err(e) => {
                return ToolExecOutput::err(format!("error: invalid tool arguments JSON: {e}"));
            }
        }
    };

    let root_real = match fs::canonicalize(root) {
        Ok(r) => r,
        Err(e) => {
            return ToolExecOutput::err(format!(
                "error: the attached folder {} cannot be read: {e}",
                root.display()
            ));
        }
    };

    let result = match name {
        "list_files" => execute_list(&root_real, &args).map(ToolExecOutput::ok),
        "read_file" => execute_read(&root_real, &args).map(ToolExecOutput::ok),
        "search_files" => execute_search(&root_real, &args).map(ToolExecOutput::ok),
        "write_file" => execute_write(&root_real, &args).map(ToolExecOutput::ok),
        "edit_file" => execute_edit(&root_real, &args).map(ToolExecOutput::ok),
        "bash" => return execute_bash(&root_real, &args, on_spawn_pgid, should_abort),
        other => Err(anyhow!("unknown tool {other}")),
    };

    match result {
        Ok(mut out) => {
            out.output = bound_output(out.output, MAX_TOOL_OUTPUT_BYTES);
            out
        }
        Err(e) => ToolExecOutput::err(bound_output(format!("error: {e:#}"), MAX_TOOL_OUTPUT_BYTES)),
    }
}

// ---------------------------------------------------------------------------
// Path jailing & atomic writes
// ---------------------------------------------------------------------------

fn is_git_segment(seg: &std::ffi::OsStr) -> bool {
    seg.to_string_lossy().eq_ignore_ascii_case(".git")
}

fn contains_git_component(path: &Path) -> bool {
    path.components().any(|c| match c {
        Component::Normal(seg) => is_git_segment(seg),
        _ => false,
    })
}

fn is_ignored_dir_name(name: &std::ffi::OsStr) -> bool {
    let s = name.to_string_lossy();
    s.eq_ignore_ascii_case(".git") || IGNORED_DIRS.contains(&s.as_ref())
}

fn shown_rel(root_real: &Path, path: &Path) -> String {
    let rel = path
        .strip_prefix(root_real)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned();
    if rel.is_empty() { ".".into() } else { rel }
}

/// Resolve a readable path jailed strictly inside `root_real`.
///
/// Rejects `.git` (case-insensitively) both before and after symlink resolution,
/// rejects `..` escaping `root_real`, rejects absolute paths outside `root_real`,
/// and rejects symlinks resolving outside `root_real`.
fn resolve_readable(root_real: &Path, path: Option<&str>) -> Result<PathBuf> {
    let Some(raw) = path.map(str::trim).filter(|p| !p.is_empty() && *p != ".") else {
        return Ok(root_real.to_path_buf());
    };

    let rel_path = Path::new(raw);
    if rel_path.is_absolute() {
        bail!("{raw} is outside the attached folder; paths must be relative to the attached folder");
    }

    let mut depth: isize = 0;
    for comp in rel_path.components() {
        match comp {
            Component::Prefix(_) | Component::RootDir => {
                bail!("{raw} is outside the attached folder; paths must be relative to the attached folder");
            }
            Component::CurDir => {}
            Component::ParentDir => {
                depth -= 1;
                if depth < 0 {
                    bail!("{raw} is outside the attached folder; only paths inside it can be read");
                }
            }
            Component::Normal(seg) => {
                if is_git_segment(seg) {
                    bail!("{raw} targets .git, which cannot be accessed");
                }
                depth += 1;
            }
        }
    }

    let joined = root_real.join(rel_path);
    let real = fs::canonicalize(&joined).map_err(|e| {
        if fs::symlink_metadata(&joined).is_ok() {
            anyhow!("{raw} traverses a broken symlink: {e}")
        } else {
            anyhow!("no file or folder {raw} in the attached folder")
        }
    })?;
    if !real.starts_with(root_real) {
        bail!("{raw} is outside the attached folder; only paths inside it can be read");
    }
    if let Ok(inside) = real.strip_prefix(root_real)
        && contains_git_component(inside)
    {
        bail!("{raw} resolves into .git, which cannot be accessed");
    }
    Ok(real)
}

/// Resolve a writable path jailed strictly inside `root_real`.
///
/// Rejects empty paths, absolute paths, `..` traversal, `.git` paths
/// (case-insensitively), and any symlink (including dangling symlinks or
/// symlinks in existing ancestors) pointing outside `root_real` or into `.git`.
fn resolve_writable(root_real: &Path, rel: &str) -> Result<PathBuf> {
    let rel = rel.trim();
    if rel.is_empty() {
        bail!("path must not be empty");
    }
    let rel_path = Path::new(rel);
    if rel_path.is_absolute() {
        bail!("{rel} is an absolute path; paths must be relative to the attached folder");
    }

    let mut clean_rel = PathBuf::new();
    for comp in rel_path.components() {
        match comp {
            Component::Prefix(_) | Component::RootDir => {
                bail!("{rel} is an absolute path; paths must be relative to the attached folder");
            }
            Component::ParentDir => {
                bail!("{rel} contains '..'; paths must stay inside the attached folder");
            }
            Component::CurDir => {}
            Component::Normal(seg) => {
                if is_git_segment(seg) {
                    bail!("{rel} targets .git, which cannot be modified");
                }
                clean_rel.push(seg);
            }
        }
    }
    if clean_rel.as_os_str().is_empty() {
        bail!("path must name a file inside the attached folder");
    }

    let candidate = root_real.join(&clean_rel);
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    let mut cursor = candidate.as_path();

    loop {
        match fs::symlink_metadata(cursor) {
            Ok(meta) => {
                let real = fs::canonicalize(cursor).map_err(|e| {
                    if meta.file_type().is_symlink() {
                        anyhow!("{rel} traverses a broken symlink: {e}")
                    } else {
                        anyhow!("cannot resolve {rel}: {e}")
                    }
                })?;
                if !real.starts_with(root_real) {
                    bail!("{rel} resolves outside the attached folder");
                }
                if let Ok(inside) = real.strip_prefix(root_real)
                    && contains_git_component(inside)
                {
                    bail!("{rel} resolves into .git, which cannot be modified");
                }
                let mut resolved = real;
                for seg in tail.into_iter().rev() {
                    resolved.push(seg);
                }
                if let Ok(inside) = resolved.strip_prefix(root_real)
                    && contains_git_component(inside)
                {
                    bail!("{rel} resolves into .git, which cannot be modified");
                }
                return Ok(resolved);
            }
            Err(_) => {
                let Some(name) = cursor.file_name() else {
                    bail!("{rel} resolves outside the attached folder");
                };
                tail.push(name.to_os_string());
                let Some(parent) = cursor.parent() else {
                    bail!("{rel} resolves outside the attached folder");
                };
                cursor = parent;
            }
        }
    }
}

struct TempFileGuard {
    path: PathBuf,
    armed: bool,
}

impl TempFileGuard {
    fn new(path: PathBuf) -> Self {
        Self { path, armed: true }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for TempFileGuard {
    fn drop(&mut self) {
        if self.armed {
            let _ = fs::remove_file(&self.path);
        }
    }
}

/// Atomically write `bytes` to `target` via temp-file + rename in the target
/// directory, preserving existing file permissions when `target` already exists.
fn atomic_write(target: &Path, bytes: &[u8]) -> Result<()> {
    let parent = target
        .parent()
        .ok_or_else(|| anyhow!("target path has no parent: {}", target.display()))?;
    let existing_perms = fs::metadata(target).ok().map(|m| m.permissions());

    let seq = ATOMIC_TMP_SEQ.fetch_add(1, Ordering::Relaxed);
    let tmp_path = parent.join(format!(".arity-tmp-{}-{seq}", std::process::id()));
    let mut guard = TempFileGuard::new(tmp_path.clone());

    {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp_path)
            .map_err(|e| anyhow!("create temp file {}: {e}", tmp_path.display()))?;
        file.write_all(bytes)?;
        file.flush()?;
    }

    if let Some(perms) = existing_perms {
        fs::set_permissions(&tmp_path, perms)?;
    }

    fs::rename(&tmp_path, target)?;
    guard.disarm();
    Ok(())
}

// ---------------------------------------------------------------------------
// Read-only file tools: list_files, read_file, search_files
// ---------------------------------------------------------------------------

fn parse_optional_u64(args: &Value, key: &str) -> Result<Option<u64>> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => n
            .as_u64()
            .map(Some)
            .ok_or_else(|| anyhow!("{key} must be a positive whole number")),
        Some(Value::String(s)) => s
            .trim()
            .parse::<u64>()
            .map(Some)
            .map_err(|_| anyhow!("{key} must be a positive whole number")),
        Some(_) => bail!("{key} must be a positive whole number"),
    }
}

fn children(root_real: &Path, dir: &Path) -> Vec<(PathBuf, bool)> {
    let Ok(read) = fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<(PathBuf, bool)> = read
        .flatten()
        .filter(|e| !is_ignored_dir_name(&e.file_name()))
        .filter_map(|e| {
            let kind = e.file_type().ok()?;
            if kind.is_symlink() {
                let real = fs::canonicalize(e.path()).ok()?;
                if !real.starts_with(root_real) {
                    return None;
                }
                if let Ok(inside) = real.strip_prefix(root_real)
                    && contains_git_component(inside)
                {
                    return None;
                }
                // Shown under its own name, but never walked into (prevents loops).
                return Some((e.path(), false));
            }
            Some((e.path(), kind.is_dir()))
        })
        .collect();
    out.sort();
    out
}

fn execute_list(root_real: &Path, args: &Value) -> Result<String> {
    let path = args.get("path").and_then(Value::as_str);
    let depth = parse_optional_u64(args, "max_depth")?
        .unwrap_or(DEFAULT_DEPTH)
        .clamp(1, MAX_DEPTH);

    let start = resolve_readable(root_real, path)?;
    if !start.is_dir() {
        bail!(
            "{} is a file; read it with read_file",
            shown_rel(root_real, &start)
        );
    }

    let mut lines = Vec::new();
    let mut more = 0usize;
    let mut stack: Vec<(PathBuf, bool, u64)> = children(root_real, &start)
        .into_iter()
        .rev()
        .map(|(p, d)| (p, d, 1))
        .collect();

    while let Some((p, is_dir, level)) = stack.pop() {
        if lines.len() >= MAX_ENTRIES {
            more += 1;
            continue;
        }
        let name = shown_rel(root_real, &p);
        lines.push(if is_dir { format!("{name}/") } else { name });
        if is_dir && level < depth {
            stack.extend(
                children(root_real, &p)
                    .into_iter()
                    .rev()
                    .map(|(c, d)| (c, d, level + 1)),
            );
        }
    }

    if lines.is_empty() {
        return Ok(format!("{} is empty", shown_rel(root_real, &start)));
    }
    if more > 0 {
        lines.push(format!(
            "(stopped at {MAX_ENTRIES} entries; {more} more not shown: list a folder inside, or a smaller max_depth)"
        ));
    }
    Ok(lines.join("\n"))
}

fn text_of(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    if bytes[..bytes.len().min(8192)].contains(&0) {
        return None;
    }
    String::from_utf8(bytes).ok()
}

fn cut_line(line: &str) -> String {
    match line.char_indices().nth(MAX_LINE_CHARS) {
        Some((i, _)) => format!("{} …", &line[..i]),
        None => line.to_string(),
    }
}

fn execute_read(root_real: &Path, args: &Value) -> Result<String> {
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .ok_or_else(|| anyhow!("read_file needs a path"))?;
    let start = parse_optional_u64(args, "start_line")?;
    let end = parse_optional_u64(args, "end_line")?;

    let file = resolve_readable(root_real, Some(path))?;
    let name = shown_rel(root_real, &file);
    if file.is_dir() {
        bail!("{name} is a folder; list it with list_files");
    }
    let text = text_of(&file)
        .ok_or_else(|| anyhow!("{name} is a binary file or not UTF-8 text, so it cannot be read"))?;
    let lines: Vec<&str> = text.lines().collect();
    let total = lines.len();
    if total == 0 {
        return Ok(format!("{name} is empty"));
    }

    let start_idx = start.unwrap_or(1).max(1) as usize;
    if start_idx > total {
        bail!("{name} has {total} lines; start_line {start_idx} is past the end");
    }
    let wanted_end = end.map_or(start_idx + DEFAULT_LINES - 1, |e| e as usize);
    if wanted_end < start_idx {
        bail!("end_line must not be before start_line");
    }
    let end_idx = wanted_end.min(start_idx + MAX_LINES - 1).min(total);

    let mut out = vec![format!("{name}: lines {start_idx}-{end_idx} of {total}")];
    out.extend((start_idx..=end_idx).map(|n| format!("{n:>5}  {}", cut_line(lines[n - 1]))));
    if end_idx < total {
        out.push(format!(
            "(more after line {end_idx}: read on with start_line {})",
            end_idx + 1
        ));
    }
    Ok(out.join("\n"))
}

fn execute_search(root_real: &Path, args: &Value) -> Result<String> {
    let query = args.get("query").and_then(Value::as_str).unwrap_or_default();
    if query.trim().is_empty() {
        bail!("search_files needs a query");
    }
    let path = args.get("path").and_then(Value::as_str);

    let start = resolve_readable(root_real, path)?;
    let needle = query.to_lowercase();
    let mut matches = Vec::new();
    let mut more = false;
    let mut seen = 0usize;
    let mut stack = vec![(start.clone(), start.is_dir())];

    'walk: while let Some((p, is_dir)) = stack.pop() {
        if is_dir {
            stack.extend(children(root_real, &p).into_iter().rev());
            continue;
        }
        seen += 1;
        if seen > MAX_SEARCH_FILES {
            more = true;
            break;
        }
        if fs::metadata(&p).map_or(true, |m| m.len() > MAX_SEARCH_BYTES) {
            continue;
        }
        let Some(text) = text_of(&p) else { continue };
        for (i, line) in text.lines().enumerate() {
            if line.to_lowercase().contains(&needle) {
                if matches.len() >= MAX_MATCHES {
                    more = true;
                    break 'walk;
                }
                matches.push(format!(
                    "{}:{}: {}",
                    shown_rel(root_real, &p),
                    i + 1,
                    cut_line(line.trim())
                ));
            }
        }
    }

    if matches.is_empty() {
        return Ok(format!(
            "no matches for {query:?} in {}",
            shown_rel(root_real, &start)
        ));
    }
    if more {
        matches.push(format!(
            "(stopped at {} matches: search a folder inside, or a longer query)",
            matches.len()
        ));
    }
    Ok(matches.join("\n"))
}

// ---------------------------------------------------------------------------
// Mutating file tools: write_file, edit_file
// ---------------------------------------------------------------------------

fn execute_write(root_real: &Path, args: &Value) -> Result<String> {
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("write_file needs a path"))?;
    let content = args
        .get("content")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("write_file needs content"))?;

    let target = resolve_writable(root_real, path)?;
    let shown = shown_rel(root_real, &target);

    if target.is_dir() {
        bail!("{shown} is a directory");
    }
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| anyhow!("cannot create parent directories for {shown}: {e}"))?;
    }
    atomic_write(&target, content.as_bytes())
        .map_err(|e| anyhow!("cannot write {shown}: {e}"))?;

    Ok(format!("wrote {shown} ({} bytes)", content.len()))
}

fn extract_str<'a>(obj: &'a Value, snake: &str, camel: &str) -> Result<Option<&'a str>> {
    match obj.get(snake) {
        Some(Value::Null) | None => {}
        Some(Value::String(s)) => return Ok(Some(s.as_str())),
        Some(_) => bail!("{snake} must be a string"),
    }
    match obj.get(camel) {
        Some(Value::Null) | None => Ok(None),
        Some(Value::String(s)) => Ok(Some(s.as_str())),
        Some(_) => bail!("{camel} must be a string"),
    }
}

fn extract_bool(obj: &Value, snake: &str, camel: &str) -> Result<bool> {
    match obj.get(snake) {
        Some(Value::Null) | None => {}
        Some(Value::Bool(b)) => return Ok(*b),
        Some(_) => bail!("{snake} must be a boolean"),
    }
    match obj.get(camel) {
        Some(Value::Null) | None => Ok(false),
        Some(Value::Bool(b)) => Ok(*b),
        Some(_) => bail!("{camel} must be a boolean"),
    }
}

struct EditSpec {
    old_text: String,
    new_text: String,
}

fn execute_edit(root_real: &Path, args: &Value) -> Result<String> {
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("edit_file needs a path"))?;

    let target = resolve_writable(root_real, path)?;
    let shown = shown_rel(root_real, &target);

    let meta = fs::metadata(&target)
        .map_err(|_| anyhow!("no file {shown} in the attached folder"))?;
    if !meta.is_file() {
        bail!("{shown} is not a regular file");
    }

    let old_single = extract_str(args, "old_text", "oldText")?;
    let new_single = extract_str(args, "new_text", "newText")?;
    let replace_all = extract_bool(args, "replace_all", "replaceAll")?;

    let edits_arg = match args.get("edits") {
        None | Some(Value::Null) => None,
        Some(Value::Array(arr)) => Some(arr),
        Some(_) => bail!("edits must be an array"),
    };

    let bytes = fs::read(&target).map_err(|e| anyhow!("cannot read {shown}: {e}"))?;
    if bytes[..bytes.len().min(8192)].contains(&0) {
        bail!("{shown} is a binary file and cannot be edited");
    }
    let raw = String::from_utf8(bytes)
        .map_err(|_| anyhow!("{shown} is not UTF-8 text and cannot be edited"))?;
    let uses_crlf = raw.contains("\r\n");
    let normalized = if uses_crlf {
        raw.replace("\r\n", "\n")
    } else {
        raw
    };

    let (updated, count) = if let Some(arr) = edits_arg {
        if old_single.is_some() || new_single.is_some() {
            bail!("pass either old_text/new_text or edits, not both");
        }
        if arr.is_empty() {
            bail!("edits must not be empty");
        }
        let mut parsed = Vec::with_capacity(arr.len());
        for (i, item) in arr.iter().enumerate() {
            if !item.is_object() {
                bail!("edits[{i}] must be an object with old_text and new_text");
            }
            let old = extract_str(item, "old_text", "oldText")?
                .ok_or_else(|| anyhow!("edits[{i}] needs old_text"))?;
            let new = extract_str(item, "new_text", "newText")?
                .ok_or_else(|| anyhow!("edits[{i}] needs new_text"))?;
            parsed.push(EditSpec {
                old_text: old.replace("\r\n", "\n"),
                new_text: new.replace("\r\n", "\n"),
            });
        }
        apply_multi_edits(&normalized, &parsed, &shown)?
    } else {
        let old = old_single
            .ok_or_else(|| anyhow!("edit_file needs old_text and new_text (or edits)"))?;
        let new = new_single.ok_or_else(|| anyhow!("edit_file needs new_text"))?;
        let old = old.replace("\r\n", "\n");
        let new = new.replace("\r\n", "\n");
        if old.is_empty() {
            bail!("old_text must not be empty");
        }
        let matches = normalized.matches(&old).count();
        if matches == 0 {
            bail!("old_text not found in {shown}");
        }
        if matches > 1 && !replace_all {
            bail!(
                "old_text matched {matches} times in {shown}; include more surrounding context or pass replace_all: true"
            );
        }
        let result = if replace_all {
            normalized.replace(&old, &new)
        } else {
            normalized.replacen(&old, &new, 1)
        };
        (result, matches)
    };

    let lines = updated.lines().count();
    let final_text = if uses_crlf {
        updated.replace('\n', "\r\n")
    } else {
        updated
    };
    atomic_write(&target, final_text.as_bytes())
        .map_err(|e| anyhow!("cannot write {shown}: {e}"))?;

    Ok(format!("edited {shown} ({count} replacement(s), {lines} lines)"))
}

fn apply_multi_edits(normalized: &str, edits: &[EditSpec], shown: &str) -> Result<(String, usize)> {
    struct Span<'a> {
        start: usize,
        end: usize,
        replacement: &'a str,
        index: usize,
    }

    let mut spans = Vec::with_capacity(edits.len());
    for (index, edit) in edits.iter().enumerate() {
        if edit.old_text.is_empty() {
            bail!("edits[{index}].old_text must not be empty");
        }
        let mut iter = normalized.match_indices(&edit.old_text);
        let Some((start, _)) = iter.next() else {
            bail!("edits[{index}].old_text not found in {shown}");
        };
        if iter.next().is_some() {
            let total = normalized.matches(&edit.old_text).count();
            bail!(
                "edits[{index}].old_text matched {total} times in {shown}; each edit must match uniquely"
            );
        }
        spans.push(Span {
            start,
            end: start + edit.old_text.len(),
            replacement: &edit.new_text,
            index,
        });
    }

    spans.sort_by_key(|s| (s.start, s.end));
    for pair in spans.windows(2) {
        if pair[1].start < pair[0].end {
            bail!(
                "edits[{}] and edits[{}] overlap in {shown}",
                pair[0].index,
                pair[1].index
            );
        }
    }

    let mut updated = normalized.to_string();
    for span in spans.iter().rev() {
        updated.replace_range(span.start..span.end, span.replacement);
    }
    Ok((updated, spans.len()))
}

// ---------------------------------------------------------------------------
// Output truncation helpers
// ---------------------------------------------------------------------------

fn bound_output(text: String, max_bytes: usize) -> String {
    if text.len() <= max_bytes {
        return text;
    }
    let mut end = max_bytes;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    let omitted = text.len() - end;
    format!("{}\n[output truncated: {omitted} bytes omitted]", &text[..end])
}

fn truncate_tail(text: String, max_bytes: usize) -> String {
    if text.len() <= max_bytes {
        return text;
    }
    let header_reserve =
        format!("[earlier output truncated: {} bytes omitted]\n", text.len()).len();
    let tail_budget = max_bytes.saturating_sub(header_reserve);
    let mut start = text.len().saturating_sub(tail_budget);
    while start < text.len() && !text.is_char_boundary(start) {
        start += 1;
    }
    format!(
        "[earlier output truncated: {start} bytes omitted]\n{}",
        &text[start..]
    )
}

fn combine_streams(stdout: &str, stderr: &str) -> String {
    match (stdout.is_empty(), stderr.is_empty()) {
        (true, true) => String::new(),
        (false, true) => stdout.to_string(),
        (true, false) => stderr.to_string(),
        (false, false) => {
            let out = stdout.strip_suffix('\n').unwrap_or(stdout);
            format!("{out}\n{stderr}")
        }
    }
}

// ---------------------------------------------------------------------------
// Process-group-reaped bash with parent-death pipe-EOF watchdog
// ---------------------------------------------------------------------------

fn parse_timeout(args: &Value) -> Result<f64> {
    let raw = match args.get("timeout") {
        None | Some(Value::Null) => return Ok(DEFAULT_TIMEOUT_SECS),
        Some(Value::Number(n)) => n
            .as_f64()
            .ok_or_else(|| anyhow!("timeout must be a positive number of seconds"))?,
        Some(Value::String(s)) => s
            .trim()
            .parse::<f64>()
            .map_err(|_| anyhow!("timeout must be a positive number of seconds"))?,
        Some(_) => bail!("timeout must be a positive number of seconds"),
    };
    if !raw.is_finite() || raw <= 0.0 {
        bail!("timeout must be a positive number of seconds");
    }
    Ok(raw.min(MAX_TIMEOUT_SECS))
}

fn format_secs(secs: f64) -> String {
    if secs.fract() == 0.0 {
        format!("{}", secs as u64)
    } else {
        format!("{secs}")
    }
}

fn spawn_shell(root_real: &Path, command: &str) -> Result<Child> {
    let build = |shell: &str| {
        let mut cmd = Command::new(shell);
        cmd.arg("-c")
            .arg(command)
            .current_dir(root_real)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        cmd
    };

    match build("bash").spawn() {
        Ok(child) => Ok(child),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => build("sh")
            .spawn()
            .map_err(|e2| anyhow!("failed to spawn shell: {e2}")),
        Err(e) => Err(anyhow!("failed to spawn bash: {e}")),
    }
}

/// Parent-death pipe-EOF watchdog guard.
///
/// Created *immediately after* spawning `bash`, so `bash` can never inherit
/// `write_fd`. Only this process holds `write_fd` (with `FD_CLOEXEC`), so if
/// this process dies abruptly (`SIGKILL`), the kernel closes `write_fd`, the
/// watchdog's `cat >/dev/null` receives EOF immediately, and the watchdog sends
/// `kill -9 -<pgid>` to reap the entire `bash` process group.
struct WatchdogGuard {
    #[cfg(unix)]
    write_fd: Option<std::os::unix::io::OwnedFd>,
    watchdog: Option<Child>,
}

impl WatchdogGuard {
    #[cfg(unix)]
    fn spawn(pgid: i64) -> Self {
        use std::os::unix::io::{FromRawFd, OwnedFd};
        use std::os::unix::process::CommandExt;

        let mut fds = [0 as libc::c_int; 2];
        if unsafe { libc::pipe(fds.as_mut_ptr()) } != 0 {
            return Self {
                write_fd: None,
                watchdog: None,
            };
        }

        // Set FD_CLOEXEC on both ends before spawning the watchdog.
        // Command::stdin(Stdio::from(read_fd)) dup2s read_fd to fd 0 in the child
        // (clearing CLOEXEC on fd 0), while write_fd stays closed in the child.
        unsafe {
            libc::fcntl(fds[0], libc::F_SETFD, libc::FD_CLOEXEC);
            libc::fcntl(fds[1], libc::F_SETFD, libc::FD_CLOEXEC);
        }

        let read_fd = unsafe { OwnedFd::from_raw_fd(fds[0]) };
        let write_fd = unsafe { OwnedFd::from_raw_fd(fds[1]) };

        let mut cmd = Command::new("/bin/sh");
        cmd.args([
            "-c",
            r#"cat >/dev/null; kill -9 -"$1" 2>/dev/null || true"#,
            "_",
            &pgid.to_string(),
        ])
        .stdin(Stdio::from(read_fd))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0);

        let watchdog = cmd.spawn().ok();
        // `cmd` (and thus the parent's copy of `read_fd`) is dropped here.
        drop(cmd);

        Self {
            write_fd: watchdog.as_ref().map(|_| write_fd),
            watchdog,
        }
    }

    #[cfg(not(unix))]
    fn spawn(_pgid: i64) -> Self {
        Self { watchdog: None }
    }

    fn shutdown(&mut self) {
        #[cfg(unix)]
        {
            // Closing write_fd delivers immediate EOF to the watchdog's `cat`.
            drop(self.write_fd.take());
        }
        if let Some(mut wd) = self.watchdog.take() {
            let deadline = Instant::now() + WATCHDOG_WAIT_TIMEOUT;
            loop {
                match wd.try_wait() {
                    Ok(Some(_)) => break,
                    Ok(None) => {
                        if Instant::now() >= deadline {
                            let _ = wd.kill();
                            let _ = wd.wait();
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(_) => {
                        let _ = wd.kill();
                        let _ = wd.wait();
                        break;
                    }
                }
            }
        }
    }
}

impl Drop for WatchdogGuard {
    fn drop(&mut self) {
        self.shutdown();
    }
}

struct StreamReader {
    buf: Arc<Mutex<Vec<u8>>>,
    done_rx: mpsc::Receiver<()>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl StreamReader {
    fn spawn<R: Read + Send + 'static>(pipe: Option<R>) -> Self {
        let buf = Arc::new(Mutex::new(Vec::new()));
        let buf_clone = Arc::clone(&buf);
        let (done_tx, done_rx) = mpsc::channel();
        let handle = std::thread::spawn(move || {
            if let Some(mut reader) = pipe {
                let mut chunk = [0u8; 8192];
                loop {
                    match reader.read(&mut chunk) {
                        Ok(0) => break,
                        Ok(n) => {
                            if let Ok(mut guard) = buf_clone.lock() {
                                if guard.len() + n <= MAX_CAPTURE_BYTES {
                                    guard.extend_from_slice(&chunk[..n]);
                                } else {
                                    // Keep rolling tail within MAX_CAPTURE_BYTES.
                                    let combined_len = guard.len() + n;
                                    let drop_bytes = combined_len - MAX_CAPTURE_BYTES;
                                    if drop_bytes >= guard.len() {
                                        guard.clear();
                                        let keep_from = n.saturating_sub(MAX_CAPTURE_BYTES);
                                        guard.extend_from_slice(&chunk[keep_from..n]);
                                    } else {
                                        guard.drain(..drop_bytes);
                                        guard.extend_from_slice(&chunk[..n]);
                                    }
                                }
                            }
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                        Err(_) => break,
                    }
                }
            }
            let _ = done_tx.send(());
        });
        Self {
            buf,
            done_rx,
            handle: Some(handle),
        }
    }

    fn collect_bounded(mut self, timeout: Duration) -> Vec<u8> {
        if self.done_rx.recv_timeout(timeout).is_ok()
            && let Some(h) = self.handle.take()
        {
            let _ = h.join();
        }
        self.buf
            .lock()
            .map(|g| g.clone())
            .unwrap_or_default()
    }
}

fn exit_status_code(status: std::process::ExitStatus) -> i32 {
    status.code().unwrap_or_else(|| {
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            status.signal().map_or(-1, |sig| 128 + sig)
        }
        #[cfg(not(unix))]
        {
            -1
        }
    })
}

fn execute_bash(
    root_real: &Path,
    args: &Value,
    on_spawn_pgid: &mut dyn FnMut(i64),
    should_abort: &mut dyn FnMut() -> bool,
) -> ToolExecOutput {
    let Some(command) = args.get("command").and_then(Value::as_str) else {
        return ToolExecOutput::err("error: bash needs a command");
    };
    if command.trim().is_empty() {
        return ToolExecOutput::err("error: bash needs a non-empty command");
    }
    let timeout_secs = match parse_timeout(args) {
        Ok(t) => t,
        Err(e) => return ToolExecOutput::err(format!("error: {e:#}")),
    };

    let mut child = match spawn_shell(root_real, command) {
        Ok(c) => c,
        Err(e) => return ToolExecOutput::err(format!("error: {e:#}")),
    };
    let pgid = child.id() as i64;
    on_spawn_pgid(pgid);

    let mut watchdog = WatchdogGuard::spawn(pgid);

    let stdout_reader = StreamReader::spawn(child.stdout.take());
    let stderr_reader = StreamReader::spawn(child.stderr.take());

    enum WaitEnd {
        Exited(std::process::ExitStatus),
        TimedOut,
        Aborted,
        WaitErr(std::io::Error),
    }

    let deadline = Instant::now() + Duration::from_secs_f64(timeout_secs);
    let outcome = loop {
        if should_abort() {
            reap_process_group(pgid);
            let _ = child.kill();
            let _ = child.wait();
            break WaitEnd::Aborted;
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                // Reap any lingering background subchildren in the process group.
                reap_process_group(pgid);
                break WaitEnd::Exited(status);
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    reap_process_group(pgid);
                    let _ = child.kill();
                    let _ = child.wait();
                    break WaitEnd::TimedOut;
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(e) => {
                reap_process_group(pgid);
                let _ = child.kill();
                let _ = child.wait();
                break WaitEnd::WaitErr(e);
            }
        }
    };

    watchdog.shutdown();

    let stdout_bytes = stdout_reader.collect_bounded(READER_JOIN_TIMEOUT);
    let stderr_bytes = stderr_reader.collect_bounded(READER_JOIN_TIMEOUT);
    let stdout = String::from_utf8_lossy(&stdout_bytes);
    let stderr = String::from_utf8_lossy(&stderr_bytes);
    let combined = combine_streams(&stdout, &stderr);

    match outcome {
        WaitEnd::Aborted => {
            let prefix = "command aborted";
            let budget = MAX_TOOL_OUTPUT_BYTES.saturating_sub(prefix.len() + 1);
            let tail = truncate_tail(combined, budget);
            let output = if tail.is_empty() {
                prefix.into()
            } else {
                format!("{prefix}\n{tail}")
            };
            ToolExecOutput {
                status: ToolReceiptStatus::Aborted,
                output,
                exit_code: None,
            }
        }
        WaitEnd::TimedOut => {
            let prefix = format!("command timed out after {}s", format_secs(timeout_secs));
            let budget = MAX_TOOL_OUTPUT_BYTES.saturating_sub(prefix.len() + 1);
            let tail = truncate_tail(combined, budget);
            let output = if tail.is_empty() {
                prefix
            } else {
                format!("{prefix}\n{tail}")
            };
            ToolExecOutput {
                status: ToolReceiptStatus::Error,
                output,
                exit_code: None,
            }
        }
        WaitEnd::WaitErr(e) => ToolExecOutput {
            status: ToolReceiptStatus::Error,
            output: format!("error: failed waiting for command: {e}"),
            exit_code: None,
        },
        WaitEnd::Exited(status) => {
            if status.success() {
                let output = truncate_tail(combined, MAX_TOOL_OUTPUT_BYTES);
                ToolExecOutput {
                    status: ToolReceiptStatus::Ok,
                    output: if output.is_empty() {
                        "(no output)".into()
                    } else {
                        output
                    },
                    exit_code: Some(0),
                }
            } else {
                let code = exit_status_code(status);
                let prefix = format!("exit code {code}");
                let budget = MAX_TOOL_OUTPUT_BYTES.saturating_sub(prefix.len() + 1);
                let tail = truncate_tail(combined, budget);
                let output = if tail.is_empty() {
                    prefix
                } else {
                    format!("{prefix}\n{tail}")
                };
                ToolExecOutput {
                    status: ToolReceiptStatus::Error,
                    output,
                    exit_code: Some(code),
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicBool;

    struct TestWorkspace {
        base: PathBuf,
        root: PathBuf,
    }

    impl TestWorkspace {
        fn new(name: &str) -> Self {
            static SEQ: AtomicU64 = AtomicU64::new(0);
            let seq = SEQ.fetch_add(1, Ordering::Relaxed);
            let base = std::env::temp_dir().join(format!(
                "arity-tools-{name}-{}-{seq}",
                std::process::id()
            ));
            let _ = fs::remove_dir_all(&base);
            let root = base.join("repo");
            fs::create_dir_all(root.join("src")).unwrap();
            fs::create_dir_all(root.join(".git")).unwrap();
            fs::write(root.join(".git/config"), "secret git config\n").unwrap();
            fs::write(base.join("outside.txt"), "outside secret\n").unwrap();
            Self { base, root }
        }
    }

    impl Drop for TestWorkspace {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.base);
        }
    }

    fn run(root: &Path, name: &str, args: Value) -> ToolExecOutput {
        let mut pgid_cb = |_pgid: i64| {};
        let mut abort_cb = || false;
        execute_tool(
            Some(root),
            name,
            &args.to_string(),
            &mut pgid_cb,
            &mut abort_cb,
        )
    }

    #[test]
    fn tool_specs_and_mutating_classification() {
        assert!(tool_specs(false).is_empty());
        let specs = tool_specs(true);
        let names: Vec<(&str, bool)> = specs
            .iter()
            .map(|s| (s.name.as_str(), s.mutating))
            .collect();
        assert_eq!(
            names,
            vec![
                ("list_files", false),
                ("read_file", false),
                ("search_files", false),
                ("write_file", true),
                ("edit_file", true),
                ("bash", true),
            ]
        );
        for (name, mutating) in names {
            assert_eq!(is_mutating_tool(name), mutating);
        }
        assert!(!is_mutating_tool("unknown"));

        // Without a workspace, execute_tool returns Error.
        let mut cb = |_| {};
        let mut abort = || false;
        let res = execute_tool(None, "list_files", "{}", &mut cb, &mut abort);
        assert_eq!(res.status, ToolReceiptStatus::Error);
    }

    #[test]
    fn path_jail_rejects_escapes_symlinks_and_case_insensitive_git_for_reads_and_writes() {
        let ws = TestWorkspace::new("jail");
        fs::write(ws.root.join("README.md"), "hello world\n").unwrap();

        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(ws.base.join("outside.txt"), ws.root.join("leak.txt"))
                .unwrap();
            std::os::unix::fs::symlink(&ws.base, ws.root.join("escape_dir")).unwrap();
            std::os::unix::fs::symlink(ws.root.join(".git/config"), ws.root.join("git_link"))
                .unwrap();
            std::os::unix::fs::symlink(
                ws.base.join("dangling_outside.txt"),
                ws.root.join("dangling"),
            )
            .unwrap();
        }

        // Read tools reject .., absolute paths, symlinks escaping root, and .git (any case).
        for bad in [
            "../outside.txt",
            "src/../../outside.txt",
            ws.base.join("outside.txt").to_str().unwrap(),
            "leak.txt",
            "escape_dir/outside.txt",
            ".git",
            ".git/config",
            ".GIT/config",
            ".Git/config",
            "git_link",
        ] {
            let r = run(&ws.root, "read_file", json!({ "path": bad }));
            assert_eq!(
                r.status,
                ToolReceiptStatus::Error,
                "read_file should reject {bad:?}: {}",
                r.output
            );
        }

        for bad_dir in ["..", "escape_dir", ".git", ".GIT", "src/../../"] {
            let l = run(&ws.root, "list_files", json!({ "path": bad_dir }));
            assert_eq!(
                l.status,
                ToolReceiptStatus::Error,
                "list_files should reject {bad_dir:?}: {}",
                l.output
            );
            let s = run(
                &ws.root,
                "search_files",
                json!({ "query": "secret", "path": bad_dir }),
            );
            assert_eq!(
                s.status,
                ToolReceiptStatus::Error,
                "search_files should reject {bad_dir:?}: {}",
                s.output
            );
        }

        // list_files and search_files hide escaping symlinks and .git / git_link.
        let listed = run(&ws.root, "list_files", json!({}));
        assert_eq!(listed.status, ToolReceiptStatus::Ok);
        assert!(!listed.output.contains(".git"));
        assert!(!listed.output.contains("leak.txt"));
        assert!(!listed.output.contains("escape_dir"));
        assert!(!listed.output.contains("git_link"));

        let searched = run(&ws.root, "search_files", json!({ "query": "secret" }));
        assert_eq!(searched.status, ToolReceiptStatus::Ok);
        assert!(searched.output.starts_with("no matches"), "{}", searched.output);

        // Write & edit tools reject .., absolute paths, escaping/dangling symlinks, and .git (any case).
        for bad in [
            "",
            ".",
            "/tmp/evil.txt",
            "../outside.txt",
            "src/../../outside.txt",
            "leak.txt",
            "escape_dir/new_dir/pwn.txt",
            "dangling",
            ".git/config",
            ".GIT/config",
            "sub/.Git/hooks/pre-commit",
            "git_link",
        ] {
            let w = run(&ws.root, "write_file", json!({ "path": bad, "content": "pwn" }));
            assert_eq!(
                w.status,
                ToolReceiptStatus::Error,
                "write_file should reject {bad:?}: {}",
                w.output
            );
            let e = run(
                &ws.root,
                "edit_file",
                json!({ "path": bad, "old_text": "a", "new_text": "b" }),
            );
            assert_eq!(
                e.status,
                ToolReceiptStatus::Error,
                "edit_file should reject {bad:?}: {}",
                e.output
            );
        }
        assert_eq!(
            fs::read_to_string(ws.base.join("outside.txt")).unwrap(),
            "outside secret\n"
        );
        assert_eq!(
            fs::read_to_string(ws.root.join(".git/config")).unwrap(),
            "secret git config\n"
        );
    }

    #[test]
    fn write_and_edit_file_atomic_permissions_single_and_multi_edits() {
        let ws = TestWorkspace::new("edit");

        let w = run(
            &ws.root,
            "write_file",
            json!({ "path": "nested/script.sh", "content": "#!/bin/sh\necho one\necho two\n" }),
        );
        assert_eq!(w.status, ToolReceiptStatus::Ok);
        let script_path = ws.root.join("nested/script.sh");

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&script_path).unwrap().permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&script_path, perms).unwrap();
        }

        // Single edit preserves permissions.
        let e1 = run(
            &ws.root,
            "edit_file",
            json!({ "path": "nested/script.sh", "old_text": "echo one", "new_text": "echo ONE" }),
        );
        assert_eq!(e1.status, ToolReceiptStatus::Ok, "{}", e1.output);
        assert_eq!(
            fs::read_to_string(&script_path).unwrap(),
            "#!/bin/sh\necho ONE\necho two\n"
        );

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&script_path).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o755, "atomic write must preserve file permissions");
        }

        // Multi-span edits array (order-independent swap).
        fs::write(ws.root.join("multi.txt"), "first = 1\nsecond = 2\nthird = 3\n").unwrap();
        let multi = run(
            &ws.root,
            "edit_file",
            json!({
                "path": "multi.txt",
                "edits": [
                    { "old_text": "third = 3", "new_text": "first = 1" },
                    { "old_text": "first = 1", "new_text": "third = 3" }
                ]
            }),
        );
        assert_eq!(multi.status, ToolReceiptStatus::Ok, "{}", multi.output);
        assert!(multi.output.contains("2 replacement(s)"), "{}", multi.output);
        assert_eq!(
            fs::read_to_string(ws.root.join("multi.txt")).unwrap(),
            "third = 3\nsecond = 2\nfirst = 1\n"
        );

        // Reject overlapping spans in multi-edit.
        let overlap = run(
            &ws.root,
            "edit_file",
            json!({
                "path": "multi.txt",
                "edits": [
                    { "old_text": "third = 3\nsecond", "new_text": "X" },
                    { "old_text": "second = 2", "new_text": "Y" }
                ]
            }),
        );
        assert_eq!(overlap.status, ToolReceiptStatus::Error);
        assert!(overlap.output.contains("overlap"), "{}", overlap.output);

        // No leftover `.arity-tmp-*` files in target directory.
        for entry in fs::read_dir(ws.root.join("nested")).unwrap().flatten() {
            assert!(
                !entry.file_name().to_string_lossy().starts_with(".arity-tmp-"),
                "leftover temp file: {:?}",
                entry.path()
            );
        }
    }

    #[cfg(unix)]
    fn pid_is_alive(pid: i32) -> bool {
        unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
    }

    #[test]
    fn bash_timeout_and_abort_reap_background_subchildren() {
        let ws = TestWorkspace::new("bash-reap");

        // 1. Timeout reaps `sleep 60 &` background subchild.
        let pid_file = ws.root.join("bg_timeout.pid");
        let cmd = format!(
            "sleep 60 & echo $! > {}; echo started; sleep 60",
            pid_file.display()
        );
        let mut spawned_pgid = 0i64;
        let mut on_spawn = |pgid: i64| spawned_pgid = pgid;
        let mut never_abort = || false;

        let start = Instant::now();
        let timed = execute_tool(
            Some(&ws.root),
            "bash",
            &json!({ "command": cmd, "timeout": 0.3 }).to_string(),
            &mut on_spawn,
            &mut never_abort,
        );
        assert_eq!(timed.status, ToolReceiptStatus::Error, "{}", timed.output);
        assert!(spawned_pgid > 1);
        assert!(timed.output.contains("timed out"), "{}", timed.output);
        assert!(timed.output.contains("started"), "{}", timed.output);
        assert!(
            start.elapsed() < Duration::from_secs(5),
            "timeout must return promptly without hanging on background child pipes"
        );

        #[cfg(unix)]
        if let Ok(pid_str) = fs::read_to_string(&pid_file)
            && let Ok(bg_pid) = pid_str.trim().parse::<i32>()
        {
            std::thread::sleep(Duration::from_millis(50));
            assert!(
                !pid_is_alive(bg_pid),
                "background subchild pid {bg_pid} should be reaped on timeout"
            );
        }

        // 2. Abort via should_abort() reaps `sleep 60 &` background subchild.
        let pid_file_abort = ws.root.join("bg_abort.pid");
        let cmd_abort = format!(
            "sleep 60 & echo $! > {}; echo before_abort; sleep 60",
            pid_file_abort.display()
        );
        let aborted_flag = Arc::new(AtomicBool::new(false));
        let trigger = Arc::clone(&aborted_flag);
        let killer_thread = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(150));
            trigger.store(true, Ordering::SeqCst);
        });

        let mut pgid_abort = 0i64;
        let mut on_spawn_abort = |pgid: i64| pgid_abort = pgid;
        let mut check_abort = || aborted_flag.load(Ordering::SeqCst);

        let start2 = Instant::now();
        let aborted = execute_tool(
            Some(&ws.root),
            "bash",
            &json!({ "command": cmd_abort, "timeout": 30 }).to_string(),
            &mut on_spawn_abort,
            &mut check_abort,
        );
        killer_thread.join().unwrap();

        assert_eq!(aborted.status, ToolReceiptStatus::Aborted, "{}", aborted.output);
        assert!(pgid_abort > 1);
        assert!(
            start2.elapsed() < Duration::from_secs(5),
            "abort must kill process group and return promptly"
        );

        #[cfg(unix)]
        if let Ok(pid_str) = fs::read_to_string(&pid_file_abort)
            && let Ok(bg_pid) = pid_str.trim().parse::<i32>()
        {
            std::thread::sleep(Duration::from_millis(50));
            assert!(
                !pid_is_alive(bg_pid),
                "background subchild pid {bg_pid} should be reaped on abort"
            );
        }

        // 3. Normal exit also reaps lingering background subchildren without hanging on stdout.
        let pid_file_done = ws.root.join("bg_done.pid");
        let cmd_done = format!(
            "sleep 60 & echo $! > {}; echo done_fast",
            pid_file_done.display()
        );
        let start3 = Instant::now();
        let done = run(&ws.root, "bash", json!({ "command": cmd_done }));
        assert_eq!(done.status, ToolReceiptStatus::Ok, "{}", done.output);
        assert_eq!(done.exit_code, Some(0));
        assert!(done.output.contains("done_fast"), "{}", done.output);
        assert!(
            start3.elapsed() < Duration::from_secs(5),
            "normal exit must reap background subchildren holding stdout"
        );
        #[cfg(unix)]
        if let Ok(pid_str) = fs::read_to_string(&pid_file_done)
            && let Ok(bg_pid) = pid_str.trim().parse::<i32>()
        {
            std::thread::sleep(Duration::from_millis(50));
            assert!(
                !pid_is_alive(bg_pid),
                "background subchild pid {bg_pid} should be reaped when bash finishes"
            );
        }

        // 4. Non-zero exit code reports Error status and captures exit_code.
        let fail = run(
            &ws.root,
            "bash",
            json!({ "command": "echo oops >&2; exit 7" }),
        );
        assert_eq!(fail.status, ToolReceiptStatus::Error);
        assert_eq!(fail.exit_code, Some(7));
        assert!(fail.output.contains("exit code 7") && fail.output.contains("oops"));
    }

    #[cfg(unix)]
    #[test]
    fn pipe_eof_watchdog_kills_process_group_on_write_fd_close() {
        use std::os::unix::process::CommandExt;

        let mut child = Command::new("/bin/sh")
            .args(["-c", "sleep 60"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .unwrap();
        let pgid = child.id() as i64;
        let mut watchdog = WatchdogGuard::spawn(pgid);

        assert!(pid_is_alive(pgid as i32));

        // Simulate abrupt parent death (kernel closing write_fd) without calling reap_process_group.
        drop(watchdog.write_fd.take());

        // Wait for child to be killed by the watchdog.
        let deadline = Instant::now() + Duration::from_secs(3);
        let mut reaped = false;
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                reaped = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        watchdog.shutdown();
        if !reaped {
            reap_process_group(pgid);
        }
        let _ = child.wait();
        assert!(reaped, "pipe-EOF watchdog must kill target process group when write_fd closes");
    }
}
