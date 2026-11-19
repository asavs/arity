# Computers

[Back to index](README.md)

Some tasks need a machine to work on: one with Xcode, one with a GPU, or one with a screen an agent can operate. The computer pool is where those machines come from.

Parts of this page began as a proposal. The points under "Decided" below have since been confirmed.

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

See [Open questions](open-questions.md).

## Decided

- **Your own machines are in the pool.**
- **A subagent generally works on its project's computer.** It can differ by task, but sharing is the default.
- **The ideal split is two machines:** the agent runs on one cloud computer and carries out its commands on another, so that a mistake or a bad instruction cannot reach the machine the agent itself lives on.
- **If nothing in the pool matches, a cloud machine is obtained.** The vault holds a key for procuring machines and GPUs, so in practice a match can always be made.
- **The user sets the spending limit.**
- **Workspaces and worktrees are cleaned up** when no longer needed, not kept forever.
