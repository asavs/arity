# Open questions

[Back to index](README.md)

Decisions the design needs but does not have yet. Move an item to the relevant page once it is decided.

## Messages and delivery

1. **Does an inbox message wake a cold agent?** It could wake it immediately, or wait until something more urgent does.
2. **Does a report to a cold parent wake it?** A report is a message too. Waking means a router call, and the parent might come back as a different model.
3. **Do the user's messages follow the same steer or inbox choice?** Or do user messages always interrupt?
4. **Can messages between neighbours go both ways?** Currently assumed yes (a child can ask its parent a question). Reports are one-way.
5. **Can subagents be woken, or only spawned fresh?**

## Model router

6. **Does a rewoken agent keep its previous model?** Keeping it helps consistency and cache reuse; re-routing helps cost and exploration.
7. **What exactly is the grade?** Which signals, with what weights, and who is allowed to grade.
8. **How is cost-driven traffic kept from skewing the leaderboard?**
9. **What does "shared" mean for subscriptions?** Pooling one person's subscriptions across their own agents is straightforward. Pooling across several people may break a provider's terms and needs checking.

## Capabilities and roles

**Biggest open area: how do system prompts, skills, tools and MCP servers differ between the secretary, project agents and subagents?** Deliberately deferred to a second pass after more of the roadmap is designed.

10. **Who picks the role?** The parent in its request, the router, or a mix.
11. **Can a parent add to or trim a role's loadout for one task?**
12. **Does a child's loadout have to be a subset of its parent's?**
13. **Who can add roles, skills and MCP servers to the registry?**

## Blind testing

15. **Is the grading hook blocking?** Deliberately left open; more background is needed before deciding. The trade-off is laid out in [Blind testing](blind-testing.md#blocking-or-not-the-trade-off).
16. **One change per trial, or several?** Leaning towards one, but there are many slots to trial (a computer use task alone could use several MCP servers or CLI tools), so possibly several.
17. **How large is the exploration budget, and who sets it?**
18. **Who grades the secretary and project agents?** The hook covers subagents. The secretary reports only to the user, and a project agent may run for weeks before a final report.
19. **Is the variant ever revealed to the grader afterwards?**
20. **Are harnesses swappable per agent, or fixed per machine?** Several third-party pieces in roadmap item 10 may be harness variants.

21. **What do agents rate users on, and who sees those ratings?** Agents can rate and write reports about users. Whether the user sees their own, and what the ratings are used for, is open.
22. **How often is a rating asked for?** Every turn, every report, or sampled.

## Papercuts

23. **Who triages and proposes fixes?** A dedicated agent, a person, or an agent whose proposals a person approves.
24. **Which fixes need a person's approval before they enter the registry?** A reworded skill is low risk; a new tool or wider access is not.
25. **Can users see papercuts that agents file?** Including ones about the user's own briefs.
26. **How is noise handled?** Limits on how many an agent can file, and how duplicates are merged.
27. **Does a papercut ever interrupt anyone?** A help request does, by being a message. Should a serious complaint?

## Database

28. **When is the model behind an agent run revealed?** After grading closes, never to the grader, or only in aggregate. Browsing the wiki must not unseal a live trial.
29. **Does the registry live in the database too?** Currently separate, on the grounds that it describes what is available, not what happened.
30. **Who can create and edit tasks?** The user, the secretary, project agents; whether subagents can.
31. **What are the clearance levels, and who assigns them?**
32. **Is there a fixed vocabulary for requirements and gates?** Free text is flexible; a fixed list can be matched to machines and checks automatically.
33. **Which fields are required?** The list is a set of candidates. Due date is marked uncertain.
34. **What stores it?** A graph database, files in a repository, or something else.

## Computers

35. **Does a subagent share its project's machine or get its own?** Sharing keeps files in one place; separate machines keep subagents from interfering.
36. **Does the agent run on the machine, or operate it from outside?** This decides what "computer use" means here.
37. **Are your own machines in the pool, and under what limits?**
38. **What happens when nothing matches?** The task waits, a cloud machine is created, or the request fails.
39. **Who sets the spending limit for cloud machines?**
40. **Is a sleeping workspace kept forever, or cleaned up after some time?**

## Access

41. **What is the vault?** An existing password manager, or something built for this.
42. **How are one-time codes and passkeys handled?** Some need a person's phone or device.
43. **Which actions always need a person's approval?** Paying, sending, deleting, changing settings; the exact list.
44. **What are the clearance levels, and which models does each allow?** Linked to question 31.
45. **Can an agent use sessions you are already signed in to, or only credentials in the vault?**
46. **How is the screen handled for sensitive tasks?** Restrict the model, mask parts of the screen, or both.
47. **Is a grant withdrawn the moment an agent goes cold?** Or kept for a short time to make waking faster.

## Naming

14. **"Project agent" or "project manager"?** Name pending.
