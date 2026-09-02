# Kernel outline

The system in English, as nested lists. One rule carries the whole design:
**everything is a name until cast, and everything is a value after.**

## 1. What the model receives at a moment

- Tool schemas
- System text
  - base facts (what every kernel is told when it wakes cold)
  - role
  - skills
  - who can be reached with the message tool
  - the bot's last ledger entries
- The conversation so far
- The newest event

Everything else exists to produce these four, or to remember what came back.

## 2. Where things live

- **Library** — files, written by people, in git. Keyed by name.
  - the wake line (one sentence; there is no memory tier)
  - roles
  - skills (text only)
  - tool definitions: a schema the model sees + the name of a runner function
  - a Library entry may point at another Library entry, by name (a skill naming tools). Nothing else.
- **Seats** — providers, keys, quota left. Keyed by seat id.
  - cast asks it who has quota
  - the wire asks it for a URL and a key
  - a seat is subscription quota or API dollars; the router has to know which
- **Bots** — the staff list. Bot name -> role. A bot is a role with a name and a ledger.
  - anyone not in the list is a person, and messages to them go out the transport
- **Ledger** — per bot, append-only. Keyed by bot name.
  - name, journal, which kernel holds the bot now
  - read at kernel birth, written at kernel death
  - this is what makes a bot outlive a kernel
- **Store** — per session, append-only JSONL. Keyed by session id.
  - every message, model turn, tool result the moment asked to keep
  - this is the conversation, and the raw evidence
- **Scorecard** — not a store. Derived by reading the Store and counting. Can be rebuilt any time.
  - wins per (task kind, spec)

## 3. Names become values

- **Spec** — a row of names. No text.
  - seat id, model id, harness, prompt name, skill names, tool names
  - points into Library and Seats only
  - is also the key the scorecard counts by
- **Cast** — `resolve(spec, bot) -> State`. Happens once, at kernel birth.
  - look the bot up: its role is the task kind, for now
  - ask the scorecard who has won this kind of task
  - ask Seats who has quota
  - choose a spec
  - read each name in it out of Library
  - read the bot's journal out of Ledger
  - add the `message` tool, always
  - copy all of it into a new State
  - after this, nothing is looked up by name again
- **State** — a kernel's whole memory. All values, all copies.
  - session id (its only pointer to the Store)
  - bot name (its only pointer to the Ledger)
  - the resolved text blocks
  - the tool schemas
  - the messages so far
  - forkable: copy it, change one field, feed the same event
- **The moment** — `transition(state, event) -> (state, effects)`. Pure. No I/O.
  - appends to messages, changes status, lists what it wants done
  - the only freshness is `new_id`, injected, so a session replays deterministically
- **Effects** — values the loop executes
  - CallModel: the payload from section 1
  - ExecuteTool
  - StoreRecord: carries session id + seat id, so a record can point back to its conversation and its spec
    - the first record of every turn is a task record: kind (the role) + a one-line summary, so a taxonomy of tasks can be grown from the store later
  - Send: text to a recipient. With a call id, the model used the `message` tool and waits for the reply. Without one, it is the turn's answer to whoever asked.
- **The loop** — pop an event, call the moment, hand each effect to its seam, push what comes back
  - also the post office: a Send to a person goes out the transport; a Send to a bot wakes that bot's kernel, runs it until it answers, and hands the answer back as the tool result
  - no hierarchy: every bot can message every bot, and the person is just another recipient
  - woken kernels stay alive until the conversation ends; then each is retired
- **Seams** — five Protocols, the whole boundary between owned and commodity code
  - Model, Tools, Store, Transport, Observer
- **Wire** — the plug behind the Model seam
  - take the payload and a seat id
  - ask Seats for URL and key
  - write the provider's JSON, read the completion back
  - return a ModelCompleted event
  - the only code that knows a provider's shape; the only place cache breakpoints would ever be written

## 4. How results flow back

- ModelCompleted → State.messages (the moment appends it)
- StoreRecord → Store[session id]
- Store → Scorecard[spec] → the next Cast
- retire → Ledger[bot]: the kernel's own report + the archivist's account (archivist reads the Store to write it)
- the next Cast for that bot reads the Ledger

So the pointer graph is a line with one loop:
Library + Seats → Spec → Cast → State → moment → effects → wire → model → back into State,
records into Store → Scorecard → Cast. Ledger hangs off the side, read at birth, written at death.

## 5. A trial is a fork

- product over the fields of a spec you want to vary → N specs
- N States from one base State, one per spec
- the same event into each
- N results, keyed by spec, into the Store
- what varies cheaply
  - a different runner behind the same tool schema: invisible to the model, whole cache shared
  - a different text block appended late: everything before it stays warm
- what varies expensively
  - a different tool schema: it renders first, so both forks pay cold
  - a different base prompt: same

## 6. Files today → naive 1.0.0

| noun | today | lines | naive |
|---|---|---|---|
| Library | roles.py, skills.py, tiers.py, tasks.py | 892 | a folder of markdown |
| Seats | auth.py, cache_economics.py | 1,196 | a table + one refresh call |
| Memory | (outside the repo) | — | files |
| Ledger | ledger.py, archivist.py | 868 | as is |
| Store | handlers.py (JsonlRecordStore), trial_events.py, record_readers.py | 2,062 | ~50 |
| Scorecard | scorecard.py, standings.py | 395 | as is |
| Spec | composer.py (record) | — | one frozen dataclass |
| Cast | composer.py (modes), orchestrator.py | 616 | ~100 |
| State + fork | types.py, terrarium.py, race.py, evidence.py, spirals.py | 4,154 | one struct + ~150 |
| The moment | transition.py | 304 | as is |
| Effects + loop | types.py, runtime.py, seams.py | 494 | as is |
| Wire | wire.py, gemini_format.py, handlers.py | 1,242 | ~80 per provider |
| Front door | cli.py, inspection_cli.py, inspection.py, pulse.py, transports.py | 2,344 | ~100 |

Not on this list, and not in 1.0.0: LiteLLM, real MCP, Docker, a TUI, the phone. Each replaces one plug behind one seam.
