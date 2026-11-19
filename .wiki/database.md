# Database

[Back to index](README.md)

The database is the system's record of what needs doing, who did what, and how it went. It is a graph: every person, project, task, agent run and model is a page, and pages link to each other. It reads like a wiki.

![The database as a graph](diagrams/database.svg)

## Pages and links

| Page | Example path | Links to |
|---|---|---|
| Person | `people/asas` | The projects they work on, the tasks they did, ratings about them |
| Project | `projects/arity` | Its tasks, its people, its project agent |
| Task | `projects/arity/tasks/...` | The tasks it blocks and is blocked by, who did it |
| Agent run | under its task | Its ratings, its papercuts, the model it ran on |
| Model | `models/...` | Every run it did, its scores |

**Progressive disclosure.** A page shows a summary and links, and detail is one step further in. `people/asas` lists projects; a project lists its tasks; a task shows who did it, whether that was the person or an agent; an agent run shows what happened.

**People and models are the same kind of page.** Both do tasks, get rated and have a profile. Neither the database nor the harness treats them as significantly different.

## What it holds

- **The task board.** Tasks are how work is offered to agents; see [How work starts](work.md).
- **Scores.** Ratings on agent runs, averaged onto model pages. The baseline diagram calls this the leaderboard.
- **Trial records.** The "ran on" link between an agent run and a model.
- **Profiles.** Person and model pages.
- **Papercuts.** Task entries attached to whatever they are about; see [Papercuts](papercuts.md).

The endpoint pool, the computer pool and the vault are separate stores. The registry is separate too, though it [may move in](open-questions.md#registry-in-the-database).

## Tasks

A task is a todo with fields.

| Field | Meaning | Used by |
|---|---|---|
| Clearance | Who is allowed to see or do the task, people as well as models | Instantiation, to limit which agents, models and credentials it can use; blind testing, to keep experiments off sensitive work |
| Priority | How important it is | Ordering; high priority work gets the best known variant |
| Blocks and blockers | Which tasks must finish first | Deciding which tasks are ready |
| Requirements | What the task needs to run: Xcode, an NVIDIA RTX card, computer use | Building the agent's loadout, and matching the task to a machine (see [Computers](computers.md)) |
| Gates | What must pass before it counts as done: tests, CI, a QA bot, the user's eyes | Grading. A gate result is a verifiable check and counts for more than a rating |
| Tags | Free labels | Search and grouping |
| Due date | When it is needed, if at all | Ordering |

- **Few fields are required.** It is cheap for a model to fill them all in, so most entries are complete anyway.
- **Requirements and gates are typed.** They come from a fixed list, so they can be matched to machines and checks automatically. The lists are [still to be written](open-questions.md#requirement-and-gate-lists).
- **The user, the secretary and project agents** create and edit tasks and set clearance.

A gate of "the user's eyes" holds a task for a person. A task that needs a one-time code or a physical device is blocked on the user the same way.

### Task states

1. **Open.** Created.
2. **Ready.** No open blockers. A ready task wakes an agent that fits it.
3. **Assigned.** A person took it, or an agent was instantiated for it.
4. **In gates.** The work is reported and its gates are being checked.
5. **Done.** All gates passed.

## Blindness

`people/asas` can show that a task was done by an agent, and which model. That link is the trial record. It is sealed while a run is being graded and opens when the run is done. A reveal option lets anyone look sooner.

## Storage

The design is two tables: pages, and typed links between pages. Each page is also viewable at its wiki path. That keeps the graph shape without needing a graph database.

What holds those tables is [undecided](open-questions.md#database-storage). The options, from simplest to heaviest:

| Option | Good | Weak |
|---|---|---|
| Markdown files in a git repository, one file per page | It is literally a wiki. Agents read and write files natively. Every change has history | Queries such as "all ready tasks I am cleared for" need an index built on the side. Many agents writing at once will conflict |
| SQLite | One file, real queries, simple to run | One writer at a time; sharing it across several computers needs a small service in front |
| Postgres | Many writers, reachable from every computer | A server to run and keep up |
| A dedicated graph database | Built for following links many steps deep | Heaviest to run; most of what is needed here is one or two steps deep |

Starting with SQLite and moving to Postgres later does not change the design.
