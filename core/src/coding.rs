//! Coding tools: `write_file`, `edit_file`, and `bash`, jailed to the attached folder.

use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use anyhow::{Result, anyhow, bail};
use serde_json::{Value, json};

use crate::tools::{
    Execution, MAX_TOOL_OUTPUT_BYTES, Replay, Tool, ToolOutput, ToolSpec, ToolVisibility,
};

const NO_FOLDER: &str =
    "no folder is attached to this conversation, so there is no workspace to edit or run commands in";

const DEFAULT_TIMEOUT_SECS: f64 = 60.0;
const MAX_TIMEOUT_SECS: f64 = 300.0;

/// The tools this module offers, for tests.
#[cfg(test)]
pub const NAMES: [&str; 3] = ["write_file", "edit_file", "bash"];

struct CodingTool {
    spec: ToolSpec,
}

impl Tool for CodingTool {
    fn name(&self) -> &'static str {
        self.spec.name
    }

    fn execution(&self) -> Execution {
        Execution::External { replay: Replay::Unsafe }
    }

    fn spec(&self, vis: &ToolVisibility) -> Option<ToolSpec> {
        vis.folder.is_some().then(|| self.spec.clone())
    }

    fn refused_message(&self, _vis: &ToolVisibility) -> Option<String> {
        Some(NO_FOLDER.into())
    }

    fn run_external(&self, vis: &ToolVisibility, args: &Value) -> Result<ToolOutput> {
        let Some(root) = vis.folder.as_deref() else {
            bail!("{NO_FOLDER}");
        };
        execute(root, self.spec.name, args)
    }
}

/// The coding tools registered in `tools::ToolRegistry`.
pub fn tools() -> Vec<Box<dyn Tool>> {
    specs()
        .into_iter()
        .map(|spec| Box::new(CodingTool { spec }) as Box<dyn Tool>)
        .collect()
}

pub fn specs() -> Vec<ToolSpec> {
    vec![
        ToolSpec {
            name: "write_file",
            description: "Create or overwrite a UTF-8 text file in the attached folder, creating parent directories as needed. Paths are relative to the attached folder; .git cannot be modified.",
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "File path, relative to the attached folder." },
                    "content": { "type": "string", "description": "Full UTF-8 text content to write to the file." }
                },
                "required": ["path", "content"]
            }),
        },
        ToolSpec {
            name: "edit_file",
            description: "Edit an existing UTF-8 text file in the attached folder by exact string replacement. Pass old_text and new_text (with optional replace_all), or pass edits as an array of {old_text, new_text} replacements matched against the original file.",
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
        },
        ToolSpec {
            name: "bash",
            description: "Run a shell command in the attached folder and return its combined stdout and stderr.",
            parameters: json!({
                "type": "object",
                "properties": {
                    "command": { "type": "string", "description": "Shell command to run in the attached folder." },
                    "timeout": { "type": "number", "description": "Timeout in seconds; 60 by default, at most 300." }
                },
                "required": ["command"]
            }),
        },
    ]
}

fn execute(root: &Path, name: &str, args: &Value) -> Result<ToolOutput> {
    match name {
        "write_file" => write_file(root, args),
        "edit_file" => edit_file(root, args),
        "bash" => bash(root, args),
        other => bail!("unknown tool {other}"),
    }
}

fn contains_git_component(path: &Path) -> bool {
    path.components()
        .any(|c| matches!(c, Component::Normal(seg) if seg == ".git"))
}

/// Resolve a writable path jailed strictly inside `root`.
///
/// Rejects empty paths, absolute paths, `..` traversal, `.git` paths, and any
/// symlink (including dangling symlinks or symlinks in existing ancestors) that
/// points outside `root` or into `.git`.
fn resolve_writable(root: &Path, rel: &str) -> Result<PathBuf> {
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
                if seg == ".git" {
                    bail!("{rel} targets .git, which cannot be modified");
                }
                clean_rel.push(seg);
            }
        }
    }
    if clean_rel.as_os_str().is_empty() {
        bail!("path must name a file inside the attached folder");
    }

    let root_real = std::fs::canonicalize(root)
        .map_err(|e| anyhow!("the attached folder {} cannot be read: {e}", root.display()))?;
    let candidate = root_real.join(&clean_rel);

    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    let mut cursor = candidate.as_path();
    loop {
        match std::fs::symlink_metadata(cursor) {
            Ok(meta) => {
                let real = std::fs::canonicalize(cursor).map_err(|e| {
                    if meta.file_type().is_symlink() {
                        anyhow!("{rel} traverses a broken symlink: {e}")
                    } else {
                        anyhow!("cannot resolve {rel}: {e}")
                    }
                })?;
                if !real.starts_with(&root_real) {
                    bail!("{rel} resolves outside the attached folder");
                }
                if let Ok(inside) = real.strip_prefix(&root_real) {
                    if contains_git_component(inside) {
                        bail!("{rel} resolves into .git, which cannot be modified");
                    }
                }
                let mut resolved = real;
                for seg in tail.into_iter().rev() {
                    resolved.push(seg);
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

fn shown_rel(root_real: &Path, target: &Path) -> String {
    target
        .strip_prefix(root_real)
        .unwrap_or(target)
        .to_string_lossy()
        .into_owned()
}

fn write_file(root: &Path, args: &Value) -> Result<ToolOutput> {
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("write_file needs a path"))?;
    let content = args
        .get("content")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("write_file needs content"))?;

    let root_real = std::fs::canonicalize(root)
        .map_err(|e| anyhow!("the attached folder {} cannot be read: {e}", root.display()))?;
    let target = resolve_writable(&root_real, path)?;
    let shown = shown_rel(&root_real, &target);

    if target.is_dir() {
        bail!("{shown} is a directory");
    }
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| anyhow!("cannot create parent directories for {shown}: {e}"))?;
    }
    std::fs::write(&target, content)
        .map_err(|e| anyhow!("cannot write {shown}: {e}"))?;

    Ok(ToolOutput::ok(format!("wrote {shown} ({} bytes)", content.len())))
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

fn edit_file(root: &Path, args: &Value) -> Result<ToolOutput> {
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("edit_file needs a path"))?;

    let root_real = std::fs::canonicalize(root)
        .map_err(|e| anyhow!("the attached folder {} cannot be read: {e}", root.display()))?;
    let target = resolve_writable(&root_real, path)?;
    let shown = shown_rel(&root_real, &target);

    let meta = std::fs::metadata(&target)
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

    let bytes = std::fs::read(&target).map_err(|e| anyhow!("cannot read {shown}: {e}"))?;
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
        let old = old_single.ok_or_else(|| anyhow!("edit_file needs old_text and new_text (or edits)"))?;
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
    std::fs::write(&target, final_text).map_err(|e| anyhow!("cannot write {shown}: {e}"))?;

    Ok(ToolOutput::ok(format!(
        "edited {shown} ({count} replacement(s), {lines} lines)"
    )))
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

fn truncate_tail(text: String, max_bytes: usize) -> String {
    if text.len() <= max_bytes {
        return text;
    }
    let header_reserve = format!("[earlier output truncated: {} bytes omitted]\n", text.len()).len();
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

fn spawn_shell(root_real: &Path, command: &str) -> Result<std::process::Child> {
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

fn kill_child_tree(child: &mut std::process::Child) {
    #[cfg(unix)]
    {
        let pgid = child.id();
        let _ = Command::new("kill")
            .args(["-9", &format!("-{pgid}")])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn bash(root: &Path, args: &Value) -> Result<ToolOutput> {
    let root_real = std::fs::canonicalize(root)
        .map_err(|e| anyhow!("the attached folder {} cannot be read: {e}", root.display()))?;
    let command = args
        .get("command")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("bash needs a command"))?;
    if command.trim().is_empty() {
        bail!("bash needs a non-empty command");
    }
    let timeout_secs = parse_timeout(args)?;

    let mut child = spawn_shell(&root_real, command)?;
    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();

    let stdout_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(ref mut out) = stdout_pipe {
            let _ = out.read_to_end(&mut buf);
        }
        buf
    });
    let stderr_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(ref mut err) = stderr_pipe {
            let _ = err.read_to_end(&mut buf);
        }
        buf
    });

    let deadline = Instant::now() + Duration::from_secs_f64(timeout_secs);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => {
                if Instant::now() >= deadline {
                    kill_child_tree(&mut child);
                    break None;
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(e) => {
                kill_child_tree(&mut child);
                let _ = stdout_thread.join();
                let _ = stderr_thread.join();
                return Err(anyhow!("failed waiting for command: {e}"));
            }
        }
    };

    let stdout_bytes = stdout_thread.join().unwrap_or_default();
    let stderr_bytes = stderr_thread.join().unwrap_or_default();
    let stdout = String::from_utf8_lossy(&stdout_bytes);
    let stderr = String::from_utf8_lossy(&stderr_bytes);
    let combined = combine_streams(&stdout, &stderr);

    let Some(status) = status else {
        let prefix = format!("command timed out after {}s", format_secs(timeout_secs));
        let budget = MAX_TOOL_OUTPUT_BYTES.saturating_sub(prefix.len() + 1);
        let tail = truncate_tail(combined, budget);
        let msg = if tail.is_empty() {
            prefix
        } else {
            format!("{prefix}\n{tail}")
        };
        return Ok(ToolOutput::err(msg));
    };

    if status.success() {
        let output = truncate_tail(combined, MAX_TOOL_OUTPUT_BYTES);
        return Ok(ToolOutput::ok(if output.is_empty() {
            "(no output)".into()
        } else {
            output
        }));
    }

    let code = status.code().unwrap_or_else(|| {
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            status.signal().map_or(-1, |sig| 128 + sig)
        }
        #[cfg(not(unix))]
        {
            -1
        }
    });
    let prefix = format!("exit code {code}");
    let budget = MAX_TOOL_OUTPUT_BYTES.saturating_sub(prefix.len() + 1);
    let output = truncate_tail(combined, budget);
    let text = if output.is_empty() {
        prefix
    } else {
        format!("{prefix}\n{output}")
    };
    Ok(ToolOutput {
        text,
        is_error: true,
        details: Some(json!({ "exit_code": code })),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::Caller;

    fn temp_workspace(name: &str) -> (PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("arity-coding-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("repo");
        std::fs::create_dir_all(&root).unwrap();
        (base, root)
    }

    fn vis(root: Option<PathBuf>) -> ToolVisibility {
        ToolVisibility {
            caller: Caller::Chat,
            folder: root,
        }
    }

    fn run_tool(root: &Path, name: &str, args: Value) -> Result<ToolOutput> {
        let all = tools();
        assert_eq!(all.iter().map(|t| t.name()).collect::<Vec<_>>(), NAMES);
        let tool = all.iter().find(|t| t.name() == name).unwrap();
        assert_eq!(tool.execution(), Execution::External { replay: Replay::Unsafe });
        assert!(tool.spec(&vis(None)).is_none());
        assert!(tool.spec(&vis(Some(root.to_path_buf()))).is_some());
        assert_eq!(tool.refused_message(&vis(None)).as_deref(), Some(NO_FOLDER));
        tool.run_external(&vis(Some(root.to_path_buf())), &args)
    }

    #[test]
    fn write_file_creates_parents_and_blocks_escapes_and_git() -> Result<()> {
        let (base, root) = temp_workspace("write");
        let outside_dir = base.join("outside");
        std::fs::create_dir_all(&outside_dir)?;
        let outside_file = outside_dir.join("secret.txt");
        std::fs::write(&outside_file, "secret")?;

        // Creates nested parent directories and writes content.
        let out = run_tool(
            &root,
            "write_file",
            json!({ "path": "nested/deep/hello.txt", "content": "hello world\n" }),
        )?;
        assert!(!out.is_error);
        assert_eq!(out.text, "wrote nested/deep/hello.txt (12 bytes)");
        assert_eq!(std::fs::read_to_string(root.join("nested/deep/hello.txt"))?, "hello world\n");

        // Overwrites existing file.
        let out2 = run_tool(
            &root,
            "write_file",
            json!({ "path": "./nested/deep/hello.txt", "content": "updated" }),
        )?;
        assert_eq!(out2.text, "wrote nested/deep/hello.txt (7 bytes)");
        assert_eq!(std::fs::read_to_string(root.join("nested/deep/hello.txt"))?, "updated");

        // Rejects empty, dot, absolute, parent traversal, and .git paths (without touching disk).
        for bad in [
            "",
            "   ",
            ".",
            "/tmp/evil.txt",
            "../outside/secret.txt",
            "nested/../../outside/secret.txt",
            ".git",
            ".git/config",
            "sub/.git/hooks/pre-commit",
        ] {
            assert!(
                run_tool(&root, "write_file", json!({ "path": bad, "content": "x" })).is_err(),
                "expected error for path {bad:?}"
            );
        }

        // Rejects symlink escaping to an outside directory (even for a brand-new nested path)
        // and symlinks pointing to an outside file or dangling outside target.
        std::os::unix::fs::symlink(&outside_dir, root.join("link_dir"))?;
        std::os::unix::fs::symlink(&outside_file, root.join("link_file"))?;
        std::os::unix::fs::symlink(outside_dir.join("does_not_exist.txt"), root.join("dangling"))?;

        for bad in ["link_dir/new_sub/pwn.txt", "link_file", "dangling"] {
            assert!(
                run_tool(&root, "write_file", json!({ "path": bad, "content": "x" })).is_err(),
                "expected symlink escape rejection for {bad:?}"
            );
        }
        assert_eq!(std::fs::read_to_string(&outside_file)?, "secret");
        assert!(!outside_dir.join("new_sub").exists());
        assert!(!outside_dir.join("does_not_exist.txt").exists());

        // Symlink that stays inside the workspace is allowed.
        std::fs::create_dir_all(root.join("real_sub"))?;
        std::os::unix::fs::symlink(root.join("real_sub"), root.join("safe_link"))?;
        let ok = run_tool(
            &root,
            "write_file",
            json!({ "path": "safe_link/new/ok.txt", "content": "inside" }),
        )?;
        assert!(!ok.is_error);
        assert_eq!(std::fs::read_to_string(root.join("real_sub/new/ok.txt"))?, "inside");

        let _ = std::fs::remove_dir_all(&base);
        Ok(())
    }

    #[test]
    fn edit_file_supports_single_multi_replace_all_crlf_and_rejects_ambiguous_or_overlapping() -> Result<()> {
        let (base, root) = temp_workspace("edit");

        // Single exact replacement + camelCase fallback.
        std::fs::write(root.join("sample.txt"), "alpha\nbeta\ngamma\n")?;
        let res = run_tool(
            &root,
            "edit_file",
            json!({ "path": "sample.txt", "oldText": "beta", "newText": "BETA" }),
        )?;
        assert!(!res.is_error);
        assert!(res.text.contains("1 replacement(s)") && res.text.contains("3 lines"), "{}", res.text);
        assert_eq!(std::fs::read_to_string(root.join("sample.txt"))?, "alpha\nBETA\ngamma\n");

        // Ambiguous match rejected unless replace_all: true.
        std::fs::write(root.join("dup.txt"), "foo\nbar\nfoo\nfoo\n")?;
        let err = run_tool(
            &root,
            "edit_file",
            json!({ "path": "dup.txt", "old_text": "foo", "new_text": "baz" }),
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("matched 3 times") && err.contains("replace_all: true"), "{err}");

        let all = run_tool(
            &root,
            "edit_file",
            json!({ "path": "dup.txt", "old_text": "foo", "new_text": "baz", "replace_all": true }),
        )?;
        assert!(all.text.contains("3 replacement(s)"), "{}", all.text);
        assert_eq!(std::fs::read_to_string(root.join("dup.txt"))?, "baz\nbar\nbaz\nbaz\n");

        // Multi-edit matches against original text (handles swapping/order-independent edits
        // and camelCase keys).
        std::fs::write(root.join("multi.txt"), "first = 1\nsecond = 2\nthird = 3\n")?;
        let multi = run_tool(
            &root,
            "edit_file",
            json!({
                "path": "multi.txt",
                "edits": [
                    { "old_text": "third = 3", "new_text": "first = 1" },
                    { "oldText": "first = 1", "newText": "third = 3" }
                ]
            }),
        )?;
        assert!(multi.text.contains("2 replacement(s)"), "{}", multi.text);
        assert_eq!(
            std::fs::read_to_string(root.join("multi.txt"))?,
            "third = 3\nsecond = 2\nfirst = 1\n"
        );

        // Multi-edit rejects overlapping spans.
        let overlap = run_tool(
            &root,
            "edit_file",
            json!({
                "path": "multi.txt",
                "edits": [
                    { "old_text": "third = 3\nsecond", "new_text": "X" },
                    { "old_text": "second = 2", "new_text": "Y" }
                ]
            }),
        )
        .unwrap_err()
        .to_string();
        assert!(overlap.contains("overlap"), "{overlap}");

        // Multi-edit rejects ambiguous individual edit.
        std::fs::write(root.join("multi_dup.txt"), "x\nx\ny\n")?;
        let amb = run_tool(
            &root,
            "edit_file",
            json!({
                "path": "multi_dup.txt",
                "edits": [
                    { "old_text": "x", "new_text": "z" }
                ]
            }),
        )
        .unwrap_err()
        .to_string();
        assert!(amb.contains("matched 2 times"), "{amb}");

        // CRLF preservation: matches LF old_text against CRLF file and restores CRLF on write.
        std::fs::write(root.join("win.txt"), "one\r\ntwo\r\nthree\r\n")?;
        run_tool(
            &root,
            "edit_file",
            json!({ "path": "win.txt", "old_text": "one\ntwo", "new_text": "ONE\nTWO" }),
        )?;
        assert_eq!(std::fs::read(root.join("win.txt"))?, b"ONE\r\nTWO\r\nthree\r\n");

        // Rejects missing file, empty old_text, missing match, and .git paths.
        assert!(run_tool(&root, "edit_file", json!({ "path": "nope.txt", "old_text": "a", "new_text": "b" })).is_err());
        assert!(run_tool(&root, "edit_file", json!({ "path": "sample.txt", "old_text": "", "new_text": "b" })).is_err());
        assert!(run_tool(&root, "edit_file", json!({ "path": "sample.txt", "old_text": "missing", "new_text": "b" })).is_err());
        assert!(run_tool(&root, "edit_file", json!({ "path": ".git/config", "old_text": "a", "new_text": "b" })).is_err());

        let _ = std::fs::remove_dir_all(&base);
        Ok(())
    }

    #[test]
    fn bash_runs_in_folder_captures_output_reports_exit_code_and_enforces_timeout() -> Result<()> {
        let (base, root) = temp_workspace("bash");
        let root_real = std::fs::canonicalize(&root)?;

        // Runs in attached folder and captures both stdout and stderr.
        let ok = run_tool(
            &root,
            "bash",
            json!({ "command": "pwd; echo out_line; echo err_line >&2" }),
        )?;
        assert!(!ok.is_error);
        assert!(ok.text.contains(root_real.to_string_lossy().as_ref()), "{}", ok.text);
        assert!(ok.text.contains("out_line") && ok.text.contains("err_line"), "{}", ok.text);

        // Empty output becomes "(no output)".
        let quiet = run_tool(&root, "bash", json!({ "command": "true" }))?;
        assert!(!quiet.is_error);
        assert_eq!(quiet.text, "(no output)");

        // Non-zero exit code returns is_error: true, exit code in text and details.
        let fail = run_tool(
            &root,
            "bash",
            json!({ "command": "echo failing >&2; exit 42" }),
        )?;
        assert!(fail.is_error);
        assert!(fail.text.starts_with("exit code 42\n"), "{}", fail.text);
        assert!(fail.text.contains("failing"), "{}", fail.text);
        assert_eq!(fail.details, Some(json!({ "exit_code": 42 })));

        // Large output retains the tail cleanly within MAX_TOOL_OUTPUT_BYTES (UTF-8 safe)
        // and never deadlocks stdout/stderr pipes.
        let big = format!("{}TAIL_MARKER", "é".repeat(MAX_TOOL_OUTPUT_BYTES));
        let truncated = truncate_tail(big, MAX_TOOL_OUTPUT_BYTES);
        assert!(truncated.len() <= MAX_TOOL_OUTPUT_BYTES);
        assert!(truncated.starts_with("[earlier output truncated:"));
        assert!(truncated.ends_with("TAIL_MARKER"));

        let loud = run_tool(
            &root,
            "bash",
            json!({ "command": "yes 0123456789abcdef | head -n 5000; echo FINAL_END" }),
        )?;
        assert!(!loud.is_error);
        assert!(loud.text.len() <= MAX_TOOL_OUTPUT_BYTES);
        assert!(loud.text.starts_with("[earlier output truncated:"));
        assert!(loud.text.contains("FINAL_END"), "tail should be preserved");

        // Timeout kills command (including child sleep) and reports timeout error.
        let start = Instant::now();
        let timed = run_tool(
            &root,
            "bash",
            json!({ "command": "echo before_sleep; sleep 30", "timeout": 0.3 }),
        )?;
        assert!(timed.is_error);
        assert!(start.elapsed() < Duration::from_secs(5), "timeout should kill child process group promptly");
        assert!(timed.text.contains("command timed out after 0.3s"), "{}", timed.text);
        assert!(timed.text.contains("before_sleep"), "{}", timed.text);

        // Rejects empty command and invalid timeout.
        assert!(run_tool(&root, "bash", json!({ "command": "   " })).is_err());
        assert!(run_tool(&root, "bash", json!({ "command": "true", "timeout": 0 })).is_err());

        let _ = std::fs::remove_dir_all(&base);
        Ok(())
    }
}
