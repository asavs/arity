# Open questions

[Back to index](README.md)

What is undecided. Each question links back to the section that raises it.

## To decide

### Model on wake

Does a woken agent keep its previous model? Keeping it helps consistency and cache reuse; picking again helps cost and exploration. Raised in [Agents](agent-tiers.md#agent-lifecycle).

### What the router returns

Does the router hand back only the model, or the assembled loadout too? Raised in [Capabilities](capabilities.md#where-a-loadout-comes-from).

### Registry in the database

Does the registry become part of the database? Leaning yes, beside people, models and tasks. Raised in [Database](database.md#what-it-holds) and [Capabilities](capabilities.md#the-three-kinds-of-capability).

### Database storage

What holds the database's two tables? The options are compared in [Database](database.md#storage).

### Vault

Is the vault built, or an existing tool? The storing and encrypting of secrets is where a small mistake is most costly and hardest to notice, and tested tools exist for it: the operating system's keychain, a password manager with a command line, an encrypted file store. That argues for wrapping one and building only the broker. Raised in [Access](access.md#what-to-build).

### Judging a good fit

How is "a good fit" judged when a task could wake more than one agent? Raised in [How work starts](work.md#the-board).

### Tier differences

How do system prompts, skills, tools and MCP servers differ between the secretary, project agents and subagents? Raised in [Agents](agent-tiers.md#what-an-agent-is-made-of).

## To draft

Direction is agreed; a first version needs writing.

### Approval for fixes

Which fixes need a person's approval before entering the registry. Raised in [Papercuts](papercuts.md#rules).

### Approval for actions

Which actions always need a person's approval. Raised in [Access](access.md#approval).

### Screen protection

Protection for what is on screen during sensitive computer use. Raised in [Access](access.md#what-the-screen-shows).

### Model reluctance

How a model's reluctance about a task is recorded and respected. Raised in [Access](access.md#clearance).

### Requirement and gate lists

The fixed lists that requirements and gates are chosen from. Raised in [Database](database.md#tasks).

## Set aside

Left out on purpose to avoid overbuilding.

### Agents rating users

Later, possibly through exit reports or by mining transcripts. Raised in [Blind testing](blind-testing.md#grading).

### Rating frequency

How often a rating is asked for. Raised in [Blind testing](blind-testing.md#grading).

### Sharing with other people

Arity is one person across their own computers. Sharing needs clearance for people and a check on which providers' terms allow it. Raised in [Access](access.md#clearance) and [Model router](model-router.md#endpoint-pool).
