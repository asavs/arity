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

Provider credentials are stored as plaintext JSON in `~/.arity/auth.json`. Arity does not
encrypt this file or put it in an operating-system credential store. Its protection is the
access control provided by the current user's filesystem. Do not use a shared account or copy
that file into logs, bug reports, backups with broader access, or a repository. Revoke tokens
if the file may have been disclosed.

Scout's project reader blocks known credential filenames and suffixes, Arity state, Git metadata,
hidden-test directories, and configured role-denied paths. These are path rules, not content-aware
secret scanning. A credential embedded in an otherwise allowed source file can still be returned;
do not keep secrets in source trees made available for research.

### Trial records and replay

Record stores may contain task briefs, candidate output, test results, model and provider
identities, and frozen artifact contents. `arity trial replay <trial-id> --json` intentionally
prints the complete stored trial event payloads, including text or base64 artifact contents
captured in frozen evidence. It is not the redacted graph projection. Treat record stores and
replay output as sensitive data and review them before sharing.

### Scout research capability

Research authority is host-issued. A target must have trusted packaged or programmatic role
provenance, opt into research access, and be named by the `ResearchScope`; the packaged capability
is granted only to Scout. Role documents discovered in project `.arity/roles/` or home
`~/.arity/roles/` are marked untrusted, so they cannot self-grant research access or replace Scout
while retaining its privileged capability. Terrarium hard-rejects dispatch of an untrusted Scout
override. Other untrusted definitions can still be loaded and executed as prompt and role
configuration for ordinary work. Treat them as code-like configuration until an explicit trust
confirmation workflow exists.

These provenance checks describe Arity's roleful packaged dispatch paths. Constructing a low-level
`SandboxToolRunner` with `role=None` and a caller-created scope is an explicit host/test seam that
does not apply role provenance. A role-free runner can also accept custom tools only when it has no
research scope; every scoped runner refuses custom tool overrides. Code allowed to construct these
objects already holds host authority and must not expose them as an untrusted control plane.

Each delegated Scout receives two independent roots: the configured invocation project and the
requesting agent's exact workspace. It can select `project` or `work`, but cannot supply a new root
or enumerate sibling workspaces. Paths must be relative; resolved traversal and symlink escapes are
rejected. Automatic project discovery looks upward for a specific project marker and both explicit
and discovered configuration reject the filesystem root and the user's home directory. A bound
work root rejects those same broad locations. Reads, lists, and literal searches have file, line,
scan, result, and output limits.

For direct Secretary/Voice chat, `work` is the role's exact session workspace; within Terrarium it
is the requesting candidate's exact workspace. The requester does not receive the cross-boundary
research tools itself. Only the separately dispatched, trusted Scout receives that scope.

`query_records` is distinct from full replay and the `watch` projection. It opens the configured
JSONL or SQLite store through a bounded query-only reader, accepts only named record kinds and
public projected filter fields, and returns a positive field allowlist only from validated completed
history. Evidence bundles, evaluations, and resolutions must themselves be exact members of the
validated resolution lifecycle; auxiliary rows must link to its task, candidate, or evidence as
their record kind requires. Current and inherited delegation task IDs are excluded. The projection
omits raw model/provider identities, prompts, outputs, artifact bodies, hidden-test bodies,
credentials, and unknown fields. Stored IDs become HMAC-based `ref-...` values that are stable only
within the same host-issued scope, including copies bound to a requester workspace. They support
correlation; they are not anonymization, authentication, or a promise that the remaining metrics
cannot identify a run when combined with other knowledge.

Reader and lifecycle validation provide structural and relationship integrity checks, not record
origin authentication. A process or user that can modify the store can create a self-consistent
forgery. Do not use the research projection as proof of who produced a record.

Every trusted role marked `research_access` is sealed at the application layer, even when no active
scope was issued: research runners expose only the research/message tool allowlist, custom tool
overrides are refused, and the personality cannot select MCP, shell, or other custom runner axes.
Execution also rejects CLI/OMP harnesses and any provider graph containing CLI-owned tools or
fallbacks. Without a valid scope, cross-boundary project, record, and web tools remain unavailable.
These checks do not create process, container, kernel, or network isolation. Files remain readable
by the Arity process itself, and web lookup and messaging have external effects.

`fetch_url` accepts only HTTP(S), rejects URL user information and local, metadata, single-label,
or non-global literal addresses, resolves hostnames and requires every returned address to be
global, and revalidates each redirect before following it. It bounds response bytes and returned
text. Fetches accept a caller value from 1 to 30 seconds as their socket timeout, but this is not a
hard end-to-end deadline. DNS validation is not pinned to the subsequent connection, so a
DNS-rebinding or validation-to-connect race remains. When a queryless direct fetch fails, is empty,
or appears to require JavaScript, it may fall back to the `r.jina.ai` reader proxy. A target URL
with a query string is never sent to that proxy: it receives one direct attempt, cross-origin
redirects are rejected by normalized scheme/host/port, and failure returns a canned error. The
reader proxy, target server, DNS resolver, and configured search provider can still observe the
requests they do receive. Never put credentials or private data in a Scout URL or search query.
`web_search` caps query length and result count, but its provider response body and rendered
snippets do not yet have independent byte ceilings. An isolated browser and stronger DNS
connection binding are future work, not current protections.

### Model-directed execution

Models can request tools. Built-in tool handlers, `LocalToolRunner`, and CLI harnesses can read
or write files and execute commands with the permissions of the Arity process. A candidate
workspace or selected working directory only chooses a starting directory; it does not confine
the process. A model-directed CLI can bring capabilities outside Arity's role-denial checks.
Treat model output and external content as untrusted instructions.

Direct Tier-0 chat never calls a CLI-owned provider node. A bare CLI, or a wrapper whose usable
primary chain ends in one, is rejected. If an otherwise usable wire wrapper has a clean primary but
CLI-owned fallback or alternate branches, Arity selects the clean primary and deliberately discards
those branches. Ordinary non-research roles can still run as explicit CLI candidates in races and
remain a known current-user-permission risk even when their working directory is set to the
candidate workspace. Trusted `research_access` personalities reject those axes regardless of
whether they received an active research scope.

For stronger containment, run Arity as a dedicated least-privilege OS user or inside an
appropriately isolated VM or container, expose only required files and network destinations,
and use separate, narrowly scoped credentials. Arity is not presently a multi-tenant security
boundary even when deployed inside such an environment.
