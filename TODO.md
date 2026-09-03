# TODO

Next work, roughly in order. What 1.0.0 deliberately left out is in
`docs/1.0.0-checklist.md`; this is what to pick up first.

## First

- `arity doctor`: which key env vars are set, which seats answer, which lock
  files are stale. Where most debugging will end.

## Follow one value through every seam

- `message(to, content, kind)`: let the sender label the task. `kind` on the
  tool schema, on `Message` and on `Send`; the moment copies it into the Send,
  the post office copies it into the Message, the store journals it for free,
  `rows()` reads it off the first Message, and `standings(role, "kind")`
  already works. The vocabulary the taxonomy should grow from is the models'
  own. ~8 lines across five files.

## Trials below the seam

- A fan-out plug behind the Tools seam: one `ExecuteTool` effect runs against
  two runners (two MCP servers, two sandboxes), a judge picks, one result goes
  back. No fork, no new kernel, the model never knows. ~30 lines. The plug
  needs the session id to record who won; hand it over when the plug is built.
- An outcome line for seam-level trials that names the factor it varied
  (`mcp: a`), and `standings()` reading that factor off the row, the same way
  `worked` is read today. `rows()` merges any extra outcome fields into the row.
- Real MCP behind the Tools seam, so there is something to fan out to.
  JSON-RPC over a subprocess: `tools/list` is `schemas`, `tools/call` is
  `execute`. `ToolSeam.schemas()` is never called today; the tool block comes
  from the library at cast. Dump the server's tool list into library JSON by
  hand first; teach cast to ask the seam later.

## Trials above the seam

- The judge. Today a person picks. First candidate: one prompt with the N
  answers, any wire, parse the number back, pass it as `pick` when the person
  presses enter without choosing. ~15 lines. A diff against hidden tests
  waits for the task bank.
- Long-lived forks: two kernels fed the same events for many turns, compared
  as trajectories rather than single answers. Keep it in the front door: the
  thing you are talking to becomes a list of States, every line goes to all
  of them, answers print side by side each turn, a pick collapses the list.
  ~20 lines, no kernel change.
- Replay a session as someone else. The journal holds every Message the
  person sent. Feed only those, in order, to a fork on a different spec and
  let it regenerate the rest. Every old session becomes a benchmark the judge
  can score.
- A task bank: a folder, one markdown prompt plus one check script per task.
  `arity bench` runs each through N specs and the check script is the pick.
  This is the closed loop the checklist calls the part that is ours.

## Plugs

- A retrying plug: a Model seam plug that wraps another and retries on a 429.
  Its cousin is a fallback plug: try seat A, on failure try seat B. Plugs
  compose; nothing demonstrates that yet.
- A sandbox behind the Tools seam: run the runner as a subprocess in a temp
  folder with the arguments as JSON. ~20 lines, same shape as the fan-out.
  A container later.
- The person is a Model plug. Put the person in the staff list with a harness
  called `human`; the plug reads stdin and returns a `ModelCompleted`. A Send
  to a person is then a wake, the Transport seam disappears, and the phone is
  a plug. Answers the mid-turn question by making the human a bot.
- An inbox folder as the transport, for when the person is not at the
  keyboard. A Send to a person writes a file; the person answers by writing
  one back. The same folder is the mailbox for a bot messaged mid-turn and
  for two `arity` processes on one home.
- OAuth subscription seats, if a direct wire ever beats the pinned CLI harness.
  `arity/auth.py` on the `0.5-sprawl` branch is the quarry.
- The TUI, with the flower as its loading screen. A finished observer with
  tests sits at `1bcb534` on `0.5-sprawl`; start there.
- Cache breakpoints in `wire_anthropic.py`, once the research in the checklist
  says where they earn their keep. One line, after the cached-token count
  lands in `rows()`.

## Memory

- A `note` tool: a bot appends to its own ledger mid-life, not only at death.
- A `recall` tool: a bot reads further back than the five entries cast wakes
  it with. With `note`, this is the deferred memory tier as two library tools
  and no kernel change.
- Report versus archive as data. Every retirement writes two accounts of one
  session. A model scoring the gap is an honesty axis per bot per spec, one
  more field on the row.

## Small and obvious

- `arity show <session>`: the archivist's transcript renderer, as a subcommand.
- `arity standings <role>` and `arity rows`: the scorecard has the functions;
  nothing prints them.
- Fold every journal as a test: loop over every session file, fold it, assert
  nothing raises. A regression test for the moment that grows itself.
- Seats reset on their clock: when `resets_at` has passed, `remaining` goes
  back to one. This is the policy the 1.0.1 notes leave open.
- Epoch from a hash of the role, skill and tool texts, instead of a hand-bumped
  `library/EPOCH`. Old evidence stops voting the moment a file changes.
- Dollars per row: a price table per model id, multiplied into `rows()`.
  Cost-aware ranking then needs no new data.

## Not doing

- Threads. The nested-call post office is what makes the program readable as
  a stack. Everything above runs without a scheduler; the inbox folder covers
  the one case that looks like it needs one.

## Research, cheapest first

- Does the keepalive register as a cache hit? Ping, real call, compare
  cached-token counts with and without the ping.
- Does `codex exec` put only the answer on stdout when it has quota?
- The rest is in `docs/1.0.0-checklist.md` §4.
