# Instantiate

[Back to index](README.md)

Instantiation is what happens when a message arrives for an agent that is cold or not yet created. The message names a role and a task. It is the "no" branch of the "Live?" decision in the [baseline](README.md). A live agent never goes through it.

Spawn (a new agent) and wake (a cold agent) are the same step. A new agent can start fresh or as a fork of its parent; see [How work starts](work.md#fresh-or-fork).

What triggers it is usually a task entry on the board, which says what kind of agent is wanted, what it requires and its clearance.

![The instantiate step](diagrams/instantiate.svg)

## Steps

| Step | What it does | Reads from | Details |
|---|---|---|---|
| 1. Resolve role | Looks up the role named in the request and gets its system prompt and loadout | Registry | [Capabilities](capabilities.md#roles) |
| 2. Pick variant | The model router chooses a model, blind. It can also trial a different harness, skill, MCP server or persona from the role's defaults | Endpoint pool, leaderboard | [Model router](model-router.md), [Blind testing](blind-testing.md) |
| 3. Load context | Reads the task, its project and the relevant profiles, so the agent starts knowing what it is doing and for whom | Database | [Database](database.md) |
| 4. Get computer | If the task lists requirements, matches them to a machine and gives the agent a workspace. Skipped otherwise | Computer pool | [Computers](computers.md) |
| 5. Grant access | Issues grants for the credentials this task needs, scoped by role and clearance | Vault | [Access](access.md) |
| 6. Assemble | Wraps the picked model with the prompt and loadout, as adjusted by the variant | | [Capabilities](capabilities.md) |

The model is settled in step 2. Step 6 does not choose it; it only attaches the prompt and loadout to it.

Each store sits beside the step that reads it. The database is also read by "Pick variant", for scores. The database is the link back to the baseline: graded reports write scores to it (the baseline calls that part the leaderboard), and "Pick variant" reads them.

## Why this is its own diagram

The baseline shows who talks to whom. Instantiation is a pipeline with its own inputs, and most of the remaining roadmap lands inside it. Keeping it separate means new features add steps here without touching the baseline.

Every roadmap item that was expected to land here now has. The steps are stacked vertically so that another can be added as one more row.

## Not settled

How system prompts, skills, tools and MCP servers differ between the secretary, project agents and subagents is unresolved. It is deliberately left for a second pass after more of the roadmap is designed. See [Open questions](open-questions.md).
