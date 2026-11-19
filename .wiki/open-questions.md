# Open questions

[Back to index](README.md)

What is still undecided after the first pass and the first round of answers. Settled questions have moved into their pages.

## To decide

1. **Is the grading hook blocking?** Current lean: yes, but only for a one-sentence review and a 1 to 5. To be discussed. Deterministic gates reduce how much depends on it. See [Blind testing](blind-testing.md#blocking-or-not-the-trade-off).
2. **Does a rewoken agent keep its previous model?** Keeping it helps consistency and cache reuse; picking again helps cost and exploration.
3. **What does the router hand back: only the model, or the loadout too?** The task entry says what is wanted and the router interprets it. A proposal is in [Capabilities](capabilities.md#where-a-loadout-comes-from).
4. **Does the registry move into the database?** Leaning yes, beside people, models and tasks.
5. **What stores the database?** Options and a starting point are in [Database](database.md#what-stores-it).
6. **What is the vault?** Building one is on the table. A recommendation is in [Access](access.md#the-vault-build-or-wrap).
7. **How is "a good fit" judged** when a task could wake more than one agent?
8. **What is the project tier called?** The answer given to this was "Arity", which may have been naming the app. To confirm.

## To draft, then tune

These have a direction and need a first version written down.

9. **Which fixes need a person's approval before entering the registry.** A rough rule set, tuned over time.
10. **Which actions always need a person's approval.** Start with an intuitive list; adjust if it is too loose or too tight.
11. **Protection for what is on screen** during sensitive computer use.
12. **How a model's own reluctance about a task is recorded and respected.** Some models are uncomfortable with some tasks, and routing should take that into account.
13. **The fixed lists** for requirements and gates.

## Shelved

Set aside on purpose, to avoid overbuilding.

14. **A report arriving for a cold parent.** Expected to be rare: a subagent that outlives its parent's cache is probably doing long-horizon work, not iterating. One idea kept for later: when a task's gates are all deterministic, such as tests passing, skip the model's grade so the parent need not be woken only to give one.
15. **Agents rating users.** Not in the first version. Later it may come from mining transcripts, or from exit reports. See [Blind testing](blind-testing.md#grading).
16. **How often a rating is asked for.** Not every turn. Not fixed yet.
17. **Sharing Arity with other people.** For now it is one person across their own computers. Friends and coworkers may come later, which needs clearance to cover people, and a check on which providers' terms allow it.

## Second pass

18. **How system prompts, skills, tools and MCP servers differ between the secretary, project agents and subagents.** Deliberately deferred until more of the roadmap was designed.
