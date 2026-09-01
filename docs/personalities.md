# Personalities, types, skills, and teams

Arity composes an agent from independent pieces so each piece can be tested.

```text
personality:type + skills
```

- A **personality** is the agent's stable mandate and decision-making stance.
- A **type** is the language or domain in which it is working.
- A **skill** is an attachable method or capability.
- A **team** records organizational responsibility. It does not control who may communicate.

For example, `developer:python` and `developer:rust` share the Developer personality while
receiving different domain rules and verification commands. `test-engineer:python` applies the
same Python type to a different mandate. Computer use, browser navigation, visualization, and
specific test frameworks belong in skills rather than personality names.

## Packaged personalities

| Personality | Mandate |
| --- | --- |
| `secretary` | Hold the human relationship and route work without deciding hidden preferences. |
| `engineer` | Own technical contracts, architecture, feasibility, and engineering coordination. |
| `designer` | Own intended outward form and experience, including journeys, concepts, naming, interaction, and ergonomics. |
| `developer:<type>` | Implement a bounded brief using the selected domain pack. |
| `test-engineer:<type>` | Build independent tests and evaluations that produce trustworthy evidence. |
| `scout` | Gather attributed facts without turning them into design or engineering decisions. |
| `judge:<type>` | Weigh frozen evidence where verified facts do not settle a preference. |

Engineer, Developer, and Test Engineer belong to the `engineering` team. Designer remains the
Engineer's tier-one peer: Designer specifies what a thing should be like to encounter and use;
Engineer specifies how it can truthfully, safely, and reliably exist. A public API is a shared
seam—the Designer owns caller-facing concepts and ergonomics while the Engineer owns executable
contracts and internals.

## Test engineering

A Test Engineer's product is evidence. Objective claims become deterministic tests when a crisp
oracle exists. Graded or subjective claims become versioned eval cases, rubrics, calibrated model
graders, human-review protocols, or combinations of those approaches. The Test Engineer may
implement the complete evaluation apparatus but remains independent from implementation of the
artifact being evaluated.

The Judge does not build that apparatus. A Judge consumes the frozen evidence and expresses an
attributable opinion about the remainder that mechanical checks cannot settle.

When external or version-sensitive research becomes its own subtask, Test Engineer delegates a
bounded question to Scout. Scout returns sources, versions, facts, and uncertainty; Test Engineer
decides how the evidence changes the verification design.

## Messaging

Team membership is not a hierarchy or access-control list. Any personality using Arity's sandbox
message seam may address any registered personality by its exact name, including a typed name or
a user-defined role. The recipient retains its own personality, skills, tool permissions, and
denial rules; a message never transfers the sender's authority.

Misspelled or unknown names fail explicitly instead of falling through task-intent routing to the
Secretary. The registry is shared across Terrarium hops so locally registered personalities do
not disappear after one delegation.

Today this is synchronous request/reply delegation through the sandbox runner, bounded by the
task's hop limit. It is not durable mail, and other tool-runner axes do not automatically acquire
the message capability.
