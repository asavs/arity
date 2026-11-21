# Arity roadmap

The order in which the design in `.wiki/` gets built, and why that order. Like `v0.0.1.md`, this is a plan and will change. The wiki is the lasting description of what Arity is.

## How to read this

**Each stage answers one question.** A stage is finished when real use has answered its question, not when its features exist. If the answer is "no", the next stage changes.

**The loop is the product.** Blind pick, rate, score, next pick. Everything after v0.0.1 either makes the loop smarter, gives it more to work with, or gives it more to vary. Nothing is allowed to replace it.

**The test for scope** at every stage: does the stage's question go unanswered without this? If not, it waits.

**Adopt the plumbing, build what is ours.** The research in `.wiki/research/` found that task boards, gateways, sandboxes and vaults already exist. What nothing else has is the blind router, grading, waking by fit, and the patch-in. Original work goes there. The runtime is the exception: Arity keeps its own small journal and loop by choice.

## Stages

| Stage | Name | The question it answers |
|---|---|---|
| v0.0.1 | The loop closes | Is rating every result bearable, and do the ratings carry any signal? |
| v0.0.2 | The loop learns | Do picks get better once scores steer them? |
| v0.1 | Work that lasts | Can I leave work on the board and come back to find it done? |
| v0.2 | Supply | Does Arity spend what I already pay for before paying more? |
| v0.3 | More to vary | Can the system improve one of its own tools from a papercut, without me? |
| v0.4 | Other machines | Does a task that needs Xcode or a GPU land on the right machine by itself? |
| v0.5 | Access | Can an agent act on my real accounts without a model ever holding a secret? |
| v0.6 | Other harnesses | Is a model better inside its own vendor's harness than inside Arity's own loop? |

### v0.0.1: the loop closes

Planned in [v0.0.1.md](v0.0.1.md). A secretary, subagents, a blind random pick of the model, a required rating, a scores table, and reveal.

**Learns:** whether the secretary can be made to rate reliably, whether its ratings differ between models at all, and whether local models can call tools well enough to be subagents.

**If the answer is no:** if ratings are noise, grading has to lean on checks that need no rater (the gates in v0.0.2) before anything else is built on scores.

### v0.0.2: the loop learns

- **Scores steer picks.** Replace the random pick with one that favours higher-scoring models while still trying uncertain ones.
- **Scores per role.** A model good at one kind of work is not assumed good at another.
- **Checks that need no rater.** A role can name a command, such as a test run, whose result is recorded beside the rating. This is the first gate.
- **Cost and time enter the score,** so "right intelligence for the task" can be weighed against "spending wisely".

**Depends on:** enough rated runs from v0.0.1 to have scores worth using.

**Decisions it forces:** whether rating must block ([open question](../.wiki/open-questions.md#blocking-ratings)). By now there is experience of living with the gate.

### v0.1: work that lasts

The first stage where Arity is more than a chat.

- **The task board.** A tasks table with a first few of the typed fields: role, requirements, blockers, gates, priority, clearance.
- **Project agents,** long-lived and named after their project, each owning its project's tasks.
- **Both routes in.** The secretary writes a task to the board, or patches the user in to an agent with a brief already written.
- **A ready task wakes an agent.** To begin with, by the role named on the task. Judging fit between several candidates comes later.
- **Done means its gates passed.**
- **Memory for agents that live for weeks.** The secretary and project agents need old history folded into summaries, not dropped. Anima's context-manager is the model, and possibly the library.
- **Pages to browse.** Read-only pages generated from the tables: a person, their projects, the tasks, who did each one.

**Depends on:** the dispatcher from v0.0.1, which already wakes a worker when a task is ready and wakes a conversation when a report arrives.

**Decisions it forces:** [judging a good fit](../.wiki/open-questions.md#judging-a-good-fit), [model on wake](../.wiki/open-questions.md#model-on-wake), and [tier differences](../.wiki/open-questions.md#tier-differences) (there are now three tiers to differ).

### v0.2: supply

- **The endpoint pool.** Several ways to reach each model, each with its own limits and reset times.
- **Spend what is about to renew first,** judged against the tightest limit that will still apply after the reset.
- **Local models across the user's computers,** available only while a GPU is free.
- **Clearance, first use.** A task marked local-only is never sent to a cloud model. The router applies this before anything else.
- **A gateway,** adopted, to hold keys and count spend.

**Depends on:** v0.0.2's pick policy, which gains a cost weight that moves with supply.

**Decisions it forces:** [database storage](../.wiki/open-questions.md#database-storage). Once several computers read and write, one SQLite file is no longer enough.

### v0.3: more to vary

- **The registry.** Roles that specialise, skills, MCP servers, and slots with interchangeable options.
- **A variant is more than a model.** Each trial records everything that differed from the role's defaults.
- **The papercut loop.** A papercut becomes a task, someone proposes a fix, the fix enters as a new variant, and it becomes the default only if it wins a blind test with a threshold set in advance.

**Depends on:** the board from v0.1, since papercuts become tasks, and enough run volume for trials on small changes to mean anything.

**Decisions it forces:** [approval for fixes](../.wiki/open-questions.md#approval-for-fixes), [registry in the database](../.wiki/open-questions.md#registry-in-the-database), [what the router returns](../.wiki/open-questions.md#what-the-router-returns).

### v0.4: other machines

- **The computer pool,** starting with the user's own machines over their private network.
- **Requirements matched to machines.**
- **Workspaces that follow the agent's life:** running, asleep, resumed, cleaned up.
- **The agent never runs its commands where it lives.** The harness stays on one machine and carries out work on another.

**Depends on:** the requirements field from v0.1.

**Decisions it forces:** [requirement and gate lists](../.wiki/open-questions.md#requirement-and-gate-lists).

### v0.5: access

Nothing before this stage gives an agent a credential.

- **A vault,** wrapped, not built.
- **The access broker:** grants that are scoped, temporary, tied to a destination and logged.
- **Approval that creates the permission** for one action, instead of pausing in front of a permission that already exists.
- **Cloud machines on demand,** which need a key and so belong here.
- **Computer use on signed-in apps,** in a virtual machine first, with screen rules.

**Depends on:** workspaces from v0.4, because the broker has to sit outside the place the agent works.

**Decisions it forces:** [vault](../.wiki/open-questions.md#vault), [approval for actions](../.wiki/open-questions.md#approval-for-actions), [screen protection](../.wiki/open-questions.md#screen-protection), [model reluctance](../.wiki/open-questions.md#model-reluctance).

### v0.6: other harnesses

- **Claude Code, Codex and agy as subagents,** run as their own programs on a workspace and driven through the harness seam.
- **The router picks a valid pair** of harness and model.
- **Subscriptions become usable,** since each is tied to its vendor's own client.
- **Blind trials across harnesses,** accepting that a harness is harder to hide than a model.

**Depends on:** workspaces (a harness needs a machine to run on) and access (it needs to be signed in).

## Why this order

- **The cheapest unknowns come first.** Whether ratings carry signal, and whether local models can call tools, could each sink the idea. Both are answered in v0.0.1 for almost no cost.
- **Learning before breadth.** v0.0.2 makes the loop use what it collects before anything is added for it to collect.
- **The board before supply and variety.** Papercuts, gates, clearance and requirements are all fields on a task. They have nowhere to live until tasks exist.
- **Machines before access before harnesses.** A broker must sit outside the agent's workspace, so workspaces come first. A vendor harness needs both a machine and a sign-in, so it comes last.
- **Supply and variety are independent of each other.** v0.2 and v0.3 could swap. Supply is first because it saves money straight away.

## Threads that run through every stage

- **One object is the core's surface.** The CLI and the app call only `Arity` in `core/src/lib.rs`. It is where another front end plugs in, and model access behind it is where another harness does.
- **Record more than is used yet.** Every trial stores the harness, and from v0.3 everything that differed, even while only the model varies. Old data then stays usable.
- **A scripted-model test for every stage,** so each can be checked without spending tokens.
- **Daily use is the real test.** Each stage should be used for actual work before the next begins.
- **The wiki changes when an intention changes,** in the same commit as the code that changed it.

## Not on the roadmap

Set aside on purpose, as in the wiki:

- Sharing Arity with other people.
- Agents rating users, and exit reports.
- A richer statistical model for scores. Worth it only when plain averages visibly mislead.
- The slot-in pieces not yet placed: Chronicle as a store for run history is the likeliest to earn a stage.
