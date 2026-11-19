# Access

[Back to index](README.md)

Agents that operate computers need to sign in to things. This page covers how they get passwords and other credentials without those secrets ever passing through a model.

Parts of this page began as a proposal. The points under "Decided" below have since been confirmed.

![Using a credential without seeing it](diagrams/access.svg)

## The rule: the model never sees the secret

An agent does not receive a password. It asks an **access broker** to sign in somewhere. The broker checks the agent is allowed, takes the secret from the **vault**, and fills it in directly on the agent's workspace.

Two reasons this matters more here than in most systems:

- **Model choice is blind and spread across providers.** Anything in an agent's context is sent to whichever provider the router picked, which nobody in the conversation knows. A password in context would go to an unknown third party.
- **Whatever a model sees can come back out**, in a report, a papercut, a log, or a message to another agent.

## Grants

A grant is permission to use one credential. It is what the "Grant access" step of [Instantiate](instantiate.md) hands out.

| Property | Meaning |
|---|---|
| Scoped | Only the credentials this task needs, decided by the role and the task's clearance |
| Temporary | Valid while the agent is live. Withdrawn when it goes cold or finishes; reissued on wake |
| Not transferable | A subagent cannot be given more than its parent holds |
| Logged | Every use is recorded against the agent run in the [database](database.md). The secret itself is never recorded |

## What the vault holds

- Passwords for sites and apps.
- API tokens and keys for services.
- Signed-in sessions.
- SSH keys.
- One-time codes, where the broker can produce them.

The endpoint pool's API keys (the ones for reaching models) follow the same rule: the router uses them and agents never see them.

## Clearance limits the variant

A task's clearance does not only limit which credentials are granted. It can also limit which models may be picked. A task that touches banking might be restricted to a local model, so that nothing on screen leaves your machines. "Pick variant" therefore has to respect clearance before it optimises for anything else.

## What the screen shows

Keeping the password out of the model's context is not enough for computer use. Once signed in, the screen itself can show private information, and an agent operating a computer sees the screen. Clearance is the control for that: it decides which models are allowed to look.

## Approval

Some actions should wait for a person even when the agent holds a grant: paying, sending, deleting, changing account settings. This is the "user's eyes" gate from the [task fields](database.md#tasks), applied to a single action instead of a whole task.

## Asking for more

An agent that finds it lacks access files a [papercut](papercuts.md) as a request for help. That reaches the parent or the user as a normal message, and either can widen the grant.

## Not settled

See [Open questions](open-questions.md).

## Decided

- **Clearance has two levels for now: local models only, or cloud models allowed.**
- **Clearance covers people as well as models.** That is what will allow sharing with friends or coworkers later.
- **Models' own reluctance counts.** Some models are uncomfortable with some tasks. Routing should be considerate of that and send the task elsewhere.
- **Agents can use sessions you are already signed in to.** The secretary adding to your calendar is the plain example.
- **One-time codes, passkeys and anything needing your device** block the task on you, as a "user's eyes" gate.
- **The approval list starts intuitive** and is adjusted if it proves too loose or too tight.
- **A grant is kept for a short while after an agent goes cold,** where that is safe, so waking is quick.
- **Protection for what is on screen** will be built.

## The vault: build or wrap

Building a password manager from scratch is on the table. A recommendation against doing all of it: the part that stores and encrypts secrets is where a small mistake is most costly and least visible, and well-tested tools already exist for it (the operating system's keychain, an existing password manager with a command line, or an encrypted file store). The part worth building is the access broker: the grants, the scoping, the logging and the filling-in. That is the part specific to Arity, and it never has to handle a secret for longer than the moment of use.
