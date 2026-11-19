# Roadmap

[Back to index](README.md)

Features in the order they are being designed. The baseline diagram shows who talks to whom. Most later features land in the [instantiate](instantiate.md) step instead of the baseline.

| # | Feature | Status | Where |
|---|---|---|---|
| 1 | User talks to all three tiers; secretary, project agents, subagents | In baseline | [Agents](agent-tiers.md) |
| 2 | APIs, subscriptions and local endpoints, shared and optimised (spend tokens about to renew) | In baseline | [Model router](model-router.md#endpoint-pool) |
| 3 | Skills, MCPs, tools | Designed, second pass planned | [Capabilities](capabilities.md), [Instantiate](instantiate.md) |
| 4 | Roles: system prompts and skill or MCP clusters (software engineer, python developer, iOS developer, writer, namer, designer) | Designed, second pass planned | [Capabilities](capabilities.md#roles), [Instantiate](instantiate.md) |
| 5 | Blind A/B testing of models, harnesses, skills, MCPs and personas. A model that creates a subagent may be hooked to grade its results | Designed | [Blind testing](blind-testing.md) |
| 6 | Papercuts: models and users can quickly complain or ask for help, feeding self-improvement | Designed | [Papercuts](papercuts.md) |
| 7 | Database: todos, plus profiles for people and models, as a graph-structured wiki | Designed | [Database](database.md) |
| 8 | Cloud computers | Proposed, needs your input | [Computers](computers.md) |
| 9 | Passwords and auth for models doing computer use | Proposed, needs your input | [Access](access.md) |
| 10 | Slot-in third-party pieces: cua driver, anima, optmem, pi-durable, computer-use | Next | |

## Order

The database (item 7) underlies blind testing and papercuts (items 5 and 6), which were designed first. Those pages now point to it. In a second pass, read the database page before those two.

## Notes captured for later items

None outstanding. The database notes are now in [Database](database.md).
