# Security

## Reporting

Use GitHub's private vulnerability reporting on this repository (Security tab,
"Report a vulnerability"). Do not open a public issue for an undisclosed problem.
Revoke any exposed credential before reporting it. Reports are handled on a
best-effort basis with no promised timeline.

## Trust model

Arity assumes one trusted person on a trusted workstation. It is not hardened
for hostile multi-user operation, tenant separation, or use as a shared service.

What that means in 1.0.0, concretely:

- **Credentials** are read from environment variables named in `~/.arity/seats.json`.
  Arity never stores a key. A pinned CLI harness (`claude`, `codex`, `agy`) uses that
  tool's own login, which is that tool's responsibility.
- **Tools run in this process.** A library tool is a Python function called directly,
  with no sandbox, no container, and no permission check beyond what the function
  does itself. Anything a model can call, it can call with whatever arguments it
  chooses. Put only tools you would run by hand into `~/.arity/library/tools`.
- **Conversations are on disk in plain text.** Every message, model reply and tool
  output is journaled under `~/.arity/store`, and every kernel's death report under
  `~/.arity/ledger`. Treat that folder like a diary.
- **Bots message bots.** A message from one bot to another is delivered as text and
  the recipient acts on it with its own tools. There is no authority model between
  bots; a compromised or confused bot can ask any other bot to do anything that bot
  can do.
- **The presence lock** under `~/.arity/locks` coordinates processes; it is not an
  access control.
- **Providers see everything you send.** The payload to a model is the tool
  schemas, the system text, and the whole conversation so far, every turn.

Confinement, credential vaults, and per-bot authority are plugs behind the Tools
and Transport seams, and are not in 1.0.0.
