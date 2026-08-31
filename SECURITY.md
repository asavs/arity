# Security Policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository: open the **Security** tab,
choose **Report a vulnerability**, and submit a private advisory. Do not report an undisclosed
vulnerability in a public issue, discussion, or pull request. Revoke any exposed credential
before reporting it, and include only the sanitized detail needed to reproduce the problem.

Reports and fixes are handled on a best-effort basis. Arity does not currently promise an
acknowledgement, remediation, release, or disclosure SLA.

## Supported versions

Arity does not yet maintain a formal supported-version matrix. Reproduce reports against the
latest release or the default branch when it is safe to do so. Older releases may not receive
security backports.

## Trust model and known limitations

Arity currently assumes one trusted user on a trusted workstation. It is not designed or
hardened for hostile multi-user operation, tenant separation, or use as a shared execution
service. Application-level role denials, workspace paths, subprocess working directories, and
tool argument checks reduce accidental reach; they are not an operating-system security
boundary.

### Credentials

Provider credentials are stored as plaintext JSON in `~/.gorkbot/auth.json`. Arity does not
encrypt this file or put it in an operating-system credential store. Its protection is the
access control provided by the current user's filesystem. Do not use a shared account or copy
that file into logs, bug reports, backups with broader access, or a repository. Revoke tokens
if the file may have been disclosed.

### Trial records and replay

Record stores may contain task briefs, candidate output, test results, model and provider
identities, and frozen artifact contents. `arity trial replay <trial-id> --json` intentionally
prints the complete stored trial event payloads, including text or base64 artifact contents
captured in frozen evidence. It is not the redacted graph projection. Treat record stores and
replay output as sensitive data and review them before sharing.

### Model-directed execution

Models can request tools. Built-in tool handlers, `LocalToolRunner`, and CLI harnesses can read
or write files and execute commands with the permissions of the Arity process. A candidate
workspace or selected working directory does not create an OS sandbox, and a model-directed
CLI can bring capabilities outside Arity's role-denial checks. Treat model output and external
content as untrusted instructions.

For stronger containment, run Arity as a dedicated least-privilege OS user or inside an
appropriately isolated VM or container, expose only required files and network destinations,
and use separate, narrowly scoped credentials. Arity is not presently a multi-tenant security
boundary even when deployed inside such an environment.
