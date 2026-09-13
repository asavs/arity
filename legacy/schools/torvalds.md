# The Linus Torvalds School: Bisectability & The Problem-First Triad

> **Location**: `.arity/schools/torvalds.md`  
> **Tradition**: Linux Kernel, Git Origin (2005–Present)  
> **Primary Source**: `torvalds/linux/Documentation/process/submitting-patches.rst`  
> **Core Axiom**: *"Describe your problem. Convince the reviewer that there is a problem worth fixing and that it makes sense for them to read past the first paragraph."*

---

## 1. The Primary Source: Linux Kernel `submitting-patches.rst`

The canonical standard for commit messages in Git was established by Linus Torvalds in the Linux kernel documentation (`Documentation/process/submitting-patches.rst`). The official instruction states:

> ### Describe your changes
> **Describe your problem.** Whether your patch is a one-line bug fix or 5000 lines of a new feature, there must be an underlying problem that motivated you to do this work. Convince the reviewer that there is a problem worth fixing and that it makes sense for them to read past the first paragraph.
>
> **Describe user-visible impact.** Straight up crashes and lockups are pretty convincing, but not all bugs are that blatant. Even if the problem was spotted during code review, describe the impact you think it can have on users. Keep in mind that the majority of Linux installations run kernels from secondary stable trees or vendor/product-specific trees that cherry-pick only specific patches from upstream, so include anything that could help route your change downstream: provoking circumstances, excerpts from dmesg, crash descriptions, performance regressions, latency spikes, lockups, etc.
>
> **Quantify optimizations and trade-offs.** If you claim improvements in performance, memory consumption, stack footprint, or binary size, include numbers that back them up. But also describe non-obvious costs. Optimizations usually aren't free but trade-offs between CPU, memory, and readability; or, when it comes to heuristics, between different workloads. Describe the expected downsides of your optimization so that the reviewer can weigh costs against benefits.
>
> **Describe your changes in imperative mood**, e.g. "make xyzzy do frotz" instead of "[This patch] makes xyzzy do frotz" or "[I] changed xyzzy to do frotz", as if you are giving orders to the codebase to change its behaviour.

---

## 2. The Inviolable Law: Bisectability

When Linus Torvalds designed Git in 2005, he designed it around **`git bisect`**—the ability to mathematically isolate any bug across 500,000 commits in approximately 19 steps ($\log_2(N)$).

```
Commit A (Compiles) ──► Commit B (BROKEN) ──► Commit C (Fixes B)
                             ▲
                    git bisect halts here!
```

* **The Problem**: Automated regression hunters (`git bisect run <test_script>`) perform a binary search across history. If the bisect lands on an intermediate commit that fails to build or has a broken dependency, the test suite aborts and the historical record is corrupted.
* **The Rule**: Every commit must be atomic, build cleanly with all warnings treated as errors, and pass the test suite in isolation. Never split a single logical change across a broken intermediate commit.

---

## 3. Real Verbatim Commits by Linus Torvalds (from `git/git`)

### Example 1: `symbolic-ref: refuse to set syntactically invalid target`
*(Authored by Linus Torvalds in `git/git`)*

```
symbolic-ref: refuse to set syntactically invalid target

You can feed absolute garbage to symbolic-ref as a target like:

  git symbolic-ref HEAD refs/heads/foo..bar

While this doesn't technically break the repo entirely (our "is it a git
directory" detector looks only for "refs/" at the start), we would never
resolve such a ref, as the ".." is invalid within a refname.

Let's flag these as invalid at creation time to help the caller realize
that what they're asking for is bogus.

A few notes:

  - We use REFNAME_ALLOW_ONELEVEL here, which lets:

     git update-ref refs/heads/foo FETCH_HEAD

    continue to work. It's unclear whether anybody wants to do something
    so odd, but it does work now, so this is erring on the safe side.

  - The existing test checks "foo" without "refs/" at all, which gets
    caught by the check_symref() check in create_symref(). But by
    checking the refname early, we catch it even if someone uses
    "refs/heads/foo..bar".
```

*Analysis:* Notice how Linus immediately shows the bogus command (`git symbolic-ref HEAD refs/heads/foo..bar`), explains why it doesn't break the detector but breaks resolution, states the fix, and then documents the edge cases and why odd behaviors were preserved ("erring on the safe side").

---

### Example 2: `pathspec: don't error out on all-exclusionary pathspec patterns` (SHA: `859b7f1d0e74`)
*(Authored by Linus Torvalds in `git/git`)*

```
pathspec: don't error out on all-exclusionary pathspec patterns

Instead of erroring out and telling the user that they should add a
positive pattern that covers everything else, just _do_ that.

For commands where we honor the current cwd by default (ie grep, ls-files
etc), we make that default positive pathspec be the current working
directory.  And for commands that default to the whole project (ie diff,
log, etc), the default positive pathspec is the whole project.

Signed-off-by: Linus Torvalds <torvalds@linux-foundation.org>
Signed-off-by: Junio C Hamano <gitster@pobox.com>
```

*Analysis:* Pure pragmatic elegance. Identifies an annoying user-facing error message, decides to do the intuitive thing automatically, and defines the exact default behavior for both command classes (cwd-scoped vs project-scoped).

---

## 4. The Torvalds Committer Checklist

- [ ] Does the commit message establish the problem/failure mode before describing the solution?
- [ ] Is the subject line in the imperative mood ("make xyzzy do frotz") without a trailing period?
- [ ] If referencing a previous commit, is it formatted with at least 12 hex chars plus the oneline summary (e.g. `Commit e21d2170f366 ("video: remove unnecessary platform_set_drvdata()")`)?
- [ ] Are trade-offs, alternative approaches, and downstream routing considerations documented?
- [ ] Is the patch completely bisectable (builds and runs cleanly in isolation)?
