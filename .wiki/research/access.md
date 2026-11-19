# Research: letting agents use credentials safely

[Back to Access](../access.md)

A survey of existing software that could serve as Arity's vault, access broker, or approval step, or that does one of those jobs well enough to copy. It is compared against the design in [Access](../access.md) and [Computers](../computers.md):

- a model never sees a secret;
- an access broker checks the grant, takes the secret from the vault and fills it in on the agent's workspace;
- grants are scoped to a task, temporary, and logged;
- the vault holds passwords, API tokens, signed-in sessions and SSH keys;
- clearance limits credentials, machines and models, down to local models only;
- some actions wait for the user's approval;
- a screen can show private information to a model;
- it is still open whether to build the vault or wrap an existing tool.

**Checked:** 2026-11-18. Every GitHub figure is stars / last push / licence / latest release, from `gh api repos/OWNER/REPO` on that date. **UNVERIFIED** marks anything no source confirmed. **Vendor claim** marks something sourced only from the vendor's own material. Products move fast in this area, so recheck anything you plan to depend on.

## Main findings

1. **Every injector has the same weak point: the process that injects holds the plaintext.** That includes 1Password's `op run`, the egress proxies, browser fillers and Skyvern. So the design question is where that process runs and what can reach it, not whether the secret exists in plaintext. Keep the broker outside the agent's sandbox, and on the same host for local-only tasks.
2. **Three channels are already solved by mature or fast-maturing tools. Arity only needs to glue them together:**
   - **HTTP headers:** placeholder-swapping egress proxies (iron-proxy, Infisical Agent Vault, OneCLI, superfly/tokenizer).
   - **SSH:** agents whose private key never leaves the password manager (1Password, Bitwarden), or short-lived certificates (Teleport).
   - **Browser logins:** fill bound to the origin and done by the runtime, not the model (1Password Agentic Autofill, Steel, Kernel, Browser Use `type_secret`).
3. **1Password is the only vault that covers all four kinds of secret with "caller never sees it" mechanics:**
   - `op run` for environment variables;
   - an SSH agent that keeps the key inside the app;
   - human-approved browser autofill for agents;
   - an audit event stream.

   Its weaknesses: it is proprietary and a cloud service, and its service-account tokens look long-lived.
4. **Several products separate approval from access by having the approval mint the credential**, rather than only pausing a loop: Auth0 CIBA, Permit.io access requests, AP2 signed mandates. This is stronger than Arity's current wording.
5. **Masking screenshots by classifying pixels is unreliable.** Windows Recall's own filter missed card numbers, and the AgentDAM and WebPII benchmarks show leaks. The controls that work are structural:
   - never screenshot while a secret is being entered;
   - mask known sensitive fields in the page before capture;
   - keep the approval UI out of the agent's capture.

---

## 1. Vaults and secret stores

### 1Password (`op` CLI, service accounts, Connect, SSH agent, Events API)

- **What:** commercial password manager by AgileBits. The CLI is closed source. Its exact licence terms are UNVERIFIED, assumed proprietary. The Connect server repo is 232★, pushed 2026-10-29, with no SPDX licence ([github.com/1Password/connect](https://github.com/1Password/connect)). The Kubernetes operator is MIT, v1.12.1, released 2026-10-21 ([github.com/1Password/onepassword-operator](https://github.com/1Password/onepassword-operator)). The Python SDK is MIT, v0.4.2 ([github.com/1Password/onepassword-sdk-python](https://github.com/1Password/onepassword-sdk-python)).
- **Releasing a secret without exposing it:**
  - `op run` resolves `op://vault/item/field` references and starts a child process with the values as environment variables ([docs](https://www.1password.dev/cli/secrets-environment-variables/)).
  - A `--no-masking` flag exists, which suggests stdout masking is on by default. The page does not say so outright, so default masking is UNVERIFIED.
  - The same page warns that other processes running as the same user can read that environment.
  - `op read "...?attribute=otp"` returns the current one-time code ([reference](https://www.1password.dev/cli/reference/commands/read.md)).
- **SSH agent:** the private key never leaves the 1Password app, and each client request is authorised ([docs](https://www.1password.dev/ssh/agent/)).
- **Scope, time and logging:**
  - Service accounts are limited to the vaults and permissions chosen when they are created, and those permissions cannot be changed later.
  - Service accounts cannot reach Personal, Private or Employee vaults or the default Shared vault ([get started](https://www.1password.dev/service-accounts/get-started.md), [overview](https://www.1password.dev/service-accounts/)).
  - Token expiry is not documented. Rotation appears to be manual (UNVERIFIED).
  - The Events API streams item usage and sign-in events. It is documented for 1Password Business ([docs](https://www.1password.dev/events-api/)). Whether other plans get it is UNVERIFIED.
- **Secret visible to:** the `op` process and the child's environment, so anything running as the same user. SSH keys stay inside the app.
- **Fit:** the vault. Make one custom "agents" vault holding only the items agents may be granted, and give each machine's broker a service account scoped to it. The rule that service accounts can't reach Personal vaults then helps: the user's other secrets are out of reach by construction. 1Password's cloud stores ciphertext only, so this does not conflict with local-only clearance. The secret is decrypted on the user's machine. That end-to-end model is general 1Password design and is not re-verified here.
- **Verdict: use as is** for the vault.

### 1Password Secure Agentic Autofill

- **What:** launched 2025-10-08 with Browserbase. When an AI agent's browser reaches a login, 1Password asks the human to approve on their phone or desktop, then fills the page over an end-to-end encrypted (Noise protocol) channel. The credential never enters the model's context or Browserbase's logs ([1Password blog](https://blog.1password.com/closing-the-credential-risk-gap-for-browser-use-ai-agents), [Browserbase](https://www.browserbase.com/1password)).
- **Availability:** early access or Enterprise beta; sources differ ([press release](https://1password.com/press/2025/oct/browserbase-ai-security-partnership), [SiliconANGLE](https://siliconangle.com/2025/10/08/1password-tackles-ai-credential-risks-new-agentic-autofill-integration-browserbase/)).
- **Claude in Chrome** uses it, and Claude sees only which login was used ([Claude support](https://support.claude.com/en/articles/15936181)).
- **Leak:** the paired platform may learn whether a matching item exists ([docs](https://www.1password.dev/agentic-autofill.md)).
- **SDK:** no general SDK for driving it from your own browser is documented ([docs](https://www.1password.dev/agentic-autofill.md)).
- **Secret visible to:** the 1Password extension and the web page only.
- **Fit:** this is Arity's access broker for browser logins, already built, but tied to particular partner platforms.
- **Verdict: reference** (copy the "approve per fill, plaintext only in the page" design). Use it as is if it becomes generally available for self-run browsers.

### Bitwarden (`bw`, Secrets Manager `bws`, SSH agent)

- **What:** Bitwarden Inc.
  - `bitwarden/clients` is GPL-3.0 except the `bitwarden_license` folder. 14.3k★, pushed 2026-11-19, CLI v2026.10.1 released 2026-10-29 ([github](https://github.com/bitwarden/clients)).
  - `bws` sits under a proprietary SDK licence, v2.1.0 released 2026-05-21 ([github.com/bitwarden/sdk-sm](https://github.com/bitwarden/sdk-sm)).
- **Release:**
  - `bws run --project-id` injects one project's secrets as environment variables. Machine-account tokens are limited to the projects they were granted ([docs](https://bitwarden.com/help/secrets-manager-cli/)).
  - `bw get` prints plaintext to stdout, so the broker must capture it ([docs](https://bitwarden.com/help/cli/)).
  - The desktop app's SSH agent keeps keys in the vault, with optional authorisation per use ([docs](https://bitwarden.com/help/ssh-agent/)).
- **Logging:** Secrets Manager event logs from the CLI are UNVERIFIED.
- **Secret visible to:** the CLI process and the child's environment.
- **Fit:** a possible open-source alternative to 1Password. Passwords and machine secrets live in two separate products, and there is no agent autofill.
- **Verdict: reference.** Use it instead of 1Password only if open source matters more than coverage.

### Bitwarden Agent Access SDK and MCP server

- **What:**
  - `bitwarden/agent-access`: Apache-2.0, 284★, pushed 2026-11-16, v0.11.0, "early preview" ([github](https://github.com/bitwarden/agent-access)).
  - `bitwarden/mcp-server`: GPL-3.0 ([github](https://github.com/bitwarden/mcp-server)).
- **How:**
  - `aac connect` returns the password to the caller as JSON.
  - `aac run` injects it into a child's environment over a Noise-encrypted tunnel to the vault ([README](https://github.com/bitwarden/agent-access)).
  - No approval per request is documented.
  - The MCP server sends vault values through the model.
- **Secret visible to:** the agent's own process. With the MCP server, the model too.
- **Verdict: skip** for Arity, because both break "the model never sees it". The tunnel is a reference for reaching a vault on another machine.

### Vaultwarden

- **What:** an unofficial, community-run Rust server compatible with the Bitwarden client API. AGPL-3.0, 71k★, pushed 2026-11-18, v1.37.5 released 2026-11-01 ([github](https://github.com/dani-garcia/vaultwarden)).
- **Unknown:** whether it supports Secrets Manager and SSH-key items is UNVERIFIED.
- **Secret visible to:** the same as the Bitwarden clients.
- **Verdict: reference.** It is a self-hosted backend for `bw`, so a fully local Bitwarden setup is possible.

### HashiCorp Vault and OpenBao

- **What:**
  - Vault (HashiCorp/IBM) has been under BUSL 1.1 since August 2023 ([InfoQ](https://www.infoq.com/news/2023/08/hashicorp-adopts-bsl)). 36.5k★, v2.2.2 released 2026-11-12 ([github](https://github.com/hashicorp/vault)).
  - OpenBao is the MPL-2.0 fork, under the Linux Foundation / OpenSSF ([openbao.org](https://openbao.org/)). 9.1k★, v2.7.2 released 2026-10-26 ([github](https://github.com/openbao/openbao)).
- **Mechanics worth copying:**
  - **Response wrapping:** the secret is placed behind a single-use token with its own short TTL, and an attempt to misuse it is detectable ([Vault docs](https://developer.hashicorp.com/vault/docs/concepts/response-wrapping), [OpenBao docs](https://openbao.org/docs/concepts/response-wrapping/)).
  - **Leases:** every dynamic secret has a lease that is revoked automatically when it expires, and revoking a token revokes its leases ([OpenBao](https://openbao.org/docs/concepts/lease/)).
  - **Fail-closed audit:** audit devices write HMACs of values, and Vault refuses requests if no audit device can log ([docs](https://developer.hashicorp.com/vault/docs/audit)).
  - **Vault Agent** renders secrets to files or environment variables, so the consuming process sees them ([docs](https://developer.hashicorp.com/vault/docs/agent-and-proxy/agent/template)).
- **Secret visible to:** whichever process unwraps the token, or reads the rendered file or environment.
- **Fit:** the best primitives for "grant, then short-lived credential, then audit". But it means running a server for one person, with unsealing and backups, and it has no browser or password-manager features.
- **Verdict: reference.** Copy wrapping, leases and fail-closed audit into the broker. If you ever run one, prefer OpenBao for its licence.

### Infisical

- **What:** Infisical Inc. MIT except `ee/` ([LICENSE](https://github.com/Infisical/infisical/blob/main/LICENSE)). 30.6k★, pushed 2026-11-19, v0.167.12 released 2026-11-17 ([github](https://github.com/Infisical/infisical)).
- **Scope:** Universal Auth tokens have a TTL, a maximum TTL, a maximum number of uses, and trusted-IP limits; the IP limits are paid ([docs](https://infisical.com/docs/documentation/platform/identities/universal-auth)).
- **Logging:** audit logs are paid, and self-hosted needs an enterprise licence ([docs](https://infisical.com/docs/documentation/platform/audit-logs.md), [pricing](https://infisical.com/pricing)).
- **Agent Proxy:** swaps dummy credentials for real ones in HTTP requests. It requires the agent to trust the proxy's certificate authority, and does not handle WebSockets ([docs](https://infisical.com/docs/documentation/platform/agent-proxy/overview)).
- **Fit:** the token limits (TTL plus maximum uses) are a good model for Arity's grants. As a vault it is a server stack, Postgres and Redis, with logging behind a paywall.
- **Verdict: reference.** See Agent Vault under brokers.

### Doppler

- **What:** a SaaS secrets service. The CLI is Apache-2.0, 404★, v3.77.1 released 2026-10-22 ([github](https://github.com/DopplerHQ/cli)).
- **Scope:** service tokens are limited to one config, read-only by default, with an optional maximum age ([docs](https://docs.doppler.com/docs/service-tokens)).
- **Unknown:** self-hosting is reported to be Enterprise only, but the source is a competitor's blog, so UNVERIFIED ([Doppler blog comparison](https://www.doppler.com/blog/infisical-doppler-secrets-management-comparison-2025)).
- **Verdict: skip.** It is cloud only and adds nothing over 1Password or Infisical.

### SOPS + age

- **What:**
  - SOPS (CNCF, getsops): MPL-2.0, 23.8k★, v3.13.3 released 2026-07-23 ([github](https://github.com/getsops/sops)).
  - age (Filippo Valsorda): BSD-3, 24.4k★, v1.3.2 released 2026-08-29 ([github](https://github.com/FiloSottile/age)).
- **Release:**
  - `sops exec-env` gives a child process the secrets as environment variables without writing plaintext to disk.
  - `exec-file` passes a FIFO that can be read once ([docs](https://getsops.io/docs/usage/advanced/)).
- **Scope, time and logging:** none beyond who holds the age key.
- **Verdict: reference / fallback.** Good for the broker's own configuration secrets in git, but it is not a vault for agent grants.

### pass / gopass

- **What:**
  - pass (Jason A. Donenfeld): GPLv2+, one GPG file per secret, synced with git ([passwordstore.org](https://www.passwordstore.org/)). Its latest release is UNVERIFIED.
  - gopass: MIT, 7.3k★, v1.17.4 released 2026-10-10. It has an age backend, and mounted sub-stores can each have their own recipients ([github](https://github.com/gopasspw/gopass), [recipients](https://github.com/gopasspw/gopass/blob/master/docs/commands/recipients.md)).
- **Secret visible to:** the caller's stdout. Anyone holding the unlocked key can read the whole store.
- **Verdict: reference.** Per-mount recipients give rough per-agent scoping, but there is no audit, no TTL and no injection.

### KeePassXC

- **What:** the KeePassXC team. GPL-2/3, 30k★, pushed 2026-11-17, v2.8.0 released 2026-10-21 ([github](https://github.com/keepassxreboot/keepassxc)).
- **SSH:** adds keys to the system ssh-agent while the database is unlocked, with confirm and lifetime options per key ([commit](https://git.tjdev.de/mirror/keepassxc/commit/785a64cc3b3123541657831e615b0e97d3341ca5)). Unlike 1Password, the key does enter ssh-agent.
- **CLI:** `keepassxc-cli` reads entries and TOTP codes. The exact flags are UNVERIFIED against the manual ([user guide](https://keepassxc.org/docs/KeePassXC_UserGuide)).
- **Secret visible to:** the caller's stdout.
- **Fit:** a synced KDBX file is fully local and offline, with no audit and no TTL.
- **Verdict: reference.** It is the fallback if a fully offline vault is ever required, for example a vault for local-only clearance.

### Proton Pass CLI

- **What:** Proton. GPL-3.0, 460★, v2.7.1 released 2026-11-16 ([github](https://github.com/protonpass/pass-cli)).
- **Features:** `pass://` references, environment and template injection, and SSH keys served from its own agent ([docs](https://protonpass.github.io/pass-cli/)).
- **Unknown:** scoped service tokens and audit are UNVERIFIED.
- **Verdict: reference.** A young open-source tool shaped like `op`. Worth rechecking in 2027.

### OS stores: macOS Keychain, Linux Secret Service, systemd-creds

- **macOS Keychain:**
  - Item ACLs can name trusted applications ([Apple](https://developer.apple.com/documentation/security/access-control-lists.md), [`security` man page](https://manpagez.com/man/1/security)).
  - If `/usr/bin/security` itself is trusted, any script can read the item ([Jamf community](https://community.jamf.com/general-discussions-2/yet-another-keychain-security-command-line-tool-question-5926)).
- **Linux Secret Service:** once a collection is unlocked, any process in the session can read it ([spec](https://specifications.freedesktop.org/secret-service/latest-single), [secret-service README](https://github.com/swiesend/secret-service)).
- **systemd-creds:**
  - Gives a single service credentials as files readable only by that service.
  - The files can be encrypted to the TPM and are held in non-swappable memory ([systemd.io](https://systemd.io/CREDENTIALS/)).
- **Fit:** none of these is a vault for agent grants. They are the right place for the broker's own bootstrap secret, such as the 1Password service-account token: a Keychain item whose ACL names the broker binary on macOS, and systemd-creds on Linux.
- **Verdict: use as is for the broker's bootstrap token. Skip as the vault.**

### Teller

- **What:** tellerops. Apache-2.0. Last release v2.0.7, 2024-05-20 ([github](https://github.com/tellerops/teller)).
- **Features:** injects environment variables from several stores. `teller redact` masks secrets in output.
- **Verdict: skip** (stale). Copy the idea of redacting output: scrub tool output for known secret values before the agent sees it.

---

## 2. Brokers and proxies that inject secrets into requests

### iron-proxy (Paradigm)

- **What:** [github.com/paradigmxyz/iron-proxy](https://github.com/paradigmxyz/iron-proxy). 873★, pushed 2026-11-18, Apache-2.0, v0.58.0 released 2026-11-18.
- **How:**
  - A default-deny egress firewall that intercepts TLS and has its own DNS.
  - Swaps placeholder tokens for real secrets in the header, query, path or body.
  - Refuses to send real secrets over plain HTTP.
  - Blocks cloud metadata and loopback addresses.
  - Enforced with nftables or TPROXY ([README](https://github.com/paradigmxyz/iron-proxy)).
- **Secret sources:** environment, file, AWS, Vault KV and 1Password.
- **Logging:** one JSON audit line per request, naming which secrets were swapped, with optional OpenTelemetry.
- **Secret visible to:** the proxy process only.
- **Fit:** the HTTP channel of Arity's access broker, run on the workspace machine or next to it, with grants turned into its placeholder rules.
- **Verdict: use as is** for API tokens. It is the most complete open-source placeholder proxy found, and it runs entirely locally.

### superfly/tokenizer (Fly.io)

- **What:** [github.com/superfly/tokenizer](https://github.com/superfly/tokenizer). 508★, pushed 2026-11-18, Apache-2.0, no tagged releases.
- **How:**
  - The client holds only a secret encrypted ("sealed") to the proxy's public key, sent in a header. Only the proxy can open it.
  - Each sealed secret carries its allowed hosts and a digest of a client auth token, so a stolen blob is useless anywhere else.
  - Processors can inject a header, sign requests (HMAC, AWS SigV4), or run OAuth flows. New tokens come back to the caller sealed again ([README](https://github.com/superfly/tokenizer)).
- **Gaps:** no TTL and no audit log. The client-to-proxy hop assumes a private network.
- **Secret visible to:** the proxy only.
- **Fit:** the cleanest model of what a grant could be: a sealed blob naming the secret, the allowed hosts, the expiry and the task.
- **Verdict: reference** (strong). Copy the grant format even if iron-proxy does the swapping.

### Infisical Agent Vault

- **What:** [github.com/Infisical/agent-vault](https://github.com/Infisical/agent-vault). 2,515★, pushed 2026-11-15. MIT outside `ee/`; GitHub reports NOASSERTION. v0.40.2, "research preview".
- **How:**
  - A local forward proxy with its own certificate authority.
  - Each agent gets its own token, checked when the connection opens, and the upstream is fixed at that point.
  - It strips any credential the agent supplied and injects the real one.
  - Logs leave out bodies and query strings.
  - Infisical notes that setting `HTTPS_PROXY` is not enforcement ([blog](https://infisical.com/blog/agent-vault-the-open-source-credential-proxy-and-vault-for-agents)).
- **Verdict: reference / alternative to iron-proxy.** Its session-per-agent model maps well to grants, but it is young.

### OneCLI

- **What:** [github.com/onecli/onecli](https://github.com/onecli/onecli). 3,646★, pushed 2026-11-09, Apache-2.0, v2.20.0.
- **How:** a Rust gateway that intercepts TLS, with a dashboard. Secrets are encrypted and decrypted per request. Rules match host and path. It can pull from 1Password or Bitwarden ([1Password integration docs](https://onecli.sh/docs/vaults/1password), [write-up](https://dev.to/jonathanfishner/how-onecli-secures-ai-agent-api-keys-without-code-changes-44hk)).
- **Verdict: reference / alternative.** The same pattern as iron-proxy, with a UI.

### Small placeholder proxies

- **What:** ethosagent/warden, CrimsonWorks-Inc/agent-vault, seekritdev/proxy, keycardai/egress-proxy and others. All under about 15★ and created in 2026 ([seekritdev/proxy](https://github.com/seekritdev/proxy)).
- **Verdict: skip as dependencies. Copy two ideas:**
  - Warden blocks upstream responses that echo a swapped secret back to the agent ([PR #403](https://github.com/ethosagent/warden/pull/403)).
  - CrimsonWorks makes placeholders single-use and bound to a destination. This is UNVERIFIED beyond the search summary.

### Teleport

- **What:** Gravitational. AGPL-3.0, 21.1k★, v18.11.10 released 2026-11-16 ([github](https://github.com/gravitational/teleport)).
- **How:**
  - SSH access with short-lived certificates, so there is no static key to hide.
  - App access can rewrite headers to inject signed identity tokens ([docs](https://goteleport.com/docs/enroll-resources/mcp-access/jwt.md)).
  - MCP access has per-tool access control ([docs](https://goteleport.com/docs/ver/19.x/enroll-resources/mcp-access/rbac/)).
  - Machine ID covers bots ([docs](https://goteleport.com/docs/machine-workload-identity/access-guides/mcp/)).
  - Sessions are audited.
- **Unknown:** injecting arbitrary vault secrets as headers is UNVERIFIED.
- **Verdict: reference.** Short-lived SSH certificates are a better answer than hiding SSH keys if Arity ends up with many machines, but Teleport is heavy for one person.

### Pomerium

- **What:** Apache-2.0, 5.1k★, v0.34.1 ([github](https://github.com/pomerium/pomerium)).
- **How:** in MCP mode it holds the upstream OAuth token per user, refreshes it and attaches it when proxying. The client only ever holds a token Pomerium issued ([reference](https://www.pomerium.com/docs/capabilities/mcp/reference), [upstream OAuth](https://www.pomerium.com/docs/capabilities/mcp/mcp-upstream-oauth.md)).
- **Verdict: reference.** The split between the agent's token and the real token is the pattern for OAuth services.

### HashiCorp Boundary

- **What:** [github.com/hashicorp/boundary](https://github.com/hashicorp/boundary) is archived. Community Edition was deprecated in October 2026 ([Infisical blog](https://infisical.com/blog/hashicorp-boundary-community-edition-deprecation)).
- **How:** credential *injection* was only ever in Enterprise. Community Edition only *brokered*, meaning it handed the credential to the user ([docs](https://developer.hashicorp.com/boundary/docs/v0.20.x/configuration/credential-management/configure-credential-injection)).
- **Verdict: skip.** Its distinction between brokering and injecting is useful vocabulary: Arity's "access broker" injects, by that definition.

### CyberArk Secretless Broker

- **What:** Apache-2.0, 399★, v2.0.0 released 2026-09 ([github](https://github.com/cyberark/secretless-broker)).
- **How:** a sidecar that completes the authentication handshake for Postgres, MySQL, SSH or HTTP, so the application never holds the password ([CyberArk blog](https://developer.cyberark.com/blog/connect-to-any-api-without-exposing-your-secrets-secretless-broker/)).
- **Verdict: reference** for databases and other non-HTTP protocols. Activity is low.

### Others

| Project | What it does | Verdict |
|---|---|---|
| [Envoy credential injector](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/credential_injector_filter) | Injects Basic or Bearer credentials for a whole workload, with no per-task grants | Skip |
| [Kong vault references](https://developer.konghq.com/gateway/entities/vault.md) | Inbound gateway, not shaped for agent egress | Skip |
| [Stripe Smokescreen](https://github.com/stripe/smokescreen) | Egress allowlist proxy that does no injection. MIT, 1.4k★ | Reference for domain allowlists |
| [mitmproxy](https://github.com/mitmproxy/mitmproxy) | Scriptable TLS interception. MIT, 47k★ | Prototype only |
| [Vault Agent / Proxy](https://developer.hashicorp.com/vault/docs/agent-and-proxy) | Proxies to Vault, not to third-party APIs | Skip as broker |
| [StrongDM Leash](https://github.com/strongdm/leash) | eBPF and Cedar policy sandbox for agents. Passes API keys through to the agent ([write-up](https://starlog.pages.dev/articles/ai-agents/strongdm-leash)) | Reference for system-call policy |
| [Tailscale Aperture](https://tailscale.com/docs/aperture/what-is-aperture) | Proprietary gateway for AI providers. Callers use a placeholder key and are identified by tailnet identity | Reference |
| [tsidp](https://github.com/tailscale/tsidp) | OIDC provider on your tailnet. BSD-3, v0.0.21 | Reference for local agent identity |
| [Aembit](https://docs.aembit.io/ai-guide/mcp/identity-gateway/concepts-mcp-gateway/index.md) | Proprietary MCP gateway that injects credentials (vendor claim) | Reference |
| [Akeyless SecretlessAI](https://www.helpnetsecurity.com/2025/07/17/akeyless-secretlessai/) | SaaS issuing ephemeral credentials per task (vendor claim) | Skip, cloud only |

---

## 3. Products built for agent credentials

### Standards

- **MCP authorization:** OAuth 2.1.
  - A server must check that the token was issued for it.
  - Passing tokens through is explicitly forbidden.
  - Downstream access uses token exchange ([2025-06-18 spec](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization), [security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)).
  - **Verdict: follow it** for any MCP server Arity exposes.
- **MCP elicitation:**
  - Form-mode elicitation must not ask for passwords, API keys, tokens or payment credentials.
  - URL mode (spec 2025-11-25) sends the user to an out-of-band page instead ([spec](https://modelcontextprotocol.io/specification/draft/client/elicitation)).
  - **Verdict: use as is.** It is the standard shape for "the user enters this secret somewhere the model can't see".
- **Okta Cross App Access (ID-JAG):** an identity-provider-issued assertion is exchanged for a token for the resource app. It is an IETF draft ([Okta](https://support.okta.com/help/s/article/understanding-cross-app-access-with-okta-for-ai-agents), [overview](https://startwithidentity.com/standards/id-jag-cross-app-access/)). **Verdict: reference.**
- **Keycard:** a token service issuing tokens that are scoped to a task, time-limited and revocable, through RFC 8693 token exchange ([blog](https://www.keycard.ai/blog/how-to-authorize-ai-agents-using-token-exchange-open-standards/), vendor claim). **Verdict: reference** for grant semantics.

### OAuth-token platforms

| Product | Where the plaintext token is | Self-host | Verdict |
|---|---|---|---|
| [Auth0 Token Vault](https://auth0.com/ai/docs/intro/token-vault.md) | Auth0's cloud. Your backend receives a short-lived provider token | No | Skip |
| [Arcade.dev](https://www.arcade.dev/blog/arcade-keeps-credentials-away-from-llm) | Injected into the tool runtime, never into the model ([storage](https://www.arcade.dev/blog/where-are-my-tokens-stored)). `arcade-mcp` is MIT, 1.1k★ | VPC option | Reference: "the tool holds the token, not the model" |
| [Composio](https://docs.composio.dev/docs/security/token-custody) | The vendor's cloud | Enterprise only | Skip |
| [Nango](https://nango.dev/docs/guides/platform/free-self-hosting) | Your self-hosted Nango, which injects into proxied calls. ELv2, 13.3k★, v0.72.6 | Yes (free) | Candidate for OAuth refresh if Arity needs many OAuth services |
| [Pipedream Connect](https://pipedream.com/docs/connect/api-proxy) | Pipedream's cloud | No | Skip |
| Descope ([docs](https://docs.descope.com/agentic-identity-hub/connections)), Stytch ([docs](https://stytch.com/docs/guides/connected-apps/ai-agents)), WorkOS ([AuthKit for MCP](https://gofastmcp.com/integrations/authkit.md)) | Vendor cloud. Built for multi-tenant SaaS | No | Skip |
| Clerk, Astrix | Not researched (UNVERIFIED) | | |

### Browser runtimes with credential handling

| Product | Mechanism | Secret visible to | Verdict |
|---|---|---|---|
| [Browser Use](https://docs.browser-use.com/open-source/secrets) (MIT, 121k★, v1.0.6) | `sensitive_data` / `type_secret(name)`: the model sees names, and values are filled after the model call, bound to domains. A `bu_2fa_code` key generates TOTP codes | The agent's own process. **Its docs warn** that screenshots are not redacted, page JavaScript can read the values back, and code the model writes can read them. A past `allowed_domains` bypass was CVE-2025-47241 ([Miggo](https://www.miggo.io/vulnerability-database/cve/CVE-2025-47241)) | Reference. Not a security boundary on its own |
| [Skyvern](https://docs.skyvern.com/credentials/introduction) (AGPL-3.0, 23.5k★, v1.0.58) | The model gets placeholder IDs. Values come from its own vault, Bitwarden, 1Password, Azure, or a webhook. TOTP via seed, a `totp_url` you host, or pushed codes ([2FA docs](https://skyvern.com/docs/developers/credentials/handle-2fa)) | The Skyvern process | Reference. Its webhook and `totp_url` shapes fit a broker; the exact interface is UNVERIFIED |
| [Stagehand](https://docs.stagehand.dev/v4/first-steps/ai-rules.md) (MIT, 26k★) | `%var%` substituted locally, never sent to the model. Experimental, and not in computer-use mode | The local process | Reference |
| [Steel](https://docs.steel.dev/overview/credentials-api/overview) (steel-browser Apache-2.0, 8k★, beta) | Credentials stored per origin and filled by the browser runtime, including TOTP. The agent never receives them | The Steel runtime. Whether this exists in the self-hosted build is UNVERIFIED | Reference (strong): fill bound to origin, done by the runtime |
| [Kernel](https://kernel.sh/docs/auth/agent/faq) (kernel-images Apache-2.0, 1.1k★) | Managed login and re-login, with TOTP and 1Password; credentials never reach the model. SaaS beta | Kernel's cloud | Reference |
| [Anchor Browser](https://docs.anchorbrowser.io/essentials/authenticated-applications) | "Identities" re-run the login, including OTP, when a session expires | Anchor's cloud | Reference for automatic re-login |
| [Browserbase contexts](https://docs.browserbase.com/features/contexts) | Saved cookies and storage, encrypted at rest | Browserbase's cloud | Reference for storing signed-in sessions |
| Hyperbrowser | 1Password fill reported only by a third party ([Towards AI](https://pub.towardsai.net/the-model-should-never-see-your-password-cbf49d49506f)); UNVERIFIED | | Skip |
| [OpenAI ChatGPT agent](https://help.openai.com/en/articles/11752874-chatgpt-agent) | At a login the user takes over the browser, and no screenshots are taken during takeover | The user and the site | Reference for the human fallback. A report that it was discontinued is UNVERIFIED |

**Vendor guidance.**
- **Anthropic:**
  - Don't give the model login information.
  - Use a dedicated VM or container.
  - Allowlist domains.
  - Logged-in apps raise the risk of prompt injection.

  Sources: [computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool.md), [Claude in Chrome safety](https://support.claude.com/en/articles/12902428-use-claude-in-chrome-safely). This matches Arity's two-machine idea in [Computers](../computers.md#two-machines-ideally).

---

## 4. One-time codes and passkeys

- **TOTP from the vault:**
  - **Tools:** `op read ...?attribute=otp` ([1Password](https://www.1password.dev/cli/reference/commands/read.md)), `bw get totp` ([third-party guide](https://privacy-tools-guide.pages.dev/how-to-store-otp-codes-in-password-manager/)) and `keepassxc-cli totp` (flags UNVERIFIED) all return the current code. So the broker can type a code without the model or the user being involved. `oathtool` needs the raw seed in the calling process ([guide](https://moi.vonos.net/security/totp-cli)).
  - **Verdict:** use as is, from inside the broker only.
- **The trade-off of keeping the seed next to the password:**
  - **Against:** if one store is compromised, both factors fall, so two-factor becomes one ([Bitwarden forum](https://community.bitwarden.com/t/security-risks-of-using-bitwarden-as-authenticator-and-password-manager/17028?page=2), [Privacy Guides](https://discuss.privacyguides.net/t/should-i-use-my-password-manager-for-storing-totp-codes/13379)).
  - **For:** it still defeats phishing and credential stuffing ([HN discussion](https://hn.svelte.dev/item/34522104)).
  - **For Arity:** a compromised broker means full takeover of any account whose seed it can read. The agent vault should hold seeds only for accounts the user is content to delegate fully. Email, bank, domain registrar and the vault itself stay on the user's device.
- **Codes sent by email or SMS:**
  - Skyvern's pull model has the site's code fetched from *your* endpoint only when the page asks ([docs](https://skyvern.com/docs/developers/credentials/handle-2fa)). That is the shape for a broker that reads the user's own inbox.
  - AgentMail gives agents their own inboxes on a third-party service ([blog](https://www.agentmail.to/blog/when-your-ai-agent-hits-2fa-or-an-email-otp)). **Verdict: skip** for the user's accounts. Use it only for accounts an agent owns.
  - Twilio positions Verify as a factor for confirming a human's approval of risky agent actions, not as a relay for codes ([fact sheet](https://static0.twilio.com/en-us/user-authentication-identity/verify/fact-sheet)). **Verdict: reference.**
- **Passkeys:**
  - **CDP virtual authenticator:** Chrome DevTools Protocol `WebAuthn.addVirtualAuthenticator` / `addCredential` can load a raw private key ([protocol definition](https://chromium.googlesource.com/devtools/devtools-frontend.git/+/HEAD/node_modules/devtools-protocol/pdl/domains/WebAuthn.pdl), [Playwright example](https://www.corbado.com/blog/passkeys-e2e-playwright-testing-webauthn-virtual-authenticator)). Anything with CDP access then holds the key, and the domain is meant for testing.
  - **Software FIDO2 authenticators:** [virtual-fido](https://github.com/bulwarkid/virtual-fido) (MIT, 1.4k★, last push 2024-08, beta) and [soft-fido2](https://docs.rs/crate/soft-fido2/latest) (AGPL-3.0, experimental) are immature.
  - **Password-manager passkeys:** these are the realistic path. Bitwarden's extension fills stored passkeys ([help](https://bitwarden.com/en-gb/help/storing-passkeys.md)), though user verification is not fully supported ([contributor docs](https://contributing.bitwarden.com/architecture/deep-dives/passkeys/implementations/provider/browser-extension)).
  - **Verdict:** reference the CDP approach; skip software FIDO2; use password-manager passkeys as is. Keep the "user's eyes" gate for passkeys by default.
- **Push to the user's phone:**
  - Auth0 CIBA pushes to Guardian ([docs](https://auth0.com/ai/docs/intro/asynchronous-authorization)).
  - 1Password Agentic Autofill asks on the phone (above).
  - [ntfy](https://github.com/binwiederhier/ntfy) (Apache-2.0, 35.6k★) is a self-hostable push channel. Using it for approvals is my suggestion, not documented prior art.

---

## 5. Approval for sensitive actions

| Mechanism | How it works | Licence / activity | Verdict |
|---|---|---|---|
| [LangGraph / LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) | `interrupt()` and middleware that pauses chosen tools. Decisions: approve, edit, reject, respond | MIT, 45k★, v1.2.22 (2026-11-14) | Reference: the decision set |
| [OpenAI Agents SDK](https://openai.github.io/openai-agents-js/guides/human-in-the-loop) | `needsApproval` per tool, or per action for computer use. The run returns interruptions and resumes from saved state ([Python](https://openai.github.io/openai-agents-python/human_in_the_loop)) | MIT, 30.8k★ | Reference. A critique argues that a pause is not a policy ([Arcjet](https://arcjet.com/learn/needsapproval-and-interrupt-are-not-a-policy)) |
| [Claude Code / Agent SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions) | A hook before each tool returns allow, deny or ask. A deny from a hook holds in every mode. A third-party report says "ask" is auto-approved in the most permissive mode ([report](https://claudeissues.com/issue/77212-pretooluse-hook-permissiondecision-ask-is-silently-auto-approved-under-bypassper), UNVERIFIED) | Proprietary | Reference: enforce with deny, not ask |
| [Auth0 async authorization (CIBA)](https://auth0.com/ai/docs/intro/asynchronous-authorization) | The agent requests, the user approves on their phone, and only then is the token issued. Generally available since 2025-11 ([blog](https://auth0.com/blog/auth0-for-ai-agents-generally-available/)) | SDK Apache-2.0 | Reference (strong): approval mints the credential |
| [Permit.io access request MCP](https://docs.permit.io/ai-security/access-request-mcp/overview) | The agent asks, a human approves, and a role is granted then removed after a one-time action | `permit-mcp`, 4★ | Reference: one-shot grant |
| [Temporal](https://docs.temporal.io/design-patterns/approval) | A signal carries the human decision into a durable wait with a timeout. Record who, why and when ([cookbook](https://docs.temporal.io/ai/cookbook/human-in-the-loop-python)) | MIT, 24.5k★, v1.32.2 | Reference. Use it as is only if approvals must wait days across restarts |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/concepts/agentic-patterns/human-in-the-loop/) | `needsApproval` plus `waitForApproval()` in Workflows | MIT, 6k★ | Reference |
| [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/agents/tools/tool-approval) | `approval_mode="always_require"` per function | Repo returned 404 by API (UNVERIFIED) | Reference |
| [HumanLayer](https://github.com/humanlayer/humanlayer) | Its approval decorator is still in the [docs](https://humanlayer.dev/docs/core/require-approval), but the repo now describes a product for orchestrating coding agents | Apache-2.0, 12.9k★ | Skip (pivoted) |
| [gotoHuman](https://docs.gotohuman.com/human-reviews) | Hosted review inbox | SaaS | Skip: a third party sees the payloads |
| [OpenAI Operator](https://openai.com/blog/introducing-operator) and the [o3 computer-use system card](https://cdn.openai.com/pdf/4375e605-f9a6-438d-bcc8-190599c183a6/o3_cua_system_card.pdf) | Confirms before significant actions. The user takes over for logins and payment. Watch mode on email and finance sites. Refuses banking transactions | Proprietary | Reference |
| [OpenAI safety checks](https://developers.openai.com/api/docs/guides/safety-checks) | Flags for injected instructions, irrelevant domains and sensitive domains; each must be acknowledged before continuing | Proprietary | Reference: "sensitive domain" as an approval trigger |
| [AP2](https://ap2-protocol.org/topics/core-concepts) | Signed mandates. **Intent:** limits the user pre-signs for purchases made while they are absent. **Cart:** the exact basket the user signs while present | Apache-2.0, 3.3k★, v0.2.0 (2026-04) | Reference: model "pay" approval as a signed cart |
| [Stripe Issuing for agents](https://docs.stripe.com/issuing/agents) | Virtual cards per agent with spending limits, merchant categories and single-use options. Apply to get access | Proprietary | Reference. A bank's single-use virtual card does the same for one person |

**A sourced starting list for "always ask".** Arity's pay / send / delete / change settings is a superset of what the vendors use:
- placing orders and sending email ([Operator](https://openai.com/blog/introducing-operator));
- entering logins or payment details, which goes to the human ([Operator](https://openai.com/blog/introducing-operator));
- banking transactions, which Operator refuses outright ([o3 system card](https://cdn.openai.com/pdf/4375e605-f9a6-438d-bcc8-190599c183a6/o3_cua_system_card.pdf));
- actions on sensitive domains such as email and finance ([OpenAI safety checks](https://developers.openai.com/api/docs/guides/safety-checks));
- funds transfers ([Twilio](https://static0.twilio.com/en-us/user-authentication-identity/verify/fact-sheet));
- any in-band request for credentials ([MCP elicitation](https://modelcontextprotocol.io/specification/draft/client/elicitation)).

**My own suggestions, not cited prior art:** accepting terms or consent, creating accounts, granting OAuth permissions, and downloading files.

---

## 6. Masking what the screen shows

| Technique | Notes | Verdict |
|---|---|---|
| **Structural: no screenshots during secret entry** | OpenAI's takeover mode takes no screenshots while the user types ([Operator](https://openai.com/blog/introducing-operator)) | Use: the broker pauses the agent's capture loop while it fills a field |
| **DOM masking before capture** | Session-replay tools are the mature prior art. Sentry masks all text and inputs by default ([docs](https://docs.sentry.io/platforms/javascript/session-replay/privacy/)). PostHog uses a `ph-no-capture` class ([docs](https://posthog.com/docs/session-replay/privacy)). [rrweb](https://github.com/rrweb-io/rrweb) is MIT, 20k★. `maskAllInputs` misses attributes and hidden fields ([OneUptime](https://oneuptime.com/blog/post/2026-08-12-session-replay-without-leaking-pii/view)) | Reference. Reuse their selector lists in a harness-run script that hides `input[type=password]`, `autocomplete="cc-*"` and `one-time-code` fields before each screenshot or accessibility-tree read. I found no agent tool that does this off the shelf |
| **Browser Use placeholders** | It advises turning vision off when secrets are in use, because screenshots can show the values ([docs](https://docs.browser-use.com/customize/sensitive-data)) | Reference |
| **[Presidio image redactor](https://microsoft.github.io/presidio/image-redactor)** | OCR, then entity recognition, then boxes painted over; runs locally. The repo now lives at `data-privacy-stack/presidio` (MIT, 12k★); the reason for the move is UNVERIFIED. The PyPI listing says beta, not production ready ([PyPI](https://pypi.org/project/presidio-image-redactor/)) | Use as a backstop only |
| **OS capture exclusion** | Windows `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` works only on windows the caller owns ([Microsoft Learn](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity)). macOS `NSWindow.sharingType = .none` is reported to be ignored by ScreenCaptureKit from 15.4 ([Apple forums](https://developer.apple.com/forums/thread/792152)) | Use for Arity's own approval window on Windows. macOS is UNVERIFIED. Can't protect third-party windows |
| **Windows Recall's filter** | On by default for passwords, IDs and cards ([Microsoft](https://support.microsoft.com/windows/d404f672-7647-41e5-886c-a3c59680af15)), yet testers found it captured card numbers ([PCWorld](https://www.pcworld.com/article/2870013/windows-recall-still-screens-sensitive-data-at-times-test-shows.html)) | Reference: evidence that classifying screenshots is unreliable |
| **Research** | AgentDAM: web agents, including one that sees only screenshots, leak private data unrelated to their task ([arXiv 2503.09780](https://arxiv.org/html/2503.09780v3)). WebPII: a benchmark for visual PII detection in computer-use agents ([arXiv 2603.17357](https://arxiv.org/html/2603.17357)). Also [GUIGuard-Bench](https://arxiv.org/pdf/2601.18842) and visual prompt injection in [VPI-Bench](https://arxiv.org/pdf/2506.02456) | Reference. WebPII is the benchmark to test any local PII-detection model against; I found no mature off-the-shelf model (UNVERIFIED) |

---

## Comparison table

Roles: **V** = vault, **B** = access broker, **A** = approval step, **S** = screen protection.

| Project | Role | Licence | Local / self-host | Plaintext visible to | Scope / time / log | Verdict |
|---|---|---|---|---|---|---|
| 1Password `op` + service accounts + SSH agent | V | Proprietary | Cloud sync, local decryption | `op` and child process; SSH key stays in app | Per vault / none documented / Events API (Business) | **Use as is** |
| 1Password Agentic Autofill | B, A | Proprietary | Partner platforms only | Extension and page | Per fill, human-approved | Reference |
| Bitwarden `bw` / `bws` / SSH agent | V | GPL-3.0 / proprietary SDK | Yes, with Vaultwarden | CLI and child process | Per project / unknown / unknown | Reference |
| Bitwarden Agent Access | B | Apache-2.0 | Yes | The agent's process | Unknown | Skip |
| OpenBao / Vault | V | MPL-2.0 / BUSL | Yes | Unwrapping process | Leases, wrapping, fail-closed audit | Reference |
| Infisical (+ Agent Vault) | V, B | MIT core | Yes | Proxy process | TTL, max uses / audit paid | Reference |
| KeePassXC | V | GPL | Yes, offline | CLI caller; ssh-agent | None | Reference (offline fallback) |
| SOPS + age, gopass | V | MPL / BSD / MIT | Yes | Caller | None | Reference |
| systemd-creds, Keychain ACL | Broker's own token | OS | Yes | Broker only | Per service or app | **Use as is** (bootstrap) |
| iron-proxy | B | Apache-2.0 | Yes | Proxy only | Placeholder rules / per-request audit | **Use as is** (HTTP) |
| superfly/tokenizer | B | Apache-2.0 | Yes | Proxy only | Host-bound sealed blobs / no log | Reference (grant format) |
| OneCLI | B | Apache-2.0 | Yes | Gateway | Host and path rules | Reference |
| Teleport | B (SSH) | AGPL-3.0 | Yes | None: short-lived certificates | Short-lived / audited | Reference |
| Pomerium | B (OAuth) | Apache-2.0 | Yes | Proxy | Per user | Reference |
| Boundary | B | BUSL, archived | — | — | — | Skip |
| Nango | B (OAuth) | ELv2 | Yes | Nango | Per connection | Candidate |
| Arcade, Composio, Auth0 Token Vault, Pipedream | B (OAuth) | Proprietary | Mostly no | Vendor cloud or tool runtime | Varies | Skip; Arcade as reference |
| Browser Use `sensitive_data` | B (browser) | MIT | Yes | Agent process; screenshots | Domain-bound | Reference |
| Skyvern | B (browser) | AGPL-3.0 | Yes | Skyvern process | Placeholder IDs | Reference |
| Steel / Kernel / Anchor | B (browser) | Mixed | Partly | Runtime | Origin-bound | Reference |
| MCP auth + URL elicitation | B, A | Open spec | — | — | Audience-bound tokens | **Follow** |
| Auth0 CIBA | A | Proprietary | No | — | Approval mints token | Reference |
| LangGraph / OpenAI SDK / Claude Code hooks | A | MIT / proprietary | Yes | — | Per tool | Reference |
| Temporal | A | MIT | Yes | — | Durable waits, audit | Reference |
| AP2, Stripe Issuing | A (payments) | Apache-2.0 / proprietary | — | — | Signed mandates / card limits | Reference |
| Session-replay masking (rrweb, Sentry) | S | MIT | Yes | — | — | Reference |
| Presidio image redactor | S | MIT | Yes | — | — | Use as backstop |
| OS capture exclusion | S | OS | Yes | — | — | Use for own approval UI (Windows) |

---

## Build or wrap the vault

**Recommendation: wrap 1Password as the vault and build only the broker. Assemble the broker partly from existing injectors.**

Reasoning:

1. **The open question's own argument holds up.** Mature tools already handle storing and encrypting secrets, and none of the agent-specific projects found builds its own crypto store if it can avoid it. iron-proxy, OneCLI, Skyvern, Anchor and Kernel all pull from 1Password or Bitwarden ([iron-proxy](https://github.com/paradigmxyz/iron-proxy), [OneCLI](https://onecli.sh/docs/vaults/1password), [Skyvern](https://www.skyvern.com/docs/developers/credentials/store-credentials), [Anchor](https://docs.anchorbrowser.io/essentials/authenticated-applications), [Kernel](https://kernel.sh/docs/auth/agent/faq)).
2. **1Password is the only candidate covering all four secret types in the vault list**, each with a release path that does not hand the secret to the caller:
   - passwords, through agentic autofill or broker-side `op read`;
   - API tokens, through `op run` or an egress proxy that reads from 1Password;
   - SSH keys, through an agent that keeps them in the app;
   - one-time codes, through `?attribute=otp`.

   A dedicated vault plus a scoped service account gives a hard boundary between "what agents may be granted" and everything else, at no extra cost.
3. **What 1Password lacks is exactly the part Arity already plans to build:** grants per task, short lifetimes, logging against the agent run, and clearance. Its service-account tokens appear to have no expiry and its permissions are fixed at creation. So the broker, not 1Password, must enforce time limits and scope within the vault, and record every use.
4. **The broker should be thin and built from parts:**
   - **HTTP API tokens:** iron-proxy as is, with grants turned into its placeholder rules. Enforce it at the network layer, because `HTTPS_PROXY` alone can be bypassed ([Infisical blog](https://infisical.com/blog/agent-vault-the-open-source-credential-proxy-and-vault-for-agents)).
   - **SSH:** forward the 1Password SSH agent socket into the workspace for the life of the grant. Or, later, short-lived certificates.
   - **Browser logins:** a small filler that drives the workspace browser over CDP. It checks the frame's origin against the grant, pauses screenshots, types the value, and never returns it. Take the design from 1Password Agentic Autofill, Steel and Browser Use. Use Agentic Autofill itself if it becomes available for self-run browsers.
   - **Signed-in sessions:** store browser profile state, cookies and storage, as vault items and load them into the workspace browser. The Browserbase contexts and Anchor identities pattern.
   - **Bootstrap:** the broker's own 1Password token lives in systemd-creds or in a Keychain item whose ACL names the broker.
5. **Local-only clearance:** 1Password decrypts on the device, so its cloud never sees plaintext. A cloud-hosted password manager is still a dependency, though. If that is unacceptable for some tasks, a KeePassXC file on the local machine is the offline fallback, behind the same broker interface. **Design the broker so that it talks to a vault interface**, not straight to `op`.

**Main risk:** 1Password is proprietary, so a change to service-account terms or pricing would hurt. The vault interface in point 5 keeps a switch to Bitwarden/Vaultwarden or OpenBao cheap.

---

## What contradicts or would improve the current design

1. **The broker does hold the secret, and for more than a moment.** [Access](../access.md#what-to-build) says it "never holds a secret for longer than the moment of use". That is true of a browser fill. It is not true of an HTTP proxy, which must hold the token for every request the grant allows, nor of an SSH agent. Restate the rule as: *the plaintext lives only in the broker's process, outside the agent's sandbox, for no longer than the grant*. Also state that the broker runs on a machine the agent's own commands cannot reach, which follows from the two-machine idea in [Computers](../computers.md#two-machines-ideally).
2. **The workspace browser is a leak path the page doesn't mention.** Once a value is filled into a page, page JavaScript, code the model writes, or a model with CDP access can read it back ([Browser Use docs](https://docs.browser-use.com/open-source/secrets)). The agent must not have raw CDP or DevTools access to a browser holding a filled secret, and fills should be bound to the origin.
3. **Approval should mint the capability, not just pause.** Auth0 CIBA, Permit.io and AP2 all make the approval produce a single-use credential. [Access](../access.md#approval) treats approval as a gate in front of an existing grant. Making the grant *for that action* come from the approval means a bypassed check cannot act on its own, and the log records exactly what was approved.
4. **Enforce with deny, not with "ask".** The Claude Code report, where a hook's "ask" is auto-approved in permissive mode while a deny holds, is a warning. Approval checks belong in the broker, which the agent cannot configure, not in the agent's harness.
5. **Grants should be bound to a destination.** superfly/tokenizer and Browser Use bind each secret to allowed hosts or domains. Add "where it may be used" to the grant properties next to scoped, temporary and logged. Without it, a granted token can be sent to any server an injected prompt names.
6. **One-time codes don't always need the user's device.** [Access](../access.md#what-the-vault-holds) blocks every one-time code on the user. If a TOTP seed is in the vault, the broker can produce the code itself. Decide per account: seeds for low-value accounts in the agent vault, and high-value accounts (email, bank, registrar, the vault itself) kept on the user's device as now. Passkeys stay with the user.
7. **The screen-protection draft has a clear structure available:**
   - no screenshots while the broker fills a field;
   - harness-side masking of known sensitive fields before every screenshot and accessibility-tree read, using session-replay selector lists;
   - Arity's own approval window excluded from capture where the OS allows;
   - pixel-level redaction (Presidio) only as a backstop, given Recall's misses and the AgentDAM and WebPII results.
8. **Logging should fail closed.** Vault refuses to serve if it cannot write its audit log ([docs](https://developer.hashicorp.com/vault/docs/audit)). The broker should do the same: no log entry, no fill.
9. **Responses can echo secrets back.** Some APIs return the credential they were sent. Scrub responses and tool output for granted values before they reach the agent: Warden's [response blocking](https://github.com/ethosagent/warden/pull/403) and Teller's `redact`.
10. **Payments can be limited outside Arity.** A single-use or spend-limited virtual card ([Stripe Issuing](https://docs.stripe.com/issuing/agents) shows the shape) caps the damage from any grant used to pay, in addition to the approval step.
11. **Signed-in sessions are a credential type with no matching fill mechanism yet.** Store them as browser profile state in the vault and load them into the workspace browser. Expired sessions can be renewed automatically by the broker, as Anchor's identities do. Treat a loaded session as being as sensitive as the password, because prompt injection can act through it ([Anthropic](https://support.claude.com/en/articles/12902428-use-claude-in-chrome-safely)).
