//! Git-tree workspace engine: shadow-index `tree_sha` snapshots, restore, fork, diff, and apply.
//!
//! Every thread with an attached git folder runs inside an isolated detached git
//! worktree under `<data_dir>/workspaces/<safe_thread_id>`. Snapshots use a
//! temporary shadow index (`GIT_INDEX_FILE`) so the user's real checkout index
//! is never mutated until `apply_tree` succeeds.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

use anyhow::{Context, Result, anyhow, bail};
use serde::{Deserialize, Serialize};

const TEMP_INDEX_PREFIX: &str = "arity-idx-";
const MAX_PATCH_BYTES: usize = 256 * 1024;
const TRUNCATION_TRAILER: &str = "\n\n[diff truncated at 256 KiB]\n";

static TEMP_INDEX_SEQ: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceInit {
    pub source_dir: String,
    pub repo_root: String,
    pub worktree_root: PathBuf,
    pub work_dir: PathBuf,
    pub base_commit: String,
    pub base_tree_sha: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DiffFileStat {
    pub path: String,
    pub status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceDiff {
    pub base_tree_sha: String,
    pub target_tree_sha: String,
    pub files: Vec<DiffFileStat>,
    pub patch: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApplyResult {
    pub files_applied: u32,
    pub summary: String,
}

/// Build a `git -C <dir>` command with inherited git environment overrides stripped.
fn git_cmd(dir: &Path) -> Command {
    let mut cmd = Command::new("git");
    cmd.env_remove("GIT_DIR")
        .env_remove("GIT_WORK_TREE")
        .env_remove("GIT_INDEX_FILE")
        .env("GIT_TERMINAL_PROMPT", "0")
        .arg("-C")
        .arg(dir);
    cmd
}

fn git_output(dir: &Path, args: &[&str]) -> Result<Output> {
    git_cmd(dir)
        .args(args)
        .output()
        .with_context(|| format!("run git {}", args.join(" ")))
}

fn git_success(dir: &Path, args: &[&str]) -> Result<String> {
    let output = git_output(dir, args)?;
    if !output.status.success() {
        bail!("git {} failed: {}", args.join(" "), command_error(&output));
    }
    output_line(&output)
}

fn output_line(output: &Output) -> Result<String> {
    let text = String::from_utf8(output.stdout.clone()).context("git returned non-UTF-8 output")?;
    Ok(text.trim_end().to_owned())
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

fn safe_thread_id(thread_id: &str) -> String {
    let mut stem: String = thread_id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '-' | '_') {
                c
            } else {
                '_'
            }
        })
        .take(48)
        .collect();
    if stem.is_empty() || stem.chars().all(|c| c == '.') {
        stem = "thread".into();
    }
    let hash = thread_id.bytes().fold(0xcbf29ce484222325_u64, |acc, b| {
        (acc ^ u64::from(b)).wrapping_mul(0x100000001b3)
    });
    format!("{stem}-{hash:016x}")
}

fn resolve_repo_and_source(source_dir: &Path) -> Result<(PathBuf, PathBuf)> {
    let source_real = source_dir
        .canonicalize()
        .with_context(|| format!("canonicalize source_dir {}", source_dir.display()))?;
    let toplevel = git_success(&source_real, &["rev-parse", "--show-toplevel"]).with_context(|| {
        format!(
            "{} is not inside a git repository",
            source_real.display()
        )
    })?;
    let repo_root = PathBuf::from(&toplevel)
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(toplevel));
    if source_real.strip_prefix(&repo_root).is_err() {
        bail!(
            "{} is not inside git repo root {}",
            source_real.display(),
            repo_root.display()
        );
    }
    Ok((repo_root, source_real))
}

fn resolve_git_index_path(repo_or_worktree: &Path) -> Result<PathBuf> {
    let raw = git_success(repo_or_worktree, &["rev-parse", "--git-path", "index"])?;
    let p = PathBuf::from(raw);
    if p.is_absolute() {
        Ok(p)
    } else {
        Ok(repo_or_worktree.join(p))
    }
}

fn clean_arity_idx_in_dir(dir: &Path) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        if name.to_string_lossy().starts_with(TEMP_INDEX_PREFIX) {
            let _ = fs::remove_file(entry.path());
        }
    }
}

/// Remove leftover `arity-idx-*` temporary index files in the git directory
/// of `repo_or_worktree` (and any registered worktree admin directories under it).
pub fn clean_stale_temp_indexes(repo_or_worktree: &Path) {
    let Ok(index_path) = resolve_git_index_path(repo_or_worktree) else {
        return;
    };
    let Some(git_dir) = index_path.parent() else {
        return;
    };
    clean_arity_idx_in_dir(git_dir);

    let worktrees_dir = git_dir.join("worktrees");
    if let Ok(entries) = fs::read_dir(worktrees_dir) {
        for entry in entries.flatten() {
            if entry.path().is_dir() {
                clean_arity_idx_in_dir(&entry.path());
            }
        }
    }
}

/// Compute `(repo_root, worktree_root, work_dir)` for `thread_id` attached to `source_dir`.
///
/// `work_dir` preserves any subdirectory offset of `source_dir` inside `repo_root`.
pub fn worktree_paths(
    data_dir: &Path,
    thread_id: &str,
    source_dir: &Path,
) -> Result<(PathBuf, PathBuf, PathBuf)> {
    let (repo_root, source_real) = resolve_repo_and_source(source_dir)?;
    let rel = source_real.strip_prefix(&repo_root).with_context(|| {
        format!(
            "{} is not inside {}",
            source_real.display(),
            repo_root.display()
        )
    })?;
    let worktree_root = data_dir.join("workspaces").join(safe_thread_id(thread_id));
    let work_dir = if rel.as_os_str().is_empty() {
        worktree_root.clone()
    } else {
        worktree_root.join(rel)
    };
    Ok((repo_root, worktree_root, work_dir))
}

struct TempIndex {
    path: PathBuf,
}

impl TempIndex {
    fn new(worktree_root: &Path) -> Result<Self> {
        let real_index = resolve_git_index_path(worktree_root)?;
        let seq = TEMP_INDEX_SEQ.fetch_add(1, Ordering::Relaxed);
        let temp_path = real_index.with_file_name(format!(
            "{TEMP_INDEX_PREFIX}{}-{seq}",
            std::process::id()
        ));
        let guard = Self { path: temp_path };
        if real_index.is_file() {
            fs::copy(&real_index, &guard.path).with_context(|| {
                format!(
                    "copy git index {} to {}",
                    real_index.display(),
                    guard.path.display()
                )
            })?;
        }
        Ok(guard)
    }

    fn command(&self, worktree_root: &Path) -> Command {
        let mut cmd = git_cmd(worktree_root);
        cmd.env("GIT_INDEX_FILE", &self.path);
        cmd
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

/// Snapshot the full working tree of `worktree_root` into git's object database
/// using a temporary shadow index and return the 40-char hex `tree_sha`.
///
/// Never modifies the real git index of `worktree_root`.
pub fn snapshot_tree(worktree_root: &Path) -> Result<String> {
    let temp_index = TempIndex::new(worktree_root)?;

    let add = temp_index
        .command(worktree_root)
        .args(["add", "-A"])
        .output()
        .context("run git add -A on shadow index")?;
    if !add.status.success() {
        bail!(
            "snapshot_tree git add -A failed: {}",
            command_error(&add)
        );
    }

    let write = temp_index
        .command(worktree_root)
        .args(["write-tree"])
        .output()
        .context("run git write-tree on shadow index")?;
    if !write.status.success() {
        bail!(
            "snapshot_tree git write-tree failed: {}",
            command_error(&write)
        );
    }

    let sha = output_line(&write)?;
    if sha.len() < 40 || !sha.chars().all(|c| c.is_ascii_hexdigit()) {
        bail!("git write-tree returned invalid tree sha: {sha:?}");
    }
    Ok(sha)
}

/// Restore `worktree_root` on disk to match `tree_sha` byte-for-byte,
/// including removing untracked files and directories left behind by aborted tools.
pub fn restore_tree(worktree_root: &Path, tree_sha: &str) -> Result<()> {
    let read = git_output(worktree_root, &["read-tree", "--reset", tree_sha])?;
    if !read.status.success() {
        bail!(
            "restore_tree git read-tree --reset {tree_sha} failed: {}",
            command_error(&read)
        );
    }

    let clean = git_output(worktree_root, &["clean", "-ffd"])?;
    if !clean.status.success() {
        bail!(
            "restore_tree git clean -ffd failed: {}",
            command_error(&clean)
        );
    }

    let checkout = git_output(worktree_root, &["checkout-index", "-f", "-a"])?;
    if !checkout.status.success() {
        bail!(
            "restore_tree git checkout-index -f -a failed: {}",
            command_error(&checkout)
        );
    }

    let verified = snapshot_tree(worktree_root)?;
    if verified != tree_sha {
        bail!(
            "restore_tree verification mismatch: expected {tree_sha}, got {verified}"
        );
    }
    Ok(())
}

fn ensure_detached_worktree(
    repo_root: &Path,
    worktree_root: &Path,
    base_commit: &str,
    recreate: bool,
) -> Result<()> {
    let valid_existing = !recreate
        && worktree_root.is_dir()
        && worktree_root.join(".git").exists()
        && git_output(worktree_root, &["rev-parse", "--is-inside-work-tree"])
            .map(|o| o.status.success())
            .unwrap_or(false);
    if valid_existing {
        return Ok(());
    }

    let parent = worktree_root
        .parent()
        .ok_or_else(|| anyhow!("worktree path has no parent: {}", worktree_root.display()))?;
    fs::create_dir_all(parent)
        .with_context(|| format!("create workspaces dir {}", parent.display()))?;

    if worktree_root.exists() {
        let _ = git_cmd(repo_root)
            .args(["worktree", "remove", "--force"])
            .arg(worktree_root)
            .output();
        if worktree_root.exists() {
            fs::remove_dir_all(worktree_root).with_context(|| {
                format!("remove stale worktree {}", worktree_root.display())
            })?;
        }
    }

    let prune = git_output(repo_root, &["worktree", "prune"])?;
    if !prune.status.success() {
        bail!(
            "git worktree prune failed: {}",
            command_error(&prune)
        );
    }

    let add = git_cmd(repo_root)
        .args(["worktree", "add", "--detach", "--no-checkout"])
        .arg(worktree_root)
        .arg(base_commit)
        .output()
        .context("run git worktree add")?;
    if !add.status.success() {
        bail!(
            "git worktree add failed: {}",
            command_error(&add)
        );
    }
    Ok(())
}

/// Initialize an isolated detached worktree for `thread_id` from `source_dir`.
///
/// Captures uncommitted edits in `source_dir` into `base_tree_sha` without
/// touching `source_dir`'s real index, then materializes `base_tree_sha` in
/// `<data_dir>/workspaces/<safe_thread_id>`.
pub fn init_workspace(
    data_dir: &Path,
    thread_id: &str,
    source_dir: &Path,
) -> Result<WorkspaceInit> {
    let (repo_root, source_real) = resolve_repo_and_source(source_dir)?;
    let base_commit = git_success(&repo_root, &["rev-parse", "--verify", "HEAD"]).with_context(
        || format!("repository {} has no HEAD commit", repo_root.display()),
    )?;
    let base_tree_sha = snapshot_tree(&repo_root)?;

    let (_, worktree_root, work_dir) = worktree_paths(data_dir, thread_id, &source_real)?;
    ensure_detached_worktree(&repo_root, &worktree_root, &base_commit, true)?;
    restore_tree(&worktree_root, &base_tree_sha)?;
    if !work_dir.exists() {
        fs::create_dir_all(&work_dir)
            .with_context(|| format!("create work_dir {}", work_dir.display()))?;
    }

    Ok(WorkspaceInit {
        source_dir: path_text(&source_real)?,
        repo_root: path_text(&repo_root)?,
        worktree_root,
        work_dir,
        base_commit,
        base_tree_sha,
    })
}

/// Ensure the detached worktree for `thread_id` exists and restore it to `target_tree_sha`.
///
/// Returns the `work_dir` corresponding to `source_dir` inside the thread's worktree.
pub fn materialize_workspace(
    data_dir: &Path,
    thread_id: &str,
    source_dir: &Path,
    target_tree_sha: &str,
) -> Result<PathBuf> {
    let (repo_root, worktree_root, work_dir) = worktree_paths(data_dir, thread_id, source_dir)?;
    let base_commit = git_success(&repo_root, &["rev-parse", "--verify", "HEAD"]).with_context(
        || format!("repository {} has no HEAD commit", repo_root.display()),
    )?;
    ensure_detached_worktree(&repo_root, &worktree_root, &base_commit, false)?;
    restore_tree(&worktree_root, target_tree_sha)?;
    if !work_dir.exists() {
        fs::create_dir_all(&work_dir)
            .with_context(|| format!("create work_dir {}", work_dir.display()))?;
    }
    Ok(work_dir)
}

fn parse_name_status_z(bytes: &[u8]) -> Result<Vec<DiffFileStat>> {
    let fields: Vec<&[u8]> = bytes
        .split(|&b| b == 0)
        .filter(|f| !f.is_empty())
        .collect();
    let mut files = Vec::new();
    let mut i = 0;
    while i < fields.len() {
        let code = String::from_utf8_lossy(fields[i]);
        i += 1;
        let status = match code.as_bytes().first().copied() {
            Some(b'A') => "added",
            Some(b'M') | Some(b'T') => "modified",
            Some(b'D') => "deleted",
            Some(b'R') | Some(b'C') => "renamed",
            _ => bail!("git diff returned unknown file status {code}"),
        };
        if i >= fields.len() {
            bail!("git diff returned incomplete changed-file list");
        }
        let mut path = String::from_utf8_lossy(fields[i]).into_owned();
        i += 1;
        if status == "renamed" {
            if i >= fields.len() {
                bail!("git diff returned incomplete rename entry");
            }
            path = String::from_utf8_lossy(fields[i]).into_owned();
            i += 1;
        }
        files.push(DiffFileStat {
            path,
            status: status.to_owned(),
        });
    }
    Ok(files)
}

fn bounded_patch(bytes: &[u8]) -> String {
    let patch = String::from_utf8_lossy(bytes);
    if patch.len() <= MAX_PATCH_BYTES {
        return patch.into_owned();
    }
    let mut end = MAX_PATCH_BYTES.saturating_sub(TRUNCATION_TRAILER.len());
    while end > 0 && !patch.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}{}", &patch[..end], TRUNCATION_TRAILER)
}

/// Pure object-database diff between two `tree_sha` snapshots in `source_dir`'s repository.
pub fn diff_trees(
    source_dir: &Path,
    base_tree_sha: &str,
    target_tree_sha: &str,
) -> Result<WorkspaceDiff> {
    let (repo_root, _) = resolve_repo_and_source(source_dir)?;

    let statuses = git_output(
        &repo_root,
        &[
            "diff",
            "--find-renames",
            "--name-status",
            "-z",
            base_tree_sha,
            target_tree_sha,
        ],
    )?;
    if !statuses.status.success() {
        bail!(
            "git diff --name-status failed: {}",
            command_error(&statuses)
        );
    }
    let files = parse_name_status_z(&statuses.stdout)?;

    let patch_out = git_output(
        &repo_root,
        &["diff", "--find-renames", base_tree_sha, target_tree_sha],
    )?;
    if !patch_out.status.success() {
        bail!("git diff failed: {}", command_error(&patch_out));
    }

    Ok(WorkspaceDiff {
        base_tree_sha: base_tree_sha.to_owned(),
        target_tree_sha: target_tree_sha.to_owned(),
        files,
        patch: bounded_patch(&patch_out.stdout),
    })
}

fn run_git_apply(repo_root: &Path, patch: &[u8], extra_args: &[&str]) -> Result<Output> {
    let mut cmd = git_cmd(repo_root);
    cmd.args(["apply", "--binary"]).args(extra_args);
    let mut child = cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("spawn git apply")?;
    {
        let mut stdin = child
            .stdin
            .take()
            .ok_or_else(|| anyhow!("git apply stdin unavailable"))?;
        stdin.write_all(patch).context("write patch to git apply")?;
    }
    child.wait_with_output().context("wait for git apply")
}

/// Apply the diff from `base_tree_sha` to `target_tree_sha` onto `source_dir`'s checkout.
///
/// Always validates with `--check` first so conflicting changes leave `source_dir` untouched.
pub fn apply_tree(
    source_dir: &Path,
    base_tree_sha: &str,
    target_tree_sha: &str,
) -> Result<ApplyResult> {
    let (repo_root, _) = resolve_repo_and_source(source_dir)?;
    if base_tree_sha == target_tree_sha {
        return Ok(ApplyResult {
            files_applied: 0,
            summary: "No changes to apply".into(),
        });
    }

    let statuses = git_output(
        &repo_root,
        &[
            "diff",
            "--find-renames",
            "--name-status",
            "-z",
            base_tree_sha,
            target_tree_sha,
        ],
    )?;
    if !statuses.status.success() {
        bail!(
            "git diff --name-status failed: {}",
            command_error(&statuses)
        );
    }
    let files = parse_name_status_z(&statuses.stdout)?;

    let patch_out = git_output(
        &repo_root,
        &["diff", "--binary", base_tree_sha, target_tree_sha],
    )?;
    if !patch_out.status.success() {
        bail!(
            "git diff --binary failed: {}",
            command_error(&patch_out)
        );
    }
    let patch = patch_out.stdout;
    if patch.is_empty() {
        return Ok(ApplyResult {
            files_applied: 0,
            summary: "No changes to apply".into(),
        });
    }

    let check_clean = run_git_apply(&repo_root, &patch, &["--check"])?;
    let use_three_way = if check_clean.status.success() {
        false
    } else {
        let check_3way = run_git_apply(&repo_root, &patch, &["--3way", "--check"])?;
        if check_3way.status.success() {
            true
        } else {
            bail!(
                "changes conflict with the target checkout; leaving {} untouched:\n{}",
                repo_root.display(),
                command_error(&check_clean)
            );
        }
    };

    let applied = if use_three_way {
        run_git_apply(&repo_root, &patch, &["--3way"])?
    } else {
        run_git_apply(&repo_root, &patch, &[])?
    };
    if !applied.status.success() {
        bail!("git apply failed: {}", command_error(&applied));
    }

    let files_applied = files.len().try_into().unwrap_or(u32::MAX);
    Ok(ApplyResult {
        files_applied,
        summary: format!("Applied {files_applied} changed file(s)"),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TestDir(PathBuf);

    impl TestDir {
        fn new(name: &str) -> Self {
            static SEQ: AtomicU64 = AtomicU64::new(0);
            let seq = SEQ.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "arity-workspace-{name}-{}-{seq}",
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
        let out = git_output(dir, args).unwrap();
        assert!(
            out.status.success(),
            "git {:?} failed: {}",
            args,
            command_error(&out)
        );
    }

    fn make_repo(name: &str) -> TestDir {
        let dir = TestDir::new(name);
        git_run(&dir.0, &["init"]);
        git_run(&dir.0, &["config", "user.email", "arity@example.invalid"]);
        git_run(&dir.0, &["config", "user.name", "Arity Test"]);
        fs::write(dir.0.join("keep.txt"), "line1\nline2\nline3\nline4\nline5\n").unwrap();
        fs::write(dir.0.join("delete.txt"), "delete me\n").unwrap();
        fs::write(dir.0.join(".gitignore"), "ignored.log\n").unwrap();
        fs::create_dir(dir.0.join("sub")).unwrap();
        fs::write(dir.0.join("sub/nested.txt"), "nested\n").unwrap();
        git_run(&dir.0, &["add", "."]);
        git_run(&dir.0, &["commit", "-m", "base"]);
        dir
    }

    #[test]
    fn init_snapshot_restore_materialize_diff_and_apply_lifecycle() {
        let repo = make_repo("lifecycle");
        let data = TestDir::new("lifecycle-data");

        // Create an uncommitted edit in source_dir before init_workspace.
        fs::write(repo.0.join("dirty_from_user.txt"), "uncommitted\n").unwrap();
        let real_index = resolve_git_index_path(&repo.0).unwrap();
        let index_before = fs::read(&real_index).unwrap();

        let init = init_workspace(&data.0, "thread/main", &repo.0.join("sub")).unwrap();
        assert_eq!(init.work_dir, init.worktree_root.join("sub"));
        assert!(init.work_dir.join("nested.txt").is_file());
        // Uncommitted edit from source_dir is present in the isolated worktree.
        assert_eq!(
            fs::read_to_string(init.worktree_root.join("dirty_from_user.txt")).unwrap(),
            "uncommitted\n"
        );
        // Real index in source_dir was never mutated.
        assert_eq!(fs::read(&real_index).unwrap(), index_before);

        // Modify, add, delete files in the isolated worktree.
        fs::write(
            init.worktree_root.join("keep.txt"),
            "line1\nline2\nline3\nline4\nchanged\n",
        )
        .unwrap();
        fs::write(init.worktree_root.join("added.txt"), "brand new\n").unwrap();
        fs::remove_file(init.worktree_root.join("delete.txt")).unwrap();
        let wt_index = resolve_git_index_path(&init.worktree_root).unwrap();
        let wt_index_before = fs::read(&wt_index).unwrap();

        let snap_sha = snapshot_tree(&init.worktree_root).unwrap();
        assert_eq!(snap_sha.len(), 40);
        assert_ne!(snap_sha, init.base_tree_sha);
        // Worktree's real index was not mutated by snapshot_tree.
        assert_eq!(fs::read(&wt_index).unwrap(), wt_index_before);

        // Dirty the workspace with half-written edits, untracked files/dirs, and colliding files.
        fs::write(init.worktree_root.join("keep.txt"), "half-written trash").unwrap();
        fs::write(init.worktree_root.join("stray.tmp"), "from killed bash").unwrap();
        fs::create_dir_all(init.worktree_root.join("junk_dir/deep")).unwrap();
        fs::write(init.worktree_root.join("junk_dir/deep/x.txt"), "junk").unwrap();
        fs::remove_file(init.worktree_root.join("added.txt")).unwrap();
        fs::create_dir(init.worktree_root.join("added.txt")).unwrap();
        fs::write(init.worktree_root.join("added.txt/collide"), "oops").unwrap();

        // restore_tree brings the worktree back to exact snap_sha byte-for-byte.
        restore_tree(&init.worktree_root, &snap_sha).unwrap();
        assert_eq!(
            fs::read_to_string(init.worktree_root.join("keep.txt")).unwrap(),
            "line1\nline2\nline3\nline4\nchanged\n"
        );
        assert_eq!(
            fs::read_to_string(init.worktree_root.join("added.txt")).unwrap(),
            "brand new\n"
        );
        assert!(!init.worktree_root.join("delete.txt").exists());
        assert!(!init.worktree_root.join("stray.tmp").exists());
        assert!(!init.worktree_root.join("junk_dir").exists());
        assert_eq!(snapshot_tree(&init.worktree_root).unwrap(), snap_sha);

        // Fork / materialize_workspace for a brand-new thread at snap_sha.
        let forked_work_dir =
            materialize_workspace(&data.0, "thread-fork-1", &repo.0.join("sub"), &snap_sha)
                .unwrap();
        let (_, fork_wt_root, _) =
            worktree_paths(&data.0, "thread-fork-1", &repo.0.join("sub")).unwrap();
        assert_eq!(forked_work_dir, fork_wt_root.join("sub"));
        assert_eq!(
            fs::read_to_string(fork_wt_root.join("added.txt")).unwrap(),
            "brand new\n"
        );
        assert_eq!(snapshot_tree(&fork_wt_root).unwrap(), snap_sha);

        // Pure object-DB diff between base_tree_sha and snap_sha.
        let diff = diff_trees(&repo.0, &init.base_tree_sha, &snap_sha).unwrap();
        let kinds: Vec<(&str, &str)> = diff
            .files
            .iter()
            .map(|f| (f.path.as_str(), f.status.as_str()))
            .collect();
        assert!(kinds.contains(&("keep.txt", "modified")), "{kinds:?}");
        assert!(kinds.contains(&("added.txt", "added")), "{kinds:?}");
        assert!(kinds.contains(&("delete.txt", "deleted")), "{kinds:?}");
        assert!(diff.patch.contains("brand new"));

        // Source checkout is still untouched before apply_tree.
        assert_eq!(
            fs::read_to_string(repo.0.join("keep.txt")).unwrap(),
            "line1\nline2\nline3\nline4\nline5\n"
        );
        assert!(!repo.0.join("added.txt").exists());
        assert!(repo.0.join("delete.txt").exists());

        // Apply cleanly onto source_dir.
        let applied = apply_tree(&repo.0, &init.base_tree_sha, &snap_sha).unwrap();
        assert_eq!(applied.files_applied, 3);
        assert_eq!(
            fs::read_to_string(repo.0.join("keep.txt")).unwrap(),
            "line1\nline2\nline3\nline4\nchanged\n"
        );
        assert_eq!(
            fs::read_to_string(repo.0.join("added.txt")).unwrap(),
            "brand new\n"
        );
        assert!(!repo.0.join("delete.txt").exists());

        // No-op apply when base == target.
        let noop = apply_tree(&repo.0, &snap_sha, &snap_sha).unwrap();
        assert_eq!(noop.files_applied, 0);
    }

    #[test]
    fn apply_tree_rejects_conflict_without_touching_source_dir() {
        let repo = make_repo("conflict");
        let data = TestDir::new("conflict-data");
        let init = init_workspace(&data.0, "conflict-thread", &repo.0).unwrap();

        fs::write(
            init.worktree_root.join("keep.txt"),
            "line1\nline2\nline3\nline4\nagent_edit\n",
        )
        .unwrap();
        fs::write(init.worktree_root.join("new_file.txt"), "should not appear\n").unwrap();
        let target_sha = snapshot_tree(&init.worktree_root).unwrap();

        // Conflicting edit on same line in user's checkout.
        fs::write(
            repo.0.join("keep.txt"),
            "line1\nline2\nline3\nline4\nuser_conflict\n",
        )
        .unwrap();

        let err = apply_tree(&repo.0, &init.base_tree_sha, &target_sha)
            .unwrap_err()
            .to_string();
        assert!(err.contains("conflict"), "{err}");
        // Verify source_dir was left completely untouched (no partial file writes).
        assert_eq!(
            fs::read_to_string(repo.0.join("keep.txt")).unwrap(),
            "line1\nline2\nline3\nline4\nuser_conflict\n"
        );
        assert!(!repo.0.join("new_file.txt").exists());
    }

    #[test]
    fn clean_stale_temp_indexes_removes_leftover_files() {
        let repo = make_repo("clean-idx");
        let index_path = resolve_git_index_path(&repo.0).unwrap();
        let stale = index_path.with_file_name("arity-idx-99999-0");
        let stale_lock = index_path.with_file_name("arity-idx-99999-0.lock");
        let unrelated = index_path.with_file_name("keep-me");
        fs::write(&stale, b"stale").unwrap();
        fs::write(&stale_lock, b"lock").unwrap();
        fs::write(&unrelated, b"keep").unwrap();

        clean_stale_temp_indexes(&repo.0);
        assert!(!stale.exists());
        assert!(!stale_lock.exists());
        assert!(unrelated.exists());
        assert!(index_path.exists());
    }

    #[test]
    fn apply_tree_handles_non_overlapping_commit_via_three_way_and_empty_tree_restore() {
        let repo = make_repo("threeway");
        let data = TestDir::new("threeway-data");

        // Prepare a 10-line file so edits at top and bottom do not overlap.
        let ten_lines = (1..=10)
            .map(|n| format!("line {n}"))
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        fs::write(repo.0.join("keep.txt"), &ten_lines).unwrap();
        git_run(&repo.0, &["add", "keep.txt"]);
        git_run(&repo.0, &["commit", "-m", "ten lines"]);

        let init = init_workspace(&data.0, "tw-thread", &repo.0).unwrap();

        // Agent edits line 10 in worktree.
        let agent_text = ten_lines.replace("line 10", "line 10 agent");
        fs::write(init.worktree_root.join("keep.txt"), &agent_text).unwrap();
        let target_sha = snapshot_tree(&init.worktree_root).unwrap();

        // User edits line 7 (inside the 3-line context of line 10) and commits in source_dir,
        // forcing plain `git apply --check` to fail and exercising the `--3way --check` fallback.
        let user_text = ten_lines.replace("line 7\n", "line 7 user\n");
        fs::write(repo.0.join("keep.txt"), &user_text).unwrap();
        git_run(&repo.0, &["add", "keep.txt"]);
        git_run(&repo.0, &["commit", "-m", "user context edit"]);

        let applied = apply_tree(&repo.0, &init.base_tree_sha, &target_sha).unwrap();
        assert_eq!(applied.files_applied, 1);
        let merged = fs::read_to_string(repo.0.join("keep.txt")).unwrap();
        assert!(merged.contains("line 7 user"), "{merged}");
        assert!(merged.contains("line 10 agent"), "{merged}");

        // Restore to an empty tree (all tracked files removed).
        for entry in fs::read_dir(&init.worktree_root).unwrap().flatten() {
            if entry.file_name() == ".git" {
                continue;
            }
            let p = entry.path();
            if p.is_dir() {
                fs::remove_dir_all(p).unwrap();
            } else {
                fs::remove_file(p).unwrap();
            }
        }
        let empty_sha = snapshot_tree(&init.worktree_root).unwrap();
        fs::write(init.worktree_root.join("stray.txt"), "stray").unwrap();
        restore_tree(&init.worktree_root, &empty_sha).unwrap();
        assert!(!init.worktree_root.join("stray.txt").exists());
        assert_eq!(snapshot_tree(&init.worktree_root).unwrap(), empty_sha);

        // Non-git directory fails init_workspace cleanly.
        let plain = TestDir::new("not-a-repo");
        assert!(init_workspace(&data.0, "plain-thread", &plain.0).is_err());
    }
}
