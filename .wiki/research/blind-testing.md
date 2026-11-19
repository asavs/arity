# Research: blind testing and self-improvement

[Back to index](../README.md) · Compares against [Blind testing](../blind-testing.md), [Papercuts](../papercuts.md), [Model router](../model-router.md) and [Open questions](../open-questions.md).

A survey of existing software and research on blind evaluation of agents in everyday use, on picking models from live feedback, and on systems that improve their own tools and prompts from feedback. All sources were checked on **2026-11-18**. Most were read through their abstract, project page or documentation rather than the full paper; where a claim comes only from a secondary summary, or could not be confirmed, it is marked **(unverified)**.

Routing products and the endpoint pool are covered in more depth by the sibling routing survey; this page touches routing only where it learns from feedback.

## Contents

1. [Blind comparison platforms and how they rank](#1-blind-comparison-platforms-and-how-they-rank)
2. [Rating scales: 1 to 5 versus pairwise](#2-rating-scales-1-to-5-versus-pairwise)
3. [Routing and exploration from live feedback](#3-routing-and-exploration-from-live-feedback)
4. [Evaluating agents in production, and LLM judges](#4-evaluating-agents-in-production-and-llm-judges)
5. [Attributing an outcome when several things vary](#5-attributing-an-outcome-when-several-things-vary)
6. [Agents that improve their own tools and prompts](#6-agents-that-improve-their-own-tools-and-prompts)
7. [Comparison table](#7-comparison-table)
8. [Recommendations for scoring and exploration](#8-recommendations-for-scoring-and-exploration)
9. [What contradicts or would improve the current design](#9-what-contradicts-or-would-improve-the-current-design)

---

## 1. Blind comparison platforms and how they rank

### LMArena (formerly Chatbot Arena)

- **Who:** LMArena, which grew out of LMSYS at UC Berkeley. Site: <https://arena.ai/faq>, methodology blog: <https://arena.ai/blog/style-control/>.
- **Maturity:** The best-known public blind leaderboard, running since 2023.
- **How it works:** A user sends a prompt, gets two anonymous answers side by side, and votes for one. Rankings come from a **Bradley-Terry model**: a logistic regression of vote outcomes on which models were in the match. The "Arena Score" is the fitted coefficient, earlier called Elo ([arena.ai blog](https://arena.ai/blog/style-control/)). **Style control** adds answer length and markdown features as extra variables in the same regression, so each model's coefficient is estimated with style held fixed; length was the dominant style effect ([lmsys blog, 2024-08-28](https://lmsys.org/blog/2024-08-28-style-control)).
- **What it says about Arity:**
  - The regression trick is the key lesson. LMArena does not average a model's win rate; it fits all matches at once and lets extra variables soak up nuisance effects. Arity can do the same with a 1 to 5 rating: regress the rating on model, harness, skills, MCP servers, persona, grader family and answer length together. This is the standard answer to "a good model is not blamed for a bad tool" (see [section 5](#5-attributing-an-outcome-when-several-things-vary)).
  - LMArena only works because it shows two answers to the same prompt. Arity runs each task once, so it cannot copy the pairwise format directly.
  - The team itself calls the style analysis observational ([lmsys blog](https://lmsys.org/blog/2024-08-28-style-control)). Arity has the advantage of randomising its own choices, which makes its comparisons experimental rather than observational, provided the randomisation probabilities are recorded.
- **Verdict:** Use as a reference for the method.

### Arena-Rank

- **Who:** LMArena. <https://github.com/lmarena/arena-rank>, PyPI `arena-rank` <https://pypi.org/project/arena-rank/>, announcement <https://news.lmarena.ai/arena-rank>.
- **Licence:** Apache 2.0 according to the announcement; the LICENSE file itself was not opened **(partly unverified)**.
- **Maturity:** Version 0.1.1 on PyPI; powers the public leaderboard ([news.lmarena.ai](https://news.lmarena.ai/arena-rank)). Built on JAX ([depscope listing](https://depscope.dev/pkg/pypi/arena-rank)).
- **How it works:** A general Bradley-Terry ranker with an extension for contextual features, used for the style-controlled leaderboards ([news.lmarena.ai](https://news.lmarena.ai/arena-rank)).
- **What it says about Arity:** Ready-made code for pairwise ranking with covariates, if Arity ever collects pairwise data (see the shadow-pair recommendation in [section 8](#8-recommendations-for-scoring-and-exploration)). It does not handle a 1 to 5 scale.
- **Verdict:** Use as is, but only for pairwise data. For 1 to 5 ratings, an ordinal regression in a general statistics library does the same job.

### Prompt-to-Leaderboard (P2L)

- **Who:** Frick, Chiang and others at Berkeley/LMArena, February 2025. Paper <https://arxiv.org/abs/2502.14855>, code <https://github.com/lmarena/p2l>.
- **Maturity:** Research code; the paper reports a live trial on Chatbot Arena in January 2025 where the P2L router took first place ([arXiv v2](https://arxiv.org/html/2502.14855v2)).
- **How it works:** An LLM reads a prompt and outputs one Bradley-Terry coefficient per model, giving a leaderboard specific to that prompt. Plain Bradley-Terry is the special case that ignores the prompt. The per-prompt leaderboard is used to route each query ([arXiv](https://arxiv.org/html/2502.14855v2)).
- **What it says about Arity:** Arity keeps scores "per role, tier and kind of task", which is a hand-made, coarse version of the same idea. P2L shows that conditioning on the task itself, rather than on a few buckets, is what makes a router good, and that the gain grows with data ([arXiv](https://arxiv.org/html/2502.14855v2)). For one person's volume of work, buckets with shared statistical strength (partial pooling) are more realistic than training a P2L model.
- **Verdict:** Use as a reference. Too data-hungry to train for one user.

### Copilot Arena

- **Who:** Chi and others (CMU/Berkeley), ICML 2025. <https://proceedings.mlr.press/v267/chi25a.html>, <https://arxiv.org/abs/2502.09328>.
- **Maturity:** Open-sourced VS Code extension with released data ([ICML abstract](https://proceedings.mlr.press/v267/chi25a.html)).
- **How it works:** Inside the developer's own editor, two models' code completions are shown as a pair and the one the developer accepts counts as a vote. Over 4.5 million suggestions and over 11,000 pairwise judgements were collected ([ICML](https://proceedings.mlr.press/v267/chi25a.html)). Preferences were consistent across programming languages but varied a lot by task category, and rankings differed from static benchmarks ([ICML](https://proceedings.mlr.press/v267/chi25a.html)).
- **What it says about Arity:** This is the closest published system to Arity's idea of "every real run doubles as a trial": evaluation inside real work, with the user's normal action (accepting a completion) as the signal. It supports two Arity choices: treating the user's edits and overrides as signals instead of asking for ratings, and keeping scores per kind of task. Copilot Arena could only do this because completions are cheap enough to generate twice.
- **Verdict:** Use as a reference.

### The Leaderboard Illusion

- **Who:** Singh, Hooker and 11 others (Cohere Labs and academic co-authors), NeurIPS 2025. <https://arxiv.org/abs/2504.20879>.
- **How it works:** A critique of Chatbot Arena. Some providers privately tested many variants (27 from one provider in one month) and published only the best score, which biases rankings through selective disclosure; a few providers also received far more of the comparison data ([arXiv abstract](https://arxiv.org/abs/2504.20879)).
- **What it says about Arity:** A direct warning for the papercut loop. If a fixer can enter several versions of a fix and the best one is kept, the winner is partly luck: this is the "best of many tries" problem. Arity needs a rule for how many variants per papercut may be trialled and how a winner is declared (see [section 9](#9-what-contradicts-or-would-improve-the-current-design)).
- **Verdict:** Use as a reference.

### Critique of Bradley-Terry stability

- A 2026 paper argues that Bradley-Terry aggregation can violate a stability property on LMArena data and proposes an alternative ([procaccia.info PDF](https://procaccia.info/wp-content/uploads/2026/01/leaderboard.pdf), [alphaxiv 2606.02547](https://www.alphaxiv.org/abs/2606.02547.md)) **(read from search summary only; unverified)**. Relevant only if Arity adopts pairwise ranking. Verdict: skip for now.

---

## 2. Rating scales: 1 to 5 versus pairwise

The evidence is genuinely mixed.

**For pairwise.**
- Liu and others (PairS) report that LLM ratings on a Likert-style scale have score distributions that do not match human ratings, and that pairwise ranking aligns better with human judgement ([arXiv 2403.16950](https://arxiv.org/abs/2403.16950)).
- Liusie and others compare absolute grading with comparative assessment by LLMs and report the comparative approach is competitive, while measuring its position bias ([arXiv 2307.07889](https://arxiv.org/abs/2307.07889)).

**Against pairwise.**
- "Pairwise or Pointwise?" (2025) finds pairwise judgements far easier to distract: preferences flipped in about 35% of cases under distractor features, against about 9% for absolute scores ([arXiv 2504.14716](https://arxiv.org/abs/2504.14716)).
- A 2025 paper reports that preferences derived from ratings and from rankings disagree in about 60% of cases for both human and AI annotators ([arXiv 2505.08498](https://arxiv.org/abs/2505.08498)) **(the link between this claim and that arXiv number comes from a search summary; unverified)**.
- Position bias, a judge favouring whichever answer comes first, exists only in pairwise settings; it is systematic and varies across judges and tasks ([Judging the Judges, arXiv 2406.07791](https://arxiv.org/html/2406.07791v7)).

**What it says about Arity:**
- Pairwise needs two outputs for the same task. Arity's rule that "the same task is not run twice" makes pairwise impossible in the main loop, so the 1 to 5 scale is the right default.
- The weakness of a 1 to 5 scale is drift between raters: one rater's 4 is another's 3, and LLM raters bunch near the top. The fix is statistical, not a different form: give each rater (each grading variant and the user) its own offset in the model (see [section 4, Dawid-Skene](#rater-calibration-dawid-skene-and-relatives)), and anchor each number with a short description.
- Pairwise is still the best tool for one specific job: deciding whether a papercut fix beats the version it replaces. That is exactly where surplus tokens could buy a second run (see [section 8](#8-recommendations-for-scoring-and-exploration)).

---

## 3. Routing and exploration from live feedback

### RouteLLM

- **Who:** LMSYS / UC Berkeley and Anyscale, ICLR 2025. <https://github.com/lm-sys/RouteLLM>, paper <https://arxiv.org/abs/2406.18665>, blog <https://www.lmsys.org/blog/2024-07-01-routellm>.
- **Licence:** Apache 2.0 ([pypi](https://pypi.org/project/routellm)).
- **Maturity:** Released 2024, pip-installable with an OpenAI-compatible server; development activity since then not checked **(unverified)**.
- **How it works:** Trains a router from Chatbot Arena preference data to choose between a strong and a weak model per query. Routers include matrix factorisation, a BERT classifier and an Elo-based ranker. The paper reports more than 2x cost saving on benchmarks while keeping most of the strong model's quality ([arXiv](https://arxiv.org/abs/2406.18665)).
- **What it says about Arity:** Offline, two models only, and trained on someone else's preferences. Arity's router chooses among many variants and learns online from its own grades. Matrix factorisation (task embedding times model embedding) is a useful idea for sharing information across task kinds.
- **Verdict:** Use as a reference.

### PILOT (bandit routing under budgets)

- **Who:** Panda and others, Findings of EMNLP 2025. <https://arxiv.org/abs/2508.21141>, <https://aclanthology.org/2025.findings-emnlp.1301>.
- **How it works:** Treats routing as a contextual bandit. It starts from a prior learned from offline human preferences, then updates from online feedback with a LinUCB variant, and a knapsack-style policy keeps each user within budget ([arXiv](https://arxiv.org/abs/2508.21141)).
- **What it says about Arity:** Matches Arity's design closely: a prior from public data (benchmarks, arena scores) for a cold start, refined by live grades, with spending handled as a separate constraint rather than mixed into the score. Arity already separates "score" from "cost weight" in [Model router](../model-router.md#how-it-chooses); PILOT is evidence that separation works.
- **Verdict:** Use as a reference.

### BaRP (routing from bandit feedback)

- **Who:** Wei and others, October 2025. <https://arxiv.org/abs/2510.07429>.
- **How it works:** A multi-objective contextual bandit in which the router sees the score and cost only of the model it picked, as in real deployment. A preference vector sets the quality-cost trade-off at run time without retraining ([arXiv](https://arxiv.org/html/2510.07429v1)).
- **What it says about Arity:** The run-time trade-off knob is what Arity needs for "exploration follows supply": when quota is about to reset, turn the cost weight down without touching any score.
- **Verdict:** Use as a reference.

### TensorZero

- **Who:** TensorZero (company). <https://github.com/tensorzero/tensorzero>, docs <https://www.tensorzero.com/docs/experimentation/run-adaptive-ab-tests>, blog <https://www.tensorzero.com/blog/bandits-in-your-llm-gateway/>.
- **Licence:** Apache 2.0 for the self-hosted stack; a separate paid "Autopilot" product exists ([Product Hunt](https://www.producthunt.com/products/tensorzero)) **(licence from secondary source; confirm on GitHub)**.
- **Maturity:** Actively developed open-source gateway.
- **How it works:** An LLM gateway where a "function" has several "variants" (model, prompt, inference strategy). The gateway spreads requests over variants with a multi-armed bandit and stops the experiment when a winner is clear; feedback and metrics are stored in the user's own database. The vendor reports finding the best variant 37% faster than a fixed A/B test **(vendor claim)** ([blog](https://www.tensorzero.com/blog/bandits-in-your-llm-gateway/)).
- **What it says about Arity:** The nearest existing software to Arity's variant registry plus router: variants as whole configurations, live feedback, adaptive allocation, and a stopping rule. It treats each variant as one indivisible arm, so it does not split credit across dimensions, and it sits in front of API calls rather than around agent harnesses like Claude Code or Codex.
- **Verdict:** Use as a reference for the experiment design and stopping rule. Possibly use as is for model-only routing of plain API calls, but it does not fit harness-level variants.

### Not Diamond

- **Who:** Not Diamond (company). Docs <https://docs.notdiamond.ai/docs/real-time-personalization>, pricing <https://notdiamond.ai/pricing>.
- **Licence:** Proprietary hosted service.
- **How it works:** A hosted router that takes per-user thumbs up/down feedback and says it uses reinforcement learning to improve picks over time; a coding-agent product uses implicit developer signals and the KV-cache state ([docs](https://docs.notdiamond.ai/docs/real-time-personalization), [blog](https://www.notdiamond.ai/blog/not-diamond-code-intelligent-model-routing-for-coding-agents)). No independent evaluation found **(vendor claims)**.
- **What it says about Arity:** Two ideas worth keeping: implicit signals count, and cache state is a routing input. The second bears on [Model on wake](../open-questions.md#model-on-wake): keeping a warm cache is a real saving the router should weigh. Sending task content to a third-party router conflicts with Arity's clearance rules.
- **Verdict:** Skip as software; use the cache idea as a reference.

### Bandits with delayed and missing feedback

- Thompson sampling works with arbitrary feedback delays ([Wu and Wager, arXiv 2202.12431](https://arxiv.org/abs/2202.12431)), and there are variants for settings where only positive events are ever observed ([Vernade and others, arXiv 1807.02089](https://arxiv.org/pdf/1807.02089v3.pdf)).
- **What it says about Arity:** Arity's best signals (rework found later, whether the result was used) arrive late. Delays are not a problem for a Thompson sampling router. Ratings that are missing for a reason ("strong opinions get recorded, average ones do not", from [Blocking ratings](../open-questions.md#blocking-ratings)) are a problem: no method found corrects that cleanly, which argues for making ratings required.
- **Verdict:** Use as a reference.

---

## 4. Evaluating agents in production, and LLM judges

### Judging LLM-as-a-Judge (MT-Bench)

- **Who:** Zheng and others (LMSYS), NeurIPS 2023. <https://arxiv.org/abs/2306.05685>.
- **How it works:** Strong LLMs grade chatbot answers. GPT-4 as judge agreed with human experts over 80% of the time, about the level at which humans agree with each other. The paper names position bias, verbosity bias (preferring longer answers) and self-enhancement bias (favouring one's own family) ([arXiv](https://arxiv.org/abs/2306.05685); bias details via [emergentmind summary](https://www.emergentmind.com/papers/2306.05685)).
- **What it says about Arity:** LLM parents can grade subagents usefully, but length must be controlled for. Arity already logs tokens, so answer length can go into the score model as a covariate, as LMArena's style control does.
- **Verdict:** Use as a reference.

### LLM Evaluators Recognize and Favor Their Own Generations

- **Who:** Panickssery, Bowman and Feng, NeurIPS 2024. <https://arxiv.org/abs/2404.13076>.
- **How it works:** GPT-3.5, GPT-4 and Llama 2 rated their own summaries above others' even when humans judged them equal. The models could also tell their own text from others' with better than chance accuracy, and fine-tuning showed a linear link between self-recognition and the strength of self-preference ([arXiv](https://arxiv.org/abs/2404.13076), [NeurIPS](https://papers.nips.cc/paper_files/paper/2024/hash/7f1f0218e45f5414c79c0679633e47bc-Abstract-Conference.html)).
- **What it says about Arity:** **Blinding does not remove family bias.** A grader that is not told the model can still recognise its own family's writing. Arity is right to record the grader's variant with each grade, but recording is not enough: the score model needs a "same family as grader" term so the bias is measured and subtracted. The router can also deliberately pair graders and doers from different families for a share of runs, which makes that term estimable.
- **Verdict:** Use as a reference. This is the strongest single piece of evidence behind an existing Arity rule.

### Rater calibration: Dawid-Skene and relatives

- The classic Dawid-Skene model estimates each annotator's error rates and corrects for them. An EMNLP 2024 paper applies a Bayesian version to correct win-rate estimates from LLM judges, optionally with a few human labels ([aclanthology 2024.emnlp-main.273](https://aclanthology.org/2024.emnlp-main.273.pdf)).
- MLflow's guide on judge calibration recommends measuring a judge against a small human-labelled set and adjusting; it notes that a judge can be very consistent and still biased ([mlflow.org](https://mlflow.org/articles/llm-judge-calibration/)).
- "How to Correctly Report LLM-as-a-Judge Evaluations" gives confidence intervals that include the uncertainty of the calibration set ([arXiv 2511.21140](https://arxiv.org/html/2511.21140v3)).
- **What it says about Arity:** The user's occasional ratings and edits are Arity's human calibration set. Each grading variant gets an offset and a noise level, estimated from cases where both the user and an agent rated the same work. That turns "the user is never required to rate" into "the user's rare ratings are worth the most".
- **Verdict:** Use as a reference for the method.

### Langfuse

- **Who:** Langfuse (company). <https://langfuse.com/docs/evaluation/scores/overview>, licensing post <https://langfuse.com/blog/2025-06-04-open-sourcing-langfuse-product>.
- **Licence:** MIT, including LLM-as-a-judge, annotation queues and experiments since June 2025 ([Langfuse blog](https://langfuse.com/blog/2025-06-04-open-sourcing-langfuse-product)).
- **Maturity:** Widely used, self-hostable tracing and evaluation platform.
- **How it works:** Every quality judgement (human annotation, LLM judge, programmatic check, end-user feedback) is stored as a score attached to a trace. Annotation queues give reviewers batches to work through, and the docs recommend calibrating a judge against human scores ([scores docs](https://langfuse.com/docs/evaluation/scores/overview)). Free-text scores cannot be aggregated ([docs](https://langfuse.com/docs/evaluation/scores/overview.md)).
- **What it says about Arity:** Its "a score is a typed record on a trace from any source" is the same shape as Arity's grade, gate and metric. It has no notion of a sealed variant or blind reveal; that would be Arity's own table. Whether to use it for trace storage is a database question.
- **Verdict:** Use as a reference for the score data model; possibly use as is for trace viewing, to be decided with the database survey.

### Arize Phoenix

- **Who:** Arize AI. <https://github.com/Arize-ai/phoenix>, licence page <https://arize.com/docs/phoenix/self-hosting/license>.
- **Licence:** Elastic License 2.0, source-available, not OSI open source; self-hosting is allowed ([Arize docs](https://arize.com/docs/phoenix/self-hosting/license)).
- **How it works:** Tracing plus an evals library of LLM-judge templates.
- **What it says about Arity:** Same ground as Langfuse with a more restrictive licence.
- **Verdict:** Skip in favour of Langfuse if a tracing tool is wanted.

### Inspect AI

- **Who:** UK AI Security Institute. <https://github.com/UKGovernmentBEIS/inspect_ai>, docs <https://inspect.aisi.org.uk/>.
- **Licence:** MIT ([libraries.io](https://libraries.io/pypi/inspect-ai/0.3.132)). Who maintains it now is reported inconsistently ([casrai guide](https://casrai.org/guides/inspect-ai-evaluation-framework-uk-aisi)) **(unverified)**.
- **How it works:** A Python framework of datasets, solvers/agents, tools and scorers, with sandboxed execution and many ready-made agent benchmarks ([aicoolies review](https://aicoolies.com/reviews/inspect-ai-review)).
- **What it says about Arity:** Offline evaluation, not live trials. Useful as a cheap screen for a papercut fix before it enters the live blind test, so an obviously broken fix never costs a real task.
- **Verdict:** Use as is for an offline pre-check stage, if one is added.

### Promptfoo

- **Who:** Promptfoo, which OpenAI announced it would acquire in March 2026 ([qaskills summary](https://qaskills.sh/blog/openai-promptfoo-acquisition-explained-2026)) **(acquisition from secondary sources; unverified)**. <https://github.com/promptfoo/promptfoo>.
- **Licence:** MIT ([socket.dev](https://socket.dev/npm/package/promptfoo)).
- **How it works:** Config-driven test suites comparing prompts and models side by side, with assertions and LLM-graded checks.
- **What it says about Arity:** Same role as Inspect AI: a pre-check for fixes. Its future direction after the acquisition is unclear.
- **Verdict:** Use as a reference; prefer Inspect AI for agent tasks.

### Anthropic skill-creator (blind comparator pattern)

- **Who:** Anthropic, in the `anthropics/skills` repository <https://github.com/anthropics/skills>. Details below come from third-party write-ups ([tessl.io](https://tessl.io/blog/anthropic-brings-evals-to-skill-creator-heres-why-thats-a-big-deal), [smartscope](https://smartscope.blog/en/generative-ai/claude/skill-creator-eval-pipeline-guide/)) **(not checked against the repository; unverified)**.
- **How it works:** Reportedly, test scenarios run on subagents with and without a skill, a comparator agent judges two outputs without knowing which configuration made each (A, B or tie), and an analyser then unblinds the result to explain why the winner won and how the loser should change. Benchmarks repeat each configuration about three times.
- **What it says about Arity:** The same seal, grade, reveal and explain sequence as Arity's loop, applied to skills offline. The "explain after reveal" step is a good addition to the papercut loop: a losing fix should produce a note on why, which becomes input to the next attempt.
- **Verdict:** Use as a reference.

---

## 5. Attributing an outcome when several things vary

### Regression with each dimension as a variable

- Not a single project but the method behind LMArena's style control ([lmsys blog](https://lmsys.org/blog/2024-08-28-style-control)) and classical factorial experiments. Ackerman and others apply combinatorial test design and analysis of variance to choose LLM pipeline settings, relying on the observation that low-order interactions dominate ([arXiv 2405.13020](https://arxiv.org/abs/2405.13020)).
- **How it works:** Fit the rating as model effect + harness effect + skill effect + MCP effect + persona effect + grader effects + a few chosen interactions. Each coefficient is that option's contribution with the others held fixed.
- **What it says about Arity:** Arity's current rule, "each option's score is its average over all the runs it appeared in" ([Blind testing](../blind-testing.md#slots)), is unbiased only if every dimension is chosen independently and uniformly. Once the router starts favouring good options, they appear together (good model with good tool), and plain averages credit each with the other's quality. Regression fixes this. The router must also record the probability with which it picked each option, so that weighting by inverse probability can correct for non-uniform picking.
- **Interactions are real:** a 2026 full-factorial study of prompt features reports super-additive effects of 3 to 12 percentage points for one model family ([alphaxiv 2609.03156](https://www.alphaxiv.org/abs/2609.03156)) **(snippet only; unverified)**. A harness tuned for one vendor's models is an obvious model × harness interaction; Arity's starting set already pairs harnesses with their own models.
- **Verdict:** Use as the scoring method (see [section 8](#8-recommendations-for-scoring-and-exploration)).

### Shapley-value attribution

- **SHARP** (February 2026) gives each agent in a multi-agent system a credit equal to the outcome with it minus the outcome without it ([arXiv 2602.08335](https://arxiv.org/abs/2602.08335)).
- **Evaluation-aligned credit for multi-LLM agents** splits a system reward across agents with Shapley values; it is conceptual, with experiments left for later ([arXiv 2511.10687](https://arxiv.org/abs/2511.10687)).
- **Model allocation in agentic workflows** (ACL Findings 2026) computes Shapley values over which workflow components get a stronger model, and measures their interactions ([aclanthology 2026.findings-acl.359](https://aclanthology.org/2026.findings-acl.359.pdf)) **(abstract only)**.
- **What it says about Arity:** Shapley values need the outcome for many combinations of the same task, which means rerunning tasks. Arity never reruns, so exact Shapley values are out of reach; the regression above estimates the same averaged contributions from different tasks. The ACL paper's question, which agent in a tree deserves the expensive model, is relevant to tier differences.
- **Verdict:** Use as a reference only.

---

## 6. Agents that improve their own tools and prompts

### AT-AT: Automatic Tool Optimization from Agent Traces

- **Who:** Listed at NeurIPS 2026. <https://neurips.cc/virtual/2026/170488> **(abstract only; authors and code not checked)**.
- **How it works:** A small coding agent reads earlier agent trajectories and evaluation results, then rewrites tool descriptions and tool implementations. Applied to production logs, it is reported to have substantially reduced MCP tool-call error rates ([NeurIPS page](https://neurips.cc/virtual/2026/170488)).
- **What it says about Arity:** The closest match to the papercut loop's "propose a fix" step for MCP servers. It uses failed tool calls as its signal, which Arity already logs. Papercuts add something AT-AT lacks: the agent's own one-line account of what went wrong.
- **Verdict:** Use as a reference; read the full paper when it is out.

### ToolObserver / OpaqueToolsBench

- **Who:** February 2026. <https://arxiv.org/abs/2602.15197>.
- **How it works:** Repeatedly refines tool documentation by watching execution feedback from tool-calling runs, compared with earlier description optimisers Play2Prompt and EasyTool ([arXiv](https://arxiv.org/html/2602.15197v1)).
- **What it says about Arity:** Tool descriptions are a cheap, low-risk kind of fix, the "reworded skill" end of [Approval for fixes](../open-questions.md#approval-for-fixes).
- **Verdict:** Use as a reference.

### GEPA (Genetic-Pareto prompt evolution)

- **Who:** Agrawal and others, ICLR 2026 oral. Paper <https://arxiv.org/abs/2507.19457>, DSPy docs <https://dspy.ai/api/optimizers/GEPA/overview/>, standalone library `gepa` ([dspy.ai](https://dspy.ai/api/optimizers/GEPA)).
- **Licence:** DSPy is MIT **(not re-checked for this page)**; the gepa library's licence was not checked **(unverified)**.
- **Maturity:** In production use through DSPy.
- **How it works:** Reads full execution traces and written feedback, not just a number, asks an LLM to reflect and propose edits to a prompt, and keeps a **Pareto set** of candidates (each best on some subset of tasks) instead of a single winner. Reported to beat reinforcement-learning tuning with far fewer runs ([dspy.ai](https://dspy.ai/api/optimizers/GEPA/overview/); figures via [secondary guide](https://www.morphllm.com/gepa-prompt-optimization) **(unverified)**).
- **What it says about Arity:**
  - Validates using text (the rating paragraph, papercut lines) as the input to fixes, not just the number.
  - Arity keeps one winner per role. GEPA's experience is that keeping several candidates, each best for some kind of task, finds better results. Since scores are already per kind of task, a role could keep different defaults per kind of task.
  - Could serve as the engine of the "propose a fix" step for personas and skills.
- **Verdict:** Use as a reference; consider using as is for persona and skill rewriting.

### TextGrad

- **Who:** Yuksekgonul, Zou and others (Stanford), Nature 2025. <https://arxiv.org/abs/2406.07496>, <https://github.com/zou-group/textgrad>.
- **How it works:** Treats LLM-written criticism as a "gradient" and propagates it backwards through a chain of LLM calls to improve each prompt or output ([arXiv](https://arxiv.org/abs/2406.07496)).
- **What it says about Arity:** The idea of passing criticism back down a chain resembles a parent's rating paragraph flowing back to the subagent's persona. GEPA is the more practical tool for the same job.
- **Verdict:** Use as a reference.

### ExpeL and Agent Workflow Memory

- **ExpeL** (Tsinghua, AAAI 2024): the agent turns past successes and failures into written insights, managed with add, upvote, downvote and edit operations, and recalls them on new tasks ([arXiv 2308.10144](https://arxiv.org/abs/2308.10144)).
- **Agent Workflow Memory** (CMU, ICML 2025): extracts reusable step sequences from successful runs and offers them when relevant ([ICML](https://icml.cc/virtual/2025/poster/45496)).
- **What it says about Arity:** Upvote and downvote on insights is close to "duplicates are left in": counting repeats gives a priority signal. These systems apply lessons directly without a blind test, which is exactly what Arity's rule "a fix is never simply applied" guards against.
- **Verdict:** Use as a reference.

### Voyager

- **Who:** Wang and others (NVIDIA, Caltech and others), 2023. <https://arxiv.org/abs/2305.16291>.
- **How it works:** An agent writes code for skills, keeps the ones that work in a library indexed by description, and builds harder skills from simpler ones ([arXiv](https://arxiv.org/html/2305.16291v2)).
- **What it says about Arity:** Early proof that a growing, versioned skill registry fed by the agent's own experience works. Voyager's test of a skill is "did it work in the environment", similar to Arity's gates.
- **Verdict:** Use as a reference.

### Darwin Gödel Machine (DGM)

- **Who:** Sakana AI, University of British Columbia and Vector Institute, 2025. <https://sakana.ai/dgm/>, <https://arxiv.org/abs/2505.22954>.
- **How it works:** A coding agent proposes edits to its own code; each edited agent is tested on a benchmark and kept in an archive, so the search can branch from any earlier version. It raised its Polyglot score from 14.2% to 30.7% ([Sakana](https://sakana.ai/dgm/)). It also sometimes gamed its own hallucination checks ("objective hacking") ([The Register](https://www.theregister.com/2025/06/02/self_improving_ai_cheat/)).
- **What it says about Arity:** Two lessons. First, an archive of past variants, not only the current default, lets the system return to a branch that later proves useful. Second, **any metric a fix can influence will be gamed**. Papercut count is a score input, and a persona or skill variant that discourages filing papercuts would look better. See [section 9](#9-what-contradicts-or-would-improve-the-current-design).
- **Verdict:** Use as a reference.

### ADAS (Meta Agent Search)

- **Who:** Hu, Lu and Clune (UBC, Vector Institute), NeurIPS 2024 / ICLR 2025. <https://arxiv.org/abs/2408.08435>, code <https://github.com/ShengranHu/ADAS>.
- **How it works:** A meta agent writes whole new agent designs in code, drawing on an archive of earlier designs, and tests them; discovered designs transferred across domains and models ([arXiv](https://arxiv.org/abs/2408.08435)).
- **What it says about Arity:** Search over whole agent designs is much broader than Arity's per-papercut fixes, and needs benchmark runs Arity does not have.
- **Verdict:** Skip for now.

### Surveys

- Gao and others, "A Survey of Self-Evolving Agents" (TMLR 2026), organised by what, when and how an agent evolves ([arXiv 2507.21046](https://arxiv.org/abs/2507.21046)).
- Fang and others, "A Comprehensive Survey of Self-Evolving AI Agents" (2025), a loop of inputs, agent system, environment and optimisers ([arXiv 2508.07407](https://arxiv.org/abs/2508.07407)).
- Verdict: use as a reference when looking for further prior art.

**No system found** combines all of Arity's loop: agent-filed one-line complaints, routed to an owner as tasks, turned into a candidate fix, and admitted only after a blind live trial. The pieces exist separately: trace-driven fixes (AT-AT, GEPA), blind comparison of a fix against the original (skill-creator, reportedly), and adaptive live trials of variants (TensorZero).

---

## 7. Comparison table

| Project or method | Kind | Licence | Maturity | Signal it learns from | Handles several things varying | Verdict |
|---|---|---|---|---|---|---|
| LMArena | Blind pairwise platform | Service | Mature | Pairwise votes | Yes, covariates in Bradley-Terry | Reference |
| Arena-Rank | Ranking library | Apache 2.0 (per post) | Early (0.1.1) | Pairwise outcomes | Yes, contextual features | Use as is, for pairwise data |
| P2L | Per-prompt leaderboard and router | Code public | Research | Pairwise votes | Per prompt | Reference |
| Copilot Arena | In-workflow blind pairs | Open source | Research, deployed | Accept or reject | No | Reference |
| Leaderboard Illusion | Critique | n/a | Peer-reviewed | n/a | n/a | Reference (warning) |
| RouteLLM | Offline router | Apache 2.0 | Released 2024 | Preference data | No, two models | Reference |
| PILOT | Bandit router | Paper | Research | Online feedback plus prior | No | Reference |
| BaRP | Bandit router | Paper | Research | Score and cost of chosen arm | No | Reference |
| TensorZero | Gateway with adaptive A/B | Apache 2.0 | Active | Metrics, feedback | Whole variant as one arm | Reference; model-only use possible |
| Not Diamond | Hosted router | Proprietary | Commercial | Thumbs, implicit | No | Skip |
| MT-Bench judge study | Judge biases | n/a | Peer-reviewed | n/a | n/a | Reference |
| Self-preference study | Judge bias | n/a | Peer-reviewed | n/a | n/a | Reference (key) |
| Dawid-Skene calibration | Rater model | n/a | Classical | Multiple raters | Rater effects | Reference (method) |
| Langfuse | Tracing and scores | MIT | Mature | Any score source | No | Reference; maybe use |
| Arize Phoenix | Tracing and evals | ELv2 | Mature | Any | No | Skip |
| Inspect AI | Offline eval framework | MIT | Mature | Scorers | No | Use for fix pre-check |
| Promptfoo | Offline eval CLI | MIT | Mature | Assertions, judges | No | Reference |
| skill-creator comparator | Blind A/B for skills | Not checked | Shipped (unverified) | Blind pairwise judge | No | Reference |
| Regression / factorial design | Attribution method | n/a | Classical | Ratings | Yes | Use |
| Shapley credit (SHARP and others) | Attribution method | n/a | Research | Counterfactual reruns | Yes, needs reruns | Reference |
| AT-AT | Tool fixer from traces | Not checked | Research | Traces, errors | No | Reference |
| ToolObserver | Tool-doc refiner | Paper | Research | Execution feedback | No | Reference |
| GEPA | Prompt evolution | DSPy MIT; gepa unchecked | Used in DSPy | Traces and text feedback | Pareto set | Reference; maybe use for fixes |
| TextGrad | Text "gradients" | Open source | Research (Nature) | LLM criticism | Chain of calls | Reference |
| ExpeL, AWM | Learning from experience | Paper code | Research | Success and failure | No | Reference |
| Voyager | Skill library | Open source | Research | Environment success | No | Reference |
| DGM | Self-editing agent | Code public | Research | Benchmark | Archive of variants | Reference (warning) |
| ADAS | Agent design search | Code public | Research | Benchmark | Whole design | Skip |

---

## 8. Recommendations for scoring and exploration

1. **Score with one regression, not separate averages.** Model each rating (an ordered 1 to 5 outcome) as the sum of effects for model, harness, skill version, each MCP server, persona, plus rater effects, answer length and a "grader is same family as doer" term. Pool across role, tier and kind of task with shared priors (a hierarchical model), so a sparse bucket borrows strength from related ones instead of starting at zero. A variant's "score" is then its posterior effect with an uncertainty band, which is exactly the "better statistical estimate" [Model router](../model-router.md#scores) already allows. Evidence: LMArena style control, factorial design work, Dawid-Skene.

2. **Randomise each slot independently during exploration, and log the probabilities.** Independent randomisation keeps dimensions from moving together, which is what makes regression attribution reliable. Logged probabilities allow inverse-probability weighting once the router starts exploiting.

3. **Keep the 1 to 5 scale for everyday grading.** It is the only form that works with "each task gets one variant", and absolute scores were less easily distracted than pairwise in the one direct comparison found. Anchor each number with a short description in the grading prompt, and let rater offsets absorb harsh and lenient graders.

4. **Add pairwise "shadow pairs" only for promoting fixes, paid for by surplus tokens.** When a papercut fix is on trial and quota is about to reset, run the old and new variant on the same low-risk task and have a blind grader pick one, with order randomised to cancel position bias. This links two existing Arity ideas (exploration follows supply; a fix must win a blind test) and gives the most reliable comparison exactly where a decision is made. Arena-Rank can rank the results.

5. **Use Thompson sampling with a cost knob for exploration.** Sample a plausible score for each eligible variant from its posterior, combine with cost through a weight that follows supply (BaRP's run-time preference vector), and pick the best. Exploration then shrinks automatically as uncertainty shrinks, and "minimum runs before affecting picks" becomes a property of the posterior rather than a separate rule. Start from public benchmark data as a prior, as PILOT does.

6. **Answer the blocking-ratings question with "yes, minimal, plus late outcomes".** Make the parent give a 1 to 5 and one sentence before continuing, because optional ratings go missing in a biased way that no surveyed method can correct. Then let late signals (gates, used or redone, rework found later) carry more weight in the regression, so the first impression is corrected by what actually happened. Treat the user's rare ratings and edits as the calibration set for agent raters.

7. **Make family bias an estimated term and plan for it.** Blinding does not stop a model recognising its own family's text. For a share of runs, choose the grader's family deliberately so the "same family" effect can be estimated and subtracted.

8. **Give the papercut loop a stopping rule and a budget.** For each papercut: at most a small fixed number of candidate fixes in trial at once; a pre-set decision rule (for example, promote when the probability the new variant is better exceeds 95% under the posterior, drop when it falls below 5% or after a run cap). This avoids the "best of many tries" bias described in the Leaderboard Illusion. TensorZero's adaptive stopping is a working example.

9. **Add an optional offline pre-check before live trials.** Run a fix through a handful of past tasks in Inspect AI or the skill-creator style comparator; a fix that fails there never costs a live task. Then, after a live trial ends, unblind and write a short "why it won or lost" note attached to the papercut (the skill-creator analyser pattern), which feeds the next attempt.

10. **Consider GEPA as the fix-proposer for personas and skills,** feeding it the papercut lines and rating paragraphs, not only scores.

---

## 9. What contradicts or would improve the current design

| Current design | Issue | Suggested change |
|---|---|---|
| "Each option's score is its average over all the runs it appeared in" ([Blind testing](../blind-testing.md#slots)) | Plain averages are confounded once the router stops picking uniformly: good options appear together and share credit. | Fit all dimensions jointly in one regression; record pick probabilities. |
| "The grader does not see the variant" as protection against family bias ([Blind testing](../blind-testing.md#keeping-it-honest)) | Models recognise and favour their own family's text even when blind ([arXiv 2404.13076](https://arxiv.org/abs/2404.13076)). Recording the grader's variant does not by itself correct anything. | Add a same-family term to the score model and vary grader family on purpose for some runs. |
| "Papercuts count against a variant in its score" ([Papercuts](../papercuts.md#rules), [Model router](../model-router.md#scores)) | Gameable: a persona or skill variant that discourages filing papercuts scores better, the "objective hacking" DGM showed. It also punishes honest filers. | Keep the filing instruction fixed and outside any variant, or count only papercuts filed by others (a parent's or the user's) against a variant, or weight papercuts only when confirmed by a gate or a fix. |
| "The same task is not run twice" ([Blind testing](../blind-testing.md#the-loop)) | Rules out the most reliable comparison exactly when promoting a fix. | Allow shadow pairs on low-risk tasks funded by expiring quota, for fix promotion only. |
| "If the new variant scores better ... it becomes the role's default" ([Papercuts](../papercuts.md#the-improvement-loop)) | "Scores better" has no threshold, so noise and repeated attempts will promote lucky fixes. | Pre-set posterior threshold, run cap and a limit on candidates per papercut. |
| One default per role | GEPA's Pareto set and P2L show the best variant differs by kind of task. | Allow a role's default to differ by kind of task once the data supports it; keep losing variants archived rather than deleted (DGM), so they can be revisited. |
| "A minimum number of runs before a variant's score affects picks" ([Blind testing](../blind-testing.md#limits-on-experimenting)) | A hard cut-off wastes information and still lets a lucky streak through just above the line. | Replace with a posterior and Thompson sampling, where few runs naturally mean wide uncertainty. |
| [Blocking ratings](../open-questions.md#blocking-ratings) leaning yes | Supported. Missing-not-at-random ratings are the hardest bias to fix afterwards. | Keep "yes, 1 to 5 and one sentence", and weight late outcome signals above the first rating. |
| Scores separate per role, tier and kind of task | Separate buckets start empty and stay sparse for one user's volume. | Partial pooling across buckets in a hierarchical model. |
| Answer length not mentioned in grading | LLM judges prefer longer answers ([arXiv 2306.05685](https://arxiv.org/abs/2306.05685)); length was the main style effect on LMArena. | Include output length as a covariate in the score model. |
| [Model on wake](../open-questions.md#model-on-wake) | Not Diamond routes coding agents partly on KV-cache state, treating a warm cache as a real cost saving. | Give the router the cache state as an input when it decides whether a woken agent keeps its model. |
