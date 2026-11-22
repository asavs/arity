//! File tools: read-only and jailed in the folder attached to a conversation
//! (see `board::folder_of`). Every path a model asks for is resolved, symlinks
//! and `..` included, and refused unless it ends up inside that folder.
//! Version control, build output, dependencies and binary files are skipped.

use std::path::{Path, PathBuf};

use anyhow::{Result, anyhow, bail};
use serde_json::{Value, json};

use crate::tools::{Execution, Replay, Tool, ToolOutput, ToolSpec, ToolVisibility};

struct FileTool {
    spec: ToolSpec,
}

impl Tool for FileTool {
    fn name(&self) -> &'static str {
        self.spec.name
    }

    fn execution(&self) -> Execution {
        Execution::External { replay: Replay::Safe }
    }

    fn spec(&self, vis: &ToolVisibility) -> Option<ToolSpec> {
        vis.folder.is_some().then(|| self.spec.clone())
    }

    fn refused_message(&self, vis: &ToolVisibility) -> Option<String> {
        vis.folder
            .is_none()
            .then(|| "no folder is attached to this conversation, so there are no files to read".into())
    }

    fn run_external(&self, vis: &ToolVisibility, args: &Value) -> Result<ToolOutput> {
        let Some(root) = vis.folder.as_deref() else {
            bail!("no folder is attached to this conversation, so there are no files to read");
        };
        Ok(ToolOutput::ok(execute(root, self.spec.name, args)?))
    }
}

/// The read-only file tools registered in `tools::ToolRegistry`.
pub fn tools() -> Vec<Box<dyn Tool>> {
    specs().into_iter().map(|spec| Box::new(FileTool { spec }) as Box<dyn Tool>).collect()
}

/// Folders never walked or searched.
const IGNORED: [&str; 4] = [".git", "target", ".build", "node_modules"];

const MAX_ENTRIES: usize = 200;
const DEFAULT_DEPTH: u64 = 2;
const MAX_DEPTH: u64 = 8;
const DEFAULT_LINES: usize = 200;
const MAX_LINES: usize = 400;
const MAX_MATCHES: usize = 80;
/// Lines longer than this are cut, so one minified file does not fill a reply.
const MAX_LINE_CHARS: usize = 400;
/// Files larger than this are not searched.
const MAX_SEARCH_BYTES: u64 = 1 << 20;
/// How many files one search looks at before it stops.
const MAX_SEARCH_FILES: usize = 20_000;

pub fn specs() -> Vec<ToolSpec> {
    vec![
        ToolSpec {
            name: "list_files",
            description: "List the files and folders in the attached folder, or a folder inside it. Folders end \
                          with /. Version control, build output and dependencies are left out.",
            parameters: json!({"type": "object", "properties": {
                "path": {"type": "string", "description": "A folder, relative to the attached folder. Left out, the folder itself."},
                "max_depth": {"type": "integer", "description": "How many levels down to list; 2 by default."}
            }}),
        },
        ToolSpec {
            name: "read_file",
            description: "Read a text file in the attached folder, with numbered lines: 200 lines unless you ask \
                          for a range, at most 400 at a time. Says how many lines the file has.",
            parameters: json!({"type": "object", "properties": {
                "path": {"type": "string", "description": "Relative to the attached folder."},
                "start_line": {"type": "integer", "description": "The first line, counting from 1."},
                "end_line": {"type": "integer", "description": "The last line."}
            }, "required": ["path"]}),
        },
        ToolSpec {
            name: "search_files",
            description: "Find text in the files of the attached folder, ignoring case. Each match is \
                          path:line: text; at most 80.",
            parameters: json!({"type": "object", "properties": {
                "query": {"type": "string", "description": "The text to find."},
                "path": {"type": "string", "description": "A folder or file to search, relative to the attached folder. Left out, all of it."}
            }, "required": ["query"]}),
        },
    ]
}

pub fn execute(root: &Path, name: &str, args: &Value) -> Result<String> {
    let root = std::fs::canonicalize(root).map_err(|e| anyhow!("the attached folder {} cannot be read: {e}", root.display()))?;
    let path = args["path"].as_str().map(str::trim).filter(|p| !p.is_empty());
    let number = |key: &str| -> Result<Option<u64>> {
        match &args[key] {
            Value::Null => Ok(None),
            Value::Number(n) => n.as_u64().map(Some).ok_or_else(|| anyhow!("{key} must be a positive whole number")),
            Value::String(s) => s.trim().parse().map(Some).map_err(|_| anyhow!("{key} must be a positive whole number")),
            _ => bail!("{key} must be a positive whole number"),
        }
    };
    match name {
        "list_files" => list(&root, path, number("max_depth")?.unwrap_or(DEFAULT_DEPTH)),
        "read_file" => {
            let path = path.ok_or_else(|| anyhow!("read_file needs a path"))?;
            read(&root, path, number("start_line")?, number("end_line")?)
        }
        "search_files" => {
            let query = args["query"].as_str().unwrap_or_default();
            if query.trim().is_empty() {
                bail!("search_files needs a query");
            }
            search(&root, query, path)
        }
        other => bail!("unknown tool {other}"),
    }
}

/// `path` resolved inside `root`, or an error if it is not there or leads out.
fn resolve(root: &Path, path: Option<&str>) -> Result<PathBuf> {
    let Some(p) = path.filter(|p| *p != ".") else { return Ok(root.to_path_buf()) };
    let joined = root.join(p);
    let real = std::fs::canonicalize(&joined).map_err(|_| anyhow!("no file or folder {p} in the attached folder"))?;
    if !real.starts_with(root) {
        bail!("{p} is outside the attached folder; only paths inside it can be read");
    }
    Ok(real)
}

/// How `path` is named to the model: relative to the root.
fn shown(root: &Path, path: &Path) -> String {
    let rel = path.strip_prefix(root).unwrap_or(path).to_string_lossy().into_owned();
    if rel.is_empty() { ".".into() } else { rel }
}

/// The entries of a folder worth showing, sorted. A symlink is kept only if it
/// leads somewhere inside the root; it is then resolved, but never walked into,
/// so a link cannot loop or take a walk outside.
fn children(root: &Path, dir: &Path) -> Vec<(PathBuf, bool)> {
    let Ok(read) = std::fs::read_dir(dir) else { return vec![] };
    let mut out: Vec<(PathBuf, bool)> = read
        .flatten()
        .filter(|e| !IGNORED.contains(&e.file_name().to_string_lossy().as_ref()))
        .filter_map(|e| {
            let kind = e.file_type().ok()?;
            if kind.is_symlink() {
                let real = std::fs::canonicalize(e.path()).ok()?;
                // Shown under its own name, but not walked into.
                return real.starts_with(root).then(|| (e.path(), false));
            }
            Some((e.path(), kind.is_dir()))
        })
        .collect();
    out.sort();
    out
}

fn list(root: &Path, path: Option<&str>, depth: u64) -> Result<String> {
    let start = resolve(root, path)?;
    if !start.is_dir() {
        bail!("{} is a file; read it with read_file", shown(root, &start));
    }
    let depth = depth.clamp(1, MAX_DEPTH);
    let mut lines = vec![];
    let mut more = 0usize;
    let mut stack: Vec<(PathBuf, bool, u64)> = children(root, &start).into_iter().rev().map(|(p, d)| (p, d, 1)).collect();
    while let Some((p, is_dir, level)) = stack.pop() {
        if lines.len() >= MAX_ENTRIES {
            more += 1;
            continue;
        }
        let name = shown(root, &p);
        lines.push(if is_dir { format!("{name}/") } else { name });
        if is_dir && level < depth {
            stack.extend(children(root, &p).into_iter().rev().map(|(c, d)| (c, d, level + 1)));
        }
    }
    if lines.is_empty() {
        return Ok(format!("{} is empty", shown(root, &start)));
    }
    if more > 0 {
        lines.push(format!(
            "(stopped at {MAX_ENTRIES} entries; {more} more not shown: list a folder inside, or a smaller max_depth)"
        ));
    }
    Ok(lines.join("\n"))
}

/// The file's text, or None if it is binary or not UTF-8.
fn text_of(path: &Path) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    if bytes[..bytes.len().min(8192)].contains(&0) {
        return None;
    }
    String::from_utf8(bytes).ok()
}

fn cut(line: &str) -> String {
    match line.char_indices().nth(MAX_LINE_CHARS) {
        Some((i, _)) => format!("{} …", &line[..i]),
        None => line.to_string(),
    }
}

fn read(root: &Path, path: &str, start: Option<u64>, end: Option<u64>) -> Result<String> {
    let file = resolve(root, Some(path))?;
    let name = shown(root, &file);
    if file.is_dir() {
        bail!("{name} is a folder; list it with list_files");
    }
    let text = text_of(&file).ok_or_else(|| anyhow!("{name} is a binary file or not UTF-8 text, so it cannot be read"))?;
    let lines: Vec<&str> = text.lines().collect();
    let total = lines.len();
    if total == 0 {
        return Ok(format!("{name} is empty"));
    }
    let start = start.unwrap_or(1).max(1) as usize;
    if start > total {
        bail!("{name} has {total} lines; start_line {start} is past the end");
    }
    let wanted = end.map_or(start + DEFAULT_LINES - 1, |e| e as usize);
    if wanted < start {
        bail!("end_line must not be before start_line");
    }
    let end = wanted.min(start + MAX_LINES - 1).min(total);
    let mut out = vec![format!("{name}: lines {start}-{end} of {total}")];
    out.extend((start..=end).map(|n| format!("{n:>5}  {}", cut(lines[n - 1]))));
    if end < total {
        out.push(format!("(more after line {end}: read on with start_line {})", end + 1));
    }
    Ok(out.join("\n"))
}

fn search(root: &Path, query: &str, path: Option<&str>) -> Result<String> {
    let start = resolve(root, path)?;
    let needle = query.to_lowercase();
    let mut matches = vec![];
    let mut more = false;
    let mut seen = 0usize;
    let mut stack = vec![(start.clone(), start.is_dir())];
    'walk: while let Some((p, is_dir)) = stack.pop() {
        if is_dir {
            stack.extend(children(root, &p).into_iter().rev());
            continue;
        }
        seen += 1;
        if seen > MAX_SEARCH_FILES {
            more = true;
            break;
        }
        if std::fs::metadata(&p).map_or(true, |m| m.len() > MAX_SEARCH_BYTES) {
            continue;
        }
        let Some(text) = text_of(&p) else { continue };
        for (i, line) in text.lines().enumerate() {
            if line.to_lowercase().contains(&needle) {
                if matches.len() >= MAX_MATCHES {
                    more = true;
                    break 'walk;
                }
                matches.push(format!("{}:{}: {}", shown(root, &p), i + 1, cut(line.trim())));
            }
        }
    }
    if matches.is_empty() {
        return Ok(format!("no matches for {query:?} in {}", shown(root, &start)));
    }
    if more {
        matches.push(format!("(stopped at {} matches: search a folder inside, or a longer query)", matches.len()));
    }
    Ok(matches.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A folder with a little project in it, and a secret beside it.
    fn project(name: &str) -> (PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("arity-files-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("repo");
        for d in ["src", ".git", "target/debug", "node_modules/x", "app/.build"] {
            std::fs::create_dir_all(root.join(d)).unwrap();
        }
        std::fs::write(base.join("secret.txt"), "the Password is hunter2\n").unwrap();
        let lines: Vec<String> = (1..=500).map(|n| format!("line {n}")).collect();
        std::fs::write(root.join("src/long.rs"), lines.join("\n")).unwrap();
        std::fs::write(root.join("src/main.rs"), "fn main() {\n    println!(\"Hello\");\n}\n").unwrap();
        std::fs::write(root.join("README.md"), "# Repo\nSay hello.\n").unwrap();
        std::fs::write(root.join(".git/config"), "hello from git\n").unwrap();
        std::fs::write(root.join("target/debug/out.txt"), "hello from target\n").unwrap();
        std::fs::write(root.join("node_modules/x/index.js"), "hello from deps\n").unwrap();
        std::fs::write(root.join("app/.build/log"), "hello from build\n").unwrap();
        std::fs::write(root.join("logo.png"), b"\x89PNG\0\0hello\0").unwrap();
        std::fs::write(root.join("latin1.txt"), b"hello caf\xe9\n").unwrap();
        (base, root)
    }

    fn run(root: &Path, name: &str, args: Value) -> Result<String> {
        execute(root, name, &args)
    }

    #[test]
    fn escapes_are_refused() -> Result<()> {
        let (base, root) = project("escape");
        std::os::unix::fs::symlink(base.join("secret.txt"), root.join("leak.txt"))?;
        std::os::unix::fs::symlink(&base, root.join("up"))?;
        for path in ["../secret.txt", "leak.txt", "up/secret.txt", "src/../../secret.txt", base.join("secret.txt").to_str().unwrap()] {
            let err = run(&root, "read_file", json!({ "path": path })).unwrap_err().to_string();
            assert!(err.contains("outside the attached folder"), "{path}: {err}");
        }
        assert!(run(&root, "list_files", json!({ "path": ".." })).unwrap_err().to_string().contains("outside"));
        assert!(run(&root, "search_files", json!({ "query": "x", "path": "up" })).unwrap_err().to_string().contains("outside"));
        // Links that lead out are not listed or searched either.
        let listed = run(&root, "list_files", json!({}))?;
        assert!(!listed.contains("leak.txt") && !listed.contains("up"), "{listed}");
        assert!(run(&root, "search_files", json!({ "query": "hunter2" }))?.starts_with("no matches"));
        // Inside is fine, `..` included, and so is a link that stays inside.
        assert!(run(&root, "read_file", json!({ "path": "src/../README.md" }))?.contains("Say hello."));
        std::os::unix::fs::symlink(root.join("README.md"), root.join("src/readme-link"))?;
        assert!(run(&root, "read_file", json!({ "path": "src/readme-link" }))?.contains("Say hello."));
        let _ = std::fs::remove_dir_all(&base);
        Ok(())
    }

    #[test]
    fn ignored_folders_and_binary_files_are_skipped() -> Result<()> {
        let (base, root) = project("ignored");
        let listed = run(&root, "list_files", json!({ "max_depth": 5 }))?;
        assert_eq!(listed, "README.md\napp/\nlatin1.txt\nlogo.png\nsrc/\nsrc/long.rs\nsrc/main.rs");
        let found = run(&root, "search_files", json!({ "query": "HELLO" }))?;
        assert_eq!(found, "README.md:2: Say hello.\nsrc/main.rs:2: println!(\"Hello\");");
        assert!(run(&root, "read_file", json!({ "path": "logo.png" })).unwrap_err().to_string().contains("binary"));
        assert!(run(&root, "read_file", json!({ "path": "latin1.txt" })).unwrap_err().to_string().contains("not UTF-8"));
        assert!(run(&root, "search_files", json!({ "query": "fn", "path": "src/main.rs" }))?.starts_with("src/main.rs:1: fn main"));
        let _ = std::fs::remove_dir_all(&base);
        Ok(())
    }

    #[test]
    fn reading_pages_through_long_files() -> Result<()> {
        let (base, root) = project("paging");
        let first = run(&root, "read_file", json!({ "path": "src/long.rs" }))?;
        assert!(first.starts_with("src/long.rs: lines 1-200 of 500\n    1  line 1\n"), "{first}");
        assert!(first.ends_with("  200  line 200\n(more after line 200: read on with start_line 201)"), "{first}");
        let wide = run(&root, "read_file", json!({ "path": "src/long.rs", "start_line": 50, "end_line": 1000 }))?;
        assert!(wide.starts_with("src/long.rs: lines 50-449 of 500"), "at most 400 lines a call");
        let last = run(&root, "read_file", json!({ "path": "src/long.rs", "start_line": "490" }))?;
        assert!(last.starts_with("src/long.rs: lines 490-500 of 500") && last.ends_with("line 500"), "{last}");
        assert!(run(&root, "read_file", json!({ "path": "src/long.rs", "start_line": 501 })).unwrap_err().to_string().contains("past the end"));
        assert!(run(&root, "read_file", json!({ "path": "src", "start_line": 1 })).unwrap_err().to_string().contains("is a folder"));
        // Listing stops at its cap with a note.
        for n in 0..250 {
            std::fs::write(root.join(format!("src/f{n:03}.txt")), "x")?;
        }
        let listed = run(&root, "list_files", json!({ "path": "src" }))?;
        assert_eq!(listed.lines().count(), MAX_ENTRIES + 1);
        assert!(listed.ends_with("(stopped at 200 entries; 52 more not shown: list a folder inside, or a smaller max_depth)"), "{listed}");
        let _ = std::fs::remove_dir_all(&base);
        Ok(())
    }
}
