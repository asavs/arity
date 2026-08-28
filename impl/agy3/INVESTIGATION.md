# Antigravity CLI (agy) headless tool-permission investigation

Binary: `C:\Users\example\AppData\Local\agy\bin\agy.exe`
Working dir: `C:\Users\example\Projects\arity\impl\agy3`

## 1. CLI surface (`--help`, `help <sub>`, `agent`, `models`, `plugin list`, `mcp list`)

Top-level flags of interest:
- `--mode` (`accept-edits`, `plan`) — agent execution mode
- `--sandbox` — "Run in a sandbox with terminal restrictions enabled" (adds restrictions, not removes)
- `--project` / `--new-project` — select or create a project (a project = a registered workspace folder)
- `--agent` — pick an agent persona; `agy agent` / `agy agents` currently lists **zero** agents (empty output), so this lever is unavailable.
- `--dangerously-skip-permissions` — banned by task constraints, not used.
- `--print` / `-p` (alias `--prompt`) takes the prompt **as its own value**, not a bare positional arg — must be `--print='...'` or it silently swallows the next flag as the prompt text (`--print --output-format json` fails with "took --output-format as its prompt").
- `--print-timeout` default 5m.
- No `config`, `trust`, or `permissions` subcommand exists anywhere in the CLI. The only administrative subcommands are `agent(s)`, `changelog`, `help`, `install`, `mcp`, `mic-serve`, `plugin(s)`, `update`.

Models: `gemini-3.7-flash-{high,medium,low}`, `gemini-3.6-flash-*`, `gemini-3.5-flash-*`, `gemini-3.1-pro-{high,low}`, `claude-sonnet-4-6`, `claude-opus-4-6-thinking`, `gpt-oss-120b-medium`. No bare `gemini-3.7-flash` id — effort is baked into the model id itself (`--model gemini-3.7-flash-high`), so `--model gemini-3.7-flash --effort high` (as literally specified in the brief) needs to become `--model gemini-3.7-flash-high` (tested via model-resolver log: `Resolving model gemini-3.7-flash-high` when `--effort high` was combined with a plain flash id in one run, so the two flags do compose — but the safer form for the real run is the explicit `-high` id).

`plugin list`: "No imported plugins." `mcp list`: one pre-existing `unityMCP` http server (unrelated, pre-existing).

## 2. Global config files (read-only, NOT modified)

Everything lives under the user's home config; there is **no per-workspace/project-local settings file inside a project directory** (confirmed empirically — after registering `agy3` as a project, no `.gemini/` or `.agy/` directory was created inside `impl\agy3`; a `.gemini/antigravity/artifacts` + `transcript.jsonl` path pattern exists per-workspace only for conversation *artifacts*, not for permission settings).

- `~/.gemini/antigravity-cli/settings.json` — **the authoritative CLI settings file** (per the CLI's own embedded docs: "The CLI is configured via `~/.gemini/antigravity-cli/settings.json`"). Contents at investigation time:
  ```
  { "colorScheme": ..., "model": "Gemini 3.5 Flash (High)",
    "permissions": { "allow": ["command(git diff)"] },
    "trustedWorkspaces": [3 unrelated absolute paths, none = agy3] }
  ```
- `~/.gemini/config/config.json` — a *shared* config (used by the Antigravity IDE too) with `userSettings.globalPermissionGrants.allow` containing 13 entries (`command(npm run)`, `command(git log)`, `command(git show)`, `command(git diff)`, `command(git grep)`, `command(git status)`, `command(Get-ChildItem)`, `command(Test-Path)`, `command(Get-Process)`, `command(Copy-Item)`, `execute_url(localhost)`, `mcp(chrome_devtools/evaluate_script)`). **Empirically these are NOT applied to the standalone CLI's own permission check** — see probe D below; only the one entry duplicated into `antigravity-cli/settings.json` (`command(git diff)`) actually worked.
- `~/.gemini/config/projects/*.json` — one file per registered project, `{id, name, projectResources.resources[].folderUri}`. `--new-project` writes a **new** file here every time it's invoked (agy's own side effect, not a hand-edit by us). No permission/allow data is stored per-project here; project registration only marks a folder as a known workspace.
- `~/.gemini/trustedFolders.json` and `~/.gemini/projects.json` — belong to a *different*, older/adjacent tool (paths reference `C:\Users\example\...`, not this machine's active user paths) — irrelevant leftover, not touched.

None of these were written to. Only `agy` itself wrote a new `~/.gemini/config/projects/<uuid>.json` as the documented side effect of `--new-project`.

## 3. Empirical probes (all from `agy3`, `--output-format json --print-timeout 3m`)

| # | Command (flags after `--print=`) | write_file | shell | Notes |
|---|---|---|---|---|
| A | `--mode accept-edits` | no (`hello.txt` absent) | no | stderr: `jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied.` cli.log: `tool_confirmation_manager.go:188 Print mode: soft-denying tool confirmation "RunCommand" at step 4` |
| B | `--mode accept-edits --sandbox` | no | no | identical denial; `--sandbox` adds restrictions, doesn't help |
| C | `--new-project --mode accept-edits` | **yes** — `hello.txt` created, contents `hi` | no | Registering the folder as a project (`~/.gemini/config/projects/e657a8cd-....json`, name `agy3`, folderUri = this dir) + `accept-edits` mode together auto-approve file *edits*. Shell still denied with the same "command" message. |
| D | `--mode accept-edits`, prompt asks for `git status` (already in the global 13-entry allowlist) | n/a | **no** | Still auto-denied. Proves the 13-entry `~/.gemini/config/config.json` list is NOT consulted by the standalone CLI's command-permission check. |
| D2 | `--mode accept-edits`, prompt asks for exactly `git diff` (the ONE entry literally present in `~/.gemini/antigravity-cli/settings.json`) | n/a | **yes** — ran, model replied `DONE` with no denial in stderr | Confirms the CLI checks an **exact prefix allow-list** sourced only from `~/.gemini/antigravity-cli/settings.json`'s `permissions.allow`, not the shared `config.json`. |
| F | `echo DONE \| agy -i "..."` (piped stdin, interactive mode) | — | — | Hung with no output at all; killed by 20s timeout. Interactive mode needs a real TTY/raw terminal and does not accept piped approvals — not viable headless. |

Effective mechanism: in headless/print mode, any tool call needing the `"command"` permission type is **hard-denied unless its exact command string is present in `permissions.allow` inside `~/.gemini/antigravity-cli/settings.json`** — no `--mode`, `--sandbox`, `--new-project`/project-trust, or shared-config allowlist entry changes this. The CLI's own stderr message states the only two remedies: add an allow-rule to that settings.json, or `--dangerously-skip-permissions`. Both are excluded by the task's hard limits. `write_file` (and presumably `read_file`) DO become usable headless once the folder is registered as a project via `--new-project` and the session runs in `--mode accept-edits`.

## 4. Conclusion / blocker

- **Read + write tools: SOLVED.** `agy --new-project --mode accept-edits --print='...'` (run once per fresh project folder; a plain `--mode accept-edits` run afterward reuses the registered project via `~/.gemini/antigravity-cli/cache/projects.json`) gives a working `write_file` (and by the same trust gate, `read_file`) with no global-settings edits required.
- **Shell tool: BLOCKED**, and blocked *by design*, not by a bug: the `"command"` permission category is gated by an exact-string allowlist that lives only in the global `~/.gemini/antigravity-cli/settings.json`, which the task forbids writing to. No project-local equivalent exists anywhere on disk or in the CLI's subcommands/flags. The only two unlock paths the CLI itself documents (`permissions.allow` edit, or `--dangerously-skip-permissions`) are both excluded by the task's hard limits.
- Because the goal requires read **+ write + shell** together, and shell cannot be legitimately unblocked under these constraints, the real BRIEF.md run (step 4) was **not** attempted — running it would produce writes but the required `python demo.py` shell step would silently no-op/deny exactly like tonight's earlier runs, which is the failure mode this investigation was launched to avoid.
- **What a human would need to change, and where:** add an entry such as `"command(python)"` (or the specific invocation needed) to `permissions.allow` in `C:\Users\example\.gemini\antigravity-cli\settings.json` — the one file the task explicitly says not to write to. There is no narrower, project-scoped file that grants shell access instead.
