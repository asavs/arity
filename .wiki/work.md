# How work starts

[Back to index](README.md)

Work reaches an agent by one of two routes. The secretary chooses between them.

![How work starts: the board or a patch-in](diagrams/work.svg)

## Two routes

| Route | What the secretary does | When it suits |
|---|---|---|
| The board | Writes a task to the task board. An agent that fits picks it up | Work that can be described and left to run |
| A patch-in | Connects the user directly to an agent, with a brief already filled in from the conversation with the secretary | Work the user wants to steer |

After either route the user can talk to the agent directly. That is the user's direct line in the [baseline](README.md).

## The board

The board is the set of tasks in the [database](database.md). The user, the secretary, project agents and subagents above the depth floor can all write to it.

**A task belongs to whoever asked for it.** Any thread—chat, secretary, project agent or worker run—that delegates a task is its parent: the result returns there, wakes the parent if idle or waiting, and is graded by the parent's post-use hook. A task filed outside any thread belongs directly to the user.

**An entry on the board is deferred work.** When a task has no open blockers it becomes ready, and an agent that fits it is woken, or created if none exists. How fit is judged is [undecided](open-questions.md#judging-a-good-fit). Claiming the task starts (or wakes) the agent's thread with the brief as its opening entry in the [journal](loop.md), and finishing sends a [report](agent-tiers.md#report) back at the timing the asker chose (**after turn** or **steer**).

A task entry carries what [instantiation](instantiate.md) needs: what kind of agent it wants, what it requires, and its clearance.

**Delegation is recursive.** An agent's run files subtasks with the same call a chat uses, one level down. At the depth floor delegation is not offered; see [The loop](loop.md#recursion).

## A patch-in

The secretary introduces the user to an agent and writes the brief, based on the conversation they were just having. The user does not have to repeat anything to the new agent.

## Fresh or fork

When an agent is created for a piece of work, there is a second choice: how much it starts out knowing.

![Fresh agent or fork](diagrams/fork.svg)

| | Starts with | In the journal | Relationship | Good for |
|---|---|---|---|---|
| Fresh | Only the brief | A new root entry | A child | An unbiased second look; narrow tasks; anything where the parent's assumptions should not carry over |
| Fork | Everything the parent knows | A new thread head pointing at an entry in the parent's chain | A sibling | Work that depends on the conversation so far; exploring one option without losing the thread |

The choice is about how naive the agent should be. It is not about cost, because a fork shares the parent's entries in the [journal](loop.md) and reads the parent's context from cache.
