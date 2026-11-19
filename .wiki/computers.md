# Computers

[Back to index](README.md)

Some tasks need a machine to work on: one with Xcode, one with a GPU, or one with a screen an agent can operate. The computer pool is where those machines come from.

This page is thinner than the others. The roadmap entry for it was two words, so most of what follows is a proposal to react to, not a record of decisions.

![Matching a task to a computer](diagrams/computers.svg)

## The computer pool

A third "what is available" store, alongside the [registry](capabilities.md) and the [endpoint pool](model-router.md#endpoint-pool).

| Kind | Cost | Limit |
|---|---|---|
| Cloud machines | Paid by the hour while running | Budget |
| Your own machines | Free | Only what you have, and only when it is on |

Each machine lists what it offers, in the same vocabulary a task uses for its requirements: Xcode, an NVIDIA RTX card, computer use.

## How a task gets a computer

1. A task's **requirements** field says what it needs. See [Database](database.md#tasks).
2. At [instantiation](instantiate.md), the "Get computer" step matches those requirements against the pool.
3. The agent is given a **workspace** on the matched machine.

A task with no requirements gets no computer. The step is skipped.

## Workspaces follow the agent's life

| Agent state | Workspace |
|---|---|
| Live | Running |
| Cold | Asleep: stopped, with its disk kept |
| Woken | Resumed where it left off |
| Finished | Released |

This mirrors the [agent lifecycle](agent-tiers.md#agent-lifecycle). It matters most for project agents, which live for weeks and should not pay for a running machine while idle.

## Design notes

- **The same optimisation as the endpoint pool applies.** Prefer what is free and available (your own machines), then the cheapest cloud machine that meets the requirements.
- **Clearance applies to machines too.** A task's clearance should limit which machines it can land on, particularly your own.
- **The machine is part of the variant.** Where an agent ran can affect how well it did, so it belongs on the trial record.

## Not settled

See [Open questions](open-questions.md#computers).
