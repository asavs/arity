# Agents

[Back to index](README.md)

## Tiers

| Tier | Lifetime | Job |
|---|---|---|
| Secretary | Persistent | The user's main point of contact. Writes tasks to the board, or patches the user in to an agent |
| Project agent | Long-lived, per project | Oversees one project over a long time and creates subagents for its tasks. Each is named after its project |
| Subagent | Short-lived, per task | Does one task and reports back |

The tier says where an agent sits and how long it lives. What it is good at is its [role](capabilities.md#roles), which is separate.

## One mechanism for everyone

The user is one more participant, not a special case. The same messages, the same delivery rules and the same pages in the database apply to people and to models. The aim is one small recursive mechanism, built on a single durable journal that Arity owns.

## Who can talk to whom

- The user can talk to the secretary, any project agent, or any subagent directly.
- Any agent can message any other agent. The baseline diagram draws only neighbours and the user's direct line; other pairs work the same way.

How work first reaches an agent is covered in [How work starts](work.md).

## Two calls

Everything between the user and agents, and between agents, is one of two calls.

### Message

A message can go in either direction, including from a child to its parent. How it is delivered depends on the recipient's state:

| Recipient state | Delivery | Instantiated first |
|---|---|---|
| Live (running now) | **Steer**: interrupts immediately. **Inbox**: read at the end of the current turn. The sender chooses | No. A live agent keeps its model |
| Cold (cache expired) | The agent is woken, then receives the message | Yes, see [Instantiate](instantiate.md) |
| Not created | The agent is created, then receives the message | Yes |

Spawn, wake, steer and inbox are the four outcomes of delivering a message, not four separate features.

The user's messages follow the same rule: the user also chooses steer or inbox.

Steer when the recipient is about to waste work. Use the inbox for everything else.

### Report

A report goes one way: up, to whoever asked for the work. That is the parent agent, or the user when the user messaged the agent directly.

Reports are graded. See [Blind testing](blind-testing.md#grading).

A report usually arrives after its recipient has finished its turn. It is delivered like any message: a cold recipient is woken to read it.

## Agent lifecycle

1. **Not created** to **Live**: [instantiated](instantiate.md), as a fresh agent or a fork.
2. **Live** to **Live**: receives steer or inbox messages. No instantiation, so no model router.
3. **Live** to **Cold**: its cache expires.
4. **Cold** to **Live**: [instantiated](instantiate.md) again. Any tier can be woken, including subagents. Whether it keeps its previous model is [undecided](open-questions.md#model-on-wake).
5. **Live** to **Finished**: the task is done and it writes its report.

## What an agent is made of

A hidden model wrapped with a role's system prompt and a loadout of skills, MCP servers and tools. See [Instantiate](instantiate.md) for how it is built and [Capabilities](capabilities.md) for what the parts are.

How the prompt and loadout differ between the three tiers is [undecided](open-questions.md#tier-differences).
