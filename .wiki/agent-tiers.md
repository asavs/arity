# Agents

[Back to index](README.md)

## Tiers

| Tier | Lifetime | Job |
|---|---|---|
| Secretary | Persistent | The user's main point of contact. Writes tasks to the board, or patches the user in to an agent |
| Project agent | Long-lived, per project | Oversees one project over a long time and creates subagents for its tasks. Each is named after its project |
| Subagent | Short-lived, per task | Does one task and reports back |

Every tier—and a plain chat—is a thread driven by [the same loop](loop.md). The tier says only where an agent sits and how long it lives. What it is good at is its [role](capabilities.md#roles), which is separate.

## One mechanism for everyone

The user is one more participant, not a special case. The user's messages, an agent's messages and returning reports all go through the same delivery call and the same pages in the [database](database.md), over one durable [journal](loop.md).

## Who can talk to whom

- The user can talk to the secretary, any project agent, or any subagent directly.
- Any agent can message any other agent. The baseline diagram draws only neighbours and the user's direct line; other pairs work the same way.

How work first reaches an agent is covered in [How work starts](work.md).

## Two calls

Everything between the user and agents, and between agents, is one of two calls.

### Message

A message can go in either direction, including from a child to its parent: a child tells its parent when the parent is about to waste work, without ending its own task.

**The sender picks a timing, matching [the loop](loop.md); the recipient's state decides what happens.**

| Timing | Live, running a turn | Live, idle | Cold or not created |
|---|---|---|---|
| **Steer** | Joins as soon as it can: a reply being written is cut off, its text kept, and the turn takes the message in and goes on. Tool calls are never cut; it joins once they have their results | Woken (starts a turn) | [Instantiated](instantiate.md), then starts a turn |
| **After turn** | Joins when the turn would end; the turn answers it instead of ending | Woken (starts a turn) | [Instantiated](instantiate.md), then starts a turn |
| **Inbox** | Joins between replies as context, asking for no answer: it never keeps a turn going | Left for its next turn; nothing wakes | Left for its next turn; not instantiated |

Spawn, wake, steer, turn continuation and quiet inbox context are outcomes of one call, not separate features. A live agent—whether running a turn or idle between turns—keeps its model and never goes through the router.

Stop is not a timing: it ends the turn, and the reply so far is kept.

Steer when the recipient is about to waste work, after turn for anything it should answer, inbox for anything it only needs to know. Work to be done is a task on the [board](work.md), not a message.

### Report

A report goes one way: up, to whoever asked for the work. That is the parent agent, or the user when the task is the user's.

Reports are graded. See [Blind testing](blind-testing.md#grading).

A report is delivered like a message, with the timing the recipient asked for when it handed out the work: **after turn** unless it said **steer**. The parent may keep working meanwhile or stop to wait. A waiting agent is woken at once by a steering report, and otherwise when all the work it handed out has reported. A note of a result for a conversation that is not waiting on it, such as the chat a task was filed in, goes to its **inbox**.

## Agent lifecycle

1. **Not created** to **Live**: [instantiated](instantiate.md), as a fresh agent or a fork.
2. **Live** (running ⇄ idle): runs turns, pauses for sub-tasks, and waits between turns while warm. Receives messages at any timing with no instantiation, so no model router.
3. **Live** to **Cold**: idle long enough that its cache and workspace expire.
4. **Cold** to **Live**: [instantiated](instantiate.md) again when a waking message or ready task arrives. Any tier can be woken, including subagents. Whether it keeps its previous model is [undecided](open-questions.md#model-on-wake).
5. **Live** to **Finished**: it hands in its report with an explicit finish, saying whether the task is done, blocked (it cannot go on, and says what it needs) or needs review. Stopping without it hands nothing in.

## What an agent is made of

A hidden model wrapped with a role's system prompt and a loadout of skills, MCP servers and tools. See [Instantiate](instantiate.md) for how it is built and [Capabilities](capabilities.md) for what the parts are.

How the prompt and loadout differ between the three tiers is [undecided](open-questions.md#tier-differences).
