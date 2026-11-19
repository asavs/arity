# How work starts

[Back to index](README.md)

Work reaches an agent by one of two routes. The secretary chooses between them.

![How work starts: the board or a patch-in](diagrams/work.svg)

## Two routes

| Route | What the secretary does | When it suits |
|---|---|---|
| The board | Writes a task to the task board. An agent that fits picks it up | Work that can be described and left to run |
| A patch-in | Connects you directly to an agent, with a brief already filled in from what you were telling the secretary | Work you want to steer yourself |

After either route you can talk to the agent directly. That is the user's direct line in the [baseline](README.md).

## The board

The board is the set of tasks in the [database](database.md). The user, the secretary and project agents can all write to it.

**An entry on the board is what wakes an agent.** When a task becomes ready, an agent that is a good fit for it is woken, or created if none exists. Agents are not polled or messaged to start work; they are pulled in by an available entry.

This also answers an earlier question: a message left for a cold agent does wake it, because it arrives as work that agent is a fit for.

A task entry carries what [instantiation](instantiate.md) needs: what kind of agent it wants, what it requires, and its clearance.

## A patch-in

The secretary introduces you to an agent and writes the brief for you, based on the conversation you were just having. You do not have to repeat yourself to the new agent.

## Fresh or fork

When an agent is created for a piece of work, there is a second choice: how much it starts out knowing.

![Fresh agent or fork](diagrams/fork.svg)

| | Starts with | Relationship | Good for |
|---|---|---|---|
| Fresh | Only the brief | A child | An unbiased second look; narrow tasks; anything where the parent's assumptions should not carry over |
| Fork | Everything the parent knows | A sibling | Work that depends on the conversation so far; exploring one option without losing the thread |

The choice is about how naive you want the agent to be. It is not about cost, because a fork reads the parent's context from cache.

## Not settled

- How "a good fit" is judged when more than one agent could take a task.
- Whether the secretary's choice of route follows rules or is left to its judgement.

See [Open questions](open-questions.md).
