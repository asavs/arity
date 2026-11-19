# Model router

[Back to index](README.md)

![Inside the model router](diagrams/router.svg)

## What it does

The model router is the one place that decides which model runs an agent. It is the "Pick variant" step of [Instantiate](instantiate.md), so it runs whenever an agent is spawned or a cold agent is woken. A live agent never goes through it.

Beyond the model, the router can also trial a different harness, skill, MCP server or persona. See [Blind testing](blind-testing.md).

The choice is **blind**: neither the user nor the parent agent is told which model they are talking to. This keeps grades honest, because the grader cannot favour a model by name.

Before weighing anything, the router rules out models the task's clearance does not allow. See [Access](access.md#clearance-limits-the-variant).

The router then weighs two inputs:

- **Quality**, from the leaderboard.
- **Cost and headroom**, from the endpoint pool.

## Leaderboard

Filled by graded reports. It is part of the [database](database.md): scores on model pages, added up from ratings on agent runs. Each grade can combine several signals.

**Outcome (the main signal)**

- Parent grade: a rubric score or, better, a comparison against past reports on similar tasks.
- Acceptance: did the parent use the result, or re-spawn the same task?
- Verifiable checks: tests pass, build is green, output matches its schema.
- User signals: the user edits, overrides or rates a result.
- Rework found later: a project agent discovers days afterwards that a subagent's work broke something. The leaderboard must accept late scores for this.

**Process**

- Steers needed: how often the agent had to be interrupted and redirected.
- Failed tool calls, counted separately from malformed calls.
- Questions or escalations to the parent.
- Report calibration: did "done" actually mean done?

**Efficiency**

- End-to-end time, tokens or cost, number of turns.
- Cache reuse on wake.

**Design rules**

- Keep separate scores per tier, role and task type. One global ranking will mislead.
- Record which model did the grading. Models tend to prefer output from their own family.
- Record why each pick was made: quality, cost or exploration. Otherwise cheap traffic looks like a preference.
- Record the loadout alongside the model, so a good model with a bad loadout is not blamed.

## Endpoint pool

One shared pool of every way to reach a model, used by all agents.

| Kind | Cost | Limit |
|---|---|---|
| API keys | Pay per token | Budget |
| Subscriptions | Flat fee, quota that renews | Quota per period |
| Local endpoints | Free | Hardware capacity |

**Rule: spend quota that is about to renew first.** Subscription quota that resets soon is effectively free, because unused quota is lost. The router treats its cost as falling toward zero as the reset approaches. Local endpoints are free but capacity-limited. API keys are the overflow.

## Choosing

Picking a model is a multi-armed bandit problem: mostly use the best-known option, and spend some traffic on uncertain ones to keep learning. Thompson sampling with cost as a constraint fits this design.
