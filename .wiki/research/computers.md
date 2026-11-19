# Research: giving agents computers

[Back to index](../README.md) · Compares against [Computers](../computers.md), [Instantiate](../instantiate.md) and [Access](../access.md)

All sources were checked on **2026-11-18**. Each claim carries its source link.

- **(vendor)** means the vendor's own docs or repository.
- **(secondary)** means a third-party article, often written by a competitor. Treat these claims as plausible but unconfirmed.
- **Unverified** marks anything I could not confirm at all.

Search summaries were the main tool, and few vendor pages were read in full. Two documents were read directly: Apple's macOS licence text and the trycua/cua repository. Prices change often, so check them again before relying on them.

## What Arity needs, restated

| Need | From |
|---|---|
| A pool of the user's own machines (a Mac with Xcode, a PC with an RTX card) on a private network, plus cloud machines bought on demand | [Computers](../computers.md#the-computer-pool) |
| Tasks matched to machines by typed requirements | [Computers](../computers.md#how-a-task-gets-a-computer) |
| A workspace that runs while the agent is live, sleeps with its disk kept when the agent is cold, resumes on wake and is released at the end | [Computers](../computers.md#workspaces-follow-the-agents-life) |
| The agent runs on one machine and executes its commands on another | [Computers](../computers.md#two-machines-ideally) |
| Agents operating a graphical desktop | [Computers](../computers.md), [Access](../access.md#what-the-screen-shows) |

---

## 1. Sandbox and microVM services built for agents

### E2B
- **What:** A cloud service of Firecracker microVM sandboxes for agents, run by E2B. The infrastructure is open source. ([e2b.dev/open-source](https://e2b.dev/open-source.md), vendor)
- **Licence:** Apache-2.0 for the SDKs, CLI, control plane and Firecracker runtime ([e2b-dev/infra](https://awesome.ecosyste.ms/projects/github.com%2Fe2b-dev%2Finfra), [developersdigest](https://www.developersdigest.tech/agent-sandboxes/e2b-runtime-oss), secondary). Self-hosting is reported to run through Terraform on GCP, with AWS in beta. A single-node "E2B Embed" option needs a Linux host with KVM ([developersdigest](https://www.developersdigest.tech/agent-sandboxes/e2b-runtime-oss), secondary). One guide calls self-hosting heavy because it needs Nomad and Consul ([beam.cloud](https://www.beam.cloud/blog/how-to-self-host-code-sandbox), secondary).
- **Maturity:** One of the most widely compared agent sandboxes ([techplained](https://www.techplained.com/best-ai-agent-sandboxes), secondary).
- **Pricing:** Billed per second. The default 2 vCPU sandbox is about $0.000028/s, with memory billed separately ([morphllm](https://www.morphllm.com/e2b-pricing), secondary). Unverified against E2B's own pricing page.
- **Start, snapshot, resume:** Pause keeps both the filesystem and memory. Paused sandboxes are reported to be kept indefinitely and not billed for compute, and `auto_resume` wakes one on the next SDK call. Pausing takes about 4 s per GiB of RAM and resuming about 1 s. Starting from a template takes about 150 ms. Sources disagree on whether pausing stops billing; the older source says no. ([fly.io](https://fly.io/learn/ai-sandbox-pricing/), [blaxel](https://blaxel.ai/blog/blaxel-vs-e2b), [agent37](https://www.agent37.com/blog/e2b-pricing), all secondary)
- **OS and GPU:** Linux only. The sandbox runtime does not support GPUs ([developersdigest](https://www.developersdigest.tech/agent-sandboxes/e2b-runtime-oss), secondary).
- **How commands arrive:** Through the Python and TypeScript SDKs over E2B's API, from anywhere.
- **Fit:** A close match to Arity's live / cold / woken / finished cycle. Pausing with memory is better than Arity's "stopped, disk kept" because the agent's processes survive as well. It also suits the two-machine split, since the agent calls the sandbox over an API.
- **Verdict: use as is** for Linux workspaces that need no GPU. Self-hosting remains a fallback because the licence is Apache-2.0.

### Daytona
- **What:** Sandboxes for agents from Daytona. Stopped sandboxes can be archived to object storage, and a snapshot can be taken from a stopped sandbox ([Daytona persistence docs](https://www.daytona.io/docs/en/persistence.md), vendor). Sandboxes auto-stop after 15 minutes idle by default ([mintlify mirror of Daytona docs](https://www.mintlify.com/daytonaio/daytona/advanced/auto-lifecycle), secondary).
- **Licence and maintenance:** The public repository was AGPL-3.0. On 11 June 2026 Daytona said it was going closed source, and the public repository is no longer maintained ([Daytona blog](https://www.daytona.io/dotfiles/updates/daytona-is-going-closed-source), vendor). A community fork called Nightona continues from v0.190.0 ([Nightona README](https://cdn.jsdelivr.net/gh/nightona-co/nightona@main/README.md), the fork's own claim).
- **Pricing:** Per second. An H100 is about $3.95/h, and preemptible GPUs are about 42% cheaper ([thundercompute](https://www.thundercompute.com/blog/daytona-vs-thunder-compute), secondary, from a competitor). See also [Daytona billing](https://www.daytona.io/docs/billing/).
- **GPU:** GPU sandboxes are reported to be deleted when stopped rather than archived, and to support filesystem snapshots only ([thundercompute](https://www.thundercompute.com/blog/modal-vs-daytona), secondary).
- **Fit:** CPU sandboxes match the sleep and resume cycle. GPU sandboxes do not, because they lose their state on stop.
- **Verdict: use as a reference.** Moving to closed source is a vendor risk, and the GPU lifecycle does not fit project agents.

### Modal Sandboxes
- **What:** Modal's serverless platform also offers sandboxes, including GPU sandboxes on more than 10 GPU types, from T4 to B300 ([Modal resources](https://modal.com/resources/best-stateful-sandboxes-long-running-agent-sessions), vendor).
- **Snapshots:** Filesystem snapshots are the stable option and are kept for 30 days by default. Memory snapshots are in alpha, do not support GPUs, end the sandbox when taken, and restore as a new sandbox rather than resuming the old one. A sandbox runs for at most 24 hours. ([Modal resources](https://modal.com/resources/best-stateful-sandboxes-long-running-agent-sessions), vendor; [fly.io comparison](https://fly.io/learn/fly-vs-modal/), secondary)
- **Licence and pricing:** A proprietary service billed by usage. Rates were not checked.
- **How commands arrive:** Through Modal's Python SDK, from anywhere.
- **Fit:** The strongest option for short GPU work. For a cold project agent, "sleep" becomes "take a filesystem snapshot, then restore into a new sandbox", which works as long as Arity treats a workspace as files rather than as a machine.
- **Verdict: use as is** for GPU subagents and bursty GPU jobs.

### Fly.io Sprites
- **What:** Persistent Firecracker microVMs from Fly.io with a 100 GB filesystem, built for coding agents ([fly.io](https://fly.io/learn/sprites-for-ai-agents/), vendor). Launched in January 2026 ([Simon Willison](https://simonwillison.net/2026/jan/9/sprites-dev), [DevClass](https://www.devclass.com/ai-ml/2026/01/13/flyio-introduces-sprites-lightweight-persistent-vms-to-isolate-agentic-ai/4079557)) and listed on the Thoughtworks Radar ([Thoughtworks](https://www.thoughtworks.com/radar/platforms/sprites)).
- **Snapshots:** Checkpoints save the writable layer, are versioned v0, v1, v2 and so on, restore in place, and do not expire ([Sprites checkpoint docs](https://fly.io/sprites/checkpoint-restore.md), vendor). Restoring is about 1 s; the 300 ms figure is the time to create a checkpoint ([boxd](https://boxd.sh/blog/boxd-vs-sprites), secondary, from a competitor).
- **OS and GPU:** Linux. No GPU found.
- **Fit:** The closest match to a long-lived project agent's workspace: the computer persists, the agent keeps its identity, and checkpoints are cheap. Checkpoints also suit "try it, roll back if it breaks" for risky steps.
- **Verdict: use as is**, as the default home for project-agent Linux workspaces, alongside E2B. Pricing was not checked.

### Morph Cloud (Infinibranch)
- **What:** VMs that can be snapshotted, branched and restored in under 250 ms ([Morph docs](https://cloud.morph.so/docs/developers), vendor; the figure has no stated percentile, per [devtune](https://devtune.ai/verticals/ai-code-sandboxes-agent-runtimes/morph.md)). Branches start from a snapshot ([Morph branch docs](https://cloud.morph.so/docs/documentation/instances/branch), vendor), and Devboxes are the workspace product built on them ([Morph Devboxes](https://cloud.morph.so/web/product/devboxes), vendor).
- **Fit:** Branching maps directly onto Arity's **fork**. A forked agent could receive a branched copy of its parent's machine as well as its context.
- **Verdict: use as a reference** for the fork-a-workspace idea. Adopt it only if forking workspaces becomes common.

### Vercel Sandbox
- **What:** Firecracker microVMs. Sandboxes are persistent by default: a snapshot is taken when a session stops and the next session resumes from it ([Vercel persistent sandboxes](https://vercel.com/docs/sandbox/concepts/persistent-sandboxes.md), vendor). A session lasts at most 45 minutes on Hobby and 24 hours on Pro. Runs only in the iad1 region. ([Vercel pricing](https://vercel.com/docs/sandbox/pricing), vendor; region claim secondary)
- **Verdict: skip.** The session limits and single region suit short tasks, not workspaces that last weeks.

### Cloudflare Sandbox SDK
- **What:** Containers on Cloudflare, generally available since April 2026 ([InfoQ](https://infoq.com/news/2026/04/cloudflare-sandboxes-ga), [Cloudflare blog](https://blog.cloudflare.com/sandbox-ga)). A sandbox sleeps after 10 minutes idle by default ([Cloudflare lifecycle docs](https://developers.cloudflare.com/sandbox/sdk/api/lifecycle/), vendor). Whether the disk survives sleep is disputed: older docs say a sleeping container wakes with a fresh disk ([fly.io quoting Cloudflare](https://fly.io/learn/fly-vs-cloudflare/), secondary), while snapshot support was reported as still rolling out after GA ([lavx](https://news.lavx.hu/article/cloudflare-sandboxes-reach-general-availability-giving-ai-agents-persistent-isolated-environments), secondary). **Unverified.**
- **Verdict: skip** for workspaces until disk persistence is confirmed.

### Microsandbox (self-hosted)
- **What:** An open-source, self-hosted sandbox running libkrun microVMs: KVM on Linux, Hypervisor.framework on macOS. It accepts OCI images and supports MCP. Licensed Apache-2.0. Its SDKs are in beta, and it has no GPU support. ([rywalker](https://rywalker.com/research/microsandbox), [Vultr guide](https://docs.vultr.com/how-to-set-up-agent-sandboxing-on-vultr-cloud-compute), [beam.cloud](https://www.beam.cloud/blog/how-to-self-host-code-sandbox), all secondary)
- **Fit:** Could provide isolated Linux workspaces **on the user's own Mac or PC** at no cost, so the agent's commands never touch the host OS.
- **Verdict: use as a reference, and trial it.** It is the leading candidate for "free sandbox on my own machine", but it is young.

---

## 2. Cloud development environments with sleep and resume

### GitHub Codespaces
- A codespace stops after 30 minutes idle by default, and stopped codespaces are kept for 30 days by default (settable from 0 to 30). A stopped codespace bills storage but not compute. ([GitHub lifecycle docs](https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle), [auto-deletion docs](https://docs.github.com/en/codespaces/setting-your-user-preferences/configuring-automatic-deletion-of-your-codespaces?tool=webui), [cost docs](https://docs.github.com/en/codespaces/managing-codespaces-for-your-organization/managing-the-cost-of-github-codespaces-in-your-organization), vendor)
- Linux only. Controlled through the `gh` CLI and REST API.
- **Verdict: use as a reference.** Its lifecycle is the same as Arity's (running, stopped with disk, deleted), and the defaults are worth copying. It is tied to one repository, which is too narrow for Arity's general workspaces.

### Coder
- **What:** Self-hosted workspaces defined with Terraform. The core is AGPL-3.0, and enterprise features are proprietary ([tooldirectory](https://tooldirectory.ai/tools/coder), secondary).
- **Lifecycle:** Persistent resources survive a stop, while ephemeral ones are rebuilt ([Coder lifecycle docs](https://coder.com/docs/user-guides/workspace-lifecycle.md), vendor). Autostop is triggered by inactivity, and when an agent reports "working" through Coder Tasks, the deadline is extended ([Coder scheduling docs](https://coder.com/docs/v2.37/user-guides/workspace-scheduling.md), vendor). Automatic deletion of dormant workspaces is a paid feature ([Coder template schedule docs](https://coder.com/docs/admin/templates/managing-templates/schedule.md), vendor).
- **Fit:** Terraform templates can target the user's own machines (through Docker) as well as any cloud. That makes Coder a ready-made "pool plus workspace lifecycle". It is, however, a large system built for teams.
- **Verdict: use as a reference.** Its model of persistent and ephemeral resources, and its "agent is working, extend the deadline" rule, are directly reusable ideas.

### Ona (formerly Gitpod)
- **What:** Gitpod rebranded as Ona, an agent platform, in September 2025 ([The Register](https://www.theregister.com/2025/09/03/gitpod_rebrands_as_ona/)). Runners deploy in the customer's own AWS account, environments are EC2 instances, and the AWS runner requires the Enterprise plan ([Ona runner docs](https://ona.com/docs/ona/runners/overview), [AWS runner](https://ona.com/docs/ona/runners/aws/overview.md), vendor).
- **Verdict: skip.** Enterprise-only and limited to AWS.

---

## 3. Getting cloud GPU machines programmatically

### SkyPilot
- **What:** An open-source tool that launches jobs and clusters across many clouds, Kubernetes, and **the user's own machines through SSH node pools** ([SkyPilot overview](https://docs.skypilot.co/en/latest/overview.html), [GitHub](https://github.com/skypilot-org/skypilot), vendor).
- **Own machines:** Hosts are listed in `~/.sky/ssh_node_pools.yaml` and launched with `sky launch --infra ssh/<pool>`. The hosts must run a Debian-based OS. ([existing machines docs](https://docs.skypilot.co/en/stable/reservations/existing-machines.html), vendor)
- **Lifecycle:** Autostop after N idle minutes, with an option to tear down. Since 0.11, autostop can wait for active SSH sessions. ([SkyPilot 0.11 blog](https://blog.skypilot.co/announcing-skypilot-0.11.0), vendor; CLI flags from [tessl](https://tessl.io/registry/skills/github/Orchestra-Research/AI-Research-SKILLs/skypilot-multi-cloud-orchestration), secondary)
- **How commands arrive:** A client/server API: the CLI or Python SDK talks to a SkyPilot API server ([SkyPilot client-server blog](https://blog.skypilot.co/client-server/), vendor).
- **Licence:** Apache-2.0. **Unverified** this session; check the repository.
- **Fit:** The closest single product to Arity's computer pool. It holds a list of resource requirements (GPU type, cloud, own machines), picks the cheapest option that qualifies, and autostops. It covers Linux GPU machines only, not Macs.
- **Verdict: use as is** as the GPU and Linux procurement layer under Arity's matcher.

### RunPod
- Stopping a pod releases its GPU and keeps `/workspace`. A stopped pod's volume disk is billed at $0.20/GB/month, compared with $0.10 while running, and its container disk is erased. ([RunPod billing](https://docs.runpod.io/accounts-billing/billing.md), [pod pricing](https://docs.runpod.io/pods/pricing.md), [manage pods](https://docs.runpod.io/pods/manage-pods.md), vendor)
- Consumer cards such as the RTX 4090 cost about $0.34/h on Community Cloud and $0.69/h on Secure Cloud as of June 2026 ([flexprice](https://flexprice.io/blog/runprod-pricing-guide-with-gpu-costs), secondary).
- **Important for Arity:** because stopping releases the GPU, a woken workspace may not get a GPU back on the same host. The fix is to keep state on a network volume and resume onto any free GPU.
- **Verdict: use as is** when the user's own RTX PC is busy or off. Reach it through SkyPilot or directly.

### Lambda
- Filesystems are created separately, attached when an instance launches through `file_system_names`, and must be in the same region. Anything outside the filesystem mount is lost when the instance is terminated. ([Lambda filesystems](https://test-docs.lambda.ai/public-cloud/filesystems/), [Lambda blog](https://lambda.ai/blog/persistent-storage-beta), vendor)
- **Verdict: use through SkyPilot.** Better suited to data-centre GPUs such as the H100 than to RTX cards.

### Vast.ai
- A GPU marketplace with a CLI. Stopping an instance halts compute billing, but storage is billed until the instance is destroyed. ([Vast CLI](https://docs.vast.ai/cli/hello-world), [Vast billing](https://docs.vast.ai/billing), vendor)
- **Verdict: use through SkyPilot,** for the cheapest consumer GPUs. Hosts vary in trust, so restrict it by clearance.

---

## 4. macOS virtual machines for Xcode

### Apple's licence (the deciding constraint)

From the **macOS Tahoe 26 Software License Agreement** ([apple.com/legal/sla/docs/macOSTahoe.pdf](https://www.apple.com/legal/sla/docs/macOSTahoe.pdf), read in full):

- **Section 2B(iii)** allows "up to two (2) additional copies or instances" in virtual machines on each Mac you own or control, "for purposes of: (a) software development; (b) testing during software development; (c) using macOS Server; or (d) personal, non-commercial use."
- **Same section:** the virtual copies may not be used "in connection with service bureau, time-sharing, terminal sharing, relay service or other similar types of services."
- **Section 3A (leasing):** a Mac running macOS may be leased to someone else only for "Permitted Developer Services". Each "lease period must be for a minimum period of twenty-four (24) consecutive hours", and during the lease the lessee "must have sole and exclusive use and control" of the hardware.

What this means for Arity:
- On the user's own Mac, at most **two macOS VMs can run at once**. Building and testing the user's own software fits purpose (a) or (b).
- macOS enforces the cap: a third VM fails with `VZErrorVirtualMachineLimitExceeded` ([Apple developer forum](https://developer.apple.com/forums/thread/830119)). The cap counts running macOS guests only; Linux guests are not limited ([eclecticlight](https://eclecticlight.co/2026/04/29/virtualisation-on-apple-silicon-macs-is-different/)).
- **A cloud Mac cannot be rented by the hour.** Every cloud Mac is a lease of at least 24 hours of a whole machine.
- **Service-bureau clause:** offering macOS VMs to other people would fall under it. That only matters once [sharing](../open-questions.md#sharing-with-other-people) is designed.

### AWS EC2 Mac
- Runs on Dedicated Hosts only, one instance per host. Billed per second but with a **24-hour minimum allocation** "to comply with the Apple macOS Software License Agreement". M4 hosts are Mac minis with 24 GB of memory. ([AWS EC2 Mac docs](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-mac-instances.html), [AWS FAQ](https://aws.amazon.com/ec2/instance-types/mac/faqs/), vendor)
- About $1.23/h in us-east-1, or about $29.52 for the minimum 24 hours ([runs-on](https://go.runs-on.com/instances/ec2/mac-m4), secondary).
- **Verdict: use as is** as a cloud fallback for Xcode work, booked for at least a day.

### Scaleway and MacStadium
- Scaleway sells hourly M4 Mac minis ([Scaleway Apple silicon pricing](https://scaleway.com/en/pricing/apple-silicon), vendor). A 24-hour minimum is reported by a competitor and is **unverified** ([vBoxx](https://mac-hosting.vboxx.eu/compare/macstadium-alternative)).
- MacStadium rents monthly and quotes prices on request, with Orka as an add-on for VMs ([MacStadium pricing](https://macstadium.com/pricing), vendor). An M2 Mac mini is listed at about $109/month ([whtop](https://www.whtop.com/plans/macstadium.com/138377), secondary).
- **Verdict: use as a reference.** A monthly Mac is only worth it if Xcode work is constant and the user's Mac is often unavailable.

### Tart, Orchard and Vetu (Cirrus Labs, now OpenAI)
- **What:** Tart runs macOS and Linux VMs on Apple silicon through Virtualization.framework. Orchard orchestrates Tart VMs across a cluster of Macs.
- **Ownership:** Cirrus Labs announced on 7 April 2026 that it was joining OpenAI's Agent Infrastructure team, would relicense Tart, Vetu and Orchard "under a more permissive license", and had stopped charging licence fees. Cirrus CI shut down on 1 June 2026. ([cirruslabs.org](https://cirruslabs.org/), vendor; [MacStadium blog](https://macstadium.com/blog/cirrus-labs-is-joining-openai))
- **Licence:** The new licence is **unverified**. Tart was previously source-available under the Fair Source licence. The repository is reported to have moved to `openai/tart` ([ossinsight](https://ossinsight.io/analyze/openai/tart)).
- **Risk:** The team behind it has moved on to other OpenAI projects ([aiidelist](https://aiidelist.com/blog/openai-brings-tart-team-into-agent-infrastructure), secondary).
- **Verdict: use as is** for running Xcode VMs on the user's own Mac, but watch for the licence and maintenance changing.

### Lume (part of trycua/cua)
- **What:** Creates and runs macOS and Linux VMs on Apple silicon through Virtualization.framework. It builds a plain macOS VM from Apple's restore image with SSH and auto-login set up. Controlled by a CLI, a local HTTP API and an **MCP server**. Requires macOS 13 or later and about 50 GB of disk per macOS guest. Up to two macOS guests at once. ([cua.ai/docs/lume](https://cua.ai/docs/lume), vendor)
- **Licence:** MIT ([trycua/cua LICENSE](https://github.com/trycua/cua/blob/main/LICENSE.md)).
- Whether it supports snapshots was **unverified**.
- **Verdict: use as is**, as the alternative to Tart. Its HTTP and MCP interfaces suit an agent on another machine.

---

## 5. Exposing the user's own machines as a pool

### Tailscale
- Tags identify non-human machines, and access policy and SSH rules can target tags. Ephemeral auth keys suit short-lived nodes, which are removed automatically once inactive. Tagged devices have key expiry turned off by default. Tailscale SSH needs an SSH policy rule that allows access. ([tags](https://tailscale.com/kb/1068/acl-tags), [auth keys](https://tailscale.com/kb/1085), [servers](https://tailscale.com/kb/1245/tailscale-ssh-with-servers), vendor)
- **Fit:** This is the "private network" in Arity's design. Tags can carry clearance (`tag:arity-clearance-local`) and capabilities (`tag:xcode`, `tag:rtx`), and cloud workspaces can join the network with ephemeral keys. The procurement key in the [vault](../access.md#what-the-vault-holds) would be a Tailscale OAuth client.
- **Verdict: use as is.**

### SkyPilot SSH node pools
- Turns the user's Linux boxes (for example the RTX PC, if it runs Linux) into a pool that SkyPilot can schedule onto. It does not cover macOS. See [section 3](#skypilot).
- **Verdict: use as is** for the Linux and GPU side.

### cua host relay
- `cua host setup` makes a computer reachable through a relay hosted at cua.ai ([trycua/cua README](https://github.com/trycua/cua), vendor).
- **Verdict: skip.** It routes traffic through a third party, where Tailscale is peer-to-peer.

### Coder and Orchard
- Coder with Docker on each machine, or Orchard for a cluster of Macs, also turns a set of machines into a pool. See above.
- **Verdict: use as a reference.** Both are more than one user with two machines needs.

**Gap:** No product found matches Arity's typed capabilities across a Mac, a Windows or Linux PC and cloud machines in one registry. That matcher is Arity's to build, and it is small.

---

## 6. Computer-use drivers and frameworks

### Which projects the user's names refer to

| Name | Candidates | Evidence | Likeliest |
|---|---|---|---|
| **"cua driver"** | **Cua Driver (`cua-driver`)** from trycua/cua. Nothing else found by that name. | Product page: [cua.ai/cua-driver](https://cua.ai/cua-driver). Hacker News launch: [Show HN](https://news.ycombinator.com/item?id=47936312). Windows release: [Cua on X](https://x.com/trycua/status/2059688960838828391) | Unambiguous: trycua's Cua Driver |
| **"computer-use"** | (a) Anthropic's **computer use tool** in the Claude API ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)); (b) Anthropic's **computer-use-demo** reference container ([repo](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo)); (c) OpenAI's **computer use tool** in the Responses API ([guide](https://developers.openai.com/api/docs/guides/tools-computer-use)); (d) the generic GitHub topic ([github.com/topics/computer-use](https://github.com/topics/computer-use)), where trycua/cua is the largest project and calls itself "computer-use 2.0" ([repo](https://github.com/trycua/cua)) | Several separate projects share the name | **Ambiguous.** Mentioned next to "cua driver", it most likely means trycua's "computer-use" stack or Anthropic's tool. **Ask the user** |

### Cua Driver and the trycua/cua stack
- **What:** An MIT-licensed project from Cua AI, Inc. ([LICENSE](https://github.com/trycua/cua/blob/main/LICENSE.md)). It has 32.6k stars, 2.5k forks and 7,300 commits, so it is very active ([repo](https://github.com/trycua/cua)). Cua Spaces and Cua Driver Bench use FSL-1.1-MIT, which becomes MIT two years after each release (same source).
- **Cua Driver:** Lets agents use desktop apps on macOS, Windows and Linux **in the background**. Agents click, type and read in windows behind the user's, "with a cursor of their own". It reads each operating system's accessibility tree and also controls Chrome through the DevTools protocol. ([cua.ai/cua-driver](https://cua.ai/cua-driver), [browser blog](https://cua.ai/blog/extension-free-browser-use), vendor)
- **How commands arrive:** `cua-driver mcp` runs a stdio MCP server, `cua-driver serve` and `call` work as a daemon, and `cua-driver connect` wires it into Claude Code, Codex and others ([CLI reference](https://cua.ai/docs/cua-driver/reference/cli), vendor).
- **Rest of the stack:** A `cua` SDK and CLI create sandboxes: Linux through gVisor, macOS and Linux through Lume, and images for Windows and macOS. `cua-spacesd` is a gRPC daemon on port 3211 inside a sandbox. ([repo](https://github.com/trycua/cua))
- **Churn:** Many earlier packages are now deprecated, including `cua-agent`, `cua-computer-server`, `lumier` and the Docker desktop images ([repo](https://github.com/trycua/cua)). Expect the API to keep changing.
- **Pricing:** Cua Spaces is free for individuals, $18/month for Pro and $30 per seat per month for Teams ([repo](https://github.com/trycua/cua); [pricing page](https://cua.ai/pricing) not read). The earlier "Cua Cloud" service appears to have ended.
- **Fit:**
  - It is a **driver**, the part that sees and acts. The model is chosen separately, so it fits Arity's blind router: any model's computer-use tool calls are carried out by the driver.
  - Running it inside a Lume or Tart VM on the user's Mac gives a safe Xcode-plus-GUI workspace.
  - Its background mode on the user's **real** desktop is convenient but breaks the two-machine safety rule. Allow it only at a high clearance.
- **Verdict: use as is** for the desktop driver layer, behind Arity's own thin interface because of the churn.

### Anthropic computer use tool
- **What:** A client toolset: `{"type": "computer_toolset_20260801"}` adds a fixed set of member tools such as `screenshot`, `left_click`, `type` and `zoom`, and **"your application runs every call"**. Claude 5.5 and later models accept only this toolset. ([platform.claude.com computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool), vendor)
- The toolset is reported to be about 17 tools, to add about 4,500 input tokens per request, and to have reached GA on 19 August 2026 ([ecorpit](https://ecorpit.com/claude-computer-use-toolset-20260801-migration-breaking-changes-2026/), [developersdigest](https://www.developersdigest.tech/blog/claude-computer-use-toolset-ga), secondary).
- **Reference container:** computer-use-demo is a Docker image with X11 and VNC plus a Streamlit agent loop ([repo](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo)).
- **Fit:** Because the client carries out every call, the two-machine split comes for free. The agent loop runs in Arity's harness, and each action is forwarded to the workspace through a driver (Cua Driver, xdotool in a container, or a VNC client). Screenshots go to whichever model the router picked, which is the [screen protection](../open-questions.md#screen-protection) issue.
- **Verdict: use as is** as one model-side interface. Use the demo container as a reference.

### OpenAI computer use tool
- **What:** The Responses API offers a `computer` tool. The model returns `computer_call` items, and the client executes each one and sends back a screenshot ([OpenAI guide](https://developers.openai.com/api/docs/guides/tools-computer-use.md), vendor; read from snippets only). Computer use was added to the Agents API with an OpenAI-hosted browser on 29 September 2026 ([mixed-news](https://mixed-news.com/en/?p=34956), secondary). Which models currently support it, and whether `computer-use-preview` has been retired, is **unverified**; sources conflict ([browserless](https://docs.browserless.io/ai-integrations/computer-use/openai-cua), [model page](https://developers.openai.com/api/docs/models/computer-use-preview.md)).
- **Sample app:** openai-cua-sample-app is MIT-licensed, with a Playwright browser agent and a PyAutoGUI desktop agent ([repo](https://github.com/openai/openai-cua-sample-app)).
- **Verdict: use as is** as a second model-side interface. The router has to translate between the two vendors' action formats. Skip OpenAI's hosted browser, because it puts the computer outside Arity's pool.

### Bytebot
- A self-hosted desktop agent in a containerised Linux desktop, licensed Apache-2.0, with about 11.1k stars and 11 contributors ([repo](https://github.com/bytebot-ai/bytebot), [trendshift](https://trendshift.io/repositories/14624)).
- **Verdict: use as a reference** for a ready-made Linux desktop container. It bundles its own agent loop, which Arity does not want.

### Microsoft UFO² and Windows options
- **UFO²:** A Windows "AgentOS" that combines UI Automation with vision and runs in a picture-in-picture desktop so the user keeps working. MIT licence. ([paper](https://arxiv.org/html/2504.14603v2), [docs](https://microsoft.github.io/UFO/ufo2/overview), vendor)
- **Windows 365 for Agents:** Cloud PCs for agents, in public preview ([Microsoft Learn](https://learn.microsoft.com/lb-lu/windows-365/agents/introduction-windows-365-for-agents), vendor).
- **Microsoft Execution Containers (MXC):** An SDK for isolating agents, announced at Build 2026 ([Windows blog](https://blogs.windows.com/windowsdeveloper/2026/06/02/windows-platform-security-for-ai-agents/), vendor).
- **Verdict: use as a reference.** Revisit if the RTX PC runs Windows and needs a sandboxed GUI. For local testing, Windows Sandbox is the built-in option ([cua blog](https://cua.ai/blog/windows-sandbox)).

---

## Comparison table

| Project | Kind | Licence / model | Sleep and resume | OS | GPU | Remote control | Arity role | Verdict |
|---|---|---|---|---|---|---|---|---|
| E2B | microVM sandbox | Apache-2.0 + SaaS | Pause keeps memory and disk, ~1 s resume | Linux | No | SDK / API | Cloud Linux workspace | Use |
| Fly.io Sprites | Persistent microVM | SaaS | Checkpoints restore in place, never expire | Linux | No | CLI / API | Project-agent workspace | Use |
| Modal Sandboxes | Sandbox + GPU | SaaS | Filesystem snapshot, restores as a new sandbox | Linux | Yes, T4 to B300 | Python SDK | GPU subagents | Use |
| Daytona | Sandbox | Was AGPL, now closed | Stop and archive (CPU); GPU deleted on stop | Linux | Yes | SDK | — | Reference |
| Morph Cloud | Branchable VMs | SaaS | Snapshot and branch in under 250 ms | Linux | ? | SDK | Workspace fork | Reference |
| Vercel Sandbox | microVM | SaaS | Auto-snapshot on stop; 45 min or 24 h sessions | Linux | No | SDK | — | Skip |
| Cloudflare Sandbox | Container | SaaS | Sleeps after 10 min; disk persistence unclear | Linux | No | SDK | — | Skip |
| Microsandbox | Self-hosted microVM | Apache-2.0 | ? | Linux guests on Linux or macOS hosts | No | SDK / MCP | Sandbox on own machines | Trial |
| Codespaces | Cloud dev environment | SaaS | Stops after 30 min, kept up to 30 days | Linux | Limited | gh / REST | — | Reference |
| Coder | Self-hosted dev environments | AGPL + enterprise | Persistent vs ephemeral resources | Any (Terraform) | Any | API | — | Reference |
| SkyPilot | Multi-cloud launcher | Apache-2.0 (unverified) | Autostop, optional teardown | Linux | Yes, any cloud | API server | GPU and Linux procurement + own Linux pool | Use |
| RunPod / Vast / Lambda | GPU clouds | SaaS | Stop keeps disk (billed); GPU released | Linux | RTX to H100 | API / CLI | Cloud GPU | Use via SkyPilot |
| AWS EC2 Mac | Cloud Mac | SaaS | 24 h minimum per host | macOS | Apple | SSH | Cloud Xcode fallback | Use |
| Tart / Orchard | macOS VMs | Relicensing to permissive (unverified) | Clone; Orchard schedules | macOS, Linux | Apple | CLI / API | Xcode VMs on own Mac | Use |
| Lume | macOS VMs | MIT | ? | macOS, Linux | Apple | CLI / HTTP / MCP | Xcode VMs on own Mac | Use |
| Tailscale | Private network | SaaS + open client | n/a | All | n/a | SSH over tailnet | The pool's network | Use |
| Cua Driver | Desktop driver | MIT | n/a | macOS, Windows, Linux | n/a | MCP / CLI / daemon | Computer-use executor | Use |
| Anthropic computer use | Model toolset | API | n/a | Any (client runs it) | n/a | Client-executed | Model side | Use |
| OpenAI computer use | Model tool | API | n/a | Any (client runs it) | n/a | Client-executed | Model side | Use |
| Bytebot | Desktop agent | Apache-2.0 | — | Linux container | No | API | — | Reference |
| UFO² / Windows 365 for Agents | Windows agents | MIT / SaaS preview | — | Windows | — | — | — | Reference |

A "?" means not found or unverified.

## Recommendation: build versus adopt

**Build (small, and specific to Arity):**
1. **The pool registry and matcher.** Each machine lists its capabilities, clearance, cost, minimum billing period and whether it is online. A task's requirements are matched against them, preferring the user's own machines. No product found does this across a Mac, a PC and the cloud.
2. **A workspace adapter interface** with seven operations: `create`, `exec`, `sleep`, `wake`, `snapshot`, `fork`, `release`. Each provider becomes an adapter (E2B, Sprites, Modal, SkyPilot, Lume or Tart, SSH). Vendors are churning (Daytona went closed source, Cirrus was bought, Cua deprecated half its packages), so nothing above this interface should name a vendor.
3. **The executor relay for the two-machine split.** The agent's harness gets model tool calls (shell or computer use) and forwards them to the workspace's executor (`cua-driver serve`, an SSH command, or a sandbox SDK call). Both vendors' computer-use tools already work this way, so this layer is thin.

**Adopt:**
- **Tailscale** for the private network and SSH, using tags for capabilities and clearance.
- **SkyPilot** for GPUs and Linux machines, including the user's own Linux machines and cheap RTX capacity from RunPod or Vast.
- **E2B or Sprites** for cloud Linux workspaces. E2B suits short tasks with pause and resume; Sprites suits project agents that live for weeks.
- **Modal** for GPU subagents.
- **Lume or Tart** for Xcode VMs on the user's Mac, with **EC2 Mac** as the cloud fallback, booked for at least 24 hours.
- **Cua Driver** as the desktop executor, paired with Anthropic's and OpenAI's computer-use tools on the model side.

## Contradictions with, and improvements to, the current design

1. **Cloud Macs cannot be "paid by the hour".** Apple's licence requires leases of at least 24 hours, and AWS enforces it. The pool needs a **minimum billing period** for each machine, and the matcher should prefer the user's own Mac even when that means waiting. ([Apple SLA §3A](https://www.apple.com/legal/sla/docs/macOSTahoe.pdf), [AWS](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-mac-instances.html))
2. **The user's Mac can host only two macOS VMs at once.** That limits how many Xcode agents can run in parallel, and the matcher must count it as a capacity. ([Apple SLA §2B(iii)](https://www.apple.com/legal/sla/docs/macOSTahoe.pdf), [Apple forum](https://developer.apple.com/forums/thread/830119))
3. **"Asleep" means different things on different providers.** E2B keeps memory, Sprites keeps the disk in place, Modal restores a copy, and RunPod releases the GPU so a woken workspace may land on another host. Workspace state should be defined as **files plus an optional memory image**, not as a particular machine, and waking means "restore onto any machine that qualifies".
4. **Sleeping is not free.** A stopped RunPod disk costs twice the running rate, and Vast and Codespaces keep billing storage. The spending limit should count sleeping workspaces, and long-cold project agents may need a further state, **archived** (snapshot in object storage, machine released), between cold and finished. Daytona's archive step is the model. ([RunPod](https://docs.runpod.io/accounts-billing/billing.md), [Daytona](https://www.daytona.io/docs/en/persistence.md))
5. **Forking an agent could also fork its machine.** Morph branches and E2B or Modal snapshots let a fork start from its parent's machine state, not only its parent's context. Worth adding to [How work starts](../work.md#fresh-or-fork).
6. **The two-machine split is easier than the page suggests.** Computer use from both Anthropic and OpenAI is executed by the client, and Cua Driver, Lume and the sandbox SDKs are all remote-controllable. The harness can run on the always-on machine (or a small cloud machine) and drive every workspace. The rule could be restated as "the harness never executes tool calls locally".
7. **Computer use on the user's real desktop is a separate risk level.** Cua Driver can operate the user's live session in the background, which conflicts with the safety split and with [screen protection](../open-questions.md#screen-protection). Make "computer use in a VM" and "computer use on the user's desktop" two separate capabilities, the second needing high clearance.
8. **Capabilities need more fields.** Add OS and CPU architecture, GPU VRAM (an RTX 4090 differs from an H100), concurrent-slot count (the macOS VM cap), minimum billing period, and whether the machine is reachable now.
9. **Idle limits should be explicit and tied to agent state.** Providers default to stopping after 10 to 30 minutes idle (Cloudflare, Daytona, Codespaces). Arity's own state should drive sleep, as Coder extends the deadline while an agent reports "working", so a provider never stops a live agent's workspace mid-task. ([Coder](https://coder.com/docs/v2.37/user-guides/workspace-scheduling.md))
10. **Sharing and macOS VMs.** If Arity is ever shared, offering macOS VMs to other people may fall under the licence's service-bureau prohibition. Record this under [sharing](../open-questions.md#sharing-with-other-people).

## Not verified

- E2B, Sprites, Modal and Microsandbox prices.
- SkyPilot's licence.
- The new Tart licence.
- Scaleway's 24-hour minimum.
- Whether Cloudflare sandboxes keep their disk through sleep.
- Lume snapshot support.
- Which OpenAI models currently support computer use.
- Star counts and activity for most projects other than trycua/cua.
