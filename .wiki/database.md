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

**People and models are the same kind of page.** A person and a model are both something that does tasks, gets rated and has a profile. The database should not treat them as significantly different, and neither should the harness.

## Tasks, also called the board

The tasks in the database are the task board. Writing an entry is how work is offered to agents; see [How work starts](work.md).

A task is a todo with fields. These are the candidate fields and what each one is for.

| Field | Meaning | Used by |
|---|---|---|
| Clearance | Who is allowed to see or do the task, people as well as models | Instantiation, to limit which agents, models and credentials it can use; blind testing, to keep experiments off sensitive work |
| Priority | How important it is | Ordering; high priority work gets the best known variant |
| Blocks and blockers | Which tasks must finish first | Deciding which tasks are ready |
| Requirements | What the task needs to run: Xcode, an NVIDIA RTX card, computer use | Building the agent's loadout, and matching the task to a machine (see [Computers](computers.md)) |
| Gates | What must pass before it counts as done: tests, CI, a QA bot, the user's eyes | Grading. A gate result is a verifiable check, stronger than a rating |
| Tags | Free labels | Search and grouping |
| Due date | When it is needed, if at all | Ordering |

**Rules for the fields**

- **Few are required.** It is cheap for a model to fill them all in, so most entries will be complete anyway.
- **Requirements and gates are typed.** They come from a fixed list, not free text, so they can be matched to machines and checks automatically.
- **Who can create and edit tasks, and set clearance:** the user, the secretary and project agents.

A gate of "the user's eyes" is how a task is held for a person. A task that needs a one-time code or a physical device is blocked on the user in the same way.

### Task states

1. **Open.** Created by the user, the secretary or a project agent.
2. **Ready.** No open blockers. A ready task wakes an agent that fits it.
3. **Assigned.** A person took it, or an agent was instantiated for it.
4. **In gates.** The work is reported; its gates are being checked.
5. **Done.** All gates passed.

## What moves into the database

Several stores designed earlier are parts of this one graph.

| Earlier name | What it is in the database |
|---|---|
| Leaderboard | Scores on model pages, added up from ratings on agent runs |
| Trial record | The "ran on" link between an agent run and a model, sealed until the run is graded |
| Profiles | Person and model pages |
| Papercut log | Papercuts, which are task entries attached to whatever they are about |

The baseline diagram still says "Leaderboard", because that is the part of the database it reads.

**The registry probably belongs here too.** Roles, skills and tools sit naturally beside people, models and completed tasks. Not decided; see [Open questions](open-questions.md).

The **endpoint pool**, the **computer pool** and the **vault** stay separate for now.

## How instantiation uses it

A task entry is what triggers [instantiation](instantiate.md), which reads the database twice:

- **Pick variant** reads scores.
- **Load context** reads the task page, its project, and the relevant profiles, so the agent starts knowing what it is doing and for whom.

## Blindness

`people/asas` can show that a task was done by an agent, and which model. That link is the trial record. It stays sealed while a run is being graded and opens when the run is done. There is also a reveal option for anyone who wants to look sooner.

## What stores it

Not decided. Four options, from simplest to heaviest.

| Option | Good | Weak |
|---|---|---|
| Markdown files in a git repository, one file per page, links between them | It is literally a wiki. Agents read and write files natively. Every change has history | Queries such as "all ready tasks I am cleared for" need an index built on the side. Many agents writing at once will conflict |
| SQLite, with a table of pages and a table of typed links | One file, real queries, simple to run | One writer at a time; sharing it across several computers needs a small service in front |
| Postgres, same two tables | Many writers, reachable from every computer | A server to run and keep up |
| A dedicated graph database | Built for following links many steps deep | Heaviest to run; most of what is needed here is one or two steps deep |

A reasonable starting point: the two-table design (pages and typed links) in SQLite or Postgres, with each page also viewable at its wiki path. That keeps the graph shape without committing to a graph database, and moving from SQLite to Postgres later does not change the design.

## Not settled

See [Open questions](open-questions.md).
