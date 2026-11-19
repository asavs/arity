# Agents

[Back to index](README.md)

## Tiers

| Tier | Lifetime | Job |
|---|---|---|
| Secretary | Persistent | The user's main point of contact. Hands work to project agents. |
| Project agent (name pending, possibly "project manager") | Long-lived, per project | Oversees one project over a long time. Spawns subagents for tasks. |
| Subagent | Short-lived, per task | Does one task and reports back. |

The tier says where an agent sits and how long it lives. What it is good at is its [role](capabilities.md#roles), which is separate.

## Who can talk to whom

- The user can talk to the secretary, any project agent, or any subagent directly.
- Any agent can message any other agent. The baseline diagram draws only neighbours and the user's direct line; other pairs work the same way.

## Two calls

Everything between the user and agents, and between agents, is one of two calls.

### Message

A message can go in either direction. How it is delivered depends on the recipient's state:

| Recipient state | Delivery | Instantiated first |
|---|---|---|
| Live (running now) | **Steer**: interrupts immediately. **Inbox**: read at the end of the current turn. The sender chooses. | No. A live agent keeps its model. |
| Cold (cache expired) | The agent is woken, then receives the message. | Yes, see [Instantiate](instantiate.md) |
| Not created | The agent is spawned, then receives the message. | Yes |

So spawn, wake, steer and inbox are not four separate features. They are the four outcomes of delivering a message.

Rough guidance for the sender: steer when the recipient is about to waste work; inbox for everything else.

### Report

A report goes one way: up, to whoever asked for the work. That is the parent agent, or the user when the user messaged the agent directly.

Every report is graded by its recipient, and the grade goes to the [leaderboard](model-router.md#leaderboard).

## Agent lifecycle

1. **Not created** to **Live**: [instantiated](instantiate.md) (spawn).
2. **Live** to **Live**: receives steer or inbox messages. No instantiation, so no model router.
3. **Live** to **Cold**: its cache expires.
4. **Cold** to **Live**: [instantiated](instantiate.md) again (wake).
5. **Live** to **Finished**: the task is done; it writes its report.

## What an agent is made of

A hidden model wrapped with a role's system prompt and a loadout of skills, MCP servers and tools. See [Instantiate](instantiate.md) for how it is built and [Capabilities](capabilities.md) for what the parts are.

How the prompt and loadout differ between the three tiers is not settled yet.
