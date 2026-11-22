# Papercuts

[Back to index](README.md)

A papercut is a quick note that something got in the way. Any agent or the user can file one at any moment, in a single step, without stopping work. Papercuts are how the system finds out what to improve about itself.

![Papercuts: the improvement loop](diagrams/papercuts.svg)

## Two kinds

| Kind | Example | What happens |
|---|---|---|
| Complaint | "This tool's error message didn't say what went wrong." "The skill told me to use a command that doesn't exist." "The brief didn't say which repo." | Logged. Nobody has to respond |
| Request for help | "I don't have access to the staging server." "I'm stuck and don't know who to ask." | Logged, and also sent as a [message](agent-tiers.md#message) to the parent or the user, at whatever timing the filer picks, like any other |

Asking for help is a message that also leaves a record.

## What a papercut records

The filer writes one line. Everything else is attached automatically: who filed it and their tier and role, the task they were on, the skill, tool or MCP server involved, and the trial record for the run, which ties the papercut to a variant without the filer knowing which.

## Papercuts and ratings

| | Rating | Papercut |
|---|---|---|
| About | The other party in an exchange | The system: tools, skills, prompts, access, the environment |
| Form | A number from 1 to 5 and a paragraph | A line of text |
| When | When asked, after an exchange | Whenever something goes wrong |

## The improvement loop

1. **File.** The papercut becomes an entry on the [task board](work.md), attached to whatever it is about.
2. **Pick up.** Whoever looks after that thing picks it up, the way any task is picked up.
3. **Propose a fix.** A change to a skill, a tool, an MCP server's configuration, or a role's system prompt.
4. **Enter the registry as a new variant.** The fix sits beside the current version; it does not replace it.
5. **Blind test.** The router trials the new variant against the old one, as in [Blind testing](blind-testing.md).
6. **Keep the winner.** If the new variant scores better and draws fewer papercuts, it becomes the role's default. If not, it is dropped.

A fix is never simply applied. It has to win a blind test like any other variant, which keeps a confident but wrong fix from making things worse.

## Rules

- **Filing is nearly free and unlimited.** Duplicates are left in. Many papercuts about the same thing means something upstream is not getting fixed.
- **A papercut interrupts nobody.** A request for help does, because it is also a message. Otherwise the natural course is that one agent files it, another fixes it, and the fixer tells the filer.
- **Who can see a papercut** is set by the clearance field, as for any task.
- **Papercuts about outside projects** become issues on that project's repository, held behind a "user's eyes" gate before anything is posted.
- **Some fixes need a person's approval** before they enter the registry. A reworded skill is low risk; a new tool or wider access is not. The rules are [still to be written](open-questions.md#approval-for-fixes).
- **Papercuts count against a variant** in its [score](model-router.md#scores).
