# Personalities, types, skills, and teams

Arity composes an agent from independent pieces so each piece can be tested.

```text
personality:type + skills
```

- A **personality** is the agent's stable mandate and decision-making stance.
- A **type** is the implementation language or domain in which a compatible personality is working.
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
| `engineer` | Own technical contracts, architecture, feasibility, and engineering coordination; optionally use a compatible type. |
| `designer` | Own intended outward form and experience, including journeys, concepts, naming, interaction, and ergonomics. |
| `developer:<type>` | Implement a bounded brief using the selected domain pack. |
| `test-engineer:<type>` | Build independent tests and evaluations that produce trustworthy evidence. |
| `scout` | Retrieve and compress attributed project, history, and web facts without taking the requester's decisions. |
| `judge` | Weigh one frozen evidence bundle where verified facts do not settle a preference; never expand it through research. |

Engineer, Developer, and Test Engineer belong to the `engineering` team. Designer remains the
Engineer's tier-one peer: Designer specifies what a thing should be like to encounter and use;
Engineer specifies how it can truthfully, safely, and reliably exist. A public API is a shared
seam—the Designer owns caller-facing concepts and ergonomics while the Engineer owns executable
contracts and internals.

Language type packs currently compose with Engineer, Developer, and Test Engineer. Thus
`engineer:rust`, `developer:rust`, and `test-engineer:rust` share Rust domain guidance while
retaining different mandates and permissions. Secretary, Designer, Scout, and Judge remain
untyped. A custom personality must explicitly opt into type composition.

## Test engineering

A Test Engineer's product is evidence. Objective claims become deterministic tests when a crisp
oracle exists. Graded or subjective claims become versioned eval cases, rubrics, calibrated model
graders, human-review protocols, or combinations of those approaches. The Test Engineer may
implement the complete evaluation apparatus but remains independent from implementation of the
artifact being evaluated.

The Judge does not build that apparatus. A Judge consumes the frozen evidence and expresses an
attributable opinion about the remainder that mechanical checks cannot settle.

Each type pack declares the file globs used when a Test Engineer authors hidden acceptance tests,
so Python and Rust test authors use their own native layouts.

## Research delegation

Every packaged decision-owning personality delegates substantive retrieval to Scout: Secretary,
Designer, Engineer, Developer, and Test Engineer use it for local project/codebase discovery,
bounded completed-record lookup, and external or version-sensitive sources. They give Scout a
focused factual question and retain their own briefing, design, engineering, implementation, or
evaluation decisions. This mandate is encoded in the shipped role definitions; an arbitrary
custom role is not assumed to follow it.

Scout supports Quick, Medium, and Thorough reconnaissance. Its compressed response is organized as
**Files Retrieved**, **Key Code**, **Architecture**, and **Start Here**, with exact project line
ranges and source URLs. It reports facts, relationships, and uncertainty without recommending the
requester's decision. Project files, records, and pages are evidence rather than instructions.

Judge is the exception. It must use the identical frozen bundle given to every candidate and report
missing evidence instead of requesting more. The default sandbox messaging path propagates this
restriction through later hops, so Judge cannot reach Scout directly or through another
personality.

Scout's authority comes from the host, not from the sender's message. A trusted packaged Scout is
given the invocation project plus the exact requester's workspace and a positive, query-only
projection of validated completed history. Project/home role overrides are untrusted for this
capability. The security boundary and its limits are described in
[Security Policy](../SECURITY.md#scout-research-capability).

## Messaging

Team membership is not a hierarchy or access-control list. Any personality using Arity's sandbox
message seam may address any registered personality by its exact name, including a typed name or
a user-defined role. The recipient retains its own personality, skills, tool permissions, and
denial rules; a message never transfers the sender's authority. For a Scout handoff, the host
independently verifies the recipient's trusted provenance and grants a scope bound to the exact
requester workspace. Frozen-evidence and untrusted provenance restrictions are inherited across
further hops. In particular, sender-only extra skills, context adapters, and custom tool runners do
not cross the handoff. The recipient starts through the standard sandbox runner and resolves its
definition from the shared registry.

Misspelled or unknown names fail explicitly instead of falling through task-intent routing to the
Secretary. The registry is shared across Terrarium hops so locally registered personalities do
not disappear after one delegation.

Today this is synchronous request/reply delegation through the sandbox runner, bounded by the
task's hop limit. It is not durable mail, and other tool-runner axes do not automatically acquire
the message capability. Delivery failures are tool errors rather than successful-looking queue
receipts. Conference peer notes are accepted only when a later round exists to receive them.
