# Arity wiki

Arity is a multi-agent system. One person works with a small hierarchy of agents. A central router decides which model runs each agent without telling anyone which it picked, and finished work is graded so that later picks improve.

![Baseline architecture](diagrams/baseline.svg)

## How to read the baseline

- **The column** is the chain of command: user, secretary, project agent, subagent.
- **Between neighbours**, messages go both ways (grey) and reports go up only (amber).
- **The rails** are the user's direct line: the left rail messages any agent, the right rail carries that agent's report straight back.
- **Every message** (left box) is delivered according to the recipient's state. A live agent gets it by steer or inbox. A cold or not-yet-created agent is instantiated first.
- **Every report** (right box) is graded by whoever receives it. Grades become scores on the leaderboard, which instantiation reads when it picks the next model.

## Pages

| Page | Covers |
|---|---|
| [Agents](agent-tiers.md) | The three tiers, messages, reports, delivery, agent states |
| [How work starts](work.md) | The task board, patch-ins, fresh agents and forks |
| [Instantiate](instantiate.md) | The six steps that build an agent on spawn or wake |
| [Model router](model-router.md) | Blind model selection, scores, the endpoint pool |
| [Capabilities](capabilities.md) | Skills, MCP servers, tools, roles, loadouts |
| [Database](database.md) | The graph of people, projects, tasks, agent runs and models |
| [Blind testing](blind-testing.md) | Variants, trial records, grading |
| [Papercuts](papercuts.md) | Quick complaints and help requests, and how they become tested fixes |
| [Computers](computers.md) | The computer pool and agent workspaces |
| [Access](access.md) | Credentials: the vault, grants, the access broker |
| [Open questions](open-questions.md) | What is undecided |
| [What is next](roadmap.md) | Work remaining on the design |

To edit the wiki or its diagrams, see [CONTRIBUTING.md](CONTRIBUTING.md).
