//! Per-conversation git worktrees and review/apply support.
//!
//! A worktree is detached at the source checkout's current commit. Changes are
//! inspected through a temporary index, so review never stages a worker's files
//! or changes the index in the user's checkout.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, anyhow, bail};
use rusqlite::{Connection, OptionalExtension, params};

pub const TABLES: &str = "
CREATE TABLE IF NOT EXISTS worktrees (
    conversation TEXT PRIMARY KEY,
    repo_root TEXT NOT NULL,
    source_dir TEXT NOT NULL,
    worktree_root TEXT NOT NULL,
    work_dir TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' -- active | applied | discarded
);
";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorktreeRecord {
    pub conversation: String,
    pub repo_root: String,
    pub source_dir: String,
    pub worktree_root: String,
    pub work_dir: String,
    pub base_commit: String,
    pub created_at: i64,
    pub status: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DiffFile {
    pub path: String,
    pub status: String,
    pub insertions: u32,
    pub deletions: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorktreeDiff {
    pub conversation: String,
    pub repo_root: String,
    pub work_dir: String,
    pub base_commit: String,
    pub status: String,
    pub files_changed: u32,
    pub insertions: u32,
    pub deletions: u32,
    pub files: Vec<DiffFile>,
    pub patch: String,
    pub truncated: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ApplyOutcome {
    pub files_applied: u32,
    pub summary: String,
}

const MAX_PATCH_BYTES: usize = 64 * 1024;
const TRUNCATION_TRAILER: &str = "\n\n[diff truncated at 64 KiB]\n";
static TEMP_INDEX_SEQUENCE: AtomicU64 = AtomicU64::new(0);

pub fn git_root(dir: &Path) -> Option<PathBuf> {
    let root = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| output_line(&output).ok())
        .map(PathBuf::from)?;
    Command::new("git")
        .arg("-C")
        .arg(&root)
        .args(["rev-parse", "--verify", "HEAD"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|_| root)
}

pub fn ensure_for_conversation(
    conn: &Connection,
    data_dir: &Path,
    conv: &str,
    source_dir: &Path,
) -> Result<Option<WorktreeRecord>> {
    if let Some(record) = get(conn, conv)? {
        if record.status == "active" && Path::new(&record.work_dir).is_dir() {
            return Ok(Some(record));
        }
        if record.status == "active" {
            let _ = Command::new("git")
                .arg("-C")
                .arg(&record.repo_root)
                .args(["worktree", "prune"])
                .output();
        }
    }

    let source_dir = match source_dir.canonicalize() {
        Ok(path) => path,
        Err(_) => return Ok(None),
    };
    let Some(repo_root) = git_root(&source_dir) else {
        return Ok(None);
    };
    let repo_root = repo_root.canonicalize().unwrap_or(repo_root);
    let rel = source_dir.strip_prefix(&repo_root).with_context(|| {
        format!(
            "{} is not inside {}",
            source_dir.display(),
            repo_root.display()
        )
    })?;
    let base_commit = git_success(&repo_root, &["rev-parse", "HEAD"])?;
    let worktree_root = data_dir
        .join("worktrees")
        .join(safe_conversation_name(conv));
    fs::create_dir_all(
        worktree_root
            .parent()
            .ok_or_else(|| anyhow!("worktree location has no parent"))?,
    )?;
    if worktree_root.exists() {
        fs::remove_dir_all(&worktree_root)
            .with_context(|| format!("remove stale worktree {}", worktree_root.display()))?;
    }
    let prune = Command::new("git")
        .arg("-C")
        .arg(&repo_root)
        .args(["worktree", "prune"])
        .output()
        .context("run git worktree prune")?;
    if !prune.status.success() {
        bail!(
            "could not prune stale git worktrees: {}",
            command_error(&prune)
        );
    }
    let output = Command::new("git")
        .arg("-C")
        .arg(&repo_root)
        .args(["worktree", "add", "--detach"])
        .arg(&worktree_root)
        .arg(&base_commit)
        .output()
        .context("run git worktree add")?;
    if !output.status.success() {
        bail!("could not create git worktree: {}", command_error(&output));
    }

    let work_dir = worktree_root.join(rel);
    let record = WorktreeRecord {
        conversation: conv.into(),
        repo_root: path_text(&repo_root)?,
        source_dir: path_text(&source_dir)?,
        worktree_root: path_text(&worktree_root)?,
        work_dir: path_text(&work_dir)?,
        base_commit,
        created_at: now(),
        status: "active".into(),
    };
    if let Err(error) = put(conn, &record) {
        let _ = remove_worktree(&record);
        return Err(error);
    }
    Ok(Some(record))
}

pub fn get(conn: &Connection, conv: &str) -> Result<Option<WorktreeRecord>> {
    conn.query_row(
        "SELECT conversation, repo_root, source_dir, worktree_root, work_dir,
                base_commit, created_at, status
         FROM worktrees WHERE conversation = ?1",
        [conv],
        |row| {
            Ok(WorktreeRecord {
                conversation: row.get(0)?,
                repo_root: row.get(1)?,
                source_dir: row.get(2)?,
                worktree_root: row.get(3)?,
                work_dir: row.get(4)?,
                base_commit: row.get(5)?,
                created_at: row.get(6)?,
                status: row.get(7)?,
            })
        },
    )
    .optional()
    .map_err(Into::into)
}

pub fn work_dir_of(conn: &Connection, conv: &str) -> Result<Option<PathBuf>> {
    Ok(get(conn, conv)?.and_then(|record| {
        let path = PathBuf::from(record.work_dir);
        (record.status == "active" && path.is_dir()).then_some(path)
    }))
}

pub fn diff(conn: &Connection, conv: &str) -> Result<Option<WorktreeDiff>> {
    let Some(record) = get(conn, conv)? else {
        return Ok(None);
    };
    // Once applied or discarded the worktree is gone: say so, with nothing left to review.
    if record.status != "active" {
        return Ok(Some(WorktreeDiff {
            conversation: record.conversation,
            repo_root: record.repo_root,
            work_dir: record.work_dir,
            base_commit: record.base_commit,
            status: record.status,
            files_changed: 0,
            insertions: 0,
            deletions: 0,
            files: vec![],
            patch: String::new(),
            truncated: false,
        }));
    }
    if !Path::new(&record.worktree_root).is_dir() {
        bail!("worktree for conversation {conv} no longer exists");
    }
    let (files, patch_bytes) = staged_changes(&record, false)?;
    let insertions = files.iter().map(|file| file.insertions).sum();
    let deletions = files.iter().map(|file| file.deletions).sum();
    let (patch, truncated) = bounded_patch(&patch_bytes);
    Ok(Some(WorktreeDiff {
        conversation: record.conversation,
        repo_root: record.repo_root,
        work_dir: record.work_dir,
        base_commit: record.base_commit,
        status: record.status,
        files_changed: files.len().try_into().unwrap_or(u32::MAX),
        insertions,
        deletions,
        files,
        patch,
        truncated,
    }))
}

pub fn apply(conn: &Connection, conv: &str) -> Result<ApplyOutcome> {
    let record = get(conn, conv)?.ok_or_else(|| anyhow!("no worktree for conversation {conv}"))?;
    if record.status != "active" {
        bail!(
            "worktree for conversation {conv} was already {}",
            record.status
        );
    }
    if !Path::new(&record.worktree_root).is_dir() {
        bail!("worktree for conversation {conv} no longer exists");
    }
    let (files, patch) = staged_changes(&record, true)?;
    if patch.is_empty() {
        conn.execute(
            "UPDATE worktrees SET status = 'applied' WHERE conversation = ?1 AND status = 'active'",
            [conv],
        )?;
        remove_worktree(&record)?;
        return Ok(ApplyOutcome {
            files_applied: 0,
            summary: "No changes to apply".into(),
        });
    }

    let repo_root = Path::new(&record.repo_root);
    let check = git_apply(repo_root, &patch, true)?;
    if !check.status.success() {
        bail!(
            "changes from conversation {conv} conflict with the main checkout; resolve or stash its \
             local edits and try again:\n{}",
            command_error(&check)
        );
    }
    let applied = git_apply(repo_root, &patch, false)?;
    if !applied.status.success() {
        bail!(
            "git could not apply the reviewed changes: {}",
            command_error(&applied)
        );
    }
    conn.execute(
        "UPDATE worktrees SET status = 'applied' WHERE conversation = ?1 AND status = 'active'",
        [conv],
    )?;
    remove_worktree(&record)?;
    let files_applied = files.len().try_into().unwrap_or(u32::MAX);
    Ok(ApplyOutcome {
        files_applied,
        summary: format!("Applied {files_applied} changed file(s)"),
    })
}

pub fn discard(conn: &Connection, conv: &str) -> Result<bool> {
    let Some(record) = get(conn, conv)? else {
        return Ok(false);
    };
    if record.status != "active" {
        return Ok(false);
    }
    remove_worktree(&record)?;
    conn.execute(
        "UPDATE worktrees SET status = 'discarded' WHERE conversation = ?1 AND status = 'active'",
        [conv],
    )?;
    Ok(true)
}

fn put(conn: &Connection, record: &WorktreeRecord) -> Result<()> {
    conn.execute(
        "INSERT OR REPLACE INTO worktrees (
             conversation, repo_root, source_dir, worktree_root, work_dir,
             base_commit, created_at, status
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            record.conversation,
            record.repo_root,
            record.source_dir,
            record.worktree_root,
            record.work_dir,
            record.base_commit,
            record.created_at,
            record.status,
        ],
    )?;
    Ok(())
}

fn staged_changes(record: &WorktreeRecord, binary: bool) -> Result<(Vec<DiffFile>, Vec<u8>)> {
    let worktree_root = Path::new(&record.worktree_root);
    let index = TempIndex::new(worktree_root)?;
    let add = index
        .command(worktree_root)
        .args(["add", "-A"])
        .output()
        .context("run git add")?;
    if !add.status.success() {
        bail!(
            "could not inspect worktree changes: {}",
            command_error(&add)
        );
    }

    let statuses = index
        .command(worktree_root)
        .args(["diff", "--cached", "--name-status", "-z", "--find-renames"])
        .arg(&record.base_commit)
        .output()
        .context("list changed files")?;
    if !statuses.status.success() {
        bail!("could not list changed files: {}", command_error(&statuses));
    }
    let stats = index
        .command(worktree_root)
        .args(["diff", "--cached", "--numstat", "--find-renames"])
        .arg(&record.base_commit)
        .output()
        .context("measure changed files")?;
    if !stats.status.success() {
        bail!("could not measure changed files: {}", command_error(&stats));
    }
    let mut files = parse_statuses(&statuses.stdout)?;
    attach_stats(&mut files, &stats.stdout)?;

    let mut command = index.command(worktree_root);
    command.args(["diff", "--cached"]);
    if binary {
        command.arg("--binary");
    }
    let patch = command
        .arg(&record.base_commit)
        .output()
        .context("create worktree patch")?;
    if !patch.status.success() {
        bail!("could not create worktree patch: {}", command_error(&patch));
    }
    Ok((files, patch.stdout))
}

fn parse_statuses(bytes: &[u8]) -> Result<Vec<DiffFile>> {
    let fields: Vec<&[u8]> = bytes
        .split(|byte| *byte == 0)
        .filter(|field| !field.is_empty())
        .collect();
    let mut files = Vec::new();
    let mut index = 0;
    while index < fields.len() {
        let code = String::from_utf8_lossy(fields[index]);
        index += 1;
        let status = match code.as_bytes().first().copied() {
            Some(b'A') => "added",
            Some(b'M') | Some(b'T') => "modified",
            Some(b'D') => "deleted",
            Some(b'R') | Some(b'C') => "renamed",
            _ => bail!("git returned unknown file status {code}"),
        };
        if index >= fields.len() {
            bail!("git returned an incomplete changed-file list");
        }
        let mut path = String::from_utf8_lossy(fields[index]).into_owned();
        index += 1;
        if status == "renamed" {
            if index >= fields.len() {
                bail!("git returned an incomplete rename");
            }
            path = String::from_utf8_lossy(fields[index]).into_owned();
            index += 1;
        }
        files.push(DiffFile {
            path,
            status: status.into(),
            insertions: 0,
            deletions: 0,
        });
    }
    Ok(files)
}

fn attach_stats(files: &mut [DiffFile], bytes: &[u8]) -> Result<()> {
    let text = String::from_utf8_lossy(bytes);
    let mut stats = text.lines();
    for file in files {
        let line = stats
            .next()
            .ok_or_else(|| anyhow!("git returned fewer statistics than files"))?;
        let mut columns = line.splitn(3, '\t');
        file.insertions = number_stat(columns.next())?;
        file.deletions = number_stat(columns.next())?;
        if columns.next().is_none() {
            bail!("git returned an incomplete numstat line");
        }
    }
    if stats.next().is_some() {
        bail!("git returned more statistics than files");
    }
    Ok(())
}

fn number_stat(value: Option<&str>) -> Result<u32> {
    match value {
        Some("-") => Ok(0),
        Some(value) => value.parse().context("parse git numstat"),
        None => bail!("git returned an incomplete numstat line"),
    }
}

fn bounded_patch(bytes: &[u8]) -> (String, bool) {
    let patch = String::from_utf8_lossy(bytes);
    if patch.len() <= MAX_PATCH_BYTES {
        return (patch.into_owned(), false);
    }
    let mut end = MAX_PATCH_BYTES.saturating_sub(TRUNCATION_TRAILER.len());
    while !patch.is_char_boundary(end) {
        end -= 1;
    }
    (format!("{}{}", &patch[..end], TRUNCATION_TRAILER), true)
}

fn git_apply(repo_root: &Path, patch: &[u8], check: bool) -> Result<Output> {
    let mut command = Command::new("git");
    command.arg("-C").arg(repo_root).args(["apply", "--binary"]);
    if check {
        command.arg("--check");
    }
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("start git apply")?;
    child
        .stdin
        .take()
        .ok_or_else(|| anyhow!("git apply stdin was unavailable"))?
        .write_all(patch)?;
    child.wait_with_output().context("wait for git apply")
}

fn remove_worktree(record: &WorktreeRecord) -> Result<()> {
    let repo_root = Path::new(&record.repo_root);
    let worktree_root = Path::new(&record.worktree_root);
    let removed = Command::new("git")
        .arg("-C")
        .arg(repo_root)
        .args(["worktree", "remove", "--force"])
        .arg(worktree_root)
        .output();
    if !matches!(removed, Ok(ref output) if output.status.success()) && worktree_root.exists() {
        fs::remove_dir_all(worktree_root)
            .with_context(|| format!("remove worktree {}", worktree_root.display()))?;
    }
    let prune = Command::new("git")
        .arg("-C")
        .arg(repo_root)
        .args(["worktree", "prune"])
        .output()
        .context("run git worktree prune")?;
    if !prune.status.success() {
        bail!("could not prune git worktrees: {}", command_error(&prune));
    }
    Ok(())
}

struct TempIndex {
    path: PathBuf,
}

impl TempIndex {
    fn new(worktree_root: &Path) -> Result<Self> {
        let git_path = git_success(worktree_root, &["rev-parse", "--git-path", "index"])?;
        let real = {
            let path = PathBuf::from(git_path);
            if path.is_absolute() {
                path
            } else {
                worktree_root.join(path)
            }
        };
        let sequence = TEMP_INDEX_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = real.with_file_name(format!(
            "arity-index-{}-{}-{sequence}",
            std::process::id(),
            now()
        ));
        fs::copy(&real, &path).with_context(|| format!("copy git index {}", real.display()))?;
        Ok(Self { path })
    }

    fn command(&self, worktree_root: &Path) -> Command {
        let mut command = Command::new("git");
        command
            .arg("-C")
            .arg(worktree_root)
            .env("GIT_INDEX_FILE", &self.path);
        command
    }
}

impl Drop for TempIndex {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
        let mut lock = self.path.as_os_str().to_owned();
        lock.push(".lock");
        let _ = fs::remove_file(PathBuf::from(lock));
    }
}

fn git_success(dir: &Path, args: &[&str]) -> Result<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .output()
        .with_context(|| format!("run git {}", args.join(" ")))?;
    if !output.status.success() {
        bail!("git {} failed: {}", args.join(" "), command_error(&output));
    }
    output_line(&output)
}

fn output_line(output: &Output) -> Result<String> {
    let line = String::from_utf8(output.stdout.clone()).context("git returned non-UTF-8 output")?;
    Ok(line.trim_end().into())
}

fn command_error(output: &Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if stderr.is_empty() {
        String::from_utf8_lossy(&output.stdout).trim().to_owned()
    } else {
        stderr
    }
}

fn path_text(path: &Path) -> Result<String> {
    path.to_str()
        .map(String::from)
        .ok_or_else(|| anyhow!("path is not valid UTF-8: {}", path.display()))
}

fn safe_conversation_name(conv: &str) -> String {
    let mut stem: String = conv
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .take(48)
        .collect();
    if stem.is_empty() || stem.chars().all(|character| character == '.') {
        stem = "conversation".into();
    }
    let hash = conv.bytes().fold(0xcbf29ce484222325_u64, |hash, byte| {
        (hash ^ u64::from(byte)).wrapping_mul(0x100000001b3)
    });
    format!("{stem}-{hash:016x}")
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TestDir(PathBuf);

    impl TestDir {
        fn new(name: &str) -> Self {
            static SEQUENCE: AtomicU64 = AtomicU64::new(0);
            let sequence = SEQUENCE.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "arity-worktree-{name}-{}-{}-{sequence}",
                std::process::id(),
                now()
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

    fn database() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(TABLES).unwrap();
        conn
    }

    fn run(dir: &Path, args: &[&str]) {
        let output = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(args)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "git {:?}: {}",
            args,
            command_error(&output)
        );
    }

    fn repo(name: &str) -> TestDir {
        let dir = TestDir::new(name);
        run(&dir.0, &["init"]);
        run(&dir.0, &["config", "user.email", "arity@example.invalid"]);
        run(&dir.0, &["config", "user.name", "Arity Test"]);
        fs::write(dir.0.join("keep.txt"), "base\n").unwrap();
        fs::write(dir.0.join("delete.txt"), "delete me\n").unwrap();
        fs::write(dir.0.join(".gitignore"), "ignored.log\n").unwrap();
        fs::create_dir(dir.0.join("sub")).unwrap();
        fs::write(dir.0.join("sub/nested.txt"), "nested\n").unwrap();
        run(&dir.0, &["add", "."]);
        run(&dir.0, &["commit", "-m", "base"]);
        dir
    }

    #[test]
    fn non_git_folder_has_no_worktree() {
        let source = TestDir::new("plain");
        let data = TestDir::new("plain-data");
        let conn = database();
        assert_eq!(
            ensure_for_conversation(&conn, &data.0, "plain", &source.0).unwrap(),
            None
        );
    }

    #[test]
    fn creates_root_and_nested_worktrees() {
        let repo = repo("create");
        let data = TestDir::new("create-data");
        let conn = database();
        let root = ensure_for_conversation(&conn, &data.0, "root/unsafe", &repo.0)
            .unwrap()
            .unwrap();
        assert_eq!(Path::new(&root.work_dir), Path::new(&root.worktree_root));
        assert!(Path::new(&root.work_dir).join("keep.txt").is_file());

        let nested = ensure_for_conversation(&conn, &data.0, "nested", &repo.0.join("sub"))
            .unwrap()
            .unwrap();
        assert_eq!(
            Path::new(&nested.work_dir),
            Path::new(&nested.worktree_root).join("sub")
        );
        assert!(Path::new(&nested.work_dir).join("nested.txt").is_file());
        assert!(
            !Path::new(&root.worktree_root)
                .to_string_lossy()
                .contains("root/unsafe")
        );
        assert!(discard(&conn, "root/unsafe").unwrap());
        assert!(discard(&conn, "nested").unwrap());
    }

    #[test]
    fn diff_includes_all_changes_without_touching_index() {
        let repo = repo("diff");
        let data = TestDir::new("diff-data");
        let conn = database();
        let record = ensure_for_conversation(&conn, &data.0, "diff", &repo.0)
            .unwrap()
            .unwrap();
        let root = Path::new(&record.worktree_root);
        fs::write(root.join("keep.txt"), "changed\n").unwrap();
        fs::remove_file(root.join("delete.txt")).unwrap();
        fs::write(root.join("new.txt"), "new\n").unwrap();
        fs::write(root.join("ignored.log"), "ignored\n").unwrap();
        let before = git_success(root, &["status", "--porcelain"]).unwrap();
        let index = git_success(root, &["rev-parse", "--git-path", "index"]).unwrap();
        let index = PathBuf::from(index);
        let index = if index.is_absolute() {
            index
        } else {
            root.join(index)
        };
        let index_before = fs::read(&index).unwrap();

        let changes = diff(&conn, "diff").unwrap().unwrap();
        let kinds: Vec<(&str, &str)> = changes
            .files
            .iter()
            .map(|file| (file.path.as_str(), file.status.as_str()))
            .collect();
        assert!(kinds.contains(&("keep.txt", "modified")));
        assert!(kinds.contains(&("delete.txt", "deleted")));
        assert!(kinds.contains(&("new.txt", "added")));
        assert!(!changes.files.iter().any(|file| file.path == "ignored.log"));
        assert!(changes.patch.contains("new.txt"));
        assert_eq!(
            git_success(root, &["status", "--porcelain"]).unwrap(),
            before
        );
        assert_eq!(fs::read(index).unwrap(), index_before);
        discard(&conn, "diff").unwrap();
    }

    #[test]
    fn apply_transfers_changes_and_removes_worktree() {
        let repo = repo("apply");
        let data = TestDir::new("apply-data");
        let conn = database();
        let record = ensure_for_conversation(&conn, &data.0, "apply", &repo.0)
            .unwrap()
            .unwrap();
        let root = Path::new(&record.worktree_root);
        fs::write(root.join("keep.txt"), "changed\n").unwrap();
        fs::write(root.join("new.txt"), "new\n").unwrap();
        fs::remove_file(root.join("delete.txt")).unwrap();

        let outcome = apply(&conn, "apply").unwrap();
        assert_eq!(outcome.files_applied, 3);
        assert_eq!(
            fs::read_to_string(repo.0.join("keep.txt")).unwrap(),
            "changed\n"
        );
        assert_eq!(fs::read_to_string(repo.0.join("new.txt")).unwrap(), "new\n");
        assert!(!repo.0.join("delete.txt").exists());
        assert_eq!(get(&conn, "apply").unwrap().unwrap().status, "applied");
        assert!(!root.exists());
    }

    #[test]
    fn apply_reports_conflicts_without_changing_main_checkout() {
        let repo = repo("conflict");
        let data = TestDir::new("conflict-data");
        let conn = database();
        let record = ensure_for_conversation(&conn, &data.0, "conflict", &repo.0)
            .unwrap()
            .unwrap();
        fs::write(
            Path::new(&record.worktree_root).join("keep.txt"),
            "worker\n",
        )
        .unwrap();
        fs::write(repo.0.join("keep.txt"), "main local edit\n").unwrap();

        let error = apply(&conn, "conflict").unwrap_err().to_string();
        assert!(error.contains("conflict"));
        assert!(error.contains("keep.txt"));
        assert_eq!(
            fs::read_to_string(repo.0.join("keep.txt")).unwrap(),
            "main local edit\n"
        );
        assert_eq!(get(&conn, "conflict").unwrap().unwrap().status, "active");
        discard(&conn, "conflict").unwrap();
    }

    #[test]
    fn diff_after_apply_or_discard_reports_status_with_nothing_to_review() {
        let repo = repo("resolved");
        let data = TestDir::new("resolved-data");
        let conn = database();
        assert_eq!(diff(&conn, "none").unwrap(), None);
        for (conv, applied) in [("applied", true), ("discarded", false)] {
            let record = ensure_for_conversation(&conn, &data.0, conv, &repo.0).unwrap().unwrap();
            fs::write(Path::new(&record.worktree_root).join("new.txt"), conv).unwrap();
            let live = diff(&conn, conv).unwrap().unwrap();
            assert_eq!((live.status.as_str(), live.files_changed), ("active", 1));
            if applied {
                apply(&conn, conv).unwrap();
            } else {
                discard(&conn, conv).unwrap();
            }
            let done = diff(&conn, conv).unwrap().unwrap();
            assert_eq!(done.status, conv);
            assert_eq!((done.files_changed, done.insertions, done.deletions), (0, 0, 0));
            assert!(done.files.is_empty() && done.patch.is_empty() && !done.truncated);
            // A resolved worktree no longer hides the folder it was made from.
            assert_eq!(work_dir_of(&conn, conv).unwrap(), None);
            fs::remove_file(repo.0.join("new.txt")).ok();
        }
    }

    #[test]
    fn discard_removes_worktree_only() {
        let repo = repo("discard");
        let data = TestDir::new("discard-data");
        let conn = database();
        let record = ensure_for_conversation(&conn, &data.0, "discard", &repo.0)
            .unwrap()
            .unwrap();
        fs::write(
            Path::new(&record.worktree_root).join("keep.txt"),
            "worker\n",
        )
        .unwrap();

        assert!(discard(&conn, "discard").unwrap());
        assert!(!Path::new(&record.worktree_root).exists());
        assert_eq!(
            fs::read_to_string(repo.0.join("keep.txt")).unwrap(),
            "base\n"
        );
        assert_eq!(get(&conn, "discard").unwrap().unwrap().status, "discarded");
        assert!(!discard(&conn, "discard").unwrap());
    }
}
