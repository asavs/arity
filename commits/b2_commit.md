Author: B2-chatgpt

Bound runtime history and unify tool execution

Run every Runtime through the same loop whether it carries a bash call, a
teammate handoff, or only agents. Tool handles remain Runtime data rather than
creating dispatch branches. A list of Runtime values recurses through that
loop and merges its results; a channel message enters the existing synthesis
path.

Keep the prompt to the newest three history entries. Append one score record
per executed agent and retain the newest ten records. The retained history is
therefore bounded at 10 strings, while prompt construction reads 3 strings;
the per-runtime result loop executes once per agent.

Non-goals:
- Full chat framework.
- General tool registry.
- Explicit recursion API.

Empirical: memory footprint is capped at 10 retained history strings; prompt
assembly reads 3 entries and the score loop runs once per agent.
