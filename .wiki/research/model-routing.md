# Research: model routing and the endpoint pool

[Back to index](../README.md)

A survey of existing software that Arity could reuse or learn from for the [model router](../model-router.md) (the "Pick variant" step of [Instantiate](../instantiate.md)) and the endpoint pool. All sources were checked on **2026-11-18**. Claims marked **(unverified)** come from a single third-party source, from search snippets only, or from memory without a page to cite.

## What Arity needs

| Arity part | Requirement |
|---|---|
| Pick variant | Choose a model per agent at spawn or wake, not per request |
| Blindness | Neither the user nor the parent agent learns which model was chosen |
| Balance | Right capability for the task (scores per tier, role, task kind) against spending tokens wisely |
| Endpoint pool | Pay-per-token API keys, flat-fee subscriptions with renewing quota, local endpoints available only while a GPU is free |
| Spend rule | Spend quota that is about to renew first |
| Clearance | Local-only tasks never reach a cloud model |

## 1. Gateways and proxies that route across providers

These sit between a caller and many providers, give one API, and handle keys, fallbacks, budgets and load balancing. None of them picks a model by task quality; they pick a *deployment* among ones the caller already named.

### LiteLLM

- **What and who:** Python SDK and proxy server ("AI gateway") with one OpenAI-style API over 100+ providers, by BerriAI. <https://github.com/BerriAI/litellm>
- **Licence:** MIT core; the `/enterprise` folder is under a commercial licence. <https://pypi.org/project/litellm-enterprise/0.1.36/>, <https://openalternative.co/litellm>
- **Activity:** Very active: about 61k stars on 2026-10-12, releases every few days, v1.10x in October 2026. <https://summary.ecosyste.ms/projects/369911>, <https://trendshift.io/repositories/435>
- **Security record:** Malicious versions 1.82.7 and 1.82.8 were published to PyPI on 2026-03-24 after a CI token leak, carrying a credential stealer. <https://bleepingcomputer.com/news/security/popular-litellm-pypi-package-compromised-in-teampcp-supply-chain-attack>, <https://www.hiddenlayer.com/research/litellm-supply-chain-attack>. A second compromise of 1.107.0 on 2026-10-21 is reported by one advisory **(unverified, single source)**: <https://www.konfirmity.com/advisories/litellm-1-107-0-supply-chain-compromise>. For a process that holds every key in the pool, this matters.
- **Routing signals:** weighted pick, latency, cost (cheapest deployment under its limits), usage (tokens/requests per minute), least-busy, rate-limit-aware; strategies set per routing group. <https://docs.litellm.ai/docs/routing>, <https://docs.litellm.ai/docs/router/learn/strategies>. Fallbacks and retries on the same page.
- **Expiring quota:** Partly. Provider budget routing caps spend per provider over a duration such as `1d` or `1mo` and filters out deployments over budget. <https://docs.litellm.ai/docs/proxy/provider_budget_routing>. This is a dollar cap that resets, not a model of "unused quota is lost at the reset"; it cannot prefer a subscription because its reset is near.
- **Hiding the model:** Callers can address a model-group alias, so the caller does not choose the model. Whether the response's `model` field and headers reveal the underlying deployment is **(unverified)**; plan on rewriting them.
- **Fit for Arity:** Good as the *endpoint pool's* execution layer for API keys and local OpenAI-compatible servers: one key store, fallbacks, spend logs, tag-based routing that can carry clearance. Not a "Pick variant" step.
- **Verdict:** **Use as is** for API keys and local endpoints, pinned to a known-good version or container digest because of the supply-chain history. Keep the pick logic outside it.

### Bifrost (Maxim AI)

- **What:** Go gateway, one API over many providers, with virtual keys, budgets, fallbacks. <https://github.com/maximhq/bifrost>
- **Licence:** Apache-2.0 core per the vendor's reference page; one blog says MIT. <https://www.getmaxim.ai/llm-info>, <https://dev.to/debmckinney/this-open-source-llm-gateway-is-54x-faster-than-litellm-heres-why-1h>. Check the LICENSE file before adopting.
- **Activity:** Active; performance claims ("50x faster than LiteLLM") are the vendor's own. <https://dev.to/kuldeep_paul/we-built-an-llm-gateway-50x-faster-than-litellm-and-its-open-source-4a57>
- **Signals:** weighted load balancing, fallbacks, budgets per virtual key **(details unverified)**.
- **Expiring quota / hiding:** No reset-aware logic found.
- **Fit / verdict:** **Use as a reference**, or as a LiteLLM replacement if a single Go binary is preferred for the process that holds keys.

### Portkey Gateway

- **What:** Gateway with retries, fallbacks, load balancing, conditional routing (route by request metadata), circuit breakers. Portkey is now part of Palo Alto Networks. <https://github.com/Portkey-AI/gateway>, <https://portkey.ai/docs/product/ai-gateway/conditional-routing>
- **Licence:** MIT. <https://geekflare.com/open-source/portkey-gateway/>
- **Maturity:** The promised 2.0 release that open-sources the enterprise gateway is still pre-release and reportedly stalled after the acquisition. <https://cdn.jsdelivr.net/gh/Portkey-AI/gateway@main/README.md>, <https://geekflare.com/open-source/portkey-gateway/>
- **Fit:** Conditional routing on metadata is a clean way to express clearance ("local only") at the gateway. Nested strategies are a good config model.
- **Verdict:** **Use as a reference.** Uncertain open-source future.

### Helicone AI Gateway

- **What:** Rust gateway by Helicone. Strategies: model latency, provider latency (P2C + PeakEWMA), weighted, cheapest. <https://docs.helicone.ai/ai-gateway/concepts/loadbalancing>, <https://www.helicone.ai/blog/introducing-ai-gateway>
- **Licence:** Apache-2.0 per its blog, GPL-3.0 per a directory **(conflicting, unverified)**.
- **Verdict:** **Use as a reference** for latency-aware deployment selection (PeakEWMA is a good way to rank local GPUs that slow down under load).

### TensorZero

- **What:** Rust gateway plus observability, A/B experiments over prompts, models and inference strategies, and feedback collection. Apache-2.0. <https://tensorzero.com/docs/comparison/openrouter>, <https://www.everydev.ai/tools/tensorzero>
- **Signals:** Its own docs say routing is static (an ordered list of providers) and dynamic model routing is not supported. <https://tensorzero.com/docs/comparison/openrouter>
- **Fit:** The closest in *spirit* to Arity's blind testing: it ties episodes to variants and collects feedback against them. Its unit is an inference or episode, not a long-lived agent.
- **Maturity note:** One post reports the repo being archived after a funding round **(unverified)**: <https://feedbagel.com/post/ai-oss-tool-repo-goes-archived-over-night-after-raising-73m-seed>. Check before depending on it.
- **Verdict:** **Use as a reference** for the trial-record and feedback schema.

### OpenRouter (hosted)

- **What:** Hosted single API over hundreds of models with provider fallback. Its Auto Router (`openrouter/auto`, powered by Not Diamond) picks a model per prompt; it is now marked deprecated in favour of `openrouter/auto-beta`, which classifies task type. The response reports which model answered. <https://openrouter.ai/docs/guides/routing/routers/auto-router>, <https://openrouter.ai/docs/guides/features/model-routing>
- **Fit:** A useful *single API key* in the pool for long-tail models. Its auto router is per prompt and not blind (the model is reported), and it does not learn from Arity's grades.
- **Verdict:** **Use as is** as one pay-per-token endpoint in the pool; **skip** its auto router.

## 2. Routers that pick a model by cost, quality or learned preference

### RouteLLM (LMSYS)

- **What:** Framework and trained routers that send each query to a strong or a weak model. The matrix-factorisation router predicts the strong model's win rate and compares it with a cost threshold. Reported 95% of GPT-4 quality with 14–26% GPT-4 calls on MT Bench. <https://github.com/lm-sys/RouteLLM>, <https://lmsys.org/blog/2024-07-01-routellm/>
- **Licence:** Apache-2.0 **(unverified)**.
- **Activity:** Routers trained on a 2024 model pair (GPT-4 and Mixtral 8x7B); little visible development since 2024 **(unverified)**.
- **Fit:** Two models only, per query, trained on Chatbot Arena preferences rather than the user's own grades. Arity picks among many models once per agent.
- **Verdict:** **Use as a reference** for the threshold idea: a single knob that trades cost against expected quality, calibrated to a target share of expensive calls.

### Not Diamond (hosted)

- **What:** Hosted model-selection API, with custom routers trained on your own evaluation data (CSV of prompts, per-model scores) via `POST /v2/pzn/trainCustomRouter`. <https://docs.notdiamond.ai/docs/router-training-quickstart>, <https://docs.notdiamond.ai/reference/train_custom_router_v2_pzn_traincustomrouter_post>
- **Fit:** Closest product to "learn picks from our own grades", but training data and prompts go to a third party, which conflicts with clearance and with [Access](../access.md)'s concern about what leaves the user's machines. It routes per prompt and has no notion of quota or local GPUs.
- **Verdict:** **Skip** as a dependency; **reference** for the data shape (prompt features plus per-model score).

### Arch-Router / Plano (Katanemo)

- **What:** A 1.5B model that maps a prompt to user-written routing policies ("code review → model X") by inferring domain and action; used by the Plano proxy, which also supports alias-based routing. <https://huggingface.co/katanemo/Arch-Router-1.5B>, <https://arxiv.org/html/2506.16655v1>, <https://docs.planoai.dev/guides/llm_router.html>
- **Licence:** Katanemo licence on the model, not an OSI licence. <https://huggingface.co/katanemo/Arch-Router-1.5B>
- **Fit:** Arity's task board already says what kind of agent and task is wanted, so a classifier to infer "task kind" is mostly redundant. Could be useful for untyped requests (papercuts, ad-hoc user messages) and runs locally.
- **Verdict:** **Use as a reference**; maybe use as is later for classifying untyped tasks locally.

### Research on learning routers from feedback

- **ParetoBandit** (2026): cost-aware contextual bandit router with an online budget pacer that holds spend to a ceiling, and models added at runtime. <https://arxiv.org/abs/2604.00136>
- **OrcaRouter** (2026): LinUCB per model, warmed offline then updated from bandit feedback in production. <https://www.alphaxiv.org/abs/2605.30736>
- **NeuralUCB routing** (2026): <https://www.alphaxiv.org/abs/2603.30035.md>; **dueling-bandit routing from pairwise preferences** (2025): <https://www.arxiv.org/pdf/2510.00841>
- **Fit:** This is the right family for Arity: one choice per agent, a delayed reward (the grade), explicit exploration, a budget that moves. A budget pacer is a direct way to implement "the weight between capability and cost moves with supply".
- **Verdict:** **Use as a reference.** Code availability is unverified for all four.

## 3. Tracking remaining quota, rate limits and reset times

| Tool | What it reads | Notes | Verdict |
|---|---|---|---|
| **ccusage** (ryoppippi) | Claude Code's local logs; groups usage into 5-hour blocks | Estimates only; cannot see server-side quota; block anchoring is approximate. `/usage` inside Claude Code is the authoritative view with reset times. <https://www.mintlify.com/ryoppippi/ccusage/features/blocks-reports>, <https://dev.to/pederaa/how-to-track-claude-code-usage-in-2026-built-in-commands-ccusage-and-desktop-dashboards-compared-1kk1> | Reference |
| **CodexBar** (steipete) | Session and weekly windows with reset countdowns for Claude Code, Codex and many others; reads existing local auth, browser cookies opt-in; macOS 14+ | Has a CLI: `codexbar usage --provider claude --format json`. <https://www.mintlify.com/steipete/codexbar>, <https://www.tech2geek.net/codexbar-track-your-ai-usage-and-token-limits-in-real-time-on-macos> | **Use as is** as a quota sensor on a Mac, or reference for per-provider adapters |
| **LiteLLM provider budgets** | Its own spend ledger | Resets a dollar cap per duration; no knowledge of subscription windows. <https://docs.litellm.ai/docs/proxy/provider_budget_routing> | Use for API keys only |
| **Provider headers** | Anthropic API returns `anthropic-ratelimit-*-remaining` and `-reset` (RFC 3339) | Documented for API keys. <https://docs.anthropic.com/claude/reference/rate-limits>. Subscription "unified" utilisation headers appear only in an unofficial gist **(unverified, undocumented)**: <https://gist.github.com/andrew-kramer-inno/34f9303a5cc29a14af7c2e729b676fc9> | Use for API keys |

Nothing found models the pool as Arity does (several kinds of window per provider, value of a token falling as its reset nears). Arity has to build this ledger.

### Window shapes by provider

| Provider | Windows | Source |
|---|---|---|
| Claude subscriptions | Rolling 5-hour window plus a weekly limit | <https://dev.to/pederaa/how-to-track-claude-code-usage-in-2026-built-in-commands-ccusage-and-desktop-dashboards-compared-1kk1> |
| ChatGPT plans (Codex) | Messages per 5 hours on Plus; "Pro plans currently have no five-hour limit"; weekly limits may apply; credits can be bought after limits | <https://learn.chatgpt.com/codex/pricing> |
| Gemini Code Assist (Google login) | 1,000 requests/user/day free, 1,500 AI Pro, 2,000 AI Ultra; reset time not stated | <https://www.geminicli.com/docs/resources/quota-and-pricing/> |
| GitHub Copilot | Monthly premium-request allowance, resets 00:00 UTC on the 1st; over-allowance requests rejected unless a budget is set | <https://docs.github.com/en/billing/concepts/product-billing/github-copilot-premium-requests> |

## 4. Exposing a coding subscription as an endpoint for other software

### Projects

- **CLIProxyAPI** (router-for-me): Go proxy, MIT, that logs in with Codex, Claude Code, Gemini CLI and others via OAuth and exposes OpenAI/Claude/Gemini-compatible endpoints, with multi-account load balancing. <https://pkg.go.dev/github.com/router-for-me/CLIProxyAPI/v7>, <https://awesome.ecosyste.ms/projects/github.com%2Frouter-for-me%2FCLIProxyAPI>
- **copilot-api** (ericc-ch): exposes a Copilot subscription as an OpenAI/Anthropic endpoint. Its own README warns it "may trigger GitHub's abuse-detection systems". <https://github.com/ericc-ch/copilot-api>
- **claude-code-router** (musistudio): MIT; the opposite direction: keeps the Claude Code harness and sends its requests to other models (OpenRouter, DeepSeek, Ollama, Gemini) with per-scenario routing and plugins. <https://github.com/musistudio/claude-code-router>

### What the providers' terms say

**Anthropic (Claude Free, Pro, Max).** Source: <https://code.claude.com/docs/en/legal-and-compliance>, <https://www.anthropic.com/legal/consumer-terms> (effective 2025-10-08), <https://code.claude.com/docs/en/agent-sdk/overview>, <https://code.claude.com/docs/en/headless>.

- Subscription OAuth is "designed to support ordinary use of Claude Code and other native Anthropic applications."
- Developers "may not collect, store, or intermediate Claude.ai credentials or session tokens." Products, "including those using the Agent SDK, should use API key authentication."
- It does not prevent a user "signing in to the unmodified Claude Code binary with their own Claude subscription".
- Usage limits "assume ordinary, individual usage of Claude Code and the Agent SDK."
- Consumer terms §3(7) bar access "through automated or non-human means" except via an API key or where Anthropic explicitly permits it. §2: "You may not share your Account login information".
- Headless mode (`claude -p`) exists and is aimed at scripts and CI; its `--bare` mode "doesn't use your subscription login" and is "recommended mode for scripted and SDK calls". Whether unattended `claude -p` on a subscription is ordinary individual use is **not settled by any page found**.
- Enforcement against third-party harnesses using subscription OAuth was reported from January 2026 and clarified in February 2026. <https://www.theregister.com/2026/02/20/anthropic_clarifies_ban_third_party_claude_access>

**OpenAI (ChatGPT plans with Codex).** Source: <https://learn.chatgpt.com/docs/auth>, <https://learn.chatgpt.com/codex/auth/ci-cd-auth> (page text partly summarised by the fetch tool, so quotes are near-verbatim), <https://conductatlas.com/platform/openai/terms-of-use-row/account-credential-sharing-prohibited/> (third-party copy; openai.com terms pages returned 403).

- "API keys are still the recommended default for automation."
- ChatGPT-auth `auth.json` with `codex exec` is documented as "an advanced workflow for enterprise and other trusted private automation"; "Do not share the same file across concurrent jobs or multiple machines."
- Terms of Use: "You may not share your account credentials or make your account available to anyone else".

**Google (Gemini CLI / Code Assist).** Source: <https://www.geminicli.com/docs/resources/tos-privacy>.

- "Directly accessing the services powering Gemini CLI … using third-party software, tools, or services … is a violation of applicable terms and policies", with "using OpenClaw with Gemini CLI OAuth" as the example.
- The same page says Gemini CLI was replaced by Antigravity CLI on 2026-06-18 for unpaid-tier and Google One users; Antigravity CLI's own terms were **not checked**.

**GitHub Copilot.** Source: <https://docs.github.com/en/site-policy/github-terms/github-terms-of-service>, <https://docs.github.com/site-policy/acceptable-use-policies/github-acceptable-use-policies>.

- "a single login may not be shared by multiple people"; "You may not share API tokens to exceed GitHub's rate limitations."
- The AUP forbids "excessive automated bulk activity". No page found explicitly allows or forbids using Copilot credentials through unofficial proxies.

### Consequence for Arity

A subscription can stay inside the terms only when **the provider's own client runs the agent**: Claude Code for a Claude subscription, Codex CLI for a ChatGPT plan, on the user's own machines, for the user alone. It cannot be an interchangeable endpoint behind a gateway. CLIProxyAPI-style pooling of Claude or Gemini subscriptions is outside the terms; for Copilot it risks abuse detection. **Verdict for all three proxies: skip** for subscriptions. claude-code-router is a **reference** for running the Claude Code harness on API-key or local models, which is allowed because no subscription credential is involved.

## 5. Serving and discovering local models across several machines

| Project | What | Licence | Fit | Verdict |
|---|---|---|---|---|
| **Ollama** | Local model server with an OpenAI-compatible `/v1`; reachable across a tailnet by binding to all interfaces. <https://tailscale.com/blog/self-host-a-local-ai-stack/>, <https://sunflare-systems.pages.dev/guides/tailscale-llm> | MIT **(unverified)** | Simplest per-machine server, mixed hardware | Use as is |
| **vLLM** | High-throughput server, OpenAI-compatible on port 8000, CUDA-centred. <https://www.sitepoint.com/from-ollama-to-vllm-a-migration-guide-for-growing-teams/> | Apache-2.0 **(unverified)** | For a dedicated NVIDIA box | Use as is where it fits |
| **llama-swap** | Go proxy that hot-swaps models on one machine on demand; per-model concurrency limit returns 429. <https://github.com/mostlygeek/llama-swap>, <https://betterstack.com/community/guides/ai/llama-swap/> | MIT **(unverified)** | Lets one GPU offer many models; the 429 is a usable "busy" signal | Use as is |
| **Olla** | Go proxy and load balancer over Ollama, LM Studio, vLLM, llama.cpp, SGLang; health checks, priority / round-robin / least-connections, model unification across endpoints. <https://thushan.github.io/olla/concepts/overview>, <https://thushan.github.io/olla/concepts/load-balancing> | Apache-2.0 **(unverified)** | Single front door for all local GPUs, with health as the availability signal | **Use as is** (candidate) |
| **GPUStack** | Cluster manager that pools GPUs across machines, picks vLLM/SGLang/TensorRT-LLM, OpenAI-compatible API. <https://github.com/gpustack/gpustack>, <https://pypi.org/project/gpustack/> | Apache-2.0 | Heavier; overlaps with Arity's [computer pool](../computers.md) | Reference |
| **exo** | Peer-to-peer cluster that splits one model across devices (MLX on Apple Silicon). <https://github.com/exo-explore/exo> | Apache-2.0 | For running a model too big for one Mac; Linux GPU support uncertain. <https://toolhalla.ai/blog/exo-framework-distributed-inference-guide-2026> | Reference |
| **Ollama Herd / Flock** | mDNS discovery of Ollama nodes behind one endpoint. <https://1clonegithubbyxjn.1934672944.workers.dev/geeks-accelerator/ollama-herd>, <https://awesome.ecosyste.ms/projects/github.com%2Fhadihonarvar%2Fflock> | **(unverified)** | Small projects; discovery idea only | Reference |

None of these knows that a GPU is "free" in Arity's sense (not in use by the user or by a higher-priority job). They report health, load and loaded models. "Available only while a GPU is free" has to come from the computer pool, which then toggles the local endpoint in the gateway.

## Comparison table

| Project | Kind | Licence | Activity | Routing signals | Expiring quota | Can hide model | Arity role | Verdict |
|---|---|---|---|---|---|---|---|---|
| LiteLLM | Gateway | MIT + commercial `/enterprise` | Very active; 2026 supply-chain incident(s) | Weighted, latency, cost, usage, least-busy, tags, budgets | Dollar budget per duration only | Via aliases; response fields to check | Pool execution layer | Use as is (pinned) |
| Bifrost | Gateway | Apache-2.0 (check) | Active | Weights, fallbacks, budgets | No | Via virtual keys (unverified) | Pool execution layer alt. | Reference / alternative |
| Portkey | Gateway | MIT | 2.0 stalled | Conditional, fallback, load balance, circuit breaker | No | Via config | Clearance routing idea | Reference |
| Helicone AI Gateway | Gateway | Apache or GPL (conflicting) | Active | Latency P2C+PeakEWMA, weighted, cost | No | Unverified | Local GPU ranking idea | Reference |
| TensorZero | Gateway + experiments | Apache-2.0 | Possibly archived | Static order; A/B experiments | No | Yes, variants by name | Trial-record schema | Reference |
| OpenRouter | Hosted API | Proprietary | Active | Provider fallback; auto router per prompt | No | No (reports model) | One API key in pool | Use as is (key only) |
| RouteLLM | Learned router | Apache-2.0 (unverified) | Little since 2024 (unverified) | Strong-vs-weak win rate vs threshold | No | Yes | Threshold idea | Reference |
| Not Diamond | Hosted learned router | Proprietary | Active | Custom router on own eval data | No | Yes | Data shape | Skip |
| Arch-Router / Plano | Policy router model | Katanemo licence | Active | Domain/action vs written policies | No | Yes | Classify untyped tasks | Reference |
| Bandit papers | Algorithms | n/a | 2025–26 | Contextual bandit on reward, budget pacer | Budget pacing | Yes | Core of Pick variant | Reference |
| ccusage | Quota tracker | MIT (unverified) | Active | n/a | Estimates 5h blocks | n/a | Sensor idea | Reference |
| CodexBar | Quota tracker | Open source (licence unverified) | Active | n/a | Reads session/weekly windows and resets | n/a | Quota sensor | Use as is (Mac) |
| CLIProxyAPI | Subscription proxy | MIT | Active | Multi-account load balance | No | n/a | Subscriptions as endpoints | Skip (terms) |
| copilot-api | Subscription proxy | Unverified | Active | n/a | No | n/a | Copilot as endpoint | Skip (terms risk) |
| claude-code-router | Harness-side router | MIT | Very active | Per-scenario rules, plugins | No | Partly | Claude Code on non-Claude models | Reference |
| Ollama / vLLM / llama-swap | Local serving | Various | Active | n/a | n/a | n/a | Local endpoints | Use as is |
| Olla | Local load balancer | Apache-2.0 (unverified) | Active | Priority, round-robin, least-connections, health | n/a | n/a | Front door for local GPUs | Use as is (candidate) |
| GPUStack / exo | Local cluster | Apache-2.0 | Active | Scheduler | n/a | n/a | Overlaps computer pool | Reference |

## Recommendation: build versus adopt

**Adopt**

- A gateway (**LiteLLM**, pinned, or **Bifrost**) as the execution layer for pay-per-token API keys and local endpoints. It holds the keys so agents never see them, logs tokens and cost per call (the efficiency part of a score), handles retries and fallbacks, and can enforce clearance with tags. Arity calls it with an opaque alias per agent run.
- **Ollama / vLLM / llama-swap** on each GPU machine and **Olla** in front of them as the single local endpoint, with health as the liveness signal.
- **OpenRouter** as one API key in the pool for models the user has no direct key for.
- **CodexBar's CLI** (or its approach) as the first quota sensor for subscriptions, until Arity has its own adapters.

**Build**

- **The Pick variant policy.** No product picks a model once per long-lived agent, from the user's own grades, across mixed supply, blind. Build a small contextual bandit (Thompson sampling or UCB) over variants per (tier, role, task kind), with a minimum-runs rule and a budget pacer in the style of ParetoBandit. This is also where exploration is throttled by supply, as [Blind testing](../blind-testing.md) already describes.
- **The supply ledger.** A per-endpoint record of each window (5-hour, weekly, daily, monthly), how much is left and when it resets, fed by the gateway's spend logs for API keys, the quota sensor for subscriptions and the computer pool for local GPUs. It yields a current "price" per endpoint that the pick policy reads.
- **Subscription lanes as harness plus account.** Represent a subscription in the pool as "run this agent in the official client (Claude Code, Codex CLI) signed in as the user", never as an HTTP endpoint. The pick then chooses a model *and* the harness that can reach it, which fits the harness slot already in [Blind testing](../blind-testing.md).
- **Response scrubbing** so the model name does not leak through response fields, headers, logs or the harness UI.

## Contradictions and improvements to the current design

1. **Subscriptions are not endpoints.** The [endpoint pool](../model-router.md#endpoint-pool) treats subscriptions as one more way to reach a model that "the router uses". Anthropic and Google terms allow subscription access only through their own clients, and Anthropic bars intermediating its tokens. In practice a Claude or ChatGPT subscription can only run agents whose harness is that provider's own CLI. The pool needs a notion of "endpoint requires harness X", and picking a subscription also fixes the harness.
2. **Unattended use is a grey area.** Anthropic's consumer terms bar "automated or non-human" access except with an API key or explicit permission, and limits assume "ordinary, individual usage". Background agents that the user did not start interactively may fall outside that. OpenAI is more permissive ("trusted private automation") but says not to share one `auth.json` across concurrent jobs or machines. The pool should cap each subscription to one machine and low concurrency, and the wiki should record which kinds of agent may use a subscription at all. This also settles part of [Sharing with other people](../open-questions.md#sharing-with-other-people): all four providers forbid sharing a login, so subscriptions can never be shared.
3. **"About to reset" does not make tokens nearly free when windows nest.** Claude and Codex have a 5-hour window inside a weekly limit. Spending the last of a 5-hour window also spends weekly quota, which is not about to reset. A token's cost should be judged against the tightest window that will still be binding after the near reset, not the nearest reset alone.
4. **Reset times are not reliably observable.** Only API-key rate-limit headers are documented. Subscription windows are visible through the official client (`/usage`) or undocumented endpoints that tools such as CodexBar read. The spend rule depends on a sensor that may break without notice; the router needs a safe default when quota is unknown.
5. **Blindness leaks.** Gateways and hosted routers return the model name by default (OpenRouter reports it). Harnesses differ visibly too: an agent running in Claude Code looks and behaves differently from one in Codex CLI, and models often name themselves when asked. If subscription lanes force a provider's own harness, the harness alone may reveal the model family to the parent. Grading blindness should be treated as best effort, and scores could record whether the grader could plausibly tell.
6. **Per-agent picks, per-request routers.** Nearly all routing software decides per request. Arity decides once per spawn or wake and keeps the model for the agent's life. This is simpler and fits the bandit framing, but a long agent can exhaust a subscription mid-task. The design needs a rule for that case: fall back to an API key with the same model (only possible when the harness can switch auth), or let the agent go cold and re-pick on wake.
7. **Local availability comes from the computer pool, not the gateway.** Local servers report health, not "free". The computer pool should own GPU reservation and switch the local endpoint on and off in the gateway, so the router sees one signal.
8. **The gateway is the most sensitive process in Arity.** It holds every key in the pool, and LiteLLM's 2026 supply-chain compromise shows the risk. It belongs under [Access](../access.md) with the vault: pinned versions, no automatic updates, network egress only to provider hosts.
9. **Keep grading data local.** Hosted learned routers (Not Diamond, OpenRouter's auto router) need prompts or evaluation data sent to a third party, which conflicts with clearance. A local bandit avoids that.
